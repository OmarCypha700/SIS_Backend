from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from common.models import TimeStampedModel
from common.validators import image_extension_validator, validate_image_size

hex_colour = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a hex colour such as #1d4ed8.")


class CgpaRetakePolicy(models.TextChoices):
    LATEST = "LATEST", "Latest attempt counts"
    BEST = "BEST", "Best attempt counts"
    ALL = "ALL", "All attempts count"


class InstitutionProfile(TimeStampedModel):
    """
    Single-row configuration for this installation: identity, branding and academic policy.

    Each institution runs its own deployment and database, so there is exactly one row.
    """

    name = models.CharField(max_length=200, default="Your Institution")
    short_name = models.CharField(max_length=20, default="SIS")
    motto = models.CharField(max_length=200, blank=True)
    logo = models.ImageField(
        upload_to="branding/", blank=True, validators=[image_extension_validator, validate_image_size]
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    website = models.URLField(blank=True)
    physical_address = models.TextField(blank=True)
    postal_address = models.TextField(blank=True)
    country = models.CharField(max_length=80, default="Ghana")
    timezone = models.CharField(max_length=64, default="Africa/Accra")
    primary_color = models.CharField(max_length=7, default="#1d4ed8", validators=[hex_colour])

    academic_year_format = models.CharField(
        max_length=40, default="{start}/{end}",
        help_text="Naming convention for academic years, e.g. {start}/{end} gives 2025/2026.",
    )
    student_number_format = models.CharField(
        max_length=60, default="{SHORT}/{YEAR}/{SEQ:04}",
        help_text="Tokens: {SHORT} institution short name, {YEAR} admission year, "
                  "{PROG} programme code, {SEQ:04} zero-padded sequence.",
    )

    # Registration policy
    allow_higher_level_courses = models.BooleanField(default=False)
    allow_retakes = models.BooleanField(default=True)
    allow_cross_programme_electives = models.BooleanField(default=False)
    allow_credit_overload = models.BooleanField(
        default=False, help_text="Allow exceeding the programme's maximum credits, subject to approval."
    )
    registration_requires_approval = models.BooleanField(default=False)

    # Results policy
    amendments_require_second_approver = models.BooleanField(
        default=True, help_text="The person approving an amendment must differ from the requester."
    )
    approver_must_differ_from_submitter = models.BooleanField(default=True)
    cgpa_retake_policy = models.CharField(
        max_length=10, choices=CgpaRetakePolicy.choices, default=CgpaRetakePolicy.LATEST
    )
    gpa_decimal_places = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    result_slip_footer = models.TextField(blank=True)

    class Meta:
        verbose_name = "institution profile"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The institution profile cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def clean(self):
        if "{SEQ" not in self.student_number_format:
            raise ValidationError({"student_number_format": "The format must include a {SEQ} token."})


class StudentNumberSequence(models.Model):
    """Per-key counter for generated student numbers (key usually the admission year)."""

    key = models.CharField(max_length=60, unique=True)
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.key}: {self.last_value}"


class AcademicRegulation(TimeStampedModel):
    """
    The institution's academic regulations, as configuration (single row).

    Every rule is optional: a blank value switches it off. Nothing here is taken from a
    national guideline; administrators enter their own institution's regulations.
    """

    # Resits / supplementary examinations
    resits_enabled = models.BooleanField(default=False)
    resit_min_total = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Only failing students with at least this course total may resit. Blank: any failed student.",
    )
    max_resits_per_course = models.PositiveSmallIntegerField(
        default=1, help_text="Resits a student may take for the same course across all attempts.",
    )
    resit_max_grade_point = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="Highest grade point a resit can earn (e.g. 2.00 caps at C). Blank: no cap.",
    )

    # Retakes (re-registering a failed course in a later semester)
    retake_max_grade_point = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="Highest grade point a retaken course can earn. Blank: no cap.",
    )
    max_attempts_per_course = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Registrations with a published result allowed per course. Blank: unlimited.",
    )
    failed_compulsory_must_be_retaken = models.BooleanField(
        default=False, help_text="A failed compulsory course becomes compulsory again whenever it is offered.",
    )

    # Progression, probation and dismissal (proposed by a progression review, applied by the registry)
    probation_cgpa_below = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    dismissal_cgpa_below = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    dismissal_cgpa_inclusive = models.BooleanField(
        default=False, help_text="Apply the dismissal threshold as 'at or below' instead of 'below' (e.g. CGPA of 1.0 or less).",
    )
    dismissal_status = models.CharField(
        max_length=20, choices=[("DISMISSED", "Dismissed"), ("WITHDRAWN", "Withdrawn")], default="DISMISSED",
        help_text="Student status recorded when a dismissal decision is applied.",
    )
    dismiss_after_consecutive_probations = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Propose dismissal when a student would be placed on probation this many times in a row.",
    )
    dismiss_when_attempts_exhausted = models.BooleanField(
        default=False, help_text="Propose dismissal when a compulsory course is failed with no attempts left.",
    )
    promotion_min_cgpa = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="Minimum CGPA to move to the next level at the end of the academic year.",
    )
    max_referred_courses_to_proceed = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Most referred (failed, not yet passed) courses a student may carry to the next level.",
    )
    dismiss_when_referred_courses_over = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Propose dismissal when a student has more than this many referred courses.",
    )
    dismiss_on_second_repeat = models.BooleanField(
        default=False, help_text="Propose dismissal when a student who has already repeated a level would repeat it again.",
    )
    promotion_min_credits_per_year = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Credits a student must earn in the academic year to move up a level.",
    )
    promotion_max_outstanding_failed_credits = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Most failed, not-yet-passed credits a student may carry to the next level.",
    )
    repeat_level_when_not_promoted = models.BooleanField(
        default=True, help_text="Propose repeating the level when promotion requirements are not met.",
    )
    reference = models.TextField(
        blank=True, help_text="Where these rules come from, e.g. the title and date of the academic regulations.",
    )

    class Meta:
        verbose_name = "academic regulation"

    def __str__(self):
        return "Academic regulations"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def clean(self):
        low = self.dismissal_cgpa_below
        for field, high, label in (("probation_cgpa_below", self.probation_cgpa_below, "probation threshold"),
                                   ("promotion_min_cgpa", self.promotion_min_cgpa, "minimum CGPA for promotion")):
            if low is not None and high is not None and low > high:
                raise ValidationError({"dismissal_cgpa_below": f"The dismissal threshold cannot be above the {label}."})
        proceed, dismiss = self.max_referred_courses_to_proceed, self.dismiss_when_referred_courses_over
        if proceed is not None and dismiss is not None and dismiss < proceed:
            raise ValidationError({"dismiss_when_referred_courses_over":
                                   "The dismissal limit cannot be lower than the number of referred courses allowed to proceed."})

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
