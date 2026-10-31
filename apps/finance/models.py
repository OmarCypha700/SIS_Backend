"""
Financial clearance: the Accounts Office decides, semester by semester, which students may
register. The system does not handle fees or payments; it only records the decision.
"""
from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


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
