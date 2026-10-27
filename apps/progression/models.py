from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class Standing(models.TextChoices):
    GOOD = "GOOD", "Good standing"
    PROBATION = "PROBATION", "Probation"
    DISMISS = "DISMISS", "Dismissal"


class Placement(models.TextChoices):
    PROMOTE = "PROMOTE", "Promote to next level"
    REPEAT = "REPEAT", "Repeat level"
    STAY = "STAY", "No change"
    COMPLETE = "COMPLETE", "Final level completed"


class ReviewStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft – not applied"
    APPLIED = "APPLIED", "Applied"


class ProgressionReview(TimeStampedModel):
    """
    End-of-semester academic standing review.

    The system proposes an outcome for each registered student from the academic
    regulations; the registry reviews, overrides or excludes individual decisions and only
    then applies them. Nothing changes on a student record until the review is applied.
    """

    semester = models.OneToOneField("academics.Semester", on_delete=models.PROTECT, related_name="progression_review")
    status = models.CharField(max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT)
    is_final_term = models.BooleanField(default=False, help_text="Promotion is only considered in the last term of the year.")
    regulation_snapshot = models.JSONField(default=dict, help_text="The regulations used to compute the proposals.")
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-semester__start_date"]

    def __str__(self):
        return f"Progression review {self.semester}"


class ProgressionDecision(TimeStampedModel):
    review = models.ForeignKey(ProgressionReview, on_delete=models.CASCADE, related_name="decisions")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="progression_decisions")
    programme = models.ForeignKey("academics.Programme", on_delete=models.PROTECT, related_name="+")
    level = models.ForeignKey("academics.Level", on_delete=models.PROTECT, related_name="+")
    semester_gpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    cgpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    credits_earned_year = models.PositiveSmallIntegerField(default=0)
    outstanding_failed_credits = models.PositiveSmallIntegerField(default=0)
    results_complete = models.BooleanField(default=True)
    proposed_standing = models.CharField(max_length=10, choices=Standing.choices)
    proposed_placement = models.CharField(max_length=10, choices=Placement.choices)
    reasons = models.JSONField(default=list)
    final_standing = models.CharField(max_length=10, choices=Standing.choices)
    final_placement = models.CharField(max_length=10, choices=Placement.choices)
    override_reason = models.TextField(blank=True)
    overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    excluded = models.BooleanField(default=False, help_text="Excluded decisions are not applied.")
    applied = models.BooleanField(default=False)

    class Meta:
        ordering = ["student__last_name", "student__first_name"]
        constraints = [models.UniqueConstraint(fields=["review", "student"], name="one_decision_per_student_per_review")]

    def __str__(self):
        return f"{self.student.student_number}: {self.final_standing}/{self.final_placement}"

    @property
    def is_overridden(self):
        return (self.final_standing, self.final_placement) != (self.proposed_standing, self.proposed_placement)
