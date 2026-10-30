"""Bulk course registration by the registry, and bulk approval of pending registrations."""
import pytest

from apps.notifications.models import Kind, Notification
from apps.progression.tests.test_regulations import register
from apps.registration.models import CourseRegistrationStatus, RegistrationStatus, SemesterRegistration
from apps.students.models import StudentStatus
from conftest import client_for, make_student

pytestmark = pytest.mark.django_db


def codes(student, semester):
    return sorted(student.course_registrations.filter(semester_registration__semester=semester,
                                                      status=CourseRegistrationStatus.ACTIVE)
                  .values_list("offering__course__code", flat=True))


def body(world, students, extra=(), **more):
    return {"semester": world.semester.pk, "students": [s.pk for s in students],
            "extra_offerings": [world.offerings[c].pk for c in extra], **more}


def test_cohort_lists_registerable_students_and_open_offerings(world, registrar):
    suspended = make_student(world.programme, world.l100, status=StudentStatus.SUSPENDED)
    data = client_for(registrar).get(f"/api/v1/registrations/bulk/cohort/?programme={world.programme.pk}"
                                     f"&level={world.l100.pk}").data
    ids = {s["id"] for s in data["students"]}
    assert {world.student.pk, world.other_student.pk} <= ids and suspended.pk not in ids
    compulsory = [o["course_code"] for o in data["offerings"] if o["is_compulsory"]]
    assert compulsory == ["CS101", "MATH101"] and data["batch_size"] == 50


def test_preview_saves_nothing_then_confirm_registers_compulsory_and_chosen_electives(world, registrar):
    api = client_for(registrar)
    students = [world.student, world.other_student]
    preview = api.post("/api/v1/registrations/bulk/preview/", body(world, students, ["CS105", "CS201"]),
                       format="json").data["results"]
    assert [r["outcome"] for r in preview] == ["ready", "ready"]
    assert preview[0]["adding"] == ["CS101", "CS105", "MATH101"]
    # CS201 needs CS101 first: skipped with the reason, the rest still goes ahead.
    assert preview[0]["skipped"][0]["course_code"] == "CS201" and "Prerequisite" in preview[0]["skipped"][0]["reason"]
    assert not SemesterRegistration.objects.exists()

    assert api.post("/api/v1/registrations/bulk/confirm/", body(world, students, ["CS105"]),
                    format="json").status_code == 400  # a reason is required
    done = api.post("/api/v1/registrations/bulk/confirm/",
                    body(world, students, ["CS105"], reason="Level 100 BSc CS, first semester"),
                    format="json").data["results"]
    assert [r["outcome"] for r in done] == ["registered", "registered"]
    for student in students:
        assert codes(student, world.semester) == ["CS101", "CS105", "MATH101"]
        reg = SemesterRegistration.objects.get(student=student)
        assert reg.status == RegistrationStatus.REGISTERED
        assert reg.events.filter(on_behalf=True, reason__startswith="Bulk registration:").exists()
    assert Notification.objects.filter(kind=Kind.REGISTRATION_REVIEWED, title__startswith="You have been registered",
                                       recipient__in=[s.user for s in students]).count() == 2


def test_existing_courses_are_kept_and_pending_or_blocked_students_skipped(world, registrar):
    register(world, world.student, world.semester, ["CS101", "MATH101", "CS107"])
    pending = make_student(world.programme, world.l100)
    register(world, pending, world.semester, ["CS101", "MATH101"])
    SemesterRegistration.objects.filter(student=pending).update(status=RegistrationStatus.PENDING)
    blocked = make_student(world.programme, world.l100, status=StudentStatus.SUSPENDED)

    rows = client_for(registrar).post(
        "/api/v1/registrations/bulk/confirm/",
        body(world, [world.student, pending, blocked], ["CS105"], reason="Add the elective"), format="json",
    ).data["results"]
    assert [r["outcome"] for r in rows] == ["registered", "pending", "blocked"]
    assert codes(world.student, world.semester) == ["CS101", "CS105", "CS107", "MATH101"]  # nothing dropped
    assert SemesterRegistration.objects.get(student=pending).status == RegistrationStatus.PENDING

    again = client_for(registrar).post("/api/v1/registrations/bulk/preview/", body(world, [world.student], ["CS105"]),
                                       format="json").data["results"]
    assert again[0]["outcome"] == "unchanged"


def test_a_student_breaking_a_rule_is_reported_without_stopping_the_others(world, registrar):
    world.programme.max_credits_per_semester = 6
    world.programme.save()
    register(world, world.other_student, world.semester, ["CS101", "MATH101"])
    rows = client_for(registrar).post(
        "/api/v1/registrations/bulk/confirm/",
        body(world, [world.student, world.other_student], ["CS105"], reason="Electives"), format="json",
    ).data["results"]
    assert [r["outcome"] for r in rows] == ["problem", "problem"]
    assert "at most 6 credits" in rows[0]["errors"][0]
    assert codes(world.other_student, world.semester) == ["CS101", "MATH101"]  # left as it was
    assert not SemesterRegistration.objects.filter(student=world.student).exists()


def test_only_the_registry_can_bulk_register_and_batches_are_limited(world, lecturer, registrar):
    assert client_for(lecturer).post("/api/v1/registrations/bulk/preview/", body(world, [world.student]),
                                     format="json").status_code == 403
    too_many = {"semester": world.semester.pk, "students": list(range(1, 52))}
    assert client_for(registrar).post("/api/v1/registrations/bulk/preview/", too_many,
                                      format="json").status_code == 400


def test_bulk_approve_and_reject_pending_registrations(world, registrar):
    for student in (world.student, world.other_student):
        register(world, student, world.semester, ["CS101", "MATH101"])
    SemesterRegistration.objects.update(status=RegistrationStatus.PENDING)
    first, second = SemesterRegistration.objects.order_by("id")
    api = client_for(registrar)
    assert api.post("/api/v1/registrations/bulk-review/", {"ids": [first.pk], "action": "reject"},
                    format="json").status_code == 400  # a reason is required
    outcome = api.post("/api/v1/registrations/bulk-review/",
                       {"ids": [first.pk, second.pk], "action": "approve", "reason": "Checked against the curriculum"},
                       format="json").data
    assert len(outcome["done"]) == 2
    assert set(SemesterRegistration.objects.values_list("status", flat=True)) == {RegistrationStatus.REGISTERED}
    again = api.post("/api/v1/registrations/bulk-review/", {"ids": [first.pk], "action": "approve", "reason": "x"},
                     format="json").data
    assert again["skipped"][0]["reason"].startswith("Only registrations awaiting approval")
