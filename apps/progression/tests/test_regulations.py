"""Academic regulations: retake caps, attempt limits, resits and progression reviews."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.academics.models import CourseOffering, LecturerAssignment, Semester
from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.institutions.models import AcademicRegulation
from apps.progression import services as progression
from apps.progression.models import ProgressionDecision
from apps.registration import services as registration
from apps.results import grading, resits
from apps.results import services as results
from apps.results.models import CourseResult, ResitEntry
from conftest import client_for, make_student, make_user

pytestmark = pytest.mark.django_db


def regulation(**values):
    reg = AcademicRegulation.load()
    for key, value in values.items():
        setattr(reg, key, value)
    reg.save()
    return reg


@pytest.fixture
def staff(world):
    return SimpleNamespace(officer=make_user(Role.RESULTS_OFFICER), registrar=make_user(Role.REGISTRAR))


def grade_offering(world, staff, offering, scores):
    """Publish results for `offering`. scores: {student: (ca_out_of_40, exam_out_of_100)}."""
    LecturerAssignment.objects.get_or_create(offering=offering, lecturer=world.lecturer)
    sheet = results.get_or_create_sheet(offering)
    results.replace_components(sheet, [
        {"name": "Coursework", "weight": 40, "max_score": 40},
        {"name": "Examination", "weight": 60, "max_score": 100, "is_examination": True},
    ], actor=world.lecturer)
    ca, exam = list(offering.assessment_components.order_by("order"))
    entries = []
    for student, (ca_score, exam_score) in scores.items():
        reg = student.course_registrations.get(offering=offering, status="ACTIVE")
        entries += [{"registration": reg.id, "component": ca.id, "score": ca_score},
                    {"registration": reg.id, "component": exam.id, "score": exam_score}]
    results.save_marks(sheet, entries, actor=world.lecturer)
    results.submit_sheet(sheet, actor=world.lecturer)
    results.approve_sheet(sheet, actor=staff.officer)
    results.publish_sheet(sheet, actor=staff.registrar)
    return sheet


def register(world, student, semester, codes):
    offerings = [CourseOffering.objects.get(course__code=c, semester=semester).pk for c in codes]
    return registration.submit_registration(student, semester, offerings, actor=student.user, on_behalf=True,
                                            reason="test")


def later_semester(world, term=2):
    now = timezone.now()
    semester = Semester.objects.create(
        academic_year=world.year, name=f"Term {term}", term_number=term,
        start_date=world.semester.end_date + datetime.timedelta(days=1),
        end_date=world.semester.end_date + datetime.timedelta(days=60),
        registration_start=now - datetime.timedelta(days=1), registration_end=now + datetime.timedelta(days=5),
        status="ACTIVE",
    )
    for code in ("CS101", "MATH101", "CS105"):
        offering = CourseOffering.objects.create(course=world.courses[code], semester=semester)
        offering.programmes.add(world.programme)
    return semester


# --- grade caps -------------------------------------------------------------

def test_cap_band_limits_grade_point_only():
    bands = [SimpleNamespace(letter=l, grade_point=Decimal(p), min_score=Decimal(m)) for l, p, m in
             [("A", "4", 80), ("B", "3", 70), ("C", "2", 60), ("F", "0", 0)]]
    capped, was = grading.cap_band(bands[0], bands, Decimal("2.00"))
    assert capped.letter == "C" and was
    same, was = grading.cap_band(bands[3], bands, Decimal("2.00"))
    assert same.letter == "F" and not was
    assert grading.cap_band(bands[0], bands, None) == (bands[0], False)


def test_retake_grade_is_capped(world, staff):
    regulation(retake_max_grade_point=Decimal("2.00"))
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"], {world.student: (10, 20)})  # 22 -> F
    second = later_semester(world)
    register(world, world.student, second, ["CS101"])
    cr = world.student.course_registrations.get(offering__semester=second)
    assert cr.is_retake
    grade_offering(world, staff, cr.offering, {world.student: (40, 100)})  # 100 -> A, capped at C
    result = CourseResult.objects.get(registration=cr)
    assert result.grade == "C" and result.grade_capped and result.total_score == Decimal("100")


# --- attempts and failed compulsory courses ---------------------------------

def test_attempt_limit_and_failed_compulsory(world, staff):
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"], {world.student: (5, 10)})
    second = later_semester(world, term=1 + 1)
    # Without the regulation a failed course from another term is optional.
    options = registration.build_options(world.student, second).options
    cs101 = next(o for o in options.values() if o.offering.course.code == "CS101")
    assert cs101.is_retake and not cs101.is_compulsory and cs101.eligible

    regulation(failed_compulsory_must_be_retaken=True)
    cs101 = next(o for o in registration.build_options(world.student, second).options.values()
                 if o.offering.course.code == "CS101")
    assert cs101.is_compulsory

    regulation(max_attempts_per_course=1)
    cs101 = next(o for o in registration.build_options(world.student, second).options.values()
                 if o.offering.course.code == "CS101")
    assert not cs101.eligible and "permitted attempts" in " ".join(cs101.reasons)


# --- resits ----------------------------------------------------------------

@pytest.fixture
def failed_class(world, staff):
    """CS101 published: student fails with 46 (resit-eligible), other student fails with 22."""
    for s in (world.student, world.other_student):
        register(world, s, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"],
                   {world.student: (28, 30), world.other_student: (10, 20)})  # 28+18=46, 10+12=22
    return world.offerings["CS101"]


def test_resit_requires_regulation_and_eligibility(world, staff, failed_class):
    with pytest.raises(Exception, match="not enabled"):
        resits.open_resit(failed_class, actor=staff.officer)
    regulation(resits_enabled=True, resit_min_total=Decimal("40"))
    with pytest.raises(Exception, match="examinations or registry"):
        resits.open_resit(failed_class, actor=world.lecturer)
    sheet = resits.open_resit(failed_class, actor=staff.officer)
    assert [e.course_result.student for e in sheet.entries.all()] == [world.student]  # 22 is below the minimum


def test_full_resit_flow_keeps_coursework_and_caps_grade(world, staff, failed_class):
    regulation(resits_enabled=True, resit_max_grade_point=Decimal("2.00"))
    officer_api = client_for(staff.officer)
    opened = officer_api.post("/api/v1/resit-sheets/open/", {"offering": failed_class.pk}, format="json")
    assert opened.status_code == 201
    sheet_id = opened.data["id"]
    assert len(opened.data["rows"]) == 2

    # Students see and register for their own resit only.
    mine = client_for(world.student.user).get("/api/v1/my-resits/").data
    assert len(mine) == 1 and mine[0]["status"] == "ELIGIBLE"
    other_entry = ResitEntry.objects.get(course_result__student=world.other_student)
    assert client_for(world.student.user).post(f"/api/v1/my-resits/{other_entry.id}/register/").status_code == 404
    assert client_for(world.student.user).post(f"/api/v1/my-resits/{mine[0]['id']}/register/").status_code == 200

    lecturer_api = client_for(world.lecturer)
    entry = ResitEntry.objects.get(course_result__student=world.student)
    assert lecturer_api.put(f"/api/v1/resit-sheets/{sheet_id}/scores/",
                            {"scores": [{"entry": other_entry.id, "score": "90"}]}, format="json").status_code == 400
    response = lecturer_api.put(f"/api/v1/resit-sheets/{sheet_id}/scores/",
                                {"scores": [{"entry": entry.id, "score": "90"}]}, format="json")
    assert response.status_code == 200
    row = next(r for r in response.data["rows"] if r["id"] == entry.id)
    # Coursework 28/40*40 = 28 kept; exam replaced: 90% of 60 = 54 -> 82 (A) capped at C.
    assert row["projected_total"] == "82" and row["projected_grade"] == "C" and row["projected_capped"]
    assert client_for(world.student.user).get("/api/v1/my-resits/").data[0]["new_grade"] is None

    assert lecturer_api.post(f"/api/v1/resit-sheets/{sheet_id}/submit/", {}, format="json").status_code == 200
    assert lecturer_api.post(f"/api/v1/resit-sheets/{sheet_id}/publish/", {}, format="json").status_code == 403
    assert officer_api.post(f"/api/v1/resit-sheets/{sheet_id}/approve/", {}, format="json").status_code == 200
    assert officer_api.post(f"/api/v1/resit-sheets/{sheet_id}/publish/", {}, format="json").status_code == 403
    published = client_for(staff.registrar).post(f"/api/v1/resit-sheets/{sheet_id}/publish/", {}, format="json")
    assert published.status_code == 200

    result = CourseResult.objects.get(student=world.student, offering=failed_class)
    assert result.grade == "C" and result.is_resit and result.grade_capped and result.is_pass
    assert ResitEntry.objects.get(pk=other_entry.pk).status == "NOT_TAKEN"
    assert client_for(world.student.user).get("/api/v1/my-resits/").data[0]["new_grade"] == "C"
    assert AuditLog.objects.filter(action="results.resit_applied").count() == 1
    # A resit grade cannot then be amended through the ordinary amendment flow.
    with pytest.raises(Exception, match="resit"):
        results.request_amendment(result, new_marks={}, reason="x", actor=world.lecturer)


def test_resit_deadline_and_limits(world, staff, failed_class):
    regulation(resits_enabled=True, max_resits_per_course=1)
    sheet = resits.open_resit(failed_class, actor=staff.officer,
                              registration_deadline=timezone.now() - datetime.timedelta(minutes=1))
    entry = sheet.entries.get(course_result__student=world.student)
    with pytest.raises(Exception, match="deadline"):
        resits.register_entries(sheet, [entry.id], actor=world.student.user)
    with pytest.raises(Exception, match="reason"):
        resits.register_entries(sheet, [entry.id], actor=staff.registrar)
    resits.register_entries(sheet, [entry.id], actor=staff.registrar, reason="Medical certificate")
    assert ResitEntry.objects.get(pk=entry.pk).status == "REGISTERED"
    with pytest.raises(Exception, match="already been opened"):
        resits.open_resit(failed_class, actor=staff.officer)


# --- progression ------------------------------------------------------------

def _publish_semester(world, staff, marks):
    """Register both students on CS101 + MATH101 and publish. marks: {student: (ca, exam)} applied to both."""
    for s in (world.student, world.other_student):
        register(world, s, world.semester, ["CS101", "MATH101"])
    for code in ("CS101", "MATH101"):
        grade_offering(world, staff, world.offerings[code], marks)


def test_review_proposes_and_registry_applies(world, staff):
    regulation(probation_cgpa_below=Decimal("2.00"), dismissal_cgpa_below=Decimal("1.00"),
               promotion_min_credits_per_year=6, promotion_max_outstanding_failed_credits=0)
    # student: 36+54=90 (A, 4.0) on both; other: 20+36=56 (D+, 1.5) on both.
    _publish_semester(world, staff, {world.student: (36, 90), world.other_student: (20, 60)})
    review = progression.generate_review(world.semester, actor=staff.registrar)
    assert review.is_final_term  # the only term of the year in the fixture
    good = review.decisions.get(student=world.student)
    weak = review.decisions.get(student=world.other_student)
    assert (good.proposed_standing, good.proposed_placement) == ("GOOD", "PROMOTE")
    assert good.cgpa == Decimal("4.00") and good.credits_earned_year == 6
    assert weak.proposed_standing == "PROBATION" and weak.proposed_placement == "PROMOTE"

    # Nothing has changed yet.
    world.student.refresh_from_db()
    assert world.student.current_level == world.l100

    # Registry overrides one decision; a reason is mandatory.
    api = client_for(staff.registrar)
    assert api.post(f"/api/v1/progression-decisions/{weak.id}/override/",
                    {"standing": "PROBATION", "placement": "REPEAT"}, format="json").status_code == 400
    assert api.post(f"/api/v1/progression-decisions/{weak.id}/override/",
                    {"standing": "PROBATION", "placement": "REPEAT", "reason": "Board decision"},
                    format="json").status_code == 200
    # Regenerating keeps the override.
    progression.generate_review(world.semester, actor=staff.registrar)
    assert ProgressionDecision.objects.get(pk=weak.pk).final_placement == "REPEAT"

    applied = api.post(f"/api/v1/progression-reviews/{review.id}/apply/")
    assert applied.status_code == 200 and applied.data["applied_counts"]["applied"] == 2
    world.student.refresh_from_db()
    world.other_student.refresh_from_db()
    assert world.student.current_level == world.l200 and world.student.academic_standing == "GOOD"
    assert world.other_student.current_level == world.l100 and world.other_student.academic_standing == "PROBATION"
    assert world.student.history.filter(change_type="LEVEL").exists()

    # Applied reviews are final.
    assert api.post(f"/api/v1/progression-reviews/{review.id}/apply/").status_code == 409
    assert api.post("/api/v1/progression-reviews/generate/", {"semester": world.semester.pk},
                    format="json").status_code == 409
    assert AuditLog.objects.filter(action="progression.applied").exists()


def test_dismissal_rules(world, staff):
    regulation(probation_cgpa_below=Decimal("2.00"), dismissal_cgpa_below=Decimal("1.00"))
    _publish_semester(world, staff, {world.student: (5, 10), world.other_student: (20, 60)})  # 11 -> F; 56 -> D+
    review = progression.generate_review(world.semester, actor=staff.registrar)
    assert review.decisions.get(student=world.student).proposed_standing == "DISMISS"
    progression.apply_review(review, actor=staff.registrar)
    world.student.refresh_from_db()
    assert world.student.status == "DISMISSED"
    other = review.decisions.get(student=world.other_student)
    assert other.final_standing == "PROBATION"

    # Consecutive probation limit: the next probation becomes a dismissal proposal.
    regulation(dismiss_after_consecutive_probations=2)
    second = later_semester(world)
    register(world, world.other_student, second, ["CS105"])
    LecturerAssignment.objects.get_or_create(offering=CourseOffering.objects.get(course__code="CS105", semester=second),
                                             lecturer=world.lecturer)
    grade_offering(world, staff, CourseOffering.objects.get(course__code="CS105", semester=second),
                   {world.other_student: (20, 60)})
    decision = progression.generate_review(second, actor=staff.registrar).decisions.get(student=world.other_student)
    assert decision.proposed_standing == "DISMISS" and "consecutive" in " ".join(decision.reasons)


def test_incomplete_results_are_not_applied(world, staff):
    regulation(probation_cgpa_below=Decimal("2.00"))
    for s in (world.student, world.other_student):
        register(world, s, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"], {world.student: (5, 10), world.other_student: (5, 10)})
    review = progression.generate_review(world.semester, actor=staff.registrar)  # MATH101 unpublished
    decision = review.decisions.get(student=world.student)
    assert not decision.results_complete and decision.proposed_standing == "PROBATION"
    counts = progression.apply_review(review, actor=staff.registrar)
    assert counts["skipped_incomplete"] == 2 and counts["applied"] == 0
    world.student.refresh_from_db()
    assert world.student.academic_standing == "GOOD"


def test_progression_permissions(world, staff):
    assert client_for(world.lecturer).get("/api/v1/progression-reviews/").status_code == 403
    assert client_for(world.student.user).get("/api/v1/progression-reviews/").status_code == 403
    assert client_for(staff.officer).get("/api/v1/progression-reviews/").status_code == 200
    assert client_for(staff.officer).post("/api/v1/progression-reviews/generate/", {"semester": world.semester.pk},
                                          format="json").status_code == 403
    assert client_for(make_user(Role.ADMIN)).patch("/api/v1/academic-regulations/", {"resits_enabled": True},
                                                   format="json").status_code == 200
    assert client_for(staff.registrar).patch("/api/v1/academic-regulations/", {"resits_enabled": False},
                                             format="json").status_code == 403
    bad = client_for(make_user(Role.ADMIN)).patch("/api/v1/academic-regulations/", {
        "probation_cgpa_below": "1.00", "dismissal_cgpa_below": "2.00"}, format="json")
    assert bad.status_code == 400


def test_new_level_student_helper(world):
    student = make_student(world.programme, world.l200)
    assert progression.next_level(student) is None


# --- amendments and resits on the same result (security audit) ---------------

def _exam_amendment(world, failed_class, score="90"):
    result = CourseResult.objects.get(student=world.student, offering=failed_class)
    exam = failed_class.assessment_components.get(is_examination=True)
    return results.request_amendment(result, new_marks={str(exam.id): score}, reason="Re-marked after appeal",
                                     actor=world.lecturer)


def test_resit_waits_for_pending_amendments(world, staff, failed_class):
    regulation(resits_enabled=True)
    amendment = _exam_amendment(world, failed_class)
    with pytest.raises(Exception, match="awaiting review"):
        resits.open_resit(failed_class, actor=staff.officer)
    results.review_amendment(amendment, approve=False, actor=make_user(Role.ADMIN), comment="Use the resit")
    assert resits.open_resit(failed_class, actor=staff.officer).entries.count() == 2


def test_amendment_cannot_overwrite_a_resit(world, staff, failed_class):
    regulation(resits_enabled=True)
    amendment = _exam_amendment(world, failed_class)
    # Opened despite the check (e.g. data from before it existed): approval must still refuse.
    from apps.results.models import ResitSheet
    sheet = ResitSheet.objects.create(offering=failed_class, opened_by=staff.officer)
    result = amendment.course_result
    ResitEntry.objects.create(sheet=sheet, course_result=result, original_total=result.total_score,
                              original_grade=result.grade)
    with pytest.raises(Exception, match="resit has been opened"):
        results.review_amendment(amendment, approve=True, actor=make_user(Role.ADMIN))
    assert CourseResult.objects.get(pk=amendment.course_result_id).grade == "F"


def test_my_resits_respects_the_results_hold(world, staff, failed_class):
    regulation(resits_enabled=True)
    resits.open_resit(failed_class, actor=staff.officer)
    client = client_for(world.student.user)
    assert client.get("/api/v1/my-resits/").status_code == 200
    world.student.status = "SUSPENDED"
    world.student.save()
    blocked = client.get("/api/v1/my-resits/")
    assert blocked.status_code == 403 and blocked.data["code"] == "results_blocked"


def test_courses_awaiting_approval_are_not_on_the_class_list(world, staff):
    reg = register(world, world.student, world.semester, ["CS101", "MATH101"])
    register(world, world.other_student, world.semester, ["CS101", "MATH101"])
    reg.status = "PENDING"
    reg.save()
    students = [r.student for r in results._active_registrations(world.offerings["CS101"])]
    assert students == [world.other_student]
