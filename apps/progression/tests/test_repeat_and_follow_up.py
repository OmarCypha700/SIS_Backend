"""Repeating a level (the whole year again) and follow-up reviews for outcomes that arrive late."""
import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.academics.models import AcademicYear, CourseOffering, Semester
from apps.progression import services as progression
from apps.progression.models import Placement, ProgressionReview
from apps.progression.tests.test_regulations import grade_offering, register, regulation, staff  # noqa: F401
from apps.registration import services as registration
from apps.results import grading, resits
from apps.results import services as results
from apps.results.models import CourseResult
from conftest import client_for

pytestmark = pytest.mark.django_db


def next_year(world):
    """The following academic year's first semester, open for registration, with Level 100 courses offered."""
    year = AcademicYear.objects.create(name="2026/2027", start_date=world.year.end_date + datetime.timedelta(days=1),
                                       end_date=world.year.end_date + datetime.timedelta(days=360), status="ACTIVE")
    now = timezone.now()
    semester = Semester.objects.create(
        academic_year=year, name="First Semester", term_number=1, start_date=year.start_date,
        end_date=year.start_date + datetime.timedelta(days=120), registration_start=now - datetime.timedelta(days=1),
        registration_end=now + datetime.timedelta(days=5), status="ACTIVE",
    )
    for code in ("CS101", "MATH101", "CS105", "CS201"):
        offering = CourseOffering.objects.create(course=world.courses[code], semester=semester)
        offering.programmes.add(world.programme)
    return semester


def repeat_rules():
    regulation(promotion_min_cgpa=Decimal("2.00"), repeat_level_when_not_promoted=True, resits_enabled=True,
               resit_min_total=None)


def options_by_code(student, semester):
    return {o.offering.course.code: o for o in registration.build_options(student, semester).options.values()}


def test_a_repeating_student_takes_the_whole_level_again(world, staff):  # noqa: F811
    repeat_rules()
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"], {world.student: (20, 55)})  # 53 -> D (1.00), passed
    grade_offering(world, staff, world.offerings["MATH101"], {world.student: (10, 20)})  # 22 -> F
    review = progression.generate_review(world.semester, actor=staff.registrar)
    assert review.decisions.get().final_placement == Placement.REPEAT
    progression.apply_review(review, actor=staff.registrar, acknowledge_outstanding=True)
    world.student.refresh_from_db()
    assert world.student.repeating_level == world.l100 and world.student.current_level == world.l100

    semester = next_year(world)
    options = options_by_code(world.student, semester)
    assert options["CS101"].is_repeat and options["CS101"].eligible and options["CS101"].is_compulsory
    assert options["MATH101"].is_retake and options["MATH101"].is_compulsory
    assert not options["CS201"].eligible  # Level 200 course: prerequisite CS101 counts, but above level

    registration.submit_registration(world.student, semester, [options["CS101"].offering.id,
                                                                options["MATH101"].offering.id],
                                     actor=world.student.user)
    cs101 = world.student.course_registrations.get(offering__semester=semester, offering__course__code="CS101")
    assert cs101.is_repeat and not cs101.is_retake

    grade_offering(world, staff, cs101.offering, {world.student: (30, 70)})  # 72 -> B (3.00), not capped
    old = CourseResult.objects.get(student=world.student, offering=world.offerings["CS101"])
    new = CourseResult.objects.get(registration=cs101)
    assert old.superseded and not new.superseded and new.grade == "B" and not new.grade_capped
    record = results.build_academic_record(world.student)
    assert record["semesters"][0]["courses"][0]["superseded"]  # still on the record, marked
    first_gpa_courses = [c for c in record["semesters"][0]["courses"] if not c["superseded"]]
    assert [c["course_code"] for c in first_gpa_courses] == ["MATH101"]


def test_promotion_ends_the_repeat(world, staff):  # noqa: F811
    world.student.repeating_level = world.l100
    world.student.save()
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    for code in ("CS101", "MATH101"):
        grade_offering(world, staff, world.offerings[code], {world.student: (35, 90)})
    review = progression.generate_review(world.semester, actor=staff.registrar)
    progression.apply_review(review, actor=staff.registrar)
    world.student.refresh_from_db()
    assert world.student.current_level == world.l200 and world.student.repeating_level is None


def test_follow_up_review_promotes_a_student_who_passed_the_resit(world, staff):  # noqa: F811
    repeat_rules()
    regulation(max_referred_courses_to_proceed=0)  # any referred course holds the student back
    for student in (world.student, world.other_student):
        register(world, student, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"],
                   {world.student: (35, 90), world.other_student: (35, 90)})
    grade_offering(world, staff, world.offerings["MATH101"],
                   {world.student: (10, 20), world.other_student: (35, 90)})  # student fails MATH101
    resit = resits.open_resit(world.offerings["MATH101"], actor=staff.officer)

    review = progression.generate_review(world.semester, actor=staff.registrar)
    api = client_for(staff.registrar)
    response = api.post(f"/api/v1/progression-reviews/{review.pk}/apply/", {}, format="json")
    assert response.status_code == 409 and response.data["code"] == "outstanding_work"
    assert api.get(f"/api/v1/progression-reviews/{review.pk}/").data["outstanding"][0]["kind"] == "resits"
    assert api.post(f"/api/v1/progression-reviews/{review.pk}/apply/", {"acknowledge_outstanding": True},
                    format="json").status_code == 200
    world.student.refresh_from_db()
    assert world.student.repeating_level == world.l100  # repeat, pending the resit
    assert api.post("/api/v1/progression-reviews/generate/", {"semester": world.semester.pk},
                    format="json").status_code == 409  # the main review is applied

    entry = resit.entries.get()
    resits.register_entries(resit, [entry.id], actor=staff.registrar, reason="Registered at the registry")
    resits.save_scores(resit, [{"entry": entry.id, "score": 90}], actor=world.lecturer)
    resits.submit(resit, actor=world.lecturer)
    resits.approve(resit, actor=staff.officer)
    resits.publish(resit, actor=staff.registrar)

    response = api.post("/api/v1/progression-reviews/generate/", {"semester": world.semester.pk, "follow_up": True},
                        format="json")
    assert response.status_code == 201 and response.data["is_follow_up"] and response.data["sequence"] == 2
    follow_up = ProgressionReview.objects.get(pk=response.data["id"])
    decision = follow_up.decisions.get()  # only the student whose outcome changed
    assert decision.student == world.student and decision.final_placement == Placement.PROMOTE
    assert api.post(f"/api/v1/progression-reviews/{follow_up.pk}/apply/", {}, format="json").status_code == 200
    world.student.refresh_from_db()
    assert world.student.current_level == world.l200 and world.student.repeating_level is None
    # The follow-up's promotion is the outcome in force, so the earlier repeat is not counted as one.
    assert not progression.has_repeated_level(world.student)
    assert grading  # imported helpers stay in use


def test_excused_students_are_provisional_until_completed(world, staff):  # noqa: F811
    for student in (world.student, world.other_student):
        register(world, student, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["MATH101"],
                   {world.student: (35, 90), world.other_student: (35, 90)})
    sheet = results.get_or_create_sheet(world.offerings["CS101"])
    results.replace_components(sheet, [{"name": "Exam", "weight": 100, "max_score": 100, "is_examination": True}],
                               actor=world.lecturer)
    exam = sheet.offering.assessment_components.get()
    reg = world.student.course_registrations.get(offering=sheet.offering)
    other = world.other_student.course_registrations.get(offering=sheet.offering)
    results.save_marks(sheet, [{"registration": other.id, "component": exam.id, "score": 80}], actor=world.lecturer)
    results.excuse(sheet, reg.id, reason="Hospitalised during the examination", actor=world.lecturer)
    results.submit_sheet(sheet, actor=world.lecturer)
    results.approve_sheet(sheet, actor=staff.officer)
    results.publish_sheet(sheet, actor=staff.registrar)

    review = progression.generate_review(world.semester, actor=staff.registrar)
    assert not review.decisions.get(student=world.student).results_complete
    assert review.decisions.get(student=world.other_student).results_complete
    kinds = [o["kind"] for o in progression.outstanding_work(world.semester)]
    assert kinds == ["incomplete"]
