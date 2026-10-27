import pytest

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


def test_audit_records_are_immutable():
    entry = log_action("test.action", entity_type="x", entity_id=1, summary="hello")
    entry.summary = "changed"
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()
    with pytest.raises(PermissionError):
        AuditLog.objects.all().delete()
    with pytest.raises(PermissionError):
        AuditLog.objects.update(summary="x")


def test_sensitive_values_are_redacted():
    entry = log_action("test.secret", before={"password": "hunter2", "nested": {"api_token": "abc"}, "name": "ok"})
    assert entry.before == {"password": "[redacted]", "nested": {"api_token": "[redacted]"}, "name": "ok"}


def test_audit_api_is_read_only_and_role_scoped(admin_user, lecturer):
    log_action("student.created", entity_type="students.student", entity_id=1)
    log_action("user.created", entity_type="accounts.user", entity_id=2)
    officer = make_user(Role.RESULTS_OFFICER)
    log_action("results.approved", entity_type="results.resultsheet", entity_id=3)

    admin = client_for(admin_user)
    assert admin.get("/api/v1/audit-logs/").data["count"] >= 3
    assert admin.post("/api/v1/audit-logs/", {"action": "x"}, format="json").status_code in (403, 405)
    first = AuditLog.objects.first()
    assert admin.delete(f"/api/v1/audit-logs/{first.pk}/").status_code in (403, 405)
    assert AuditLog.objects.filter(pk=first.pk).exists()

    officer_view = client_for(officer).get("/api/v1/audit-logs/").data
    assert {row["entity_type"] for row in officer_view["results"]} == {"results.resultsheet"}
    assert client_for(lecturer).get("/api/v1/audit-logs/").status_code == 403


def test_request_id_is_propagated(admin_user):
    client = client_for(admin_user)
    response = client.get("/api/v1/auth/me/", HTTP_X_REQUEST_ID="abcdef0123456789")
    assert response["X-Request-ID"] == "abcdef0123456789"
