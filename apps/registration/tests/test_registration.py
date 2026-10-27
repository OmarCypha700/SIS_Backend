import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academics.models import CourseOffering, Semester
from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.registration.models import (
    CourseRegistration, RegistrationEvent, RegistrationStatus, SemesterRegistration,
)
from apps.registration import services
from apps.results.models import CourseResult, ResultSheet
from conftest import client_for, make_student, make_user

pytestmark = pytest.mark.django_db

SUBMIT = "/api/v1/registrations/submit/"


def ids(world, *codes):
    return [world.offerings[c].pk for c in codes]


def submit(world, student, *codes):
    return client_for(student.user).post(SUBMIT, {"offering_ids": ids(world, *codes)}, format="json")


def test_student_sees_options_with_compulsory_and_eligibility(world):
    response = client_for(world.student.user).get("/api/v1/registrations/current/")
    assert response.status_code == 200
    options = {o["course_code"]: o for o in response.data["options"]}
    assert options["CS101"]["is_compulsory"] and options["MATH101"]["is_compulsory"]
    assert options["CS105"]["category"] == "ELECTIVE" and not options["CS105"]["is_compulsory"]
    assert not options["CS201"]["eligible"]  # above level and prerequisite not met
    assert response.data["window_open"] is True


def test_successful_registration_is_recorded(world):
    response = submit(world, world.student, "CS101", "MATH101", "CS105")
    assert response.status_code == 200, response.data
    assert response.data["status"] == RegistrationStatus.REGISTERED
    assert response.data["total_credits"] == 9
    assert len(response.data["courses"]) == 3
    reg = SemesterRegistration.objects.get(student=world.student)
    assert reg.events.filter(action="COURSE_ADDED").count() == 3
    assert AuditLog.objects.filter(action="registration.submitted", entity_id=str(reg.pk)).exists()


def test_amending_registration_drops_without_erasing_history(world):
    submit(world, world.student, "CS101", "MATH101", "CS105")
    response = submit(world, world.student, "CS101", "MATH101", "CS107")
    assert response.status_code == 200
    dropped = CourseRegistration.objects.get(student=world.student, offering=world.offerings["CS105"])
    assert dropped.status == "DROPPED" and dropped.dropped_at is not None
    assert RegistrationEvent.objects.filter(action="COURSE_DROPPED").count() == 1


def test_database_constraint_prevents_duplicate_active_course(world):
    submit(world, world.student, "CS101", "MATH101")
    reg = SemesterRegistration.objects.get(student=world.student)
    with pytest.raises(IntegrityError), transaction.atomic():
        CourseRegistration.objects.create(semester_registration=reg, student=world.student,
                                          offering=world.offerings["CS101"])


def test_compulsory_courses_required(world):
    response = submit(world, world.student, "CS101", "CS105")
    assert response.status_code == 400
    assert any(e.get("course_code") == "MATH101" for e in response.data["errors"])
    assert not SemesterRegistration.objects.exists()


def test_invalid_selection_rolls_back_everything(world):
    response = submit(world, world.student, "CS101", "MATH101", "CS201")
    assert response.status_code == 400
    assert not SemesterRegistration.objects.exists()
    assert not CourseRegistration.objects.exists()


def test_failure_after_writes_rolls_back(world, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(RegistrationEvent.objects, "bulk_create", boom)
    with pytest.raises(RuntimeError):
        services.submit_registration(world.student, world.semester, ids(world, "CS101", "MATH101"),
                                     actor=world.student.user)
    assert not SemesterRegistration.objects.exists()
    assert not CourseRegistration.objects.exists()


def test_registration_window_enforced(world):
    now = timezone.now()
    Semester.objects.filter(pk=world.semester.pk).update(registration_start=now - datetime.timedelta(days=30),
                                                         registration_end=now - datetime.timedelta(days=5),
                                                         add_drop_deadline=None)
    response = submit(world, world.student, "CS101", "MATH101")
    assert response.status_code == 400 and response.data["code"] == "window_closed"


def test_add_drop_deadline_allows_amendments_after_close(world):
    submit(world, world.student, "CS101", "MATH101")
    now = timezone.now()
    Semester.objects.filter(pk=world.semester.pk).update(registration_end=now - datetime.timedelta(hours=1),
                                                         add_drop_deadline=now + datetime.timedelta(days=2))
    assert submit(world, world.student, "CS101", "MATH101", "CS105").status_code == 200
    # New registrations are no longer accepted once the main window has closed.
    assert submit(world, world.other_student, "CS101", "MATH101").status_code == 400


def test_prerequisites_enforced_until_passed(world):
    world.profile.allow_higher_level_courses = True
    world.profile.save()
    response = submit(world, world.student, "CS101", "MATH101", "CS201")
    assert response.status_code == 400
    assert "Prerequisite not met: CS101" in str(response.data["errors"])
    _publish_result(world, world.student, "CS101", passed=True)
    ctx = services.build_options(world.student, world.semester)
    option = ctx.options[world.offerings["CS201"].pk]
    assert "Prerequisite" not in " ".join(option.reasons)


def _publish_result(world, student, code, passed):
    """Create a published result for the course in an earlier semester."""
    prior, _ = Semester.objects.get_or_create(
        academic_year=world.year, term_number=3, defaults=dict(
            name="Earlier", start_date=world.year.start_date, end_date=world.year.start_date + datetime.timedelta(days=20),
            registration_start=timezone.now() - datetime.timedelta(days=400),
            registration_end=timezone.now() - datetime.timedelta(days=390), status="CLOSED"),
    )
    offering, _ = CourseOffering.objects.get_or_create(course=world.courses[code], semester=prior)
    reg = SemesterRegistration.objects.create(student=student, semester=prior, programme=student.programme,
                                              level=student.current_level)
    cr = CourseRegistration.objects.create(semester_registration=reg, student=student, offering=offering)
    sheet, _ = ResultSheet.objects.get_or_create(offering=offering, grading_scheme=world.scheme, status="PUBLISHED")
    CourseResult.objects.create(sheet=sheet, registration=cr, student=student, offering=offering,
                                total_score=Decimal("75" if passed else "30"), grade="B+" if passed else "F",
                                grade_point=Decimal("3.5" if passed else "0"), is_pass=passed, credits=3,
                                is_published=True)
    return cr


def test_passed_courses_cannot_be_retaken_and_failed_are_flagged(world):
    _publish_result(world, world.student, "CS105", passed=True)
    _publish_result(world, world.other_student, "CS105", passed=False)
    passed_opts = services.build_options(world.student, world.semester).options
    failed_opts = services.build_options(world.other_student, world.semester).options
    assert not passed_opts[world.offerings["CS105"].pk].eligible
    assert failed_opts[world.offerings["CS105"].pk].is_retake
    world.profile.allow_retakes = False
    world.profile.save()
    assert not services.build_options(world.other_student, world.semester).options[world.offerings["CS105"].pk].eligible


def test_credit_limits(world):
    world.programme.max_credits_per_semester = 6
    world.programme.save()
    response = submit(world, world.student, "CS101", "MATH101", "CS105")
    assert response.status_code == 400 and "at most 6 credits" in str(response.data["errors"])
    world.profile.allow_credit_overload = True
    world.profile.save()
    response = submit(world, world.student, "CS101", "MATH101", "CS105")
    assert response.status_code == 200 and response.data["status"] == RegistrationStatus.PENDING
    world.programme.min_credits_per_semester = 12
    world.programme.max_credits_per_semester = 15
    world.programme.save()
    response = submit(world, world.other_student, "CS101", "MATH101")
    assert response.status_code == 400 and "at least 12" in str(response.data["errors"])


def test_capacity_enforced(world):
    offering = world.offerings["CS105"]
    offering.capacity = 1
    offering.save()
    assert submit(world, world.student, "CS101", "MATH101", "CS105").status_code == 200
    response = submit(world, world.other_student, "CS101", "MATH101", "CS105")
    assert response.status_code == 400 and "full" in str(response.data["errors"])


def test_status_policy_blocks_registration(world):
    suspended = make_student(world.programme, world.l100, status="SUSPENDED")
    response = submit(world, suspended, "CS101", "MATH101")
    assert response.status_code == 400 and response.data["code"] == "status_blocked"


def test_cross_programme_courses_need_policy(world):
    from apps.academics.models import Programme
    other_programme = Programme.objects.create(code="BSC-IT", name="IT", department=world.department)
    other_programme.levels.set([world.l100])
    outsider = make_student(other_programme, world.l100)
    ctx = services.build_options(outsider, world.semester)
    assert not ctx.options[world.offerings["CS105"].pk].eligible
    world.profile.allow_cross_programme_electives = True
    world.profile.save()
    ctx = services.build_options(outsider, world.semester)
    assert ctx.options[world.offerings["CS105"].pk].eligible


def test_registration_approval_workflow(world, registrar):
    world.profile.registration_requires_approval = True
    world.profile.save()
    response = submit(world, world.student, "CS101", "MATH101")
    assert response.data["status"] == RegistrationStatus.PENDING
    reg_id = response.data["id"]
    client = client_for(registrar)
    assert client.post(f"/api/v1/registrations/{reg_id}/review/", {"approve": False}, format="json").status_code == 400
    approved = client.post(f"/api/v1/registrations/{reg_id}/review/", {"approve": True}, format="json")
    assert approved.status_code == 200 and approved.data["status"] == RegistrationStatus.REGISTERED
    assert client_for(world.student.user).post(f"/api/v1/registrations/{reg_id}/review/", {"approve": True},
                                               format="json").status_code == 403


def test_admin_registration_on_behalf(world, registrar):
    now = timezone.now()
    Semester.objects.filter(pk=world.semester.pk).update(registration_end=now - datetime.timedelta(days=1),
                                                         add_drop_deadline=None)
    client = client_for(registrar)
    payload = {"student": world.student.pk, "semester": world.semester.pk,
               "offering_ids": ids(world, "CS101", "MATH101")}
    assert client.post("/api/v1/registrations/on-behalf/", payload, format="json").status_code == 400  # no reason
    response = client.post("/api/v1/registrations/on-behalf/", {**payload, "reason": "Late medical clearance"},
                           format="json")
    assert response.status_code == 200, response.data
    assert response.data["events"][0]["on_behalf"] is True
    assert AuditLog.objects.filter(action="registration.on_behalf").exists()


def test_students_only_see_their_own_registrations(world):
    submit(world, world.student, "CS101", "MATH101")
    submit(world, world.other_student, "CS101", "MATH101")
    other_reg = SemesterRegistration.objects.get(student=world.other_student)
    client = client_for(world.student.user)
    listing = client.get("/api/v1/registrations/").data
    assert listing["count"] == 1
    assert client.get(f"/api/v1/registrations/{other_reg.pk}/").status_code == 404
    assert client.get(f"/api/v1/registrations/options/?student={world.other_student.pk}").status_code == 403


def test_course_with_marks_cannot_be_dropped(world):
    from apps.results.models import AssessmentComponent, StudentMark
    submit(world, world.student, "CS101", "MATH101", "CS105")
    cr = CourseRegistration.objects.get(student=world.student, offering=world.offerings["CS105"])
    component = AssessmentComponent.objects.create(offering=world.offerings["CS105"], name="Exam", weight=100)
    StudentMark.objects.create(registration=cr, component=component, score=50)
    response = submit(world, world.student, "CS101", "MATH101")
    assert response.status_code == 400 and "cannot be dropped" in str(response.data["errors"])


def test_registry_reports(world, registrar):
    submit(world, world.student, "CS101", "MATH101")
    client = client_for(registrar)
    missing = client.get("/api/v1/registrations/not-registered/").data
    assert [row["id"] for row in missing["results"]] == [world.other_student.pk]
    stats = client.get("/api/v1/registrations/statistics/").data
    assert stats["registered"] == 1 and stats["eligible_students"] == 2
    export = client.get(f"/api/v1/registrations/export/?semester={world.semester.pk}")
    assert export.status_code == 200 and "CS101" in export.content.decode()
    filtered = client.get(f"/api/v1/registrations/?offering={world.offerings['CS101'].pk}").data
    assert filtered["count"] == 1


def test_class_lists_restricted_to_assigned_lecturers(world):
    submit(world, world.student, "CS101", "MATH101")
    offering = world.offerings["CS101"]
    response = client_for(world.lecturer).get(f"/api/v1/class-lists/{offering.pk}/")
    assert response.status_code == 200 and response.data["count"] == 1
    csv_response = client_for(world.lecturer).get(f"/api/v1/class-lists/{offering.pk}/?export=csv")
    assert csv_response.status_code == 200 and world.student.student_number in csv_response.content.decode()
    stranger = make_user(Role.LECTURER)
    assert client_for(stranger).get(f"/api/v1/class-lists/{offering.pk}/").status_code == 404
    assert client_for(world.student.user).get(f"/api/v1/class-lists/{offering.pk}/").status_code == 403
