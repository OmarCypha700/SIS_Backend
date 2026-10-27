import datetime

import pytest
from django.utils import timezone

from apps.academics.models import Semester
from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.registration import services as registration_services
from apps.results.models import CourseResult, GradingScheme, ResultSheet
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(world):
    """Both students registered for CS101; the sheet opened by the lecturer with two components."""
    for student in (world.student, world.other_student):
        registration_services.submit_registration(
            student, world.semester, [world.offerings["CS101"].pk, world.offerings["MATH101"].pk], actor=student.user)
    lecturer = client_for(world.lecturer)
    offering = world.offerings["CS101"]
    sheet = lecturer.get(f"/api/v1/result-sheets/for-offering/{offering.pk}/").data
    response = lecturer.put(f"/api/v1/result-sheets/{sheet['id']}/components/", {"components": [
        {"name": "Continuous assessment", "weight": "40", "max_score": "40"},
        {"name": "Final examination", "weight": "60", "max_score": "100"},
    ]}, format="json")
    assert response.status_code == 200, response.data
    detail = response.data
    officer = make_user(Role.RESULTS_OFFICER)
    publisher = make_user(Role.REGISTRAR)  # registrar has publish capability by default
    return world, lecturer, detail, officer, publisher


def _marks(detail, values):
    """values: list of (ca, exam) per row, in row order."""
    ca, exam = detail["components"][0]["id"], detail["components"][1]["id"]
    entries = []
    for row, (ca_score, exam_score) in zip(detail["rows"], values):
        entries.append({"registration": row["registration"], "component": ca, "score": ca_score})
        entries.append({"registration": row["registration"], "component": exam, "score": exam_score})
    return {"marks": entries}


def test_component_weights_must_total_100(setup):
    world, lecturer, detail, *_ = setup
    response = lecturer.put(f"/api/v1/result-sheets/{detail['id']}/components/", {"components": [
        {"name": "Exam", "weight": "90", "max_score": "100"}]}, format="json")
    assert response.status_code == 400 and response.data["code"] == "invalid_weights"


def test_mark_ranges_validated_on_backend(setup):
    world, lecturer, detail, *_ = setup
    response = lecturer.put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(41, 50), (10, 50)]),
                            format="json")
    assert response.status_code == 400 and response.data["code"] == "invalid_marks"
    response = lecturer.put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(-1, 50), (10, 50)]),
                            format="json")
    assert response.status_code == 400
    response = lecturer.put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [("abc", 50), (10, 50)]),
                            format="json")
    assert response.status_code == 400


def test_draft_marks_compute_totals(setup):
    world, lecturer, detail, *_ = setup
    response = lecturer.put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(36, 80), (10, None)]),
                            format="json")
    assert response.status_code == 200
    rows = {r["student_number"]: r for r in response.data["rows"]}
    first = rows[world.student.student_number]
    assert first["total"] == "84" and first["grade"] == "A"
    assert rows[world.other_student.student_number]["missing_components"]
    assert response.data["summary"]["missing"] == 1


def test_only_assigned_lecturer_enters_marks(setup):
    world, lecturer, detail, officer, publisher = setup
    stranger = make_user(Role.LECTURER)
    assert client_for(stranger).put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(1, 1), (1, 1)]),
                                    format="json").status_code == 404
    assert client_for(officer).put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(1, 1), (1, 1)]),
                                   format="json").status_code == 403
    assert client_for(world.student.user).get(f"/api/v1/result-sheets/{detail['id']}/").status_code == 403


def test_submission_requires_complete_marks(setup):
    world, lecturer, detail, *_ = setup
    lecturer.put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(36, 80), (10, None)]), format="json")
    response = lecturer.post(f"/api/v1/result-sheets/{detail['id']}/submit/", {}, format="json")
    assert response.status_code == 400 and response.data["code"] == "missing_marks"


def test_full_workflow_and_student_visibility(setup):
    world, lecturer, detail, officer, publisher = setup
    sheet_url = f"/api/v1/result-sheets/{detail['id']}"
    lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 80), (10, 20)]), format="json")
    student = client_for(world.student.user)

    assert lecturer.post(f"{sheet_url}/submit/", {}, format="json").status_code == 200
    # Marks are locked once submitted.
    assert lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(1, 1), (1, 1)]), format="json").status_code == 409
    # Lecturers cannot approve or publish; officers cannot publish without the capability.
    assert lecturer.post(f"{sheet_url}/approve/", {}, format="json").status_code == 403
    assert lecturer.post(f"{sheet_url}/publish/", {}, format="json").status_code == 403
    assert student.get("/api/v1/my-results/").data["semesters"] == []

    officer_client = client_for(officer)
    assert officer_client.post(f"{sheet_url}/publish/", {}, format="json").status_code == 403  # not approved yet
    assert officer_client.post(f"{sheet_url}/approve/", {}, format="json").status_code == 200
    assert officer_client.post(f"{sheet_url}/publish/", {}, format="json").status_code == 403  # no capability
    assert student.get("/api/v1/my-results/").data["semesters"] == []

    response = client_for(publisher).post(f"{sheet_url}/publish/", {}, format="json")
    assert response.status_code == 200 and response.data["status"] == "PUBLISHED"

    record = student.get("/api/v1/my-results/").data
    course = record["semesters"][0]["courses"][0]
    assert course["course_code"] == "CS101" and course["grade"] == "A" and course["total"] == "84.00"
    assert record["summary"]["cgpa"] == "4.00"
    other = client_for(world.other_student.user).get("/api/v1/my-results/").data
    assert other["semesters"][0]["courses"][0]["grade"] == "F"
    actions = set(AuditLog.objects.filter(entity_type="results.resultsheet").values_list("action", flat=True))
    assert {"results.submitted", "results.approved", "results.published", "marks.saved"} <= actions

    # Published results are immutable through normal operations.
    assert lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(1, 1), (1, 1)]), format="json").status_code == 409
    assert officer_client.post(f"{sheet_url}/return/", {"comment": "x"}, format="json").status_code == 409

    slip = student.get(f"/api/v1/my-results/slip/?semester={world.semester.pk}")
    assert slip.status_code == 200 and slip.data["semester"]["gpa"] == "4.00"


def test_return_for_correction_and_resubmit(setup):
    world, lecturer, detail, officer, _ = setup
    sheet_url = f"/api/v1/result-sheets/{detail['id']}"
    lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 80), (10, 20)]), format="json")
    lecturer.post(f"{sheet_url}/submit/", {}, format="json")
    officer_client = client_for(officer)
    assert officer_client.post(f"{sheet_url}/return/", {}, format="json").status_code == 400  # reason required
    returned = officer_client.post(f"{sheet_url}/return/", {"comment": "Check exam scores"}, format="json")
    assert returned.data["status"] == "RETURNED"
    assert lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 85), (10, 20)]), format="json").status_code == 200
    assert lecturer.post(f"{sheet_url}/submit/", {}, format="json").data["status"] == "SUBMITTED"


def test_approver_must_differ_from_submitter(setup):
    world, lecturer, detail, *_ = setup
    world.lecturer.can_approve_results = True
    world.lecturer.save()
    sheet_url = f"/api/v1/result-sheets/{detail['id']}"
    lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 80), (10, 20)]), format="json")
    lecturer.post(f"{sheet_url}/submit/", {}, format="json")
    response = lecturer.post(f"{sheet_url}/approve/", {}, format="json")
    assert response.status_code == 403


def test_entry_deadline(setup):
    world, lecturer, detail, *_ = setup
    Semester.objects.filter(pk=world.semester.pk).update(result_entry_deadline=timezone.now() - datetime.timedelta(hours=1))
    response = lecturer.put(f"/api/v1/result-sheets/{detail['id']}/marks/", _marks(detail, [(1, 1), (1, 1)]),
                            format="json")
    assert response.status_code == 409 and response.data["code"] == "entry_closed"


def _publish(setup):
    world, lecturer, detail, officer, publisher = setup
    sheet_url = f"/api/v1/result-sheets/{detail['id']}"
    lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 80), (10, 20)]), format="json")
    lecturer.post(f"{sheet_url}/submit/", {}, format="json")
    client_for(officer).post(f"{sheet_url}/approve/", {}, format="json")
    client_for(publisher).post(f"{sheet_url}/publish/", {}, format="json")
    return CourseResult.objects.get(student=world.other_student, offering=world.offerings["CS101"])


def test_amendment_requires_separate_approval(setup):
    world, lecturer, detail, officer, publisher = setup
    result = _publish(setup)
    exam = str(detail["components"][1]["id"])
    request = lecturer.post("/api/v1/result-amendments/", {
        "course_result": result.pk, "new_marks": {exam: "90"}, "reason": "Script re-marked after appeal",
    }, format="json")
    assert request.status_code == 201, request.data
    assert request.data["original_grade"] == "F" and request.data["new_grade"] == "C"
    result.refresh_from_db()
    assert result.grade == "F"  # nothing changes until approved

    # The requester cannot approve their own amendment; lecturers lack the capability anyway.
    assert lecturer.post(f"/api/v1/result-amendments/{request.data['id']}/review/", {"approve": True},
                         format="json").status_code == 403
    approved = client_for(publisher).post(f"/api/v1/result-amendments/{request.data['id']}/review/",
                                          {"approve": True, "comment": "Verified"}, format="json")
    assert approved.status_code == 200 and approved.data["status"] == "APPLIED"
    result.refresh_from_db()
    assert result.grade == "C" and result.is_amended and result.is_published
    log = AuditLog.objects.get(action="results.amended")
    assert log.before["grade"] == "F" and log.after["grade"] == "C"


def test_amendment_authorisation(setup):
    world, lecturer, detail, officer, publisher = setup
    result = _publish(setup)
    exam = str(detail["components"][1]["id"])
    payload = {"course_result": result.pk, "new_marks": {exam: "60"}, "reason": "x"}
    assert client_for(world.student.user).post("/api/v1/result-amendments/", payload, format="json").status_code == 403
    stranger = make_user(Role.LECTURER)
    assert client_for(stranger).post("/api/v1/result-amendments/", payload, format="json").status_code == 404
    assert client_for(publisher).post("/api/v1/result-amendments/", payload, format="json").status_code == 403


def test_student_status_can_block_result_access(setup):
    world, *_ = setup
    world.student.status = "SUSPENDED"
    world.student.save()
    assert client_for(world.student.user).get("/api/v1/my-results/").status_code == 403


def test_staff_academic_record_includes_unpublished(setup):
    world, lecturer, detail, officer, publisher = setup
    sheet_url = f"/api/v1/result-sheets/{detail['id']}"
    lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 80), (10, 20)]), format="json")
    lecturer.post(f"{sheet_url}/submit/", {}, format="json")
    record = client_for(officer).get(f"/api/v1/students/{world.student.pk}/academic-record/").data
    assert record["semesters"][0]["courses"][0]["is_published"] is False


def test_grading_scheme_admin(world, admin_user, lecturer):
    client = client_for(admin_user)
    payload = {"name": "Pass/Fail", "round_to_decimals": 0, "bands": [
        {"letter": "P", "min_score": "50", "max_score": "100", "grade_point": "1", "is_pass": True},
        {"letter": "F", "min_score": "0", "max_score": "48", "grade_point": "0", "is_pass": False},
    ]}
    response = client.post("/api/v1/grading-schemes/", payload, format="json")
    assert response.status_code == 400 and "gap" in str(response.data["errors"])
    payload["bands"][1]["max_score"] = "49"
    response = client.post("/api/v1/grading-schemes/", payload, format="json")
    assert response.status_code == 201
    assert client.post(f"/api/v1/grading-schemes/{response.data['id']}/set-default/").status_code == 200
    assert GradingScheme.objects.get(is_default=True).name == "Pass/Fail"
    assert client_for(lecturer).post("/api/v1/grading-schemes/", payload, format="json").status_code == 403


def test_scheme_used_by_submitted_results_is_locked(setup, admin_user):
    world, lecturer, detail, *_ = setup
    sheet_url = f"/api/v1/result-sheets/{detail['id']}"
    lecturer.put(f"{sheet_url}/marks/", _marks(detail, [(36, 80), (10, 20)]), format="json")
    lecturer.post(f"{sheet_url}/submit/", {}, format="json")
    response = client_for(admin_user).patch(f"/api/v1/grading-schemes/{world.scheme.pk}/",
                                            {"round_to_decimals": 1}, format="json")
    assert response.status_code == 400
    assert ResultSheet.objects.get(pk=detail["id"]).grading_scheme_id == world.scheme.pk


def test_dashboards_per_role(setup, admin_user):
    world, lecturer, detail, officer, publisher = setup
    expectations = [(admin_user, "registry"), (publisher, "registry"), (officer, "results_officer"),
                    (world.lecturer, "lecturer"), (world.student.user, "student")]
    for user, kind in expectations:
        response = client_for(user).get("/api/v1/dashboard/")
        assert response.status_code == 200 and response.data["kind"] == kind
    lecturer_data = client_for(world.lecturer).get("/api/v1/dashboard/").data
    assert lecturer_data["totals"]["students"] == 4  # two students on two courses
    student_data = client_for(world.student.user).get("/api/v1/dashboard/").data
    assert student_data["registration"]["total_credits"] == 6
