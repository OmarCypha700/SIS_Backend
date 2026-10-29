"""
Resit (supplementary) examinations.

1. An authorised officer opens a resit for an offering whose results are published. Failing
   students who meet the regulations become ELIGIBLE entries.
2. Students (or the registry on their behalf) register before the deadline.
3. The assigned lecturer enters a score out of 100 for each registered student.
4. Submit -> approve -> publish, with the same capabilities as ordinary results.
5. On publication the course result is recalculated: coursework is kept and the resit score
   replaces the components marked as examinations (or the whole total if none are marked),
   then the regulation's resit grade cap is applied.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.services import log_action
from apps.institutions.models import AcademicRegulation, InstitutionProfile
from apps.notifications import events as notify_events
from common.exceptions import DomainError

from . import grading
from .models import (
    CourseResult, ResitEntry, ResitEntryStatus, ResitSheet, ResultSheet, SheetStatus, StudentMark,
)
from .services import _bands, forbidden, is_assigned_lecturer

HUNDRED = Decimal("100")
RESULTS_ROLES = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)


def visible_resit_sheets(user, queryset):
    role = getattr(user, "role", None)
    if role in RESULTS_ROLES:
        return queryset
    if role == Role.LECTURER:
        return queryset.filter(offering__assignments__lecturer=user).distinct()
    return queryset.none()


def resits_used(student, course_id):
    return ResitEntry.objects.filter(
        course_result__student=student, course_result__offering__course_id=course_id,
        status=ResitEntryStatus.COMPLETED,
    ).count()


@transaction.atomic
def open_resit(offering, *, actor, registration_deadline=None):
    regulation = AcademicRegulation.load()
    if actor.role not in RESULTS_ROLES:
        raise forbidden("Only examinations or registry staff can open a resit.")
    if not regulation.resits_enabled:
        raise DomainError("Resits are not enabled in the academic regulations.", code="resits_disabled")
    sheet = ResultSheet.objects.filter(offering=offering).first()  # fresh status, not a cached relation
    if sheet is None or sheet.status != SheetStatus.PUBLISHED:
        raise DomainError("A resit can only be opened after the course results are published.", code="not_published")
    if ResitSheet.objects.filter(offering=offering).exists():
        raise DomainError("A resit has already been opened for this course offering.", code="exists", status_code=409)

    failing = CourseResult.objects.filter(sheet=sheet, is_pass=False, is_published=True).select_related("student", "offering")
    if regulation.resit_min_total is not None:
        failing = failing.filter(total_score__gte=regulation.resit_min_total)
    candidates = [r for r in failing if resits_used(r.student, offering.course_id) < regulation.max_resits_per_course]
    resit = ResitSheet.objects.create(offering=offering, registration_deadline=registration_deadline, opened_by=actor)
    ResitEntry.objects.bulk_create([
        ResitEntry(sheet=resit, course_result=r, original_total=r.total_score, original_grade=r.grade) for r in candidates
    ])
    log_action("resit.opened", actor=actor, instance=resit, summary=f"Resit opened for {offering}",
               metadata={"eligible": len(candidates), "deadline": registration_deadline})
    notify_events.resit_opened(resit, actor)
    return resit


def _check_registration_open(sheet, actor):
    if sheet.status not in (SheetStatus.DRAFT, SheetStatus.RETURNED):
        raise DomainError("Resit registration has closed for this course.", code="closed", status_code=409)
    is_staff = actor.role in (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)
    if not is_staff and sheet.registration_deadline and timezone.now() > sheet.registration_deadline:
        raise DomainError("The resit registration deadline has passed.", code="deadline_passed", status_code=409)


@transaction.atomic
def register_entries(sheet, entry_ids, *, actor, reason=""):
    """Register eligible entries. Students may only register themselves."""
    sheet = ResitSheet.objects.select_for_update().get(pk=sheet.pk)
    _check_registration_open(sheet, actor)
    entries = list(sheet.entries.select_related("course_result__student").filter(pk__in=entry_ids))
    if len(entries) != len(set(entry_ids)):
        raise DomainError("One or more resit entries were not found.", code="not_found", status_code=404)
    on_behalf = actor.role != Role.STUDENT
    if on_behalf and actor.role not in (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER):
        raise forbidden("You cannot register students for resits.")
    if on_behalf and not (reason or "").strip():
        raise DomainError("A reason is required when registering students for a resit.", code="reason_required")
    now = timezone.now()
    for entry in entries:
        if not on_behalf and entry.course_result.student.user_id != actor.pk:
            raise DomainError("Resit entry not found.", code="not_found", status_code=404)
        if entry.status != ResitEntryStatus.ELIGIBLE:
            raise DomainError("Already registered for this resit.", code="already_registered", status_code=409)
        entry.status, entry.registered_by, entry.registered_at = ResitEntryStatus.REGISTERED, actor, now
        entry.save(update_fields=["status", "registered_by", "registered_at", "updated_at"])
        log_action("resit.registered", actor=actor, instance=entry, reason=reason,
                   summary=f"{entry.course_result.student.student_number} registered for the {sheet.offering.course.code} resit")
    return entries


@transaction.atomic
def save_scores(sheet, scores, *, actor):
    """scores: [{"entry": id, "score": number|null}]"""
    sheet = ResitSheet.objects.select_for_update().select_related("offering").get(pk=sheet.pk)
    if not is_assigned_lecturer(actor, sheet.offering):
        raise forbidden("Only lecturers assigned to this course can enter resit scores.")
    if not sheet.is_editable:
        raise DomainError("This resit can no longer be edited.", code="sheet_locked", status_code=409)
    entries = {e.id: e for e in sheet.entries.filter(status=ResitEntryStatus.REGISTERED)}
    errors, changed = [], []
    for index, item in enumerate(scores):
        entry = entries.get(item.get("entry"))
        if entry is None:
            errors.append({"index": index, "message": "Only registered resit candidates can be given a score."})
            continue
        raw = item.get("score")
        try:
            score = None if raw in (None, "") else Decimal(str(raw)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            errors.append({"index": index, "entry": entry.id, "message": f"'{raw}' is not a valid score."})
            continue
        if score is not None and not (0 <= score <= HUNDRED):
            errors.append({"index": index, "entry": entry.id, "message": "Resit scores must be between 0 and 100."})
            continue
        if entry.score != score:
            changed.append({"entry": entry.id, "from": entry.score, "to": score})
            entry.score = score
            entry.save(update_fields=["score", "updated_at"])
    if errors:
        raise DomainError("Some scores are invalid. Nothing was saved.", code="invalid_marks", errors=errors)
    if changed:
        log_action("resit.scores_saved", actor=actor, instance=sheet, summary=f"Saved {len(changed)} resit score(s)",
                   metadata={"changes": changed})
    return len(changed)


def preview(entry, sheet=None):
    """Recalculate the course result with the resit score. Returns (total, band, capped) or None."""
    if entry.score is None:
        return None
    result = entry.course_result
    offering = result.offering
    scheme = result.sheet.grading_scheme
    bands = _bands(scheme)
    components = list(offering.assessment_components.all())
    exam = [c for c in components if c.is_examination]
    if exam:
        coursework_components = [c for c in components if not c.is_examination]
        marks = {m.component_id: m.score for m in StudentMark.objects.filter(registration_id=result.registration_id)}
        coursework = grading.weighted_total(coursework_components, marks)
        exam_weight = sum((c.weight for c in exam), Decimal(0))
        total = coursework + entry.score / HUNDRED * exam_weight
    else:
        total = entry.score
    rounded, band = grading.grade(total, scheme, bands)
    band, capped = grading.cap_band(band, bands, AcademicRegulation.load().resit_max_grade_point)
    return rounded, band, capped


def _transition(sheet, *, actor, to_status, comment, action):
    previous = sheet.status
    sheet.status = to_status
    sheet.last_comment = comment
    sheet.save()
    log_action(f"resit.{action}", actor=actor, instance=sheet, reason=comment,
               summary=f"Resit {sheet.offering}: {previous} -> {to_status}",
               before={"status": previous}, after={"status": to_status})
    notify_events.resit_changed(sheet, actor, to_status)


@transaction.atomic
def submit(sheet, *, actor, comment=""):
    sheet = ResitSheet.objects.select_for_update().select_related("offering").get(pk=sheet.pk)
    if not is_assigned_lecturer(actor, sheet.offering):
        raise forbidden("Only a lecturer assigned to this course can submit resit scores.")
    if not sheet.is_editable:
        raise DomainError("Only draft or returned resits can be submitted.", code="invalid_transition", status_code=409)
    registered = list(sheet.entries.filter(status=ResitEntryStatus.REGISTERED).select_related("course_result__student"))
    if not registered:
        raise DomainError("No students are registered for this resit.", code="no_students")
    missing = [e.course_result.student.student_number for e in registered if e.score is None]
    if missing:
        raise DomainError(f"{len(missing)} registered student(s) have no resit score.", code="missing_marks",
                          errors=[{"student_number": n} for n in missing])
    sheet.submitted_by, sheet.submitted_at = actor, timezone.now()
    _transition(sheet, actor=actor, to_status=SheetStatus.SUBMITTED, comment=comment, action="submitted")
    return sheet


@transaction.atomic
def return_for_correction(sheet, *, actor, comment=""):
    sheet = ResitSheet.objects.select_for_update().get(pk=sheet.pk)
    if not actor.can_approve_results:
        raise forbidden("You are not authorised to review results.")
    if sheet.status not in (SheetStatus.SUBMITTED, SheetStatus.APPROVED):
        raise DomainError("Only submitted or approved resits can be returned.", code="invalid_transition", status_code=409)
    if not (comment or "").strip():
        raise DomainError("Give a reason so the lecturer knows what to correct.", code="comment_required")
    sheet.approved_by = sheet.approved_at = None
    _transition(sheet, actor=actor, to_status=SheetStatus.RETURNED, comment=comment, action="returned")
    return sheet


@transaction.atomic
def approve(sheet, *, actor, comment=""):
    sheet = ResitSheet.objects.select_for_update().get(pk=sheet.pk)
    if not actor.can_approve_results:
        raise forbidden("You are not authorised to approve results.")
    if sheet.status != SheetStatus.SUBMITTED:
        raise DomainError("Only submitted resits can be approved.", code="invalid_transition", status_code=409)
    if InstitutionProfile.load().approver_must_differ_from_submitter and sheet.submitted_by_id == actor.pk:
        raise forbidden("Resit scores must be approved by someone other than the person who submitted them.")
    sheet.approved_by, sheet.approved_at = actor, timezone.now()
    _transition(sheet, actor=actor, to_status=SheetStatus.APPROVED, comment=comment, action="approved")
    return sheet


@transaction.atomic
def publish(sheet, *, actor, comment=""):
    sheet = ResitSheet.objects.select_for_update().get(pk=sheet.pk)
    if not actor.can_publish_results:
        raise forbidden("You are not authorised to publish results.")
    if sheet.status != SheetStatus.APPROVED:
        raise DomainError("Only approved resits can be published.", code="invalid_transition", status_code=409)
    if InstitutionProfile.load().publisher_must_differ_from_submitter and sheet.submitted_by_id == actor.pk:
        raise forbidden("Resit results must be published by someone other than the person who submitted them.")
    for entry in sheet.entries.select_related("course_result__sheet__grading_scheme", "course_result__offering",
                                              "course_result__student"):
        if entry.status == ResitEntryStatus.ELIGIBLE:
            entry.status = ResitEntryStatus.NOT_TAKEN
            entry.save(update_fields=["status", "updated_at"])
            continue
        if entry.status != ResitEntryStatus.REGISTERED:
            continue
        total, band, capped = preview(entry)
        result = entry.course_result
        before = {"total": str(result.total_score), "grade": result.grade, "grade_point": str(result.grade_point)}
        result.total_score, result.grade, result.grade_point, result.is_pass = total, band.letter, band.grade_point, band.is_pass
        result.is_resit, result.grade_capped = True, capped
        result.save()
        entry.status, entry.new_total, entry.new_grade = ResitEntryStatus.COMPLETED, total, band.letter
        entry.save(update_fields=["status", "new_total", "new_grade", "updated_at"])
        log_action("results.resit_applied", actor=actor, instance=result, before=before,
                   after={"total": str(total), "grade": band.letter, "grade_point": str(band.grade_point), "capped": capped},
                   summary=f"Resit result for {result.student.student_number} {result.offering.course.code}")
    sheet.published_by, sheet.published_at = actor, timezone.now()
    _transition(sheet, actor=actor, to_status=SheetStatus.PUBLISHED, comment=comment, action="published")
    return sheet


def student_entries(student):
    return (ResitEntry.objects.filter(course_result__student=student)
            .exclude(status=ResitEntryStatus.NOT_TAKEN)
            .select_related("sheet__offering__course", "sheet__offering__semester__academic_year")
            .order_by("-created_at"))
