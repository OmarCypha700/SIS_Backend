"""
Late registration penalty.

A student who has not registered by the time the registration window closes may still register
during the semester's late registration period, but only after the late registration penalty (set
in Institution settings) has been added to their account and paid in full. Students who never
register late are never charged.

* The penalty is charged once per student and semester, when the student (or registry staff on
  their behalf) asks to register late.
* It is paid like any other fee; payments pay it before this semester's share of the fees.
* A waiver (e.g. medical grounds) counts as paid. Reversing the penalty also removes it, without
  charging it again.
* Changes to an existing registration during add/drop are not late registration.
"""
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from common.exceptions import DomainError

from . import accounts
from .models import EntryKind, LedgerEntry


def _has_registration(student, semester):
    from apps.registration.models import RegistrationStatus, SemesterRegistration

    return SemesterRegistration.objects.filter(student=student, semester=semester).exclude(
        status__in=[RegistrationStatus.REJECTED, RegistrationStatus.CANCELLED]).exists()


def state(student, semester, profile=None, at=None):
    """The penalty as it applies to this student now, or None when it does not apply."""
    profile = profile or InstitutionProfile.load()
    at = at or timezone.now()
    amount = profile.late_registration_penalty
    if not amount or amount <= 0 or at <= semester.registration_end or _has_registration(student, semester):
        return None
    charged = LedgerEntry.objects.filter(student=student, semester=semester, kind=EntryKind.PENALTY).exists()
    needed = accounts.requirement(student, semester, profile)
    outstanding = needed["penalty_outstanding"] if charged else accounts.money(amount)
    closes = semester.late_registration_end
    return {
        "amount": accounts.money(amount),
        "charged": charged,
        "outstanding": outstanding,
        "late_registration_open": semester.late_registration_is_open(at),
        "closes": closes,
        "message": _message(semester, amount, charged, outstanding),
    }


def _message(semester, amount, charged, outstanding):
    closed = timezone.localtime(semester.registration_end).strftime("%d %b %Y")
    if not charged:
        return (f"Registration closed on {closed}. A late registration penalty of GHS {amount:,.2f} applies: add it "
                f"to your account and pay it to register.")
    if outstanding > 0:
        return f"Pay the late registration penalty (GHS {outstanding:,.2f} outstanding) to register."
    return ""


def block(student, semester, profile=None):
    """The message shown when the penalty stops the student registering, else None."""
    current = state(student, semester, profile)
    if current is None or (current["charged"] and current["outstanding"] <= 0):
        return None
    return current["message"]


@transaction.atomic
def charge(student, semester, *, actor):
    """Add the penalty to the student's account (once). Returns the entry, new or existing."""
    profile = InstitutionProfile.load()
    current = state(student, semester, profile)
    if current is None:
        raise DomainError("No late registration penalty applies.", code="no_penalty")
    existing = LedgerEntry.objects.filter(student=student, semester=semester, kind=EntryKind.PENALTY).first()
    if existing is not None:
        return existing
    entry = LedgerEntry.objects.create(
        student=student, kind=EntryKind.PENALTY, amount=current["amount"], academic_year=semester.academic_year,
        semester=semester, created_by=actor, description=f"Late registration penalty, {semester}")
    log_action("finance.late_penalty", actor=actor, instance=entry,
               summary=f"Late registration penalty of GHS {current['amount']} for {student.student_number}, {semester}")
    return entry
