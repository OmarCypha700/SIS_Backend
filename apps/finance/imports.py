"""
Importing a bank statement in two steps: preview, then confirm.

Each row is classified before anything is saved:

* new                - will be recorded;
* possible_duplicate - the student already has a payment of the same amount within a few days under
                       a different reference (e.g. recorded by hand from the slip, or paid online).
                       Recorded only if the Accounts Office ticks it;
* recorded           - this reference is already recorded (skipped);
* repeated           - the same reference appears earlier in the file (skipped);
* unknown            - no student has this number (skipped);
* invalid            - the amount, date or reference is missing or wrong (skipped).

Confirming checks every row again, so a stale or edited preview cannot record a duplicate.
"""
import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.students.models import Student
from common.exceptions import DomainError

from . import accounts
from .models import Payment, PaymentStatus

DUPLICATE_WINDOW = datetime.timedelta(days=3)
RECORDABLE = ("new", "possible_duplicate")


def _text(record, key):
    value = record.get(key, "")
    return "" if value is None else str(value).strip()


def _classify(records):
    numbers = [_text(r, "student_number") for r in records]
    students = {s.student_number.lower(): s for s in Student.objects.filter(student_number__in=[n for n in numbers if n])}
    references = [_text(r, "reference") for r in records]
    existing = set(Payment.objects.filter(reference__in=[r for r in references if r]).values_list("reference", flat=True))
    seen, rows = set(), []
    for index, record in enumerate(records, start=2):
        number, reference = _text(record, "student_number"), _text(record, "reference")
        row = {"row": record.get("row") or index, "student_number": number, "reference": reference, "amount": _text(record, "amount"),
               "date": _text(record, "date")[:10], "method": (_text(record, "method") or "bank").lower()[:30],
               "note": _text(record, "note")[:255], "student": None, "full_name": "", "status": "new", "message": ""}
        rows.append(row)
        student = students.get(number.lower())
        if student is not None:
            row["student"], row["full_name"] = student.pk, student.full_name
        try:
            amount = accounts._amount(row["amount"].replace(",", ""))
            row["amount"] = str(amount)
        except DomainError as exc:
            row["status"], row["message"] = "invalid", exc.message
            continue
        paid_on = parse_date(row["date"]) if row["date"] else None
        if row["date"] and paid_on is None:
            row["status"], row["message"] = "invalid", "Use the date format YYYY-MM-DD."
        elif not reference:
            row["status"], row["message"] = "invalid", "The reference (bank slip or transaction number) is missing."
        elif reference in existing:
            row["status"], row["message"] = "recorded", "This reference is already recorded."
        elif reference in seen:
            row["status"], row["message"] = "repeated", "The same reference appears earlier in this file."
        elif student is None:
            row["status"], row["message"] = "unknown", "No student has this number."
        else:
            match = _same_payment(student, amount, paid_on or timezone.localdate(), reference)
            if match is not None:
                when = timezone.localtime(match.paid_at).strftime("%d %b %Y") if match.paid_at else "recently"
                how = "online" if match.source == "ONLINE" else f"under {match.reference}"
                row["status"] = "possible_duplicate"
                row["message"] = f"GHS {amount:,.2f} was already recorded {how} on {when}. Tick it only if this is a second payment."
        if reference:
            seen.add(reference)
    return rows


def _same_payment(student, amount, day, reference):
    return (Payment.objects.filter(student=student, status=PaymentStatus.SUCCESS, amount=amount,
                                   paid_at__date__gte=day - DUPLICATE_WINDOW, paid_at__date__lte=day + DUPLICATE_WINDOW)
            .exclude(reference=reference).order_by("-paid_at").first())


def preview(records):
    rows = _classify(records)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"rows": rows, "counts": counts}


def confirm(records, *, actor):
    """Record the chosen rows. `records` are the rows the Accounts Office ticked in the preview."""
    recorded, skipped, touched = [], [], {}
    for row in _classify(records):
        if row["status"] not in RECORDABLE:
            skipped.append({"row": row["row"], "reference": row["reference"], "reason": row["message"]})
            continue
        try:
            with transaction.atomic():
                student = Student.objects.get(pk=row["student"])
                payment = accounts.record_manual_payment(
                    student, amount=row["amount"], channel=row["method"], reference=row["reference"], note=row["note"],
                    paid_on=parse_date(row["date"]) if row["date"] else None, actor=actor)
            recorded.append(payment.receipt_number)
            touched[student.pk] = student
        except DomainError as exc:
            skipped.append({"row": row["row"], "reference": row["reference"], "reason": exc.message})
    in_credit = []
    for student in touched.values():
        balance = accounts.balance(student)
        if balance < 0:
            in_credit.append({"student": student.pk, "student_number": student.student_number,
                              "full_name": student.full_name, "credit": -balance})
    return {"recorded": len(recorded), "receipts": recorded, "skipped": skipped, "in_credit": in_credit}
