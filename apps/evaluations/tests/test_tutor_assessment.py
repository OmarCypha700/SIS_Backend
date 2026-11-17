"""Tutor assessment before results: the hold, anonymity, rules, reports and permissions."""
import datetime

import pytest
from django.core.management import call_command
from django.db import models
from django.utils import timezone

from apps.academics.models import LecturerAssignment
from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.evaluations import services
from apps.evaluations.models import (
    AssessmentComment, AssessmentCompletion, AssessmentResponse, CommentStatus, CourseAssessment, Questionnaire,
)
from apps.institutions.models import InstitutionProfile
from apps.notifications.models import Kind, Notification
from apps.progression.tests.test_regulations import register
from apps.results import services as results
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


def settings(**values):
    profile = InstitutionProfile.load()
    for key, value in values.items():
        setattr(profile, key, value)
    profile.save()


def publish(world, code, officer, registrar, marks=(60, 70), without_tutors=False):
    """Mark both students and publish the course's results."""
    sheet = results.get_or_create_sheet(world.offerings[code])
    results.replace_components(sheet, [{"name": "Coursework", "weight": 40, "max_score": 100},
                                       {"name": "Exam", "weight": 60, "max_score": 100, "is_examination": True}],
                               actor=world.lecturer)
    work, exam = sheet.offering.assessment_components.order_by("order")
    entries = []
    for student in (world.student, world.other_student):
        reg = student.course_registrations.get(offering=sheet.offering)
        entries += [{"registration": reg.id, "component": work.id, "score": marks[0]},
                    {"registration": reg.id, "component": exam.id, "score": marks[1]}]
    results.save_marks(sheet, entries, actor=world.lecturer)
    results.submit_sheet(sheet, actor=world.lecturer)
    results.approve_sheet(sheet, actor=officer)
    if without_tutors:
        LecturerAssignment.objects.filter(offering=sheet.offering).delete()
    return results.publish_sheet(sheet, actor=registrar)


@pytest.fixture
def setup(world, results_officer, registrar):
    settings(tutor_assessment_enabled=True)
    for student in (world.student, world.other_student):
        register(world, student, world.semester, ["CS101", "MATH101"])
    return world


def answers(assessment, rating=4, comment=""):
    data, commented = {}, False
    for q in assessment.questions:
        if q["kind"] == "RATING":
            data[q["key"]] = str(rating)
        else:
            data[q["key"]] = "" if commented else comment  # the comment goes in the first comment question
            commented = True
    return data


def submit(student, assessment, lecturer, **kwargs):
    return client_for(student.user).post(f"/api/v1/my-assessments/{assessment.pk}/submit/",
                                         {"lecturer": lecturer.pk, "answers": answers(assessment, **kwargs)},
                                         format="json")


def course_of(record, code):
    return next(c for s in record["semesters"] for c in s["courses"] if c["course_code"] == code)


def my_results(student):
    return client_for(student.user).get("/api/v1/my-results/").data


def test_publication_opens_an_assessment_and_holds_the_result(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get(offering=setup.offerings["CS101"])
    assert assessment.closes_on == timezone.localdate() + datetime.timedelta(days=14)
    assert list(assessment.lecturers.all()) == [setup.lecturer]
    assert Questionnaire.objects.get(is_active=True).questions.count() == 12  # the preset, loaded on first use

    record = my_results(setup.student)
    course = course_of(record, "CS101")
    assert course["assessment_required"] is True and course["assessment_id"] == assessment.pk
    assert course["total"] is None and course["grade"] is None and course["grade_point"] is None
    assert record["semesters"][0]["gpa"] is None and record["summary"]["cgpa"] is None
    assert record["assessments_pending"] == 1
    note = Notification.objects.get(recipient=setup.student.user, kind=Kind.ASSESSMENT_DUE)
    assert note.link == f"/my-assessments/{assessment.pk}".replace("my-assessments", "my-results/assess")
    assert "B" not in note.body and "60" not in note.body  # never the grade or marks


def test_every_tutor_unlocks_the_course_and_every_course_unlocks_the_gpa(setup, results_officer, registrar):
    second = make_user(Role.LECTURER)
    LecturerAssignment.objects.create(offering=setup.offerings["CS101"], lecturer=second)
    publish(setup, "CS101", results_officer, registrar)
    publish(setup, "MATH101", results_officer, registrar)
    cs101 = CourseAssessment.objects.get(offering=setup.offerings["CS101"])
    maths = CourseAssessment.objects.get(offering=setup.offerings["MATH101"])

    first = submit(setup.student, cs101, setup.lecturer)
    assert first.status_code == 201 and first.data["unlocked"] is False
    assert course_of(my_results(setup.student), "CS101")["grade"] is None  # one tutor of two

    done = submit(setup.student, cs101, second)
    assert done.data["unlocked"] is True and done.data["result"]["grade"]
    record = my_results(setup.student)
    assert course_of(record, "CS101")["grade"] and course_of(record, "MATH101")["grade"] is None
    assert record["semesters"][0]["gpa"] is None  # MATH101 still waiting

    submit(setup.student, maths, setup.lecturer)
    record = my_results(setup.student)
    assert record["semesters"][0]["gpa"] and record["summary"]["cgpa"] and record["assessments_pending"] == 0
    # The other student has assessed nothing and still sees nothing.
    assert course_of(my_results(setup.other_student), "CS101")["grade"] is None


def test_answers_cannot_be_traced_to_a_student(setup, results_officer, registrar):
    for model in (AssessmentResponse, AssessmentComment):
        fields = {f.name: f for f in model._meta.get_fields() if getattr(f, "concrete", False)}
        assert isinstance(fields["id"], models.UUIDField)  # no sequence that lines up with completions
        related = {f.related_model._meta.label for f in fields.values() if f.is_relation}
        assert "students.Student" not in related
        assert not any(isinstance(f, models.DateTimeField) for f in fields.values())  # dates, never times
    assert "ratings" not in {f.name for f in AssessmentCompletion._meta.get_fields()}

    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get(offering=setup.offerings["CS101"])
    submit(setup.student, assessment, setup.lecturer, rating=5, comment="Very clear lectures")
    response = AssessmentResponse.objects.get()
    assert response.submitted_on == timezone.localdate() and response.uniform is True
    assert AssessmentComment.objects.get().text == "Very clear lectures"
    assert AssessmentCompletion.objects.get().student == setup.student
    assert not AuditLog.objects.filter(action__startswith="evaluations.").exists()  # submissions are not logged


def test_the_hold_lifts_when_the_window_closes_or_the_setting_is_off(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() - datetime.timedelta(days=1))
    assert course_of(my_results(setup.student), "CS101")["grade"]
    assert submit(setup.student, assessment, setup.lecturer).status_code == 409  # closed

    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() + datetime.timedelta(days=3))
    assert course_of(my_results(setup.student), "CS101")["grade"] is None
    settings(tutor_assessment_enabled=False)
    assert course_of(my_results(setup.student), "CS101")["grade"]


def test_slips_statements_and_the_dashboard_respect_the_hold(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    api = client_for(setup.student.user)
    slip = api.get(f"/api/v1/my-results/slip-pdf/?semester={setup.semester.pk}")
    assert slip.status_code == 409 and slip.data["code"] == "assessment_required"
    assert api.get("/api/v1/my-results/transcript-pdf/").status_code == 409
    slip_data = api.get(f"/api/v1/my-results/slip/?semester={setup.semester.pk}").data
    assert course_of({"semesters": [slip_data["semester"]]}, "CS101")["grade"] is None
    dashboard = api.get("/api/v1/dashboard/").data["results"]
    assert dashboard["cgpa"] is None and dashboard["assessments_pending"] == 1

    submit(setup.student, CourseAssessment.objects.get(), setup.lecturer)
    assert api.get(f"/api/v1/my-results/slip-pdf/?semester={setup.semester.pk}").status_code == 200
    assert api.get("/api/v1/my-results/transcript-pdf/").status_code == 200
    # Staff views are never held.
    staff = client_for(registrar).get(f"/api/v1/students/{setup.other_student.pk}/academic-record/")
    assert staff.status_code == 200


def test_submission_rules(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    api = client_for(setup.student.user)
    url = f"/api/v1/my-assessments/{assessment.pk}/submit/"

    missing = api.post(url, {"lecturer": setup.lecturer.pk, "answers": {}}, format="json")
    assert missing.status_code == 400 and missing.data["code"] == "answers_missing"
    bad = answers(assessment)
    bad[assessment.questions[0]["key"]] = "9"
    assert api.post(url, {"lecturer": setup.lecturer.pk, "answers": bad}, format="json").status_code == 400
    stranger = make_user(Role.LECTURER)
    assert api.post(url, {"lecturer": stranger.pk, "answers": answers(assessment)}, format="json").status_code == 400

    assert submit(setup.student, assessment, setup.lecturer).status_code == 201
    again = submit(setup.student, assessment, setup.lecturer)
    assert again.status_code == 409 and again.data["code"] == "already_assessed"
    assert AssessmentResponse.objects.count() == 1

    # A student without a result for the course cannot see or answer it.
    outsider = make_user(Role.STUDENT)
    from conftest import make_student
    outsider_student = make_student(setup.programme, setup.l100)
    assert client_for(outsider_student.user).get(f"/api/v1/my-assessments/{assessment.pk}/").status_code == 404
    assert submit(outsider_student, assessment, setup.lecturer).status_code == 404
    assert outsider  # unused account without a student record
    # Staff cannot use the student endpoints.
    assert client_for(setup.lecturer).get("/api/v1/my-assessments/").status_code == 403


def test_assessments_open_once_only_when_switched_on_and_with_tutors(setup, results_officer, registrar):
    settings(tutor_assessment_enabled=False)
    publish(setup, "CS101", results_officer, registrar)
    assert not CourseAssessment.objects.exists()

    settings(tutor_assessment_enabled=True)
    publish(setup, "MATH101", results_officer, registrar, without_tutors=True)
    assert not CourseAssessment.objects.exists()  # nobody to assess: results show straight away
    assert course_of(my_results(setup.student), "MATH101")["grade"]

    sheet = results.get_or_create_sheet(setup.offerings["CS101"])
    LecturerAssignment.objects.get_or_create(offering=setup.offerings["CS101"], lecturer=setup.lecturer)
    assert services.open_for(sheet) is not None
    assert services.open_for(sheet) is None  # once per course: amendments and resits do not reopen it


def test_questions_are_frozen_once_used(setup, results_officer, registrar):
    qa = client_for(make_user(Role.QA_OFFICER))
    created = qa.post("/api/v1/questionnaires/load-preset/")
    assert created.status_code == 201 and created.data["is_active"] is True
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    snapshot = list(assessment.questions)

    pk = created.data["id"]
    change = qa.patch(f"/api/v1/questionnaires/{pk}/", {"questions": [{"text": "New?", "kind": "RATING"}]},
                      format="json")
    assert change.status_code == 409
    assert qa.delete(f"/api/v1/questionnaires/{pk}/").status_code == 409
    copy = qa.post(f"/api/v1/questionnaires/{pk}/copy/")
    assert copy.status_code == 201
    assert qa.patch(f"/api/v1/questionnaires/{copy.data['id']}/",
                    {"questions": [{"text": "Was the tutor punctual?", "kind": "RATING"}]},
                    format="json").status_code == 200
    assert qa.post(f"/api/v1/questionnaires/{copy.data['id']}/activate/").data["is_active"] is True
    assessment.refresh_from_db()
    assert assessment.questions == snapshot
    assert AuditLog.objects.filter(action="evaluations.questionnaire_activated").exists()


def test_reports_show_averages_only_with_enough_responses(setup, results_officer, registrar, admin_user):
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    submit(setup.student, assessment, setup.lecturer, rating=5)
    qa = client_for(make_user(Role.QA_OFFICER))

    row = qa.get("/api/v1/tutor-assessments/").data["results"][0]
    assert row["responses"] == 1 and row["eligible"] == 2 and row["response_rate"] == 50
    assert row["enough_responses"] is False and row["overall"] is None
    assert all(q["average"] is None and q["distribution"] is None for q in row["questions"])

    settings(tutor_assessment_min_responses=2)  # (the settings form allows 3 or more)
    submit(setup.other_student, assessment, setup.lecturer, rating=3)
    detail = qa.get(f"/api/v1/tutor-assessments/{assessment.pk}/").data["tutors"][0]
    assert detail["overall"] == 4.0 and detail["uniform"] == 2
    first = detail["questions"][0]
    assert first["average"] == 4.0 and first["distribution"] == [0, 0, 1, 0, 1]

    export = qa.get("/api/v1/tutor-assessments/export/")
    assert export.status_code == 200 and b"Response rate" in export.content
    assert AuditLog.objects.filter(action="evaluations.report_exported").exists()
    assert client_for(admin_user).get("/api/v1/tutor-assessments/").status_code == 200
    for user in (setup.student.user, setup.lecturer, registrar, results_officer):
        assert client_for(user).get("/api/v1/tutor-assessments/").status_code == 403


def test_tutors_see_their_own_feedback_after_closing_with_approved_comments(setup, results_officer, registrar):
    settings(tutor_assessment_min_responses=2)
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    submit(setup.student, assessment, setup.lecturer, comment="Explains clearly")
    submit(setup.other_student, assessment, setup.lecturer, comment="Please return scripts sooner")
    tutor = client_for(setup.lecturer)

    open_now = tutor.get("/api/v1/my-teaching-feedback/").data
    assert open_now["feedback"] == [] and open_now["open"][0]["course_code"] == "CS101"
    assert "responses" not in open_now["open"][0]  # no live counts while students answer

    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() - datetime.timedelta(days=1))
    report = tutor.get("/api/v1/my-teaching-feedback/").data["feedback"][0]
    assert report["overall"] == 4.0 and report["comments"] == []  # comments wait for review

    qa = client_for(make_user(Role.QA_OFFICER))
    pending = qa.get("/api/v1/tutor-assessments/comments/").data
    assert len(pending) == 2
    by_text = {c["text"]: c["id"] for c in pending}
    hide = f"/api/v1/tutor-assessments/comments/{by_text['Please return scripts sooner']}/review/"
    assert qa.post(hide, {"status": "HIDDEN"}, format="json").status_code == 400  # needs a reason
    qa.post(hide, {"status": "HIDDEN", "reason": "Duplicate of another remark"}, format="json")
    qa.post(f"/api/v1/tutor-assessments/comments/{by_text['Explains clearly']}/review/", {"status": "APPROVED"},
            format="json")
    assert AuditLog.objects.filter(action="evaluations.comment_reviewed").count() == 2

    report = tutor.get("/api/v1/my-teaching-feedback/").data["feedback"][0]
    assert [c["text"] for c in report["comments"]] == ["Explains clearly"]
    other = client_for(make_user(Role.LECTURER)).get("/api/v1/my-teaching-feedback/").data
    assert other["feedback"] == [] and other["open"] == []


def test_reminders_before_closing_and_feedback_notices_after(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    submit(setup.student, assessment, setup.lecturer)
    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() + datetime.timedelta(days=2))
    call_command("send_assessment_notices")
    call_command("send_assessment_notices")  # hourly runs send it once
    due = Notification.objects.filter(kind=Kind.ASSESSMENT_DUE, title__startswith="Assess your tutor")
    assert [n.recipient for n in due] == [setup.other_student.user]

    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() - datetime.timedelta(days=1))
    call_command("send_assessment_notices")
    call_command("send_assessment_notices")
    ready = Notification.objects.filter(kind=Kind.FEEDBACK_READY)
    assert [n.recipient for n in ready] == [setup.lecturer]
    assert CommentStatus.PENDING  # (enum import used)


def test_quality_assurance_officers_get_their_own_dashboard(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    submit(setup.student, CourseAssessment.objects.get(), setup.lecturer, comment="Good pace")
    data = client_for(make_user(Role.QA_OFFICER)).get("/api/v1/dashboard/").data
    assert data["kind"] == "quality"
    assert data["quality"] == {"open": 1, "comments_to_review": 1, "responses": 1, "response_rate": 50,
                               "enabled": True}


def test_institutions_can_keep_feedback_from_tutors(setup, results_officer, registrar):
    settings(tutor_feedback_to_tutors=False, tutor_assessment_min_responses=2)
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    submit(setup.student, assessment, setup.lecturer)
    submit(setup.other_student, assessment, setup.lecturer)
    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() - datetime.timedelta(days=1))

    tutor = client_for(setup.lecturer)
    refused = tutor.get("/api/v1/my-teaching-feedback/")
    assert refused.status_code == 403 and refused.data["code"] == "feedback_not_shared"
    assert tutor.get("/api/v1/auth/me/").data["features"]["teaching_feedback"] is False
    call_command("send_assessment_notices")
    assert not Notification.objects.filter(kind=Kind.FEEDBACK_READY).exists()  # not told about what they cannot see
    # Quality assurance still sees everything.
    qa = client_for(make_user(Role.QA_OFFICER))
    assert qa.get(f"/api/v1/tutor-assessments/{assessment.pk}/").data["tutors"][0]["overall"] == 4.0

    settings(tutor_feedback_to_tutors=True)
    assert tutor.get("/api/v1/auth/me/").data["features"]["teaching_feedback"] is True
    assert tutor.get("/api/v1/my-teaching-feedback/").data["feedback"][0]["overall"] == 4.0


def test_students_are_not_told_when_results_would_show_anyway(setup, results_officer, registrar):
    publish(setup, "CS101", results_officer, registrar)
    assessment = CourseAssessment.objects.get()
    api = client_for(setup.student.user)
    held = course_of(my_results(setup.student), "CS101")
    assert "assessment_closes_on" not in held and "closes_on" not in str(api.get("/api/v1/my-assessments/").data)
    assert "closes_on" not in api.get(f"/api/v1/my-assessments/{assessment.pk}/").data
    CourseAssessment.objects.filter(pk=assessment.pk).update(closes_on=timezone.localdate() + datetime.timedelta(days=2))
    call_command("send_assessment_notices")
    reminder = Notification.objects.filter(kind=Kind.ASSESSMENT_DUE, title__startswith="Assess your tutor").first()
    assert reminder is not None and not any(month in reminder.body for month in
                                            ("January", "February", "March", "April", "May", "June", "July", "August",
                                             "September", "October", "November", "December"))
