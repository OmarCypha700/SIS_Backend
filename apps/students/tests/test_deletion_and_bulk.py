"""Deleting records created in error, and bulk actions on selected students and staff."""
import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.progression.tests.test_regulations import register
from apps.students.models import Student
from conftest import client_for, make_student, make_user

pytestmark = pytest.mark.django_db


def fresh_student(world):
    """A record created by mistake: never registered, never signed in."""
    return make_student(world.programme, world.l100)


def test_admin_deletes_a_record_created_in_error(world, admin_user):
    student = fresh_student(world)
    api = client_for(admin_user)
    check = api.get(f"/api/v1/students/{student.pk}/deletion-check/").data
    assert check == {"can_delete": True, "blockers": []}

    assert api.post(f"/api/v1/students/{student.pk}/delete-record/", {}, format="json").status_code == 400  # reason
    response = api.post(f"/api/v1/students/{student.pk}/delete-record/", {"reason": "Duplicate from import"},
                        format="json")
    assert response.status_code == 204
    assert not Student.objects.filter(pk=student.pk).exists()
    assert not User.objects.filter(email=student.email).exists()
    entry = AuditLog.objects.get(action="student.deleted")
    assert entry.reason == "Duplicate from import" and entry.before["student_number"] == student.student_number


def test_records_with_history_cannot_be_deleted(world, admin_user):
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    signed_in = fresh_student(world)
    signed_in.user.last_login = timezone.now()
    signed_in.user.save()
    api = client_for(admin_user)

    check = api.get(f"/api/v1/students/{world.student.pk}/deletion-check/").data
    assert check["can_delete"] is False and "The student has course registrations." in check["blockers"]
    response = api.post(f"/api/v1/students/{world.student.pk}/delete-record/", {"reason": "x"}, format="json")
    assert response.status_code == 409 and "Deactivate it instead" in response.data["detail"]
    assert api.get(f"/api/v1/students/{signed_in.pk}/deletion-check/").data["blockers"] == ["The person has signed in."]
    assert Student.objects.filter(pk__in=[world.student.pk, signed_in.pk]).count() == 2


def test_only_administrators_delete(world, registrar):
    student = fresh_student(world)
    api = client_for(registrar)
    assert api.get(f"/api/v1/students/{student.pk}/deletion-check/").status_code == 403
    assert api.post(f"/api/v1/students/{student.pk}/delete-record/", {"reason": "x"}, format="json").status_code == 403
    bulk = api.post("/api/v1/students/bulk/", {"action": "delete", "ids": [student.pk], "reason": "x"}, format="json")
    assert bulk.status_code == 403 and Student.objects.filter(pk=student.pk).exists()


def test_bulk_delete_skips_records_with_history(world, admin_user):
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    duplicates = [fresh_student(world) for _ in range(2)]
    ids = [d.pk for d in duplicates] + [world.student.pk, 999999]
    response = client_for(admin_user).post("/api/v1/students/bulk/", {"action": "delete", "ids": ids,
                                                                      "reason": "Bad import"}, format="json")
    assert response.status_code == 200
    assert [d["id"] for d in response.data["done"]] == [d.pk for d in duplicates]
    skipped = {s["id"]: s["reason"] for s in response.data["skipped"]}
    assert "course registrations" in skipped[world.student.pk] and skipped[999999] == "Not found."
    assert not Student.objects.filter(pk__in=[d.pk for d in duplicates]).exists()
    assert Student.objects.filter(pk=world.student.pk).exists()
    assert AuditLog.objects.filter(action="student.deleted").count() == 2
    assert AuditLog.objects.filter(action="student.bulk_delete").exists()


def test_registrar_bulk_deactivates_and_changes_status(world, registrar):
    api = client_for(registrar)
    ids = [world.student.pk, world.other_student.pk]
    response = api.post("/api/v1/students/bulk/", {"action": "deactivate", "ids": ids, "reason": "Left"}, format="json")
    assert len(response.data["done"]) == 2
    assert not User.objects.filter(pk__in=[world.student.user_id, world.other_student.user_id], is_active=True).exists()
    api.post("/api/v1/students/bulk/", {"action": "activate", "ids": ids, "reason": "Back"}, format="json")
    assert User.objects.filter(pk=world.student.user_id, is_active=True).exists()

    assert api.post("/api/v1/students/bulk/", {"action": "change_status", "ids": ids, "reason": "x"},
                    format="json").status_code == 400  # status required
    world.other_student.status = "DEFERRED"
    world.other_student.save()
    response = api.post("/api/v1/students/bulk/", {"action": "change_status", "status": "DEFERRED", "ids": ids,
                                                    "reason": "Deferred admission"}, format="json")
    assert [d["id"] for d in response.data["done"]] == [world.student.pk]
    assert response.data["skipped"][0]["reason"] == "The student already has this status."
    world.student.refresh_from_db()
    assert world.student.status == "DEFERRED"


def test_bulk_requires_a_reason_and_limits_size(world, registrar):
    api = client_for(registrar)
    assert api.post("/api/v1/students/bulk/", {"action": "deactivate", "ids": [world.student.pk]},
                    format="json").status_code == 400
    too_many = list(range(1, 502))
    assert api.post("/api/v1/students/bulk/", {"action": "deactivate", "ids": too_many, "reason": "x"},
                    format="json").status_code == 400


# --- Staff accounts --------------------------------------------------------------

def test_staff_account_created_in_error_can_be_deleted(admin_user):
    mistake = make_user(Role.LECTURER)
    api = client_for(admin_user)
    assert api.get(f"/api/v1/users/{mistake.pk}/deletion-check/").data["can_delete"] is True
    assert api.post(f"/api/v1/users/{mistake.pk}/delete-record/", {"reason": "Typo in email"},
                    format="json").status_code == 204
    assert not User.objects.filter(pk=mistake.pk).exists()
    assert AuditLog.objects.get(action="user.deleted").before["email"] == mistake.email


def test_used_staff_accounts_cannot_be_deleted(world, admin_user):
    api = client_for(admin_user)
    teaching = api.get(f"/api/v1/users/{world.lecturer.pk}/deletion-check/").data
    assert "The person is assigned to teach a course." in teaching["blockers"]
    own = api.post(f"/api/v1/users/{admin_user.pk}/delete-record/", {"reason": "x"}, format="json")
    assert own.status_code == 409 and "your own account" in own.data["detail"]
    active = make_user(Role.RESULTS_OFFICER)
    AuditLog.objects.create(actor=active, action="results.approved")
    assert "recorded activity" in " ".join(api.get(f"/api/v1/users/{active.pk}/deletion-check/").data["blockers"])
    # Students are not reachable through staff accounts.
    assert api.get(f"/api/v1/users/{world.student.user_id}/deletion-check/").status_code == 404


def test_staff_bulk_actions(world, admin_user):
    mistakes = [make_user(Role.LECTURER) for _ in range(2)]
    api = client_for(admin_user)
    ids = [admin_user.pk, world.lecturer.pk, *[m.pk for m in mistakes]]
    response = api.post("/api/v1/users/bulk/", {"action": "deactivate", "ids": ids, "reason": "End of contract"},
                        format="json")
    assert len(response.data["done"]) == 3
    assert response.data["skipped"] == [{"id": admin_user.pk, "label": f"{admin_user.get_full_name()} <{admin_user.email}>",
                                         "reason": "You cannot deactivate your own account."}]
    response = api.post("/api/v1/users/bulk/", {"action": "delete", "ids": ids, "reason": "Created in error"},
                        format="json")
    assert sorted(d["id"] for d in response.data["done"]) == sorted(m.pk for m in mistakes)
    assert User.objects.filter(pk__in=[admin_user.pk, world.lecturer.pk]).count() == 2
    assert client_for(make_user(Role.REGISTRAR)).post("/api/v1/users/bulk/", {"action": "deactivate", "ids": ids,
                                                                              "reason": "x"},
                                                      format="json").status_code == 403
