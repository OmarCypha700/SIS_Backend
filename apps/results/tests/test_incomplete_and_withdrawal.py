"""Students who cannot finish a course: excused (Incomplete), deferred examinations, withdrawal mid-semester."""
import pytest

from apps.institutions.models import AcademicRegulation
from apps.progression.tests.test_regulations import register
from apps.registration import services as registration
from apps.registration.models import CourseRegistrationStatus, RegistrationStatus
from apps.results import resits, services as results
from apps.results.models import CourseResult, ResitEntryStatus, SheetStatus
from apps.students.models import StudentStatus
from conftest import client_for

pytestmark = pytest.mark.django_db


def regulations(**values):
    regulation = AcademicRegulation.load()
    for key, value in values.items():
        setattr(regulation, key, value)
    regulation.save()


def prepared_sheet(world, code="CS101"):
    for student in (world.student, world.other_student):
        register(world, student, world.semester, ["CS101", "MATH101"])
    offering = world.offerings[code]
    sheet = results.get_or_create_sheet(offering)
    results.replace_components(sheet, [{"name": "Coursework", "weight": 40, "max_score": 100},
                                       {"name": "Exam", "weight": 60, "max_score": 100, "is_examination": True}],
                               actor=world.lecturer)
    return sheet


def reg_of(student, sheet):
    return student.course_registrations.get(offering=sheet.offering)


def mark(world, sheet, student, coursework, exam=None):
    work, exam_c = sheet.offering.assessment_components.order_by("order")
    entries = [{"registration": reg_of(student, sheet).id, "component": work.id, "score": coursework}]
    if exam is not None:
        entries.append({"registration": reg_of(student, sheet).id, "component": exam_c.id, "score": exam})
    results.save_marks(sheet, entries, actor=world.lecturer)


def publish(world, sheet, officer, registrar):
    results.submit_sheet(sheet, actor=world.lecturer)
    results.approve_sheet(sheet, actor=officer)
    return results.publish_sheet(sheet, actor=registrar)


def test_an_excused_student_is_incomplete_not_failed(world, results_officer, registrar):
    sheet = prepared_sheet(world)
    mark(world, sheet, world.student, 60, 70)
    mark(world, sheet, world.other_student, 55)  # missed the examination with a medical certificate
    with pytest.raises(Exception, match="missing required marks"):
        results.submit_sheet(sheet, actor=world.lecturer)

    api = client_for(world.lecturer)
    response = api.post(f"/api/v1/result-sheets/{sheet.pk}/excuse/",
                        {"registration": reg_of(world.other_student, sheet).id, "reason": "Medical certificate KBTH/221"},
                        format="json")
    assert response.status_code == 200
    row = next(r for r in response.data["rows"] if r["student_id"] == world.other_student.pk)
    assert row["grade"] == "I" and row["excused"].startswith("Medical") and row["missing_components"] == []
    assert response.data["summary"]["incomplete"] == 1

    publish(world, sheet, results_officer, registrar)
    result = CourseResult.objects.get(student=world.other_student, offering=sheet.offering)
    assert result.is_incomplete and result.grade == "I" and not result.is_pass

    record = results.build_academic_record(world.other_student)
    course = record["semesters"][0]["courses"][0]
    assert course["is_incomplete"] and record["semesters"][0]["credits_attempted"] == 0
    passed, failed = results.student_course_history(world.other_student)
    assert sheet.offering.course_id not in failed  # not a fail, not a referred course
    assert registration.attempt_counts(world.other_student) == {}


def test_deferred_examination_completes_the_result_without_a_resit_cap(world, results_officer, registrar):
    regulations(resits_enabled=False, resit_max_grade_point="2.00")
    sheet = prepared_sheet(world)
    mark(world, sheet, world.student, 60, 70)
    mark(world, sheet, world.other_student, 90)
    results.excuse(sheet, reg_of(world.other_student, sheet).id, reason="Bereavement", actor=world.lecturer)
    publish(world, sheet, results_officer, registrar)

    resit = resits.open_resit(sheet.offering, actor=results_officer)  # allowed although resits are off
    entry = resit.entries.get()
    assert entry.is_deferred and entry.status == ResitEntryStatus.REGISTERED
    resits.save_scores(resit, [{"entry": entry.id, "score": 90}], actor=world.lecturer)
    resits.submit(resit, actor=world.lecturer)
    resits.approve(resit, actor=results_officer)
    resits.publish(resit, actor=registrar)

    result = CourseResult.objects.get(student=world.other_student, offering=sheet.offering)
    assert not result.is_incomplete and not result.is_resit and not result.grade_capped
    assert result.total_score == 90 and result.grade_point > 2  # a first sitting, not capped
    assert resits.resits_used(world.other_student, sheet.offering.course_id) == 0


def test_a_deferred_candidate_who_does_not_sit_stays_incomplete(world, results_officer, registrar):
    regulations(resits_enabled=True, resit_min_total=None)
    sheet = prepared_sheet(world)
    mark(world, sheet, world.student, 40, 40)  # failed: resit candidate
    mark(world, sheet, world.other_student, 50)
    results.excuse(sheet, reg_of(world.other_student, sheet).id, reason="Approved absence", actor=world.lecturer)
    publish(world, sheet, results_officer, registrar)
    resit = resits.open_resit(sheet.offering, actor=results_officer)
    failing = resit.entries.get(is_deferred=False)
    resits.register_entries(resit, [failing.id], actor=registrar, reason="Registered at the registry")
    resits.save_scores(resit, [{"entry": failing.id, "score": 60}], actor=world.lecturer)
    resits.submit(resit, actor=world.lecturer)  # the deferred candidate needs no score
    resits.approve(resit, actor=results_officer)
    resits.publish(resit, actor=registrar)
    assert resit.entries.get(is_deferred=True).status == ResitEntryStatus.NOT_TAKEN
    assert CourseResult.objects.get(student=world.other_student, offering=sheet.offering).is_incomplete


def test_an_amendment_with_the_missing_marks_completes_the_result(world, results_officer, registrar, admin_user):
    sheet = prepared_sheet(world)
    mark(world, sheet, world.student, 60, 70)
    mark(world, sheet, world.other_student, 50)
    results.excuse(sheet, reg_of(world.other_student, sheet).id, reason="Sat the paper late with permission",
                   actor=world.lecturer)
    publish(world, sheet, results_officer, registrar)
    result = CourseResult.objects.get(student=world.other_student, offering=sheet.offering)
    exam = sheet.offering.assessment_components.get(is_examination=True)
    amendment = results.request_amendment(result, new_marks={str(exam.id): 80}, reason="Late paper marked",
                                          actor=world.lecturer)
    results.review_amendment(amendment, approve=True, actor=registrar)
    result.refresh_from_db()
    assert not result.is_incomplete and result.grade != "I" and result.is_pass


def test_withdrawing_mid_semester_takes_the_student_off_open_class_lists(world, results_officer, registrar):
    sheet = prepared_sheet(world)  # CS101 open; publish MATH101 first so it stays on the record
    math = results.get_or_create_sheet(world.offerings["MATH101"])
    results.replace_components(math, [{"name": "Exam", "weight": 100, "max_score": 100}], actor=world.lecturer)
    component = math.offering.assessment_components.get()
    results.save_marks(math, [{"registration": reg_of(s, math).id, "component": component.id, "score": 70}
                              for s in (world.student, world.other_student)], actor=world.lecturer)
    publish(world, math, results_officer, registrar)

    response = client_for(registrar).post(f"/api/v1/students/{world.other_student.pk}/change-status/",
                                          {"status": "DEFERRED", "reason": "Deferred on medical grounds",
                                           "withdraw_from_semester": True}, format="json")
    assert response.status_code == 200
    world.other_student.refresh_from_db()
    assert world.other_student.status == StudentStatus.DEFERRED
    assert reg_of(world.other_student, sheet).status == CourseRegistrationStatus.DROPPED
    assert reg_of(world.other_student, math).status == CourseRegistrationStatus.ACTIVE  # published: kept
    _, rows = results.sheet_rows(sheet)
    assert [r["student_id"] for r in rows] == [world.student.pk]

    # With nothing published, the whole semester registration is cancelled.
    registration_row = world.student.registrations.get()
    response = client_for(registrar).post(f"/api/v1/registrations/{registration_row.pk}/withdraw/",
                                          {"reason": "Withdrew from the programme"}, format="json")
    assert response.status_code == 200
    registration_row.refresh_from_db()
    assert registration_row.status == RegistrationStatus.REGISTERED  # MATH101 published, so kept
    assert client_for(world.lecturer).post(f"/api/v1/registrations/{registration_row.pk}/withdraw/",
                                           {"reason": "x"}, format="json").status_code == 403


def test_withdrawal_with_no_results_yet_cancels_the_registration(world, registrar):
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    registration_row = world.student.registrations.get()
    registration.withdraw_from_semester(registration_row, reason="Withdrew in week 2", actor=registrar)
    registration_row.refresh_from_db()
    assert registration_row.status == RegistrationStatus.CANCELLED and registration_row.total_credits == 0
    assert not world.student.course_registrations.filter(status=CourseRegistrationStatus.ACTIVE).exists()
    assert SheetStatus.DRAFT  # nothing to submit for this student
