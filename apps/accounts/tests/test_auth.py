import re

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from conftest import PASSWORD, client_for, make_user

pytestmark = pytest.mark.django_db


def test_login_success_sets_session_and_returns_profile(api, lecturer):
    response = api.post("/api/v1/auth/login/", {"email": lecturer.email.upper(), "password": PASSWORD}, format="json")
    assert response.status_code == 200
    assert response.data["role"] == Role.LECTURER
    assert "sis_sessionid" in response.cookies
    assert response.cookies["sis_sessionid"]["httponly"]
    assert api.get("/api/v1/auth/me/").status_code == 200
    assert AuditLog.objects.filter(action="auth.login", actor=lecturer).exists()


def test_login_failure_is_generic_and_audited(api, lecturer):
    wrong = api.post("/api/v1/auth/login/", {"email": lecturer.email, "password": "nope"}, format="json")
    unknown = api.post("/api/v1/auth/login/", {"email": "ghost@example.test", "password": "nope"}, format="json")
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.data["detail"] == unknown.data["detail"] == "Invalid email or password."
    log = AuditLog.objects.filter(action="auth.login_failed").first()
    assert log is not None
    assert "nope" not in str(log.metadata)


def test_inactive_account_cannot_log_in(api, lecturer):
    lecturer.is_active = False
    lecturer.save()
    response = api.post("/api/v1/auth/login/", {"email": lecturer.email, "password": PASSWORD}, format="json")
    assert response.status_code == 400


def test_login_is_rate_limited(api, lecturer):
    statuses = [api.post("/api/v1/auth/login/", {"email": lecturer.email, "password": "bad"}, format="json").status_code
                for _ in range(11)]
    assert statuses[-1] == 429


def test_logout_ends_session(api, lecturer):
    api.post("/api/v1/auth/login/", {"email": lecturer.email, "password": PASSWORD}, format="json")
    assert api.post("/api/v1/auth/logout/").status_code == 204
    assert api.get("/api/v1/auth/me/").status_code == 403


def test_unauthenticated_requests_are_rejected(api):
    for url in ["/api/v1/auth/me/", "/api/v1/students/", "/api/v1/registrations/", "/api/v1/result-sheets/",
                "/api/v1/audit-logs/", "/api/v1/users/", "/api/v1/dashboard/", "/api/v1/my-results/"]:
        assert api.get(url).status_code in (401, 403), url


def test_csrf_is_enforced_for_session_clients(lecturer):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(lecturer)
    response = client.post("/api/v1/auth/password/change/", {"current_password": PASSWORD, "new_password": "x"},
                           format="json")
    assert response.status_code == 403
    client.get("/api/v1/auth/csrf/")
    token = client.cookies["csrftoken"].value
    response = client.post("/api/v1/auth/password/change/",
                           {"current_password": PASSWORD, "new_password": "Another-Strong-99"},
                           format="json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 204


def test_password_change_validates_current_password(lecturer):
    client = client_for(lecturer)
    bad = client.post("/api/v1/auth/password/change/", {"current_password": "wrong", "new_password": "New-Pass-12345"},
                      format="json")
    assert bad.status_code == 400 and "current_password" in bad.data["errors"]
    weak = client.post("/api/v1/auth/password/change/", {"current_password": PASSWORD, "new_password": "123"},
                       format="json")
    assert weak.status_code == 400 and "new_password" in weak.data["errors"]
    ok = client.post("/api/v1/auth/password/change/", {"current_password": PASSWORD, "new_password": "New-Pass-12345"},
                     format="json")
    assert ok.status_code == 204
    lecturer.refresh_from_db()
    assert lecturer.check_password("New-Pass-12345")


def test_password_reset_flow(api, lecturer):
    response = api.post("/api/v1/auth/password/reset/", {"email": lecturer.email}, format="json")
    unknown = api.post("/api/v1/auth/password/reset/", {"email": "nobody@example.test"}, format="json")
    assert response.status_code == unknown.status_code == 200
    assert response.data == unknown.data
    assert len(mail.outbox) == 1
    uid, token = re.search(r"uid=([^&\s]+)&token=([^\s]+)", mail.outbox[0].body).groups()

    bad = api.post("/api/v1/auth/password/reset/confirm/",
                   {"uid": uid, "token": "bad-token", "new_password": "Reset-Pass-2025"}, format="json")
    assert bad.status_code == 400
    good = api.post("/api/v1/auth/password/reset/confirm/",
                    {"uid": uid, "token": token, "new_password": "Reset-Pass-2025"}, format="json")
    assert good.status_code == 204
    lecturer.refresh_from_db()
    assert lecturer.check_password("Reset-Pass-2025")
    reused = api.post("/api/v1/auth/password/reset/confirm/",
                      {"uid": uid, "token": token, "new_password": "Reset-Pass-2026"}, format="json")
    assert reused.status_code == 400


def test_user_cannot_escalate_role_through_profile_update(lecturer):
    client = client_for(lecturer)
    response = client.patch("/api/v1/auth/me/", {"role": "ADMIN", "can_publish_results": True, "phone": "0200000000"},
                            format="json")
    assert response.status_code == 200
    lecturer.refresh_from_db()
    assert lecturer.role == Role.LECTURER
    assert not lecturer.can_publish_results
    assert lecturer.phone == "0200000000"


class TestUserManagement:
    def test_admin_creates_staff_with_role_defaults(self, admin_user):
        client = client_for(admin_user)
        response = client.post("/api/v1/users/", {
            "email": "Officer@Example.test", "first_name": "Ama", "last_name": "Owusu", "role": "RESULTS_OFFICER",
            "password": "Temp-Password-1",
        }, format="json")
        assert response.status_code == 201, response.data
        user = User.objects.get(email="officer@example.test")
        assert user.can_approve_results and not user.can_publish_results
        assert user.must_change_password
        assert "password" not in response.data
        assert AuditLog.objects.filter(action="user.created", entity_id=str(user.pk)).exists()

    def test_non_admins_cannot_manage_users(self, registrar, lecturer):
        for user in (registrar, lecturer):
            client = client_for(user)
            assert client.get("/api/v1/users/").status_code == 403
            assert client.post("/api/v1/users/", {"email": "x@example.test", "role": "ADMIN"}, format="json").status_code == 403

    def test_cannot_create_student_accounts_or_change_own_role(self, admin_user):
        client = client_for(admin_user)
        response = client.post("/api/v1/users/", {"email": "s@example.test", "first_name": "A", "last_name": "B",
                                                  "role": "STUDENT", "password": "Temp-Password-1"}, format="json")
        assert response.status_code == 400
        response = client.patch(f"/api/v1/users/{admin_user.pk}/", {"role": "LECTURER"}, format="json")
        assert response.status_code == 400
        assert client.post(f"/api/v1/users/{admin_user.pk}/deactivate/").status_code == 400

    def test_role_change_is_audited(self, admin_user, lecturer):
        client = client_for(admin_user)
        response = client.patch(f"/api/v1/users/{lecturer.pk}/", {"role": "RESULTS_OFFICER",
                                                                  "can_approve_results": True}, format="json")
        assert response.status_code == 200
        log = AuditLog.objects.get(action="user.updated", entity_id=str(lecturer.pk))
        assert log.before["role"] == "LECTURER" and log.after["role"] == "RESULTS_OFFICER"

    def test_superuser_flags_are_not_exposed(self, admin_user, lecturer):
        client = client_for(admin_user)
        client.patch(f"/api/v1/users/{lecturer.pk}/", {"is_superuser": True, "is_staff": True}, format="json")
        lecturer.refresh_from_db()
        assert not lecturer.is_superuser and not lecturer.is_staff
        assert "is_superuser" not in client.get(f"/api/v1/users/{lecturer.pk}/").data

    def test_lecturer_lookup_for_registry(self, registrar):
        make_user(Role.LECTURER, last_name="Asante")
        response = client_for(registrar).get("/api/v1/users/lecturers/?search=asan")
        assert response.status_code == 200
        assert response.data["count"] == 1


def test_login_requires_csrf_token(lecturer):
    client = APIClient(enforce_csrf_checks=True)
    blocked = client.post("/api/v1/auth/login/", {"email": lecturer.email, "password": PASSWORD}, format="json")
    assert blocked.status_code == 403
    client.get("/api/v1/auth/csrf/")
    allowed = client.post("/api/v1/auth/login/", {"email": lecturer.email, "password": PASSWORD}, format="json",
                          HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    assert allowed.status_code == 200
