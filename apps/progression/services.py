"""
Progression review: proposes academic standing (good / probation / dismissal) and placement
(promote / repeat) per student from the configured academic regulations. Nothing is applied
until the registry applies the review; every individual decision can be overridden or excluded.
"""
from django.db import transaction
from django.utils import timezone

from apps.academics.models import CourseCategory, Level, ProgrammeCourse, Semester
from apps.audit.services import log_action, snapshot
from apps.institutions.models import AcademicRegulation, InstitutionProfile
from apps.notifications import events as notify_events
from apps.registration.models import CourseRegistration, RegistrationStatus, SemesterRegistration
from apps.results import grading
from apps.results.models import CourseResult
from apps.students import services as student_services
from apps.students.models import AcademicStanding, StudentStatus
from common.exceptions import DomainError

from .models import Placement, ProgressionDecision, ProgressionReview, ReviewStatus, Standing


def is_final_term(semester):
    return not Semester.objects.filter(academic_year=semester.academic_year, term_number__gt=semester.term_number).exists()


def next_level(student):
    levels = student.programme.levels.order_by("order")
    if not levels.exists():
        levels = Level.objects.order_by("order")
    return levels.filter(order__gt=student.current_level.order).first()


def consecutive_probations(student, before_semester):
    """Probations applied in a row, most recent first, before this semester."""
    count = 0
    decisions = (ProgressionDecision.objects.filter(student=student, applied=True,
                                                    review__semester__start_date__lt=before_semester.start_date)
                 .order_by("-review__semester__start_date"))
    for decision in decisions:
        if decision.final_standing != Standing.PROBATION:
            break
        count += 1
    return count


def evaluate(student, semester, regulation, *, final_term, places=2):
    """Compute the figures and proposed outcome for one student. Returns a dict of decision fields."""
    results = sorted(
        CourseResult.objects.filter(student=student, is_published=True,
                                    offering__semester__start_date__lte=semester.start_date)
        .select_related("offering__semester", "offering__course"),
        key=lambda r: (r.offering.semester.start_date, r.offering.course.code),
    )
    profile = InstitutionProfile.load()
    counted = grading.apply_retake_policy(results, profile.cgpa_retake_policy)
    cgpa = grading.gpa(((r.grade_point, r.credits) for r in counted), places)
    this_semester = [r for r in results if r.offering.semester_id == semester.id]
    semester_gpa = grading.gpa(((r.grade_point, r.credits) for r in this_semester), places)
    credits_year = sum(r.credits for r in results
                       if r.is_pass and r.offering.semester.academic_year_id == semester.academic_year_id)

    latest = {}
    for result in results:  # chronological, so the last attempt wins
        latest[result.offering.course_id] = result
    failed_now = [r for r in latest.values() if not r.is_pass]
    outstanding = sum(r.credits for r in failed_now)
    referred = len(failed_now)  # courses failed and not yet passed ("referred")

    registered = CourseRegistration.objects.filter(student=student, offering__semester=semester, status="ACTIVE")
    complete = not registered.exclude(result__is_published=True).exists()

    reasons = []
    standing, placement = Standing.GOOD, Placement.STAY
    if not complete:
        reasons.append("Some results for this semester are not yet published; the decision is provisional.")

    # Dismissal and probation
    threshold = regulation.dismissal_cgpa_below
    below_dismissal = cgpa is not None and threshold is not None and (
        cgpa <= threshold if regulation.dismissal_cgpa_inclusive else cgpa < threshold)
    if below_dismissal:
        standing = Standing.DISMISS
        comparison = "at or below" if regulation.dismissal_cgpa_inclusive else "below"
        reasons.append(f"CGPA {cgpa} is {comparison} the dismissal threshold of {threshold}.")
    elif regulation.dismiss_when_referred_courses_over is not None and referred > regulation.dismiss_when_referred_courses_over:
        standing = Standing.DISMISS
        reasons.append(f"{referred} referred courses (more than {regulation.dismiss_when_referred_courses_over}): "
                       f"{', '.join(sorted(r.offering.course.code for r in failed_now))}.")
    elif cgpa is not None and regulation.probation_cgpa_below is not None and cgpa < regulation.probation_cgpa_below:
        previous = consecutive_probations(student, semester)
        limit = regulation.dismiss_after_consecutive_probations
        if limit and previous + 1 >= limit:
            standing = Standing.DISMISS
            reasons.append(f"CGPA {cgpa} is below {regulation.probation_cgpa_below} for the "
                           f"{previous + 1}{_suffix(previous + 1)} consecutive review (limit {limit}).")
        else:
            standing = Standing.PROBATION
            reasons.append(f"CGPA {cgpa} is below the probation threshold of {regulation.probation_cgpa_below}.")
    if regulation.dismiss_when_attempts_exhausted and regulation.max_attempts_per_course and standing != Standing.DISMISS:
        compulsory = set(ProgrammeCourse.objects.filter(programme=student.programme, category=CourseCategory.COMPULSORY)
                         .values_list("course_id", flat=True))
        attempts = {}
        for result in results:
            attempts[result.offering.course_id] = attempts.get(result.offering.course_id, 0) + 1
        exhausted = [r.offering.course.code for r in failed_now
                     if r.offering.course_id in compulsory and attempts[r.offering.course_id] >= regulation.max_attempts_per_course]
        if exhausted:
            standing = Standing.DISMISS
            reasons.append(f"Compulsory course(s) failed with no attempts left: {', '.join(sorted(exhausted))}.")

    # Placement (end of the academic year only)
    if standing != Standing.DISMISS and final_term:
        target = next_level(student)
        shortfalls = []
        if regulation.promotion_min_credits_per_year is not None and credits_year < regulation.promotion_min_credits_per_year:
            shortfalls.append(f"earned {credits_year} credits this year (minimum {regulation.promotion_min_credits_per_year})")
        if (regulation.promotion_max_outstanding_failed_credits is not None
                and outstanding > regulation.promotion_max_outstanding_failed_credits):
            shortfalls.append(f"{outstanding} failed credits outstanding (maximum {regulation.promotion_max_outstanding_failed_credits})")
        if regulation.promotion_min_cgpa is not None and (cgpa is None or cgpa < regulation.promotion_min_cgpa):
            shortfalls.append(f"CGPA {cgpa if cgpa is not None else 'not available'} (minimum {regulation.promotion_min_cgpa})")
        if regulation.max_referred_courses_to_proceed is not None and referred > regulation.max_referred_courses_to_proceed:
            shortfalls.append(f"{referred} referred courses (maximum {regulation.max_referred_courses_to_proceed})")
        if shortfalls:
            placement = Placement.REPEAT if regulation.repeat_level_when_not_promoted else Placement.STAY
            reasons.append("Not eligible for promotion: " + "; ".join(shortfalls) + ".")
            if placement == Placement.REPEAT and regulation.dismiss_on_second_repeat and has_repeated_level(student):
                standing, placement = Standing.DISMISS, Placement.STAY
                reasons.append(f"Has already repeated {student.current_level.name}; a second repeat leads to withdrawal.")
        elif target is None:
            placement = Placement.COMPLETE
            reasons.append("Final level of the programme; completion and graduation are decided separately.")
        else:
            placement = Placement.PROMOTE
            reasons.append(f"Meets the requirements to move to {target.name}.")
    elif not final_term and standing != Standing.DISMISS:
        reasons.append("Promotion is only considered after the last term of the academic year.")

    if not reasons:
        reasons.append("Meets all configured requirements.")
    return {
        "semester_gpa": semester_gpa, "cgpa": cgpa, "credits_earned_year": credits_year,
        "outstanding_failed_credits": outstanding, "results_complete": complete,
        "proposed_standing": standing, "proposed_placement": placement, "reasons": reasons,
    }


def has_repeated_level(student):
    """True if an applied review already made this student repeat their current level."""
    return ProgressionDecision.objects.filter(
        student=student, applied=True, excluded=False, final_placement=Placement.REPEAT, level=student.current_level,
    ).exists()


def _suffix(n):
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


@transaction.atomic
def generate_review(semester, *, actor):
    """Create or refresh the draft review. Registry overrides and exclusions are kept."""
    review = ProgressionReview.objects.select_for_update().filter(semester=semester).first()
    if review and review.status == ReviewStatus.APPLIED:
        raise DomainError("This semester's progression review has already been applied.", code="applied", status_code=409)
    regulation = AcademicRegulation.load()
    final_term = is_final_term(semester)
    if review is None:
        review = ProgressionReview.objects.create(semester=semester, generated_by=actor)
    review.is_final_term = final_term
    review.regulation_snapshot = snapshot(regulation)
    review.generated_by = actor
    review.save()

    previous = {d.student_id: d for d in review.decisions.all()}
    registrations = (SemesterRegistration.objects.filter(semester=semester, status=RegistrationStatus.REGISTERED)
                     .select_related("student__programme", "student__current_level"))
    keep = set()
    for registration in registrations:
        student = registration.student
        if student.status != StudentStatus.ACTIVE:
            continue
        values = evaluate(student, semester, regulation, final_term=final_term)
        decision = previous.get(student.id)
        if decision is None:
            decision = ProgressionDecision(review=review, student=student,
                                           final_standing=values["proposed_standing"],
                                           final_placement=values["proposed_placement"])
        elif not decision.override_reason:
            decision.final_standing, decision.final_placement = values["proposed_standing"], values["proposed_placement"]
        decision.programme, decision.level = student.programme, student.current_level
        for key, value in values.items():
            setattr(decision, key, value)
        decision.save()
        keep.add(student.id)
    review.decisions.exclude(student_id__in=keep).delete()
    log_action("progression.generated", actor=actor, instance=review, summary=f"Generated progression review for {semester}",
               metadata={"students": len(keep), "final_term": final_term})
    return review


def _editable(decision):
    if decision.review.status != ReviewStatus.DRAFT:
        raise DomainError("This review has been applied and can no longer be changed.", code="applied", status_code=409)


@transaction.atomic
def override_decision(decision, *, standing, placement, reason, actor):
    _editable(decision)
    if not (reason or "").strip():
        raise DomainError("A reason is required to change a proposed outcome.", code="reason_required")
    if standing == Standing.DISMISS and placement not in (Placement.STAY,):
        placement = Placement.STAY
    before = {"standing": decision.final_standing, "placement": decision.final_placement}
    decision.final_standing, decision.final_placement = standing, placement
    decision.override_reason, decision.overridden_by = reason, actor
    decision.save()
    log_action("progression.overridden", actor=actor, instance=decision, reason=reason, before=before,
               after={"standing": standing, "placement": placement},
               summary=f"Changed progression outcome for {decision.student.student_number}")
    return decision


@transaction.atomic
def set_excluded(decision, *, excluded, reason, actor):
    _editable(decision)
    if excluded and not (reason or "").strip():
        raise DomainError("A reason is required to exclude a student from this review.", code="reason_required")
    decision.excluded = excluded
    if excluded:
        decision.override_reason = reason
    decision.save(update_fields=["excluded", "override_reason", "updated_at"])
    log_action("progression.excluded" if excluded else "progression.included", actor=actor, instance=decision,
               reason=reason, summary=f"{'Excluded' if excluded else 'Included'} {decision.student.student_number}")
    return decision


@transaction.atomic
def apply_review(review, *, actor):
    """Apply every included decision whose results are complete. Returns counts."""
    review = ProgressionReview.objects.select_for_update().select_related("semester").get(pk=review.pk)
    if review.status != ReviewStatus.DRAFT:
        raise DomainError("This review has already been applied.", code="applied", status_code=409)
    label = f"Progression review {review.semester}"
    regulation = AcademicRegulation.load()
    counts = {"applied": 0, "skipped_incomplete": 0, "excluded": 0}
    applied = []
    for decision in review.decisions.select_related("student__programme", "student__current_level"):
        if decision.excluded:
            counts["excluded"] += 1
            continue
        if not decision.results_complete:
            counts["skipped_incomplete"] += 1
            continue
        student = decision.student
        reason = f"{label}: " + (decision.override_reason or " ".join(decision.reasons))
        if decision.final_standing == Standing.DISMISS:
            status = regulation.dismissal_status or StudentStatus.DISMISSED
            if student.status != status:
                student_services.change_status(student, status=status, reason=reason, actor=actor, notify=False)
        else:
            new_standing = (AcademicStanding.PROBATION if decision.final_standing == Standing.PROBATION
                            else AcademicStanding.GOOD)
            if student.academic_standing != new_standing:
                previous = student.academic_standing
                student.academic_standing = new_standing
                student.save(update_fields=["academic_standing", "updated_at"])
                log_action("student.standing_changed", actor=actor, instance=student, reason=reason,
                           before={"academic_standing": previous}, after={"academic_standing": new_standing},
                           summary=f"{student.student_number}: {previous} -> {new_standing}")
            if decision.final_placement == Placement.PROMOTE:
                target = next_level(student)
                if target is not None:
                    student_services.change_placement(student, programme=student.programme, level=target,
                                                      reason=reason, actor=actor)
        decision.applied = True
        decision.save(update_fields=["applied", "updated_at"])
        applied.append(decision)
        counts["applied"] += 1
    review.status, review.applied_by, review.applied_at = ReviewStatus.APPLIED, actor, timezone.now()
    review.save()
    log_action("progression.applied", actor=actor, instance=review, summary=f"Applied {label}", metadata=counts)
    notify_events.progression_applied(applied, actor)
    return counts


def summary(review):
    decisions = list(review.decisions.all())
    tally = lambda field, value: sum(1 for d in decisions if getattr(d, field) == value and not d.excluded)  # noqa: E731
    return {
        "students": len(decisions),
        "good": tally("final_standing", Standing.GOOD),
        "probation": tally("final_standing", Standing.PROBATION),
        "dismiss": tally("final_standing", Standing.DISMISS),
        "promote": tally("final_placement", Placement.PROMOTE),
        "repeat": tally("final_placement", Placement.REPEAT),
        "overridden": sum(1 for d in decisions if d.override_reason and not d.excluded),
        "excluded": sum(1 for d in decisions if d.excluded),
        "incomplete": sum(1 for d in decisions if not d.results_complete),
    }

