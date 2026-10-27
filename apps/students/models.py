from django.conf import settings
from django.db import models

from common.models import TimeStampedModel
from common.validators import image_extension_validator, validate_image_size


class StudentStatus(models.TextChoices):
    APPLICANT = "APPLICANT", "Applicant"
    ACTIVE = "ACTIVE", "Active"
    DEFERRED = "DEFERRED", "Deferred"
    SUSPENDED = "SUSPENDED", "Suspended"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    COMPLETED = "COMPLETED", "Completed"
    GRADUATED = "GRADUATED", "Graduated"
    DISMISSED = "DISMISSED", "Dismissed"


class Gender(models.TextChoices):
    FEMALE = "F", "Female"
    MALE = "M", "Male"
    OTHER = "O", "Other"
    UNDISCLOSED = "U", "Prefer not to say"


class StatusPolicy(models.Model):
    """What a student in a given status may do. Editable per institution."""

    status = models.CharField(max_length=20, choices=StudentStatus.choices, unique=True)
    can_register = models.BooleanField(default=False)
    can_view_results = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "status policies"

    def __str__(self):
        return self.status

    @classmethod
    def for_status(cls, status):
        policy = cls.objects.filter(status=status).first()
        if policy is None:
            # Fall back to the documented defaults if the table was not seeded.
            return cls(status=status, **DEFAULT_STATUS_POLICIES.get(status, {}))
        return policy


DEFAULT_STATUS_POLICIES = {
    StudentStatus.APPLICANT: {"can_register": False, "can_view_results": False},
    StudentStatus.ACTIVE: {"can_register": True, "can_view_results": True},
    StudentStatus.DEFERRED: {"can_register": False, "can_view_results": True},
    StudentStatus.SUSPENDED: {"can_register": False, "can_view_results": False},
    StudentStatus.WITHDRAWN: {"can_register": False, "can_view_results": True},
    StudentStatus.COMPLETED: {"can_register": False, "can_view_results": True},
    StudentStatus.GRADUATED: {"can_register": False, "can_view_results": True},
    StudentStatus.DISMISSED: {"can_register": False, "can_view_results": True},
}


class AcademicStanding(models.TextChoices):
    GOOD = "GOOD", "Good standing"
    PROBATION = "PROBATION", "On probation"


class Student(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="student_profile")
    student_number = models.CharField(max_length=40, unique=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True)
    residential_address = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)
    emergency_contact_relationship = models.CharField(max_length=60, blank=True)
    admission_date = models.DateField(null=True, blank=True)
    admission_year = models.PositiveSmallIntegerField()
    entry_academic_year = models.ForeignKey(
        "academics.AcademicYear", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    programme = models.ForeignKey("academics.Programme", on_delete=models.PROTECT, related_name="students")
    current_level = models.ForeignKey("academics.Level", on_delete=models.PROTECT, related_name="students")
    status = models.CharField(max_length=20, choices=StudentStatus.choices, default=StudentStatus.ACTIVE)
    academic_standing = models.CharField(max_length=12, choices=AcademicStanding.choices, default=AcademicStanding.GOOD)
    photo = models.ImageField(
        upload_to="students/photos/", blank=True, validators=[image_extension_validator, validate_image_size]
    )
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["programme", "current_level"]),
        ]

    def __str__(self):
        return f"{self.student_number} {self.full_name}"

    @property
    def full_name(self):
        return " ".join(part for part in [self.first_name, self.middle_name, self.last_name] if part)


class StudentProgrammeHistory(models.Model):
    """Every programme/level/status placement change, kept permanently."""

    class ChangeType(models.TextChoices):
        ADMISSION = "ADMISSION", "Admission"
        PROGRAMME = "PROGRAMME", "Programme change"
        LEVEL = "LEVEL", "Level change"
        STATUS = "STATUS", "Status change"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="history")
    change_type = models.CharField(max_length=12, choices=ChangeType.choices)
    from_programme = models.ForeignKey("academics.Programme", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_programme = models.ForeignKey("academics.Programme", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    from_level = models.ForeignKey("academics.Level", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_level = models.ForeignKey("academics.Level", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    reason = models.TextField(blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "student programme history"
