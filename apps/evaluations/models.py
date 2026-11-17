"""
Tutor assessment: students assess each course's tutors before they see their result for it.

Anonymity is built into the tables:
* AssessmentCompletion records only THAT a student assessed a tutor (so their result can be shown);
* AssessmentResponse and AssessmentComment hold WHAT was said, with no link to any student, random
  UUID keys (no sequence that lines up with completions) and dates, never times.
Both are written in one transaction, so each student answers once per tutor, yet nothing joins them.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel


class QuestionKind(models.TextChoices):
    RATING = "RATING", "Rating (1 to 5)"
    COMMENT = "COMMENT", "Comment"


class Questionnaire(TimeStampedModel):
    """A set of questions. One is active at a time; once used by an assessment it can only be copied."""

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["-is_active", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["is_active"], condition=Q(is_active=True),
                                               name="one_active_questionnaire")]

    def __str__(self):
        return self.name

    @property
    def is_used(self):
        return CourseAssessment.objects.filter(questionnaire=self).exists()


class Question(models.Model):
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(max_length=300)
    label = models.CharField(max_length=40, blank=True, help_text="Short name used in reports, e.g. Clarity.")
    kind = models.CharField(max_length=10, choices=QuestionKind.choices, default=QuestionKind.RATING)
    required = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text


class CourseAssessment(models.Model):
    """The assessment of one course offering's tutors, opened when its results are first published."""

    offering = models.OneToOneField("academics.CourseOffering", on_delete=models.PROTECT, related_name="assessment")
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.PROTECT, related_name="+")
    # The questions as they were when it opened: [{"key", "text", "label", "kind", "required"}].
    questions = models.JSONField()
    lecturers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+")
    opens_on = models.DateField()
    closes_on = models.DateField(help_text="Last day students can assess; results show for everyone after it.")
    reminder_sent = models.BooleanField(default=False)
    feedback_notified = models.BooleanField(default=False)

    class Meta:
        ordering = ["-opens_on", "-id"]

    def __str__(self):
        return f"Assessment of {self.offering}"


class AssessmentCompletion(models.Model):
    """That a student assessed a tutor. Says nothing about what they answered."""

    assessment = models.ForeignKey(CourseAssessment, on_delete=models.CASCADE, related_name="completions")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="+")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    completed_on = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assessment", "student", "lecturer"],
                                               name="one_assessment_per_student_tutor")]


class AssessmentResponse(models.Model):
    """One student's ratings of one tutor. No student, no time of day, a random key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(CourseAssessment, on_delete=models.CASCADE, related_name="responses")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    ratings = models.JSONField(help_text="Question key: rating 1 to 5.")
    uniform = models.BooleanField(default=False, help_text="Every rating was the same.")
    submitted_on = models.DateField()


class CommentStatus(models.TextChoices):
    PENDING = "PENDING", "Waiting for review"
    APPROVED = "APPROVED", "Approved"
    HIDDEN = "HIDDEN", "Hidden"


class AssessmentComment(models.Model):
    """A written answer. Tutors see it only after quality assurance approves it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(CourseAssessment, on_delete=models.CASCADE, related_name="comments")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    question_key = models.CharField(max_length=20)
    text = models.TextField(max_length=2000)
    status = models.CharField(max_length=10, choices=CommentStatus.choices, default=CommentStatus.PENDING,
                              db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_on = models.DateField(null=True, blank=True)
