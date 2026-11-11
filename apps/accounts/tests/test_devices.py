"""Signed-in devices: listing sign-ins, ending them, password changes and the maximum sign-in length."""
import time
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import devices
from apps.accounts.models import Role, SignedInDevice, SignInMethod
from apps.audit.models import AuditLog
from apps.institutions.models import InstitutionProfile
from apps.notifications.models import PushSubscription
from conftest import PASSWORD, client_for, make_user

pytestmark = pytest.mark.django_db

CHROME_WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36")
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                 "Version/18.1 Mobile/15E148 Safari/604.1")
SAMSUNG_ANDROID = ("Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36")


def sign_in(user, user_agent=CHROME_WINDOWS):
    client = APIClient(HTTP_USER_AGENT=user_agent)
    response = client.post("/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json")
    assert response.status_code == 200, response.data
    return client


def device_of(client):
    return SignedInDevice.objects.get(session_key=client.session.session_key)


def subscribe(device, n):
    return PushSubscription.objects.create(user=device.user, device=device, endpoint=f"https://push.example/{n}",
                                           p256dh="k", auth="a")


def signed_in(client):
    return client.get("/api/v1/auth/me/").status_code == 200


def test_describe_recognises_common_browsers():
    assert devices.describe(CHROME_WINDOWS) == ("Chrome", "Windows")
    assert devices.describe(SAFARI_IPHONE) == ("Safari", "iPhone")
    assert devices.describe(SAMSUNG_ANDROID) == ("Samsung Internet", "Android")
    assert devices.describe("") == ("Unknown browser", "unknown device")


def test_each_sign_in_is_listed_and_this_device_is_marked(lecturer):
    laptop = sign_in(lecturer)
    sign_in(lecturer, SAFARI_IPHONE)
    rows = laptop.get("/api/v1/auth/devices/").data
    assert len(rows) == 2
    current = [row for row in rows if row["current"]]
    assert len(current) == 1
    assert (current[0]["browser"], current[0]["system"]) == ("Chrome", "Windows")
    assert current[0]["method"] == SignInMethod.PASSWORD
    assert {row["system"] for row in rows} == {"Windows", "iPhone"}
    # Someone else's sign-ins are never listed.
    other = client_for(make_user(Role.LECTURER)).get("/api/v1/auth/devices/").data
    assert len(other) == 1 and other[0]["current"]


def test_signing_out_removes_the_device(lecturer):
    client = sign_in(lecturer)
    assert SignedInDevice.objects.filter(user=lecturer).count() == 1
    client.post("/api/v1/auth/logout/")
    assert not SignedInDevice.objects.filter(user=lecturer).exists()


def test_sign_out_another_device_ends_it_and_its_notifications(lecturer):
    laptop, phone = sign_in(lecturer), sign_in(lecturer, SAFARI_IPHONE)
    phone_device = device_of(phone)
    subscribe(phone_device, 1)
    kept = subscribe(device_of(laptop), 2)

    response = laptop.delete(f"/api/v1/auth/devices/{phone_device.pk}/")
    assert response.status_code == 204
    assert not signed_in(phone)
    assert signed_in(laptop)
    assert list(PushSubscription.objects.filter(user=lecturer)) == [kept]
    assert AuditLog.objects.filter(action="auth.device_signed_out", summary="Signed out Safari on iPhone").exists()

    # This device is signed out with Sign out, not from the list; another person's device is not found.
    assert laptop.delete(f"/api/v1/auth/devices/{device_of(laptop).pk}/").status_code == 400
    stranger = sign_in(make_user(Role.LECTURER))
    assert stranger.delete(f"/api/v1/auth/devices/{device_of(laptop).pk}/").status_code == 404
    assert signed_in(laptop)


def test_sign_out_of_all_other_devices(lecturer):
    laptop, phone, tablet = sign_in(lecturer), sign_in(lecturer, SAFARI_IPHONE), sign_in(lecturer, SAMSUNG_ANDROID)
    subscribe(device_of(phone), 1)
    mine = subscribe(device_of(laptop), 2)
    response = laptop.post("/api/v1/auth/devices/sign-out-others/")
    assert response.data == {"signed_out": 2}
    assert signed_in(laptop) and not signed_in(phone) and not signed_in(tablet)
    assert list(PushSubscription.objects.filter(user=lecturer)) == [mine]
    assert len(laptop.get("/api/v1/auth/devices/").data) == 1


def test_password_change_signs_out_other_devices_by_default(lecturer):
    laptop, phone = sign_in(lecturer), sign_in(lecturer, SAFARI_IPHONE)
    subscribe(device_of(phone), 1)
    mine = subscribe(device_of(laptop), 2)
    response = laptop.post("/api/v1/auth/password/change/",
                           {"current_password": PASSWORD, "new_password": "A-new-Passw0rd-2026"}, format="json")
    assert response.status_code == 204
    assert signed_in(laptop) and not signed_in(phone)
    assert list(PushSubscription.objects.filter(user=lecturer)) == [mine]
    # This device keeps its row, with the new session id.
    rows = laptop.get("/api/v1/auth/devices/").data
    assert len(rows) == 1 and rows[0]["current"]


def test_password_change_can_keep_other_devices_signed_in(lecturer):
    laptop, phone = sign_in(lecturer), sign_in(lecturer, SAFARI_IPHONE)
    subscribe(device_of(phone), 1)
    response = laptop.post("/api/v1/auth/password/change/", {
        "current_password": PASSWORD, "new_password": "A-new-Passw0rd-2026", "sign_out_other_devices": False,
    }, format="json")
    assert response.status_code == 204
    assert signed_in(laptop) and signed_in(phone)
    assert PushSubscription.objects.filter(user=lecturer).count() == 1


def test_a_password_set_by_an_administrator_signs_out_everywhere(admin_user, lecturer):
    phone = sign_in(lecturer, SAFARI_IPHONE)
    subscribe(device_of(phone), 1)
    response = client_for(admin_user).post(f"/api/v1/users/{lecturer.pk}/set-password/",
                                           {"new_password": "Temporary-Passw0rd-1"}, format="json")
    assert response.status_code == 204
    assert not signed_in(phone)
    assert not PushSubscription.objects.filter(user=lecturer).exists()
    assert not SignedInDevice.objects.filter(user=lecturer).exists()


def test_administrator_signs_a_user_out_everywhere(admin_user, lecturer):
    laptop, phone = sign_in(lecturer), sign_in(lecturer, SAFARI_IPHONE)
    subscribe(device_of(phone), 1)
    admin = client_for(admin_user)
    assert admin.post(f"/api/v1/users/{lecturer.pk}/sign-out-everywhere/", {}, format="json").status_code == 400
    response = admin.post(f"/api/v1/users/{lecturer.pk}/sign-out-everywhere/", {"reason": "Lost phone"}, format="json")
    assert response.data == {"signed_out": 2}
    assert not signed_in(laptop) and not signed_in(phone)
    assert not PushSubscription.objects.filter(user=lecturer).exists()
    log = AuditLog.objects.get(action="user.signed_out_everywhere")
    assert log.reason == "Lost phone"
    # Only administrators; and not on their own account (that is the profile page's job).
    assert client_for(lecturer).post(f"/api/v1/users/{admin_user.pk}/sign-out-everywhere/",
                                     {"reason": "x"}, format="json").status_code == 403
    assert admin.post(f"/api/v1/users/{admin_user.pk}/sign-out-everywhere/",
                      {"reason": "x"}, format="json").status_code == 400


def test_registrar_signs_a_student_out_everywhere(registrar, world):
    student = world.student
    phone = sign_in(student.user, SAFARI_IPHONE)
    response = client_for(registrar).post(f"/api/v1/students/{student.pk}/sign-out-everywhere/",
                                          {"reason": "Phone stolen"}, format="json")
    assert response.status_code == 200
    assert response.data == {"signed_out": 1}
    assert not signed_in(phone)


def test_deactivating_an_account_ends_its_sign_ins_and_notifications(admin_user, lecturer):
    phone = sign_in(lecturer, SAFARI_IPHONE)
    subscribe(device_of(phone), 1)
    client_for(admin_user).post(f"/api/v1/users/{lecturer.pk}/deactivate/", {"reason": "Left"}, format="json")
    assert not signed_in(phone)
    assert not PushSubscription.objects.filter(user=lecturer).exists()


def _age_sign_in(client, seconds):
    session = client.session
    session[devices.SIGNED_IN_AT] = int(time.time()) - seconds
    session.save()


def test_staff_sign_ins_end_after_the_maximum_length(lecturer):
    client = sign_in(lecturer)
    _age_sign_in(client, 23 * 3600)
    assert signed_in(client)
    _age_sign_in(client, 25 * 3600)  # default: 24 hours for staff
    assert not signed_in(client)
    assert not SignedInDevice.objects.filter(user=lecturer).exists()


def test_students_stay_signed_in_longer_and_the_lengths_are_settings(world):
    student = world.student
    client = sign_in(student.user)
    _age_sign_in(client, 6 * 24 * 3600)
    assert signed_in(client)  # default: 7 days for students
    profile = InstitutionProfile.load()
    profile.student_session_days = 3
    profile.save()  # saving clears the cached lengths
    assert not signed_in(client)


def test_session_lengths_are_in_institution_settings(admin_user):
    admin = client_for(admin_user)
    data = admin.get("/api/v1/institution/settings/").data
    assert (data["student_session_days"], data["staff_session_hours"]) == (7, 24)
    response = admin.patch("/api/v1/institution/settings/", {"staff_session_hours": 0}, format="json")
    assert response.status_code == 400
    response = admin.patch("/api/v1/institution/settings/", {"staff_session_hours": 12}, format="json")
    assert response.status_code == 200
    assert devices.session_limits() == (7, 12)


def test_a_session_from_before_devices_were_recorded_is_listed_on_its_next_request(lecturer):
    client = sign_in(lecturer)
    session = client.session
    for key in (devices.DEVICE_ID, devices.SIGNED_IN_AT, devices.SEEN_AT, devices.SEEN_KEY):
        session.pop(key, None)
    session.save()
    SignedInDevice.objects.all().delete()
    assert signed_in(client)
    assert SignedInDevice.objects.filter(user=lecturer, session_key=client.session.session_key).exists()


def test_last_activity_is_written_at_most_every_few_minutes(lecturer):
    client = sign_in(lecturer)
    device = device_of(client)
    long_ago = timezone.now() - timedelta(hours=1)
    SignedInDevice.objects.filter(pk=device.pk).update(last_seen_at=long_ago)
    client.get("/api/v1/auth/me/")
    device.refresh_from_db()
    assert device.last_seen_at == long_ago  # seen less than 5 minutes ago: not written again
    session = client.session
    session[devices.SEEN_AT] = int(time.time()) - 600
    session.save()
    client.get("/api/v1/auth/me/")
    device.refresh_from_db()
    assert device.last_seen_at > long_ago


def test_push_subscriptions_are_linked_to_the_device(lecturer, settings):
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = "test-public", "test-private"
    client = sign_in(lecturer)
    endpoint = "https://fcm.googleapis.com/fcm/send/device-link"
    response = client.post("/api/v1/notifications/push/subscribe/",
                           {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}, format="json")
    assert response.status_code == 201
    assert PushSubscription.objects.get(endpoint=endpoint).device == device_of(client)


def test_daily_cleanup_removes_expired_sessions_and_their_devices(lecturer):
    live, old = sign_in(lecturer), sign_in(lecturer, SAFARI_IPHONE)
    Session.objects.filter(session_key=old.session.session_key).update(expire_date=timezone.now() - timedelta(hours=1))
    subscription = subscribe(device_of(old), 1)
    call_command("clear_old_sessions")
    assert list(SignedInDevice.objects.filter(user=lecturer)) == [device_of(live)]
    subscription.refresh_from_db()  # an expired sign-in keeps its notifications
    assert subscription.device is None
