"""
Fees and financial clearance.

* Fee schedules set what each programme and level is billed for a semester or an academic year.
* Each student has an append-only ledger: bills (charges and adjustments) and settlements
  (payments, waivers and refunds). Mistakes are corrected with a reversing entry, never by editing.
* Payments are made online through Paystack or recorded by the Accounts Office. Only a payment
  confirmed with Paystack's server is credited.
* Financial clearance records, per semester, whether a student may register. It is set
  automatically once enough is paid, or by the Accounts Office.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel

MONEY = {"max_digits": 12, "decimal_places": 2}


class FinancialClearance(TimeStampedModel):
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="clearances")
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="clearances")
    cleared = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True, help_text="e.g. receipt number, payment plan, sponsor letter.")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["student__last_name", "student__first_name"]
        constraints = [models.UniqueConstraint(fields=["student", "semester"], name="one_clearance_per_student_semester")]
        indexes = [models.Index(fields=["semester", "cleared"])]

    def __str__(self):
        return f"{self.student.student_number} {self.semester}: {'cleared' if self.cleared else 'not cleared'}"


class FeeSchedule(TimeStampedModel):
    """What one programme and level is billed for a semester, or for a whole academic year (semester blank)."""

    programme = models.ForeignKey("academics.Programme", on_delete=models.PROTECT, related_name="fee_schedules")
    level = models.ForeignKey("academics.Level", on_delete=models.PROTECT, related_name="fee_schedules")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="fee_schedules")
    semester = models.ForeignKey("academics.Semester", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="fee_schedules")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-academic_year__start_date", "semester__term_number", "programme__code", "level__order"]
        constraints = [
            models.UniqueConstraint(fields=["programme", "level", "academic_year", "semester"],
                                    name="one_fee_schedule_per_period"),
            models.UniqueConstraint(fields=["programme", "level", "academic_year"], condition=Q(semester__isnull=True),
                                    name="one_yearly_fee_schedule"),
        ]

    def __str__(self):
        return f"{self.programme.code} {self.level.name} {self.period_label}"

    @property
    def period_label(self):
        return f"{self.academic_year} · {self.semester.name}" if self.semester_id else f"{self.academic_year} (whole year)"

    @property
    def total(self):
        return sum((item.amount for item in self.items.all()), Decimal("0"))


class FeeItem(models.Model):
    schedule = models.ForeignKey(FeeSchedule, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=120)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.description}: {self.amount}"


class EntryKind(models.TextChoices):
    CHARGE = "CHARGE", "Fee charged"
    ADJUSTMENT = "ADJUSTMENT", "Bill adjustment"
    PAYMENT = "PAYMENT", "Payment"
    WAIVER = "WAIVER", "Waiver or scholarship"
    REFUND = "REFUND", "Refund"
    PENALTY = "PENALTY", "Late registration penalty"
    REVERSAL = "REVERSAL", "Reversal"


class EntrySide(models.TextChoices):
    BILL = "BILL", "Bill"  # what the student was billed for a period
    SETTLEMENT = "SETTLEMENT", "Settlement"  # what was paid, waived or refunded


BILL_KINDS = (EntryKind.CHARGE, EntryKind.ADJUSTMENT, EntryKind.PENALTY)


class LedgerEntry(models.Model):
    """
    One line on a student's account. `amount` is signed: positive means the student owes more,
    negative is a credit. Entries are never changed or deleted; `reverses` corrects a mistake.
    """

    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="ledger_entries")
    kind = models.CharField(max_length=12, choices=EntryKind.choices)
    side = models.CharField(max_length=10, choices=EntrySide.choices, editable=False)
    amount = models.DecimalField(**MONEY)
    description = models.CharField(max_length=200)
    # The billing period, for bills: an academic year, optionally narrowed to one semester.
    academic_year = models.ForeignKey("academics.AcademicYear", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")
    semester = models.ForeignKey("academics.Semester", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    fee_item = models.ForeignKey(FeeItem, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    payment = models.ForeignKey("Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "ledger entries"
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="ledger_amount_not_zero"),
            models.UniqueConstraint(fields=["student", "fee_item"], condition=Q(kind="CHARGE"),
                                    name="one_charge_per_fee_item"),
        ]
        indexes = [models.Index(fields=["student", "side"])]

    def __str__(self):
        return f"{self.student_id} {self.kind} {self.amount}"

    def save(self, *args, **kwargs):
        if not self.side:
            if self.kind == EntryKind.REVERSAL:
                self.side = self.reverses.side
            else:
                self.side = EntrySide.BILL if self.kind in BILL_KINDS else EntrySide.SETTLEMENT
        super().save(*args, **kwargs)


class PaymentSource(models.TextChoices):
    ONLINE = "ONLINE", "Online payment"
    MANUAL = "MANUAL", "Recorded by the Accounts Office"


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SUCCESS = "SUCCESS", "Successful"
    FAILED = "FAILED", "Failed"
    ABANDONED = "ABANDONED", "Not completed"
    MISMATCH = "MISMATCH", "Needs attention"
    REVERSED = "REVERSED", "Reversed"


class Payment(TimeStampedModel):
    """
    One payment attempt. `amount` is what goes to school fees; `fee` is the transaction fee the
    student paid on top; `total` is what was charged. Only SUCCESS payments count as paid; a
    REVERSED payment was credited and then taken off the account because it was recorded in error.
    """

    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="payments")
    reference = models.CharField(max_length=64, unique=True)
    source = models.CharField(max_length=10, choices=PaymentSource.choices)
    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    fee = models.DecimalField(**MONEY, default=Decimal("0"))
    total = models.DecimalField(**MONEY)
    channel = models.CharField(max_length=30, blank=True, help_text="mobile_money, card, bank, bank_transfer or cash.")
    paid_at = models.DateTimeField(null=True, blank=True)
    receipt_number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    gateway_message = models.CharField(max_length=255, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=255, blank=True, help_text="e.g. bank slip or teller number.")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"]), models.Index(fields=["student", "status"])]

    def __str__(self):
        return f"{self.reference} {self.status} {self.total}"
