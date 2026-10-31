"""
Financial clearance rules.

When the institution turns on *Students must be financially cleared before registering*, a student
can only register for a semester once the Accounts Office has cleared them for it. Clearance is
recorded per semester; revoking it later does not undo a registration (marks may exist), but can
withhold results if the institution also turns that on.
"""
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from common.exceptions import DomainError

from .models import FinancialClearance

NOT_CLEARED_MESSAGE = ("You have not been financially cleared for {semester}. "
                       "Please contact the Accounts Office.")


def is_cleared(student, semester):
    return FinancialClearance.objects.filter(student=student, semester=semester, cleared=True).exists()


def clearance_block(student, semester, profile=None):
    """The message shown when the student may not register because they are not cleared, else None."""
    profile = profile or InstitutionProfile.load()
    if not profile.require_financial_clearance or is_cleared(student, semester):
        return None
    return NOT_CLEARED_MESSAGE.format(semester=semester)


def results_withheld(student):
    """True when results are withheld until the student is cleared for the current semester."""
    from apps.academics.models import Semester

    profile = InstitutionProfile.load()
    if not profile.withhold_results_until_cleared:
        return False
    semester = Semester.objects.filter(is_current=True).first()
    return semester is not None and not is_cleared(student, semester)


@transaction.atomic
def set_clearance(students, semester, *, cleared, note="", actor, notify=True):
    """Clear or revoke several students for a semester. Each change is audited. Returns the students changed."""
    note = (note or "").strip()
    if not cleared and not note:
        raise DomainError("Give a reason for revoking clearance.", code="reason_required")
    existing = {c.student_id: c for c in FinancialClearance.objects.select_for_update()
                .filter(semester=semester, student__in=students)}
    now = timezone.now()
    changed = []
    for student in students:
        clearance = existing.get(student.pk)
        if clearance is not None and clearance.cleared == cleared and (not note or clearance.note == note):
            continue
        if clearance is None:
            clearance = FinancialClearance(student=student, semester=semester)
        clearance.cleared, clearance.decided_by, clearance.decided_at = cleared, actor, now
        if note or cleared:
            clearance.note = note[:255]
        clearance.save()
        log_action("finance.cleared" if cleared else "finance.clearance_revoked", actor=actor, instance=clearance,
                   reason=note, summary=f"{student.student_number} {'cleared' if cleared else 'not cleared'} for {semester}")
        changed.append(student)
    if cleared and notify and changed:
        from apps.notifications import events

        events.financially_cleared(changed, semester, actor)
    return changed


def import_rows(records, semester, *, actor):
    """Rows of student_number, optional cleared (yes/no) and note. Unknown numbers are reported, not fatal."""
    from apps.students.models import Student

    numbers = [str(r.get("student_number", "")).strip() for r in records]
    students = {s.student_number.lower(): s for s in Student.objects.filter(student_number__in=[n for n in numbers if n])}
    to_clear, to_revoke, unknown = [], [], []
    notes = {}
    for record, number in zip(records, numbers):
        student = students.get(number.lower())
        if student is None:
            if number:
                unknown.append(number)
            continue
        flag = str(record.get("cleared", "yes")).strip().lower() or "yes"
        (to_revoke if flag in ("no", "n", "false", "0") else to_clear).append(student)
        notes[student.pk] = str(record.get("note", "")).strip()
    newly_cleared, changed = [], 0
    with transaction.atomic():
        for student in to_clear:
            done = set_clearance([student], semester, cleared=True, note=notes[student.pk] or "Imported",
                                 actor=actor, notify=False)
            newly_cleared += done
            changed += len(done)
        for student in to_revoke:
            changed += len(set_clearance([student], semester, cleared=False,
                                         note=notes[student.pk] or "Imported: not cleared", actor=actor))
    if newly_cleared:
        from apps.notifications import events

        events.financially_cleared(newly_cleared, semester, actor)
    return {"cleared": len(to_clear), "not_cleared": len(to_revoke), "changed": changed, "unknown": unknown}
