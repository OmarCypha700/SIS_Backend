from django.conf import settings
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel


class RegistrationStatus(models.TextChoices):
    PENDING = "PENDING", "Awaiting approval"
    REGISTERED = "REGISTERED", "Registered"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


class SemesterRegistration(TimeStampedModel):
    """A student's registration for one semester; the courses hang off it."""

    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="registrations")
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="registrations")
    # Programme and level at the time of registration, kept for historical reporting.
    programme = models.ForeignKey("academics.Programme", on_delete=models.PROTECT, related_name="+")
    level = models.ForeignKey("academics.Level", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=12, choices=RegistrationStatus.choices, default=RegistrationStatus.REGISTERED)
    total_credits = models.PositiveSmallIntegerField(default=0)
    requires_approval_reason = models.CharField(max_length=255, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-semester__start_date", "student__last_name"]
        constraints = [
            models.UniqueConstraint(fields=["student", "semester"], name="one_registration_per_semester")
        ]
        indexes = [models.Index(fields=["semester", "status"])]

    def __str__(self):
        return f"{self.student.student_number} {self.semester}"


class CourseRegistrationStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Registered"
    DROPPED = "DROPPED", "Dropped"


class CourseRegistration(TimeStampedModel):
    semester_registration = models.ForeignKey(SemesterRegistration, on_delete=models.CASCADE, related_name="courses")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="course_registrations")
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT, related_name="registrations")
    status = models.CharField(max_length=10, choices=CourseRegistrationStatus.choices, default=CourseRegistrationStatus.ACTIVE)
    is_retake = models.BooleanField(default=False)
    category = models.CharField(max_length=12, blank=True, help_text="Compulsory/elective at the time of registration.")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    dropped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["offering__course__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "offering"], condition=Q(status="ACTIVE"), name="no_duplicate_active_course_registration"
            )
        ]
        indexes = [models.Index(fields=["offering", "status"])]

    def __str__(self):
        return f"{self.student.student_number} {self.offering.course.code} ({self.status})"


class RegistrationEvent(models.Model):
    class Action(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Registration submitted"
        COURSE_ADDED = "COURSE_ADDED", "Course added"
        COURSE_DROPPED = "COURSE_DROPPED", "Course dropped"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    semester_registration = models.ForeignKey(SemesterRegistration, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=20, choices=Action.choices)
    offering = models.ForeignKey("academics.CourseOffering", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    on_behalf = models.BooleanField(default=False)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
