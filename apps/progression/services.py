"""
Progression review: proposes academic standing (good / probation / dismissal) and placement
(promote / repeat) per student from the configured academic regulations. Nothing is applied
until the registry applies the review; every individual decision can be overridden or excluded.

A semester has one main review and, after it is applied, any number of follow-up reviews. A
follow-up picks up outcomes that arrived later: students who were provisional or excluded, and
students not promoted whose outcome has since changed (a passed resit or deferred examination, a
result published late, an amendment).
"""
from django.db import transaction
from django.utils import timezone

from apps.academics.models import CourseCategory, Level, ProgrammeCourse, Semester
from apps.audit.services import log_action, snapshot
from apps.institutions.models import AcademicRegulation, InstitutionProfile
from apps.notifications import events as notify_events
from apps.registration.models import CourseRegistration, RegistrationStatus, SemesterRegistration
from apps.results import grading
from apps.results.models import AmendmentStatus, CourseResult, ResitSheet, ResultAmendment, ResultSheet, SheetStatus
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


def applied_outcomes(student, *, exclude_semester=None):
    """The decision in force for each semester (the last applied one, follow-ups included), newest first."""
    decisions = (ProgressionDecision.objects.filter(student=student, applied=True)
                 .select_related("review__semester").order_by("-review__semester__start_date", "-review__sequence"))
    if exclude_semester is not None:
        decisions = decisions.exclude(review__semester=exclude_semester)
    latest, seen = [], set()
    for decision in decisions:
        if decision.review.semester_id not in seen:
            seen.add(decision.review.semester_id)
            latest.append(decision)
    return latest


def consecutive_probations(student, before_semester):
    """Probations applied in a row, most recent first, before this semester."""
    count = 0
    decisions = [d for d in applied_outcomes(student) if d.review.semester.start_date < before_semester.start_date]
    for decision in decisions:
        if decision.final_standing != Standing.PROBATION:
            break
        count += 1
    return count


def evaluate(student, semester, regulation, *, final_term, places=2):
    """Compute the figures and proposed outcome for one student. Returns a dict of decision fields."""
    # Incomplete results (excused) and results superseded by a repeat year never count.
    results = sorted(
        CourseResult.objects.filter(student=student, is_published=True, is_incomplete=False, superseded=False,
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
    complete = not registered.exclude(result__is_published=True, result__is_incomplete=False).exists()

    reasons = []
    standing, placement = Standing.GOOD, Placement.STAY
    if not complete:
        reasons.append("Some results for this semester are not yet published or are Incomplete; "
                       "the decision is provisional.")

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
            if (placement == Placement.REPEAT and regulation.dismiss_on_second_repeat
                    and has_repeated_level(student, exclude_semester=semester)):
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


def has_repeated_level(student, *, exclude_semester=None):
    """True if an earlier semester's decision in force made this student repeat their current level."""
    return any(d.final_placement == Placement.REPEAT and d.level_id == student.current_level_id
               for d in applied_outcomes(student, exclude_semester=exclude_semester))


def outstanding_work(semester):
    """Things that can still change this semester's outcomes. Applying before they finish needs confirming."""
    items = []
    unpublished = (ResultSheet.objects.filter(offering__semester=semester, offering__registrations__status="ACTIVE")
                   .exclude(status=SheetStatus.PUBLISHED).distinct().count())
    never_started = (semester.offerings.filter(registrations__status="ACTIVE", result_sheet__isnull=True)
                     .distinct().count())
    if unpublished or never_started:
        items.append({"kind": "results", "count": unpublished + never_started,
                      "message": f"{unpublished + never_started} course(s) have results not yet published."})
    resits_open = ResitSheet.objects.filter(offering__semester=semester).exclude(status=SheetStatus.PUBLISHED).count()
    if resits_open:
        items.append({"kind": "resits", "count": resits_open,
                      "message": f"{resits_open} resit or deferred examination sheet(s) are not yet published."})
    amendments = ResultAmendment.objects.filter(course_result__offering__semester=semester,
                                                status=AmendmentStatus.PENDING).count()
    if amendments:
        items.append({"kind": "amendments", "count": amendments,
                      "message": f"{amendments} result amendment(s) are awaiting approval."})
    incomplete = CourseResult.objects.filter(offering__semester=semester, is_incomplete=True).count()
    if incomplete:
        items.append({"kind": "incomplete", "count": incomplete,
                      "message": f"{incomplete} result(s) are Incomplete, awaiting a deferred examination or the "
                                 "missing marks."})
    return items


def _latest_review(semester):
    return ProgressionReview.objects.filter(semester=semester).order_by("-sequence").first()


def _follow_up_candidate(student, semester, values):
    """In a follow-up, include students with no outcome in force, or not promoted whose outcome has changed."""
    previous = next((d for d in applied_outcomes(student) if d.review.semester_id == semester.id), None)
    if previous is None:
        return True
    if previous.final_placement in (Placement.PROMOTE, Placement.COMPLETE) or previous.final_standing == Standing.DISMISS:
        return False  # already moved on (or dismissed): changes are handled by the registry
    if student.current_level_id != previous.level_id:
        return False  # placement changed by hand since
    return (values["proposed_standing"], values["proposed_placement"]) != (previous.final_standing,
                                                                           previous.final_placement)


def _suffix(n):
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


@transaction.atomic
def generate_review(semester, *, actor, follow_up=False):
    """
    Create or refresh the semester's draft review. Registry overrides and exclusions are kept.
    With follow_up, start a follow-up once the latest review has been applied.
    """
    list(ProgressionReview.objects.select_for_update().filter(semester=semester))
    review = _latest_review(semester)
    if review and review.status == ReviewStatus.APPLIED:
        if not follow_up:
            raise DomainError("This semester's progression review has already been applied. Start a follow-up review "
                              "for outcomes that arrived since.", code="applied", status_code=409)
        review = ProgressionReview.objects.create(semester=semester, generated_by=actor, sequence=review.sequence + 1)
    elif follow_up and review is None:
        raise DomainError("Prepare and apply the main review first.", code="no_review", status_code=409)
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
        if review.is_follow_up and not _follow_up_candidate(student, semester, values):
            continue
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
    log_action("progression.generated", actor=actor, instance=review, summary=f"Generated {review}",
               metadata={"students": len(keep), "final_term": final_term, "sequence": review.sequence})
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
def apply_review(review, *, actor, acknowledge_outstanding=False):
    """Apply every included decision whose results are complete. Returns counts."""
    review = ProgressionReview.objects.select_for_update().select_related("semester").get(pk=review.pk)
    if review.status != ReviewStatus.DRAFT:
        raise DomainError("This review has already been applied.", code="applied", status_code=409)
    outstanding = outstanding_work(review.semester)
    if outstanding and not acknowledge_outstanding:
        raise DomainError("Some of this semester's outcomes can still change. Confirm to apply anyway; students "
                          "affected later can be handled in a follow-up review.", code="outstanding_work",
                          status_code=409, errors=outstanding)
    label = str(review)
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
                _set_repeating(student, None, reason=reason, actor=actor)
            elif decision.final_placement == Placement.REPEAT:
                # The whole level is taken again next year, passed courses included.
                _set_repeating(student, student.current_level, reason=reason, actor=actor)
        decision.applied = True
        decision.save(update_fields=["applied", "updated_at"])
        applied.append(decision)
        counts["applied"] += 1
    review.status, review.applied_by, review.applied_at = ReviewStatus.APPLIED, actor, timezone.now()
    review.save()
    log_action("progression.applied", actor=actor, instance=review, summary=f"Applied {label}",
               metadata={**counts, "outstanding_acknowledged": [o["kind"] for o in outstanding]})
    notify_events.progression_applied(applied, actor)
    return counts


def _set_repeating(student, level, *, reason, actor):
    if student.repeating_level_id == (level.pk if level else None):
        return
    before = student.repeating_level_id
    student.repeating_level = level
    student.save(update_fields=["repeating_level", "updated_at"])
    log_action("student.repeat_set" if level else "student.repeat_cleared", actor=actor, instance=student, reason=reason,
               before={"repeating_level": before}, after={"repeating_level": level.pk if level else None},
               summary=f"{student.student_number}: " + (f"repeating {level.name}" if level else "no longer repeating"))


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

