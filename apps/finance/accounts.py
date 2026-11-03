"""
Student accounts: balances, the registration rule, and the Accounts Office's manual entries.

The registration rule (see docs/payments.md):

    needed = everything billed for earlier periods
           + registration_payment_percent % of the current period's bill
    the student meets it when payments, waivers and refunds (settled) >= needed

Payments are applied to the oldest bill first, so earlier balances are always paid in full before
any of it counts towards the current bill. A period is the semester, or the academic year when
fees are billed per year.
"""
import datetime
from decimal import ROUND_CEILING, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.academics.models import Semester
from apps.audit.services import log_action
from apps.institutions.models import FeeBillingPeriod, InstitutionProfile, StudentNumberSequence
from apps.institutions.services import render_format
from common.exceptions import DomainError

from .models import EntryKind, EntrySide, FinancialClearance, LedgerEntry, Payment, PaymentSource, PaymentStatus

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
RECEIPT_FORMAT = "{SHORT}/RCP/{YEAR}/{SEQ:06}"


def money(value):
    return Decimal(value).quantize(CENT)


def ceil_cents(value):
    return (Decimal(value) * 100).to_integral_value(rounding=ROUND_CEILING) / 100


def current_semester():
    return Semester.objects.select_related("academic_year").filter(is_current=True).first()


def balance(student):
    """What the student owes now (negative: in credit)."""
    return money(LedgerEntry.objects.filter(student=student).aggregate(s=Sum("amount"))["s"] or ZERO)


def settled(student):
    """Payments and waivers, less refunds."""
    total = LedgerEntry.objects.filter(student=student, side=EntrySide.SETTLEMENT).aggregate(s=Sum("amount"))["s"]
    return money(-(total or ZERO))


def _bucket(year_start, term, semester, per_year):
    """Where a bill's period falls relative to `semester`: 'earlier', 'current' or 'later'."""
    current_start = semester.academic_year.start_date
    if year_start < current_start:
        return "earlier"
    if year_start > current_start:
        return "later"
    if per_year or term is None:
        return "current"
    if term < semester.term_number:
        return "earlier"
    return "current" if term == semester.term_number else "later"


def requirement(student, semester, profile=None):
    """How much the student must have paid to register for `semester`, and whether they have."""
    profile = profile or InstitutionProfile.load()
    per_year = profile.fee_billing_period == FeeBillingPeriod.ACADEMIC_YEAR
    buckets = {"earlier": ZERO, "current": ZERO, "later": ZERO}
    rows = (LedgerEntry.objects.filter(student=student, side=EntrySide.BILL)
            .values("academic_year__start_date", "semester__term_number").annotate(total=Sum("amount")))
    for row in rows:
        if row["academic_year__start_date"] is None:
            buckets["earlier"] += row["total"]
            continue
        buckets[_bucket(row["academic_year__start_date"], row["semester__term_number"], semester, per_year)] += row["total"]
    earlier, current = max(money(buckets["earlier"]), ZERO), max(money(buckets["current"]), ZERO)
    percent = profile.registration_payment_percent
    needed = earlier + ceil_cents(current * percent / 100)
    paid = settled(student)
    towards_current = max(paid - earlier, ZERO)
    return {
        "billed": current > 0,
        "percent_required": percent,
        "earlier_balance": max(earlier - paid, ZERO),
        "current_bill": current,
        "paid": paid,
        "needed": needed,
        "short_by": max(needed - paid, ZERO),
        "percent_paid": int(min(towards_current / current * 100, Decimal(100))) if current else None,
        "meets": current > 0 and paid >= needed,
        "balance": balance(student),
    }


def _target_semesters(semester, profile):
    """Semesters a payment clears: the current one, and later ones of its year when billing per year."""
    if profile.fee_billing_period != FeeBillingPeriod.ACADEMIC_YEAR:
        return [semester]
    return list(Semester.objects.filter(academic_year=semester.academic_year, term_number__gte=semester.term_number))


def refresh_clearance(students):
    """
    Clear students for the current semester once they meet the registration rule. Never revokes,
    and never overrides a clearance a person revoked (with a reason) for the semester.
    Returns the students newly cleared.
    """
    from . import services

    semester = current_semester()
    if semester is None:
        return []
    profile = InstitutionProfile.load()
    targets = _target_semesters(semester, profile)
    newly = []
    for student in students:
        result = requirement(student, semester, profile)
        if not result["meets"]:
            continue
        existing = {c.semester_id: c for c in FinancialClearance.objects.filter(student=student, semester__in=targets)}
        for target in targets:
            clearance = existing.get(target.pk)
            if clearance is not None and (clearance.cleared or clearance.decided_by_id is not None):
                continue
            note = f"Automatic: {profile.registration_payment_percent}% of fees paid"
            if services.set_clearance([student], target, cleared=True, note=note, actor=None, notify=target == semester):
                if target == semester:
                    newly.append(student)
    return newly


def next_receipt_number():
    profile = InstitutionProfile.load()
    year = timezone.localdate().year
    sequence, _ = StudentNumberSequence.objects.select_for_update().get_or_create(key=f"receipt:{year}")
    while True:
        sequence.last_value += 1
        candidate = render_format(RECEIPT_FORMAT, short=profile.short_name, year=year, seq=sequence.last_value)
        if not Payment.objects.filter(receipt_number=candidate).exists():
            break
    sequence.save(update_fields=["last_value"])
    return candidate


def _period(semester):
    semester = semester or current_semester()
    if semester is None:
        raise DomainError("Choose the semester this applies to; no current semester is set.", code="semester_required")
    profile = InstitutionProfile.load()
    per_year = profile.fee_billing_period == FeeBillingPeriod.ACADEMIC_YEAR
    return semester.academic_year, (None if per_year else semester)


def _reason(reason):
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("Give a reason; it is kept in the audit log.", code="reason_required")
    return reason[:200]


def _amount(value):
    try:
        amount = money(value)
    except Exception:  # noqa: BLE001 - any unparseable input
        raise DomainError("Enter an amount in cedis, e.g. 1500.00.", code="invalid_amount") from None
    if amount <= 0:
        raise DomainError("The amount must be more than zero.", code="invalid_amount")
    return amount


@transaction.atomic
def record_manual_payment(student, *, amount, channel="bank", reference="", note="", paid_on=None, actor):
    """A payment made at the bank or cashier, entered by the Accounts Office."""
    amount = _amount(amount)
    reference = (reference or "").strip()
    if not reference:
        # Without the bank's own number, the same deposit could be credited again from a bank statement.
        raise DomainError("Enter the bank slip, teller or receipt number.", code="reference_required")
    if Payment.objects.filter(reference=reference).exists():
        raise DomainError(f"A payment with reference {reference} is already recorded.", code="duplicate_reference",
                          status_code=409)
    paid_at = timezone.now()
    if paid_on:
        paid_at = timezone.make_aware(datetime.datetime.combine(paid_on, datetime.time(12)))
    payment = Payment.objects.create(
        student=student, reference=reference[:64], source=PaymentSource.MANUAL,
        status=PaymentStatus.SUCCESS, amount=amount, fee=ZERO, total=amount, channel=(channel or "bank")[:30],
        paid_at=paid_at, receipt_number=next_receipt_number(), note=(note or "").strip()[:255], recorded_by=actor,
    )
    LedgerEntry.objects.create(student=student, kind=EntryKind.PAYMENT, amount=-amount, payment=payment,
                               description=f"Payment {payment.receipt_number}", created_by=actor)
    log_action("finance.payment_recorded", actor=actor, instance=payment,
               summary=f"Recorded GHS {amount} for {student.student_number} ({payment.receipt_number})",
               metadata={"reference": payment.reference, "channel": payment.channel})
    _after_settlement(student, payment)
    return payment


def credit_payment(payment, *, actor=None):
    """Put a confirmed online payment on the ledger (called once, by the gateway confirmation)."""
    LedgerEntry.objects.create(student=payment.student, kind=EntryKind.PAYMENT, amount=-payment.amount, payment=payment,
                               description=f"Online payment {payment.receipt_number}", created_by=actor)
    _after_settlement(payment.student, payment)


def _after_settlement(student, payment):
    refresh_clearance([student])
    if payment is not None:
        from apps.notifications import events

        transaction.on_commit(lambda: events.payment_received(payment))


@transaction.atomic
def add_entry(student, *, kind, amount, reason, semester=None, actor):
    """
    WAIVER (scholarship or exemption: reduces what is owed), REFUND (money returned: the student owes
    it again) or ADJUSTMENT (a correction to the bill; a negative amount lowers it).
    """
    reason = _reason(reason)
    if kind == EntryKind.ADJUSTMENT:
        try:
            signed = money(amount)
        except Exception:  # noqa: BLE001
            raise DomainError("Enter an amount in cedis, e.g. -200.00.", code="invalid_amount") from None
        if signed == 0:
            raise DomainError("The amount cannot be zero.", code="invalid_amount")
    elif kind == EntryKind.WAIVER:
        signed = -_amount(amount)
    elif kind == EntryKind.REFUND:
        signed = _amount(amount)
        if signed > settled(student):
            raise DomainError("A refund cannot be more than the student has paid.", code="refund_too_large")
    else:
        raise DomainError("Choose a waiver, refund or adjustment.", code="invalid_kind")
    year, sem = _period(semester) if kind == EntryKind.ADJUSTMENT else (None, None)
    entry = LedgerEntry.objects.create(student=student, kind=kind, amount=signed, description=reason,
                                       academic_year=year, semester=sem, created_by=actor)
    log_action(f"finance.{kind.lower()}", actor=actor, instance=entry, reason=reason,
               summary=f"{EntryKind(kind).label} of GHS {abs(signed)} for {student.student_number}")
    refresh_clearance([student])
    return entry


@transaction.atomic
def reverse_entry(entry, *, reason, actor):
    reason = _reason(reason)
    entry = LedgerEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.kind == EntryKind.REVERSAL:
        raise DomainError("A reversal cannot itself be reversed. Add a new entry instead.", code="not_reversible")
    if LedgerEntry.objects.filter(reverses=entry).exists():
        raise DomainError("This entry has already been reversed.", code="already_reversed", status_code=409)
    reversal = LedgerEntry.objects.create(
        student=entry.student, kind=EntryKind.REVERSAL, amount=-entry.amount, reverses=entry,
        description=f"Reversal: {reason}", academic_year=entry.academic_year, semester=entry.semester,
        fee_item=entry.fee_item, payment=entry.payment, created_by=actor)
    log_action("finance.reversed", actor=actor, instance=reversal, reason=reason,
               summary=f"Reversed {entry.get_kind_display().lower()} of GHS {abs(entry.amount)} for "
                       f"{entry.student.student_number}")
    refresh_clearance([entry.student])
    return reversal


def statement(student):
    """Ledger lines, oldest first, with the running balance."""
    running = ZERO
    lines = []
    entries = (LedgerEntry.objects.filter(student=student)
               .select_related("academic_year", "semester", "payment", "created_by")
               .prefetch_related("reversed_by"))
    for entry in entries:
        running += entry.amount
        lines.append({
            "id": entry.id, "date": entry.created_at, "kind": entry.kind, "kind_display": entry.get_kind_display(),
            "description": entry.description, "amount": entry.amount, "balance": running,
            "period": (str(entry.semester) if entry.semester_id else
                       (f"{entry.academic_year} (year)" if entry.academic_year_id else "")),
            "receipt_number": entry.payment.receipt_number if entry.payment_id else None,
            "payment_id": entry.payment_id,
            "reversed": hasattr(entry, "reversed_by"),
            "is_reversal": entry.kind == EntryKind.REVERSAL,
            "recorded_by": entry.created_by.get_full_name() if entry.created_by_id else "",
        })
    return lines
