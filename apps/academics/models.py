from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from common.models import TimeStampedModel


class PeriodStatus(models.TextChoices):
    PLANNED = "PLANNED", "Planned"
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"


class AcademicYear(TimeStampedModel):
    name = models.CharField(max_length=40, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=PeriodStatus.choices, default=PeriodStatus.PLANNED)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError({"end_date": "End date must be after the start date."})


class Semester(TimeStampedModel):
    """An academic term. Institutions decide how many terms a year has."""

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="semesters")
    name = models.CharField(max_length=60)
    term_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    start_date = models.DateField()
    end_date = models.DateField()
    registration_start = models.DateTimeField()
    registration_end = models.DateTimeField()
    add_drop_deadline = models.DateTimeField(null=True, blank=True)
    result_entry_deadline = models.DateTimeField(null=True, blank=True)
    result_publication_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=PeriodStatus.choices, default=PeriodStatus.PLANNED)
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "term_number"], name="unique_term_per_year"),
            models.UniqueConstraint(fields=["is_current"], condition=Q(is_current=True),
                                    name="single_current_semester"),
        ]

    def __str__(self):
        return f"{self.academic_year.name} {self.name}"

    def clean(self):
        errors = {}
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            errors["end_date"] = "End date must be after the start date."
        if self.registration_start and self.registration_end and self.registration_start >= self.registration_end:
            errors["registration_end"] = "Registration must close after it opens."
        if self.add_drop_deadline and self.registration_start and self.add_drop_deadline < self.registration_start:
            errors["add_drop_deadline"] = "The add/drop deadline cannot be before registration opens."
        year = self.academic_year if self.academic_year_id else None
        if year and self.start_date and self.end_date:
            if self.start_date < year.start_date or self.end_date > year.end_date:
                errors["start_date"] = "Semester dates must fall within the academic year."
        if errors:
            raise ValidationError(errors)

    @property
    def amendment_deadline(self):
        return self.add_drop_deadline or self.registration_end

    def registration_is_open(self, at=None):
        at = at or timezone.now()
        return self.status == PeriodStatus.ACTIVE and self.registration_start <= at <= self.registration_end

    def amendments_are_open(self, at=None):
        at = at or timezone.now()
        return self.status == PeriodStatus.ACTIVE and self.registration_start <= at <= self.amendment_deadline

    def result_entry_is_open(self, at=None):
        at = at or timezone.now()
        return self.result_entry_deadline is None or at <= self.result_entry_deadline


class Department(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Level(TimeStampedModel):
    """Institution-defined level of study, e.g. Level 100 or Year 1."""

    name = models.CharField(max_length=40, unique=True)
    order = models.PositiveSmallIntegerField(unique=True, help_text="1 for the first level, 2 for the next, ...")

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.name


class AwardType(models.TextChoices):
    CERTIFICATE = "CERTIFICATE", "Certificate"
    DIPLOMA = "DIPLOMA", "Diploma"
    HND = "HND", "Higher National Diploma"
    BACHELOR = "BACHELOR", "Bachelor's Degree"
    POSTGRAD_DIPLOMA = "PGD", "Postgraduate Diploma"
    MASTER = "MASTER", "Master's Degree"
    DOCTORATE = "DOCTORATE", "Doctorate"
    OTHER = "OTHER", "Other"


class Programme(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="programmes")
    award_type = models.CharField(max_length=20, choices=AwardType.choices, default=AwardType.BACHELOR)
    duration_years = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1)])
    levels = models.ManyToManyField(Level, related_name="programmes", blank=True)
    required_credits = models.PositiveSmallIntegerField(null=True, blank=True)
    min_credits_per_semester = models.PositiveSmallIntegerField(null=True, blank=True)
    max_credits_per_semester = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        low, high = self.min_credits_per_semester, self.max_credits_per_semester
        if low is not None and high is not None and low > high:
            raise ValidationError({"max_credits_per_semester": "Maximum credits must be at least the minimum."})


class CourseType(models.TextChoices):
    THEORY = "THEORY", "Theory"
    PRACTICAL = "PRACTICAL", "Practical"
    CLINICAL = "CLINICAL", "Clinical"
    PROJECT = "PROJECT", "Project / Dissertation"
    SEMINAR = "SEMINAR", "Seminar"
    OTHER = "OTHER", "Other"


class Course(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    credits = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="courses")
    course_type = models.CharField(max_length=20, choices=CourseType.choices, default=CourseType.THEORY)
    prerequisites = models.ManyToManyField("self", symmetrical=False, related_name="required_for", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.title}"


class CourseCategory(models.TextChoices):
    COMPULSORY = "COMPULSORY", "Compulsory"
    ELECTIVE = "ELECTIVE", "Elective"


class ProgrammeCourse(TimeStampedModel):
    """Places a course in a programme's curriculum at a given level and term."""

    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name="curriculum")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="programme_links")
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="+")
    term_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    category = models.CharField(max_length=12, choices=CourseCategory.choices, default=CourseCategory.COMPULSORY)

    class Meta:
        ordering = ["programme", "level__order", "term_number", "course__code"]
        constraints = [models.UniqueConstraint(fields=["programme", "course"], name="unique_course_per_programme")]

    def __str__(self):
        return f"{self.programme.code}: {self.course.code} ({self.get_category_display()})"


class OfferingStatus(models.TextChoices):
    OPEN = "OPEN", "Open for registration"
    CLOSED = "CLOSED", "Closed"
    CANCELLED = "CANCELLED", "Cancelled"


class CourseOffering(TimeStampedModel):
    """A course delivered in a particular semester."""

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offerings")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="offerings")
    programmes = models.ManyToManyField(
        Programme, blank=True, related_name="offerings",
        help_text="Programmes whose students may register. Empty means any programme with the course in its curriculum.",
    )
    levels = models.ManyToManyField(Level, blank=True, related_name="offerings")
    lecturers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="LecturerAssignment", related_name="assigned_offerings", blank=True
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=OfferingStatus.choices, default=OfferingStatus.OPEN)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["course__code"]
        constraints = [models.UniqueConstraint(fields=["course", "semester"], name="unique_offering_per_semester")]

    def __str__(self):
        return f"{self.course.code} ({self.semester})"


class LecturerAssignment(TimeStampedModel):
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE, related_name="assignments")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lecturer_assignments")
    is_coordinator = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["offering", "lecturer"], name="unique_lecturer_per_offering")]

    def __str__(self):
        return f"{self.lecturer} -> {self.offering}"
