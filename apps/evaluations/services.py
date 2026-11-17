"""
Tutor assessment rules.

* An assessment opens when a course's results are FIRST published (resits and amendments do not reopen
  it), for every tutor assigned to the course at that moment, and closes after the institution's
  window (14 days by default).
* While open, a student who has not assessed every tutor of a course cannot see that course's score
  or grade, the semester GPA, or the CGPA. After it closes, or with the setting off, everything shows.
* Answers are stored without any link to the student (see models.py). Reports give averages only for
  a tutor with enough responses, and list comments in random-key order, never in the order received.
"""
from collections import defaultdict
from datetime import timedelta
from statistics import mean

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academics.models import LecturerAssignment
from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from apps.results.models import CourseResult
from common.exceptions import DomainError

from .models import (
    AssessmentComment, AssessmentCompletion, AssessmentResponse, CommentStatus, CourseAssessment, Question,
    Questionnaire, QuestionKind,
)
from .presets import PRESET_NAME, PRESET_QUESTIONS

MAX_COMMENT = 2000


def _profile():
    return InstitutionProfile.load()


def _today():
    return timezone.localdate()


def tutors_see_feedback():
    """Whether tutors may see their own results (an institution setting; QA and administrators always can)."""
    return _profile().tutor_feedback_to_tutors


# --- Questionnaires -------------------------------------------------------------------

@transaction.atomic
def load_preset(*, actor=None, activate=False):
    """Create the built-in questionnaire (a copy the institution can change)."""
    questionnaire = Questionnaire.objects.create(name=PRESET_NAME, created_by=actor)
    Question.objects.bulk_create([
        Question(questionnaire=questionnaire, order=i, label=label, text=text, kind=kind, required=required)
        for i, (label, text, kind, required) in enumerate(PRESET_QUESTIONS, 1)
    ])
    if activate:
        activate_questionnaire(questionnaire, actor=actor)
    elif actor is not None:
        log_action("evaluations.questionnaire_created", actor=actor, instance=questionnaire,
                   summary=f"Loaded the standard questionnaire as '{questionnaire.name}'")
    return questionnaire


@transaction.atomic
def activate_questionnaire(questionnaire, *, actor=None):
    if not questionnaire.questions.filter(kind=QuestionKind.RATING).exists():
        raise DomainError("A questionnaire needs at least one rating question.", code="no_rating_questions")
    Questionnaire.objects.filter(is_active=True).exclude(pk=questionnaire.pk).update(is_active=False)
    questionnaire.is_active = True
    questionnaire.save(update_fields=["is_active", "updated_at"])
    if actor is not None:
        log_action("evaluations.questionnaire_activated", actor=actor, instance=questionnaire,
                   summary=f"Made '{questionnaire.name}' the questionnaire for new assessments")
    return questionnaire


@transaction.atomic
def copy_questionnaire(questionnaire, *, actor):
    copy = Questionnaire.objects.create(name=f"{questionnaire.name} (copy)"[:120], created_by=actor)
    Question.objects.bulk_create([
        Question(questionnaire=copy, order=q.order, label=q.label, text=q.text, kind=q.kind, required=q.required)
        for q in questionnaire.questions.all()
    ])
    log_action("evaluations.questionnaire_copied", actor=actor, instance=copy,
               summary=f"Copied '{questionnaire.name}' to change it")
    return copy


def require_editable(questionnaire):
    if questionnaire.is_used:
        raise DomainError("This questionnaire has been used, so its questions cannot change. Copy it instead.",
                          code="questionnaire_used", status_code=409)


def _snapshot(questionnaire):
    return [{"key": f"q{q.pk}", "text": q.text, "label": q.label or q.text[:40], "kind": q.kind,
             "required": q.required} for q in questionnaire.questions.all()]


# --- Opening ---------------------------------------------------------------------------

def open_for(sheet):
    """Called when a result sheet is published. Opens the course's tutor assessment once, if switched on."""
    profile = _profile()
    if not profile.tutor_assessment_enabled:
        return None
    offering = sheet.offering
    if CourseAssessment.objects.filter(offering=offering).exists():
        return None
    lecturer_ids = list(LecturerAssignment.objects.filter(offering=offering, lecturer__is_active=True)
                        .values_list("lecturer_id", flat=True))
    if not lecturer_ids:
        return None  # nobody to assess: the results show straight away
    questionnaire = Questionnaire.objects.filter(is_active=True).first() or load_preset(activate=True)
    today = _today()
    assessment = CourseAssessment.objects.create(
        offering=offering, questionnaire=questionnaire, questions=_snapshot(questionnaire),
        opens_on=today, closes_on=today + timedelta(days=profile.tutor_assessment_days),
    )
    assessment.lecturers.set(lecturer_ids)
    return assessment


# --- The hold on a student's results --------------------------------------------------------

def pending(student):
    """
    Open assessments this student still has to complete, by offering id:
    {offering_id: {"assessment": CourseAssessment, "remaining": [lecturer ids]}}.
    Empty when the feature is off.
    """
    if not _profile().tutor_assessment_enabled:
        return {}
    assessments = (CourseAssessment.objects
                   .filter(closes_on__gte=_today(), offering__course_results__student=student,
                           offering__course_results__is_published=True)
                   .select_related("offering__course").prefetch_related("lecturers").distinct())
    done = set(AssessmentCompletion.objects.filter(student=student, assessment__in=assessments)
               .values_list("assessment_id", "lecturer_id"))
    result = {}
    for assessment in assessments:
        remaining = [u.pk for u in assessment.lecturers.all() if (assessment.pk, u.pk) not in done]
        if remaining:
            result[assessment.offering_id] = {"assessment": assessment, "remaining": remaining}
    return result


HIDDEN_COURSE_FIELDS = ("total", "grade", "grade_point", "is_pass", "grade_capped")


def hold(record, student, waiting=None):
    """
    The academic record as the student may see it now: each course whose tutors are not all assessed
    shows no score or grade, and while anything is waiting the GPAs, CGPA and credits earned (which
    would give the hidden results away) are blank too.
    """
    waiting = pending(student) if waiting is None else waiting
    record["assessments_pending"] = len(waiting)
    if not waiting:
        return record
    earliest = None
    for semester in record["semesters"]:
        locked = False
        for course in semester["courses"]:
            item = waiting.get(course.get("offering_id"))
            if item is None:
                continue
            for field in HIDDEN_COURSE_FIELDS:
                course[field] = None
            # No closing date: students are not told when results would show without assessing.
            course.update(assessment_required=True, assessment_id=item["assessment"].pk)
            locked = True
        if locked:
            semester.update(gpa=None, credits_earned=None, assessment_required=True)
            earliest = semester["semester_id"] if earliest is None else earliest
        if earliest is not None:
            semester["cgpa"] = None  # this and every later CGPA include the hidden results
    record["summary"].update(cgpa=None, credits_earned=None)
    return record


def semester_on_hold(record, semester_id):
    return any(s["semester_id"] == semester_id and s.get("assessment_required") for s in record["semesters"])


# --- Submitting -------------------------------------------------------------------------------

def _clean_answers(questions, answers):
    if not isinstance(answers, dict):
        raise DomainError("Answer the questions.", code="invalid_answers")
    ratings, comments, missing = {}, {}, []
    for question in questions:
        value = answers.get(question["key"])
        if question["kind"] == QuestionKind.RATING:
            try:
                number = int(value)
            except (TypeError, ValueError):
                number = None
            if number is None or not 1 <= number <= 5:
                if question["required"] or value not in (None, ""):
                    missing.append(question["label"])
                continue
            ratings[question["key"]] = number
        else:
            text = str(value or "").strip()
            if len(text) > MAX_COMMENT:
                raise DomainError(f"Keep each comment under {MAX_COMMENT} characters.", code="comment_too_long")
            if text:
                comments[question["key"]] = text
            elif question["required"]:
                missing.append(question["label"])
    if missing:
        raise DomainError("Answer every required question: " + ", ".join(missing) + ".", code="answers_missing",
                          errors={"missing": missing})
    if not ratings:
        raise DomainError("Answer the rating questions.", code="answers_missing")
    return ratings, comments


@transaction.atomic
def submit(student, assessment, lecturer_id, answers):
    """Record one student's assessment of one tutor: a completion (who) and a response (what), unlinked."""
    today = _today()
    if not assessment.opens_on <= today <= assessment.closes_on:
        raise DomainError("This assessment has closed.", code="assessment_closed", status_code=409)
    if not CourseResult.objects.filter(student=student, offering_id=assessment.offering_id, is_published=True).exists():
        raise DomainError("This assessment is not for you.", code="not_found", status_code=404)
    try:
        lecturer_id = int(lecturer_id)
    except (TypeError, ValueError):
        lecturer_id = None
    if lecturer_id is None or not assessment.lecturers.filter(pk=lecturer_id).exists():
        raise DomainError("Choose a tutor of this course.", code="invalid_lecturer")
    ratings, comments = _clean_answers(assessment.questions, answers)
    try:
        with transaction.atomic():
            AssessmentCompletion.objects.create(assessment=assessment, student=student, lecturer_id=lecturer_id,
                                                completed_on=today)
    except IntegrityError:
        raise DomainError("You have already assessed this tutor.", code="already_assessed", status_code=409) from None
    values = list(ratings.values())
    AssessmentResponse.objects.create(assessment=assessment, lecturer_id=lecturer_id, ratings=ratings,
                                      uniform=len(values) > 1 and len(set(values)) == 1, submitted_on=today)
    AssessmentComment.objects.bulk_create([
        AssessmentComment(assessment=assessment, lecturer_id=lecturer_id, question_key=key, text=text)
        for key, text in comments.items()
    ])


# --- Reports ------------------------------------------------------------------------------------

def eligible_count(assessment):
    """Students who can assess: those with a published result for the course."""
    return CourseResult.objects.filter(offering_id=assessment.offering_id, is_published=True).values("student").distinct().count()


def _round(value):
    return round(value, 2) if value is not None else None


def tutor_report(assessment, lecturer, *, minimum=None, comment_statuses=(CommentStatus.APPROVED,),
                 eligible=None, exclude_uniform=False):
    """One tutor's results for one course. Averages only with at least `minimum` responses."""
    minimum = _profile().tutor_assessment_min_responses if minimum is None else minimum
    responses = list(AssessmentResponse.objects.filter(assessment=assessment, lecturer=lecturer)
                     .values_list("ratings", "uniform"))
    uniform_count = sum(1 for _, u in responses if u)
    used = [r for r, u in responses if not (exclude_uniform and u)]
    count = len(responses)
    eligible = eligible_count(assessment) if eligible is None else eligible
    enough = count >= minimum
    questions = []
    for question in assessment.questions:
        if question["kind"] != QuestionKind.RATING:
            continue
        values = [r[question["key"]] for r in used if question["key"] in r]
        questions.append({
            "key": question["key"], "label": question["label"], "text": question["text"],
            "average": _round(mean(values)) if enough and values else None,
            "distribution": [values.count(n) for n in range(1, 6)] if enough else None,
        })
    averages = [q["average"] for q in questions if q["average"] is not None]
    comments = []
    if enough:
        comments = [{"id": str(c.id), "question": c.question_key, "text": c.text, "status": c.status}
                    for c in AssessmentComment.objects.filter(assessment=assessment, lecturer=lecturer,
                                                               status__in=comment_statuses).order_by("id")]
    labels = {q["key"]: q["label"] for q in assessment.questions}
    for c in comments:
        c["question"] = labels.get(c["question"], c["question"])
    offering = assessment.offering
    return {
        "assessment_id": assessment.pk,
        "lecturer": {"id": lecturer.pk, "name": lecturer.get_full_name()},
        "course_code": offering.course.code, "course_title": offering.course.title,
        "department": offering.course.department.name if offering.course.department_id else "",
        "semester": f"{offering.semester.academic_year.name} {offering.semester.name}",
        "semester_id": offering.semester_id,
        "opens_on": assessment.opens_on, "closes_on": assessment.closes_on,
        "is_closed": _today() > assessment.closes_on,
        "responses": count, "eligible": eligible,
        "response_rate": _round(count * 100 / eligible) if eligible else None,
        "enough_responses": enough, "minimum": minimum,
        "overall": _round(mean(averages)) if averages else None,
        "uniform": uniform_count,
        "questions": questions,
        "comments": comments,
    }


def report_rows(assessments, *, lecturer=None):
    """Summary rows (one per tutor and course) for lists and exports."""
    minimum = _profile().tutor_assessment_min_responses
    rows = []
    for assessment in assessments:
        eligible = eligible_count(assessment)
        tutors = [lecturer] if lecturer is not None else list(assessment.lecturers.all())
        for tutor in tutors:
            report = tutor_report(assessment, tutor, minimum=minimum, comment_statuses=(), eligible=eligible)
            report.pop("comments")
            rows.append(report)
    return rows


def lecturer_trend(lecturer):
    """A tutor's overall average course by course, oldest first (closed assessments with enough responses)."""
    assessments = (CourseAssessment.objects.filter(lecturers=lecturer, closes_on__lt=_today())
                   .select_related("offering__course__department", "offering__semester__academic_year")
                   .order_by("closes_on"))
    return [{"semester": r["semester"], "course_code": r["course_code"], "overall": r["overall"]}
            for r in report_rows(assessments, lecturer=lecturer) if r["overall"] is not None]


@transaction.atomic
def review_comment(comment, *, status, reason, actor):
    if status not in (CommentStatus.APPROVED, CommentStatus.HIDDEN):
        raise DomainError("Approve or hide the comment.", code="invalid_status")
    if status == CommentStatus.HIDDEN and not (reason or "").strip():
        raise DomainError("Give a reason for hiding a comment.", code="reason_required")
    comment.status, comment.reviewed_by, comment.reviewed_on = status, actor, _today()
    comment.save(update_fields=["status", "reviewed_by", "reviewed_on"])
    log_action("evaluations.comment_reviewed", actor=actor, entity_type="evaluations.assessmentcomment",
               entity_id=str(comment.pk), reason=reason or "",
               summary=f"{'Approved' if status == CommentStatus.APPROVED else 'Hid'} a comment on "
                       f"{comment.assessment.offering.course.code}")
    return comment


# --- Scheduled task --------------------------------------------------------------------------

def send_reminders_and_feedback():
    """Daily: remind students 3 days before an assessment closes; tell tutors when their feedback is ready."""
    from apps.notifications import events

    today = _today()
    reminded = 0
    for assessment in CourseAssessment.objects.filter(reminder_sent=False, closes_on__lte=today + timedelta(days=3),
                                                      closes_on__gte=today).select_related("offering__course"):
        done = defaultdict(set)
        for student_id, lecturer_id in AssessmentCompletion.objects.filter(assessment=assessment).values_list(
                "student_id", "lecturer_id"):
            done[student_id].add(lecturer_id)
        tutors = set(assessment.lecturers.values_list("pk", flat=True))
        students = (CourseResult.objects.filter(offering_id=assessment.offering_id, is_published=True)
                    .values_list("student_id", flat=True).distinct())
        waiting = [s for s in students if not tutors <= done[s]]
        if waiting and _profile().tutor_assessment_enabled:
            from apps.students.models import Student

            events.assessment_due(assessment, list(Student.objects.filter(pk__in=waiting).select_related("user")))
            reminded += len(waiting)
        assessment.reminder_sent = True
        assessment.save(update_fields=["reminder_sent"])
    ready = 0
    share = tutors_see_feedback()
    for assessment in CourseAssessment.objects.filter(feedback_notified=False, closes_on__lt=today).select_related(
            "offering__course"):
        if share:  # tutors are only told when they may look
            events.feedback_ready(assessment)
        assessment.feedback_notified = True
        assessment.save(update_fields=["feedback_notified"])
        ready += 1 if share else 0
    return reminded, ready
