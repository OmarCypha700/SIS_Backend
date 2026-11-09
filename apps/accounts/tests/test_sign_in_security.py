"""The growing wait after wrong passwords, and signing in with a passkey."""
import datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts import passkeys, signin
from apps.accounts.models import Passkey, Role, SignInDelay
from apps.audit.models import AuditLog
from apps.notifications.models import Kind, Notification
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db
PASSWORD = "Correct-Horse-Battery-9"


def attempt(api, email, password):
    return api.post("/api/v1/auth/login/", {"email": email, "password": password}, format="json")


@pytest.fixture
def person(db):
    user = make_user(Role.LECTURER)
    user.set_password(PASSWORD)
    user.save()
    return user


def test_wait_schedule():
    assert [signin.wait_after(n) for n in range(1, 12)] == [0, 0, 0, 15, 30, 60, 120, 240, 480, 900, 900]


def test_three_free_attempts_then_a_growing_wait(api, person):
    for _ in range(3):
        response = attempt(api, person.email, "wrong")
        assert response.status_code == 400 and response.data["code"] == "invalid_credentials"
    fourth = attempt(api, person.email, "wrong")
    assert fourth.status_code == 429 and fourth.data["code"] == "sign_in_delayed"
    assert fourth.data["errors"]["retry_after"] == 15 and "15 seconds" in fourth.data["detail"]
    # While waiting, even the right password is refused (and not checked).
    waiting = attempt(api, person.email, PASSWORD)
    assert waiting.status_code == 429 and 0 < waiting.data["errors"]["retry_after"] <= 15
    # After the wait, the right password works and the count is cleared.
    SignInDelay.objects.filter(email=person.email).update(locked_until=timezone.now() - datetime.timedelta(seconds=1))
    assert attempt(api, person.email, PASSWORD).status_code == 200
    assert not SignInDelay.objects.filter(email=person.email).exists()


def test_unknown_emails_get_the_same_treatment(api):
    responses = [attempt(api, "nobody@example.test", "wrong") for _ in range(4)]
    assert [r.status_code for r in responses] == [400, 400, 400, 429]
    assert responses[3].data["detail"].startswith("Invalid email or password.")


def test_email_case_and_spaces_do_not_reset_the_count(api, person):
    for email in (person.email, person.email.upper(), f"  {person.email} "):
        attempt(api, email, "wrong")
    assert attempt(api, person.email.title(), "wrong").status_code == 429


def test_failures_are_forgotten_after_a_day(person):
    old = timezone.now() - datetime.timedelta(hours=25)
    SignInDelay.objects.create(email=person.email, failures=8, last_failure_at=old)
    assert signin.record_failure(person.email) == 0  # counted from one again


def test_the_account_holder_is_warned_after_ten_wrong_passwords(person, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(signin.ALERT_AT):
            signin.record_failure(person.email)
    assert Notification.objects.filter(recipient=person, kind=Kind.SECURITY).count() == 1
    assert AuditLog.objects.filter(action="auth.repeated_failed_sign_ins").exists()


def test_administrator_clears_the_wait_and_a_new_password_does_too(api, person, admin_user):
    for _ in range(5):
        attempt(api, person.email, "wrong")
    assert signin.seconds_left(person.email) > 0
    response = client_for(admin_user).post(f"/api/v1/users/{person.pk}/clear-sign-in-delay/")
    assert response.status_code == 200 and response.data["was_waiting_seconds"] > 0
    assert signin.seconds_left(person.email) == 0
    for _ in range(5):
        attempt(api, person.email, "wrong")
    client_for(admin_user).post(f"/api/v1/users/{person.pk}/set-password/", {"new_password": "Another-Strong-Pass-42"},
                                format="json")
    assert signin.seconds_left(person.email) == 0
    assert client_for(person).post(f"/api/v1/users/{person.pk}/clear-sign-in-delay/").status_code == 403


# --- Passkeys ----------------------------------------------------------------------------------

@pytest.fixture
def fake_device(monkeypatch):
    """Stands in for the browser's signature checks, which need a real authenticator."""
    state = {"sign_count": 0}

    def registered(**kwargs):
        assert kwargs["require_user_verification"] is True and kwargs["expected_challenge"]
        return SimpleNamespace(credential_id=b"credential-1", credential_public_key=b"public-key", sign_count=0)

    def authenticated(**kwargs):
        assert kwargs["require_user_verification"] is True and kwargs["credential_public_key"] == b"public-key"
        state["sign_count"] += 1
        return SimpleNamespace(new_sign_count=state["sign_count"])

    monkeypatch.setattr(passkeys, "verify_registration_response", registered)
    monkeypatch.setattr(passkeys, "verify_authentication_response", authenticated)
    return state


CREDENTIAL = {"id": "Y3JlZGVudGlhbC0x", "rawId": "Y3JlZGVudGlhbC0x", "type": "public-key",
              "response": {"transports": ["internal"]}}


def test_register_a_passkey_then_sign_in_with_it(api, person, fake_device):
    me = client_for(person)
    options = me.post("/api/v1/auth/passkeys/register/options/").data
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["rp"]["id"] == "localhost" and options["user"]["name"] == person.email
    added = me.post("/api/v1/auth/passkeys/register/", {"credential": CREDENTIAL, "name": "My phone"}, format="json")
    assert added.status_code == 201 and added.data["name"] == "My phone"
    assert [p["name"] for p in me.get("/api/v1/auth/passkeys/").data] == ["My phone"]

    options = api.post("/api/v1/auth/passkeys/sign-in/options/").data
    assert options["userVerification"] == "required" and options.get("allowCredentials", []) == []
    signed_in = api.post("/api/v1/auth/passkeys/sign-in/", {"credential": CREDENTIAL}, format="json")
    assert signed_in.status_code == 200 and signed_in.data["email"] == person.email
    assert api.get("/api/v1/auth/me/").data["email"] == person.email
    assert Passkey.objects.get().sign_count == 1
    assert AuditLog.objects.filter(action="auth.passkey_sign_in").exists()


def test_a_challenge_is_used_once(api, person, fake_device):
    Passkey.objects.create(user=person, credential_id=CREDENTIAL["id"], public_key=b"public-key", name="Phone")
    api.post("/api/v1/auth/passkeys/sign-in/options/")
    assert api.post("/api/v1/auth/passkeys/sign-in/", {"credential": CREDENTIAL}, format="json").status_code == 200
    again = api.post("/api/v1/auth/passkeys/sign-in/", {"credential": CREDENTIAL}, format="json")
    assert again.status_code == 400 and again.data["code"] == "passkey_expired"


def test_removed_passkeys_and_disabled_accounts_cannot_sign_in(api, person, fake_device):
    api.post("/api/v1/auth/passkeys/sign-in/options/")
    unknown = api.post("/api/v1/auth/passkeys/sign-in/", {"credential": CREDENTIAL}, format="json")
    assert unknown.status_code == 400 and unknown.data["code"] == "passkey_unknown"
    Passkey.objects.create(user=person, credential_id=CREDENTIAL["id"], public_key=b"public-key", name="Phone")
    person.is_active = False
    person.save()
    api.post("/api/v1/auth/passkeys/sign-in/options/")
    assert api.post("/api/v1/auth/passkeys/sign-in/", {"credential": CREDENTIAL}, format="json").status_code == 400


def test_people_manage_only_their_own_passkeys(person, fake_device):
    other = make_user(Role.STUDENT)
    theirs = Passkey.objects.create(user=other, credential_id="b3RoZXI", public_key=b"k", name="Their phone")
    me = client_for(person)
    assert me.delete(f"/api/v1/auth/passkeys/{theirs.pk}/").status_code == 404
    assert Passkey.objects.filter(pk=theirs.pk).exists()
    mine = Passkey.objects.create(user=person, credential_id="bWluZQ", public_key=b"k", name="Mine")
    assert me.delete(f"/api/v1/auth/passkeys/{mine.pk}/").status_code == 204
    assert AuditLog.objects.filter(action="auth.passkey_removed").exists()
