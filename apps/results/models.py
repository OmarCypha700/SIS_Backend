from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel


class GradingScheme(TimeStampedModel):
    """
    Grade boundaries and rounding rules.

    Course totals are rounded to `round_to_decimals` places (half-up) first, then matched to
    the band whose min_score <= total <= max_score (both ends inclusive). Bands must cover
    0-100 contiguously at that precision, so there are no gaps or overlaps.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    round_to_decimals = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(2)])
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True), name="single_default_grading_scheme")
        ]

    def __str__(self):
        return self.name

    @property
    def step(self):
        return Decimal(1).scaleb(-self.round_to_decimals)


def validate_bands(bands, round_to_decimals):
    """bands: iterable of dicts/objects with min_score, max_score, letter. Raises ValidationError."""
    step = Decimal(1).scaleb(-round_to_decimals)
    items = sorted(
        ({"letter": _get(b, "letter"), "min": Decimal(str(_get(b, "min_score"))), "max": Decimal(str(_get(b, "max_score")))}
         for b in bands),
        key=lambda b: b["min"],
    )
    if not items:
        raise ValidationError("At least one grade band is required.")
    letters = [b["letter"] for b in items]
    if len(set(letters)) != len(letters):
        raise ValidationError("Each grade letter may only appear once.")
    for band in items:
        if band["min"] > band["max"]:
            raise ValidationError(f"Band {band['letter']}: minimum is greater than maximum.")
        if band["min"] != band["min"].quantize(step) or band["max"] != band["max"].quantize(step):
            raise ValidationError(f"Band {band['letter']}: boundaries must use at most {round_to_decimals} decimal places.")
    if items[0]["min"] != 0:
        raise ValidationError("The lowest band must start at 0.")
    if items[-1]["max"] != 100:
        raise ValidationError("The highest band must end at 100.")
    for lower, upper in zip(items, items[1:]):
        expected = lower["max"] + step
        if upper["min"] < expected:
            raise ValidationError(f"Bands {lower['letter']} and {upper['letter']} overlap.")
        if upper["min"] > expected:
            raise ValidationError(
                f"There is a gap between {lower['letter']} (max {lower['max']}) and {upper['letter']} (min {upper['min']})."
            )


def _get(obj, key):
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


class GradeBand(models.Model):
    scheme = models.ForeignKey(GradingScheme, on_delete=models.CASCADE, related_name="bands")
    letter = models.CharField(max_length=4)
    min_score = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])
    max_score = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, validators=[MinValueValidator(0)])
    is_pass = models.BooleanField(default=True)
    remark = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["-min_score"]
        constraints = [models.UniqueConstraint(fields=["scheme", "letter"], name="unique_letter_per_scheme")]

    def __str__(self):
        return f"{self.letter} ({self.min_score}-{self.max_score})"


class AssessmentComponent(TimeStampedModel):
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.CASCADE, related_name="assessment_components")
    name = models.CharField(max_length=80)
    weight = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(100)])
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=100, validators=[MinValueValidator(Decimal("0.01"))])
    is_required = models.BooleanField(default=True)
    is_examination = models.BooleanField(
        default=False, help_text="Replaced by the resit score in a resit. If no component is marked, a resit replaces the whole total.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["offering", "name"], name="unique_component_name_per_offering")]

    def __str__(self):
        return f"{self.name} ({self.weight}%)"


class SheetStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted for review"
    RETURNED = "RETURNED", "Returned for correction"
    APPROVED = "APPROVED", "Approved"
    PUBLISHED = "PUBLISHED", "Published"


EDITABLE_SHEET_STATUSES = (SheetStatus.DRAFT, SheetStatus.RETURNED)


class ResultSheet(TimeStampedModel):
    """The results of one course offering, moving through the approval workflow."""

    offering = models.OneToOneField("academics.CourseOffering", on_delete=models.PROTECT, related_name="result_sheet")
    grading_scheme = models.ForeignKey(GradingScheme, on_delete=models.PROTECT, related_name="sheets")
    status = models.CharField(max_length=10, choices=SheetStatus.choices, default=SheetStatus.DRAFT, db_index=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    last_comment = models.TextField(blank=True)

    def __str__(self):
        return f"Results: {self.offering}"

    @property
    def is_editable(self):
        return self.status in EDITABLE_SHEET_STATUSES


class ResultSheetEvent(models.Model):
    sheet = models.ForeignKey(ResultSheet, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=10)
    to_status = models.CharField(max_length=10)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class StudentMark(TimeStampedModel):
    registration = models.ForeignKey("registration.CourseRegistration", on_delete=models.PROTECT, related_name="marks")
    component = models.ForeignKey(AssessmentComponent, on_delete=models.CASCADE, related_name="marks")
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["registration", "component"], name="one_mark_per_component")]

    def clean(self):
        if self.score is not None and self.component_id and self.score > self.component.max_score:
            raise ValidationError({"score": f"Score cannot exceed {self.component.max_score}."})


class CourseResult(TimeStampedModel):
    """Computed course outcome. Raw marks are kept so results can be recalculated."""

    sheet = models.ForeignKey(ResultSheet, on_delete=models.PROTECT, related_name="results")
    registration = models.OneToOneField("registration.CourseRegistration", on_delete=models.PROTECT, related_name="result")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="course_results")
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT, related_name="course_results")
    total_score = models.DecimalField(max_digits=5, decimal_places=2)
    grade = models.CharField(max_length=4)
    grade_point = models.DecimalField(max_digits=4, decimal_places=2)
    is_pass = models.BooleanField()
    credits = models.PositiveSmallIntegerField()
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    is_amended = models.BooleanField(default=False)
    is_resit = models.BooleanField(default=False, help_text="The grade comes from a resit examination.")
    grade_capped = models.BooleanField(default=False, help_text="The grade was limited by a retake or resit cap.")

    class Meta:
        ordering = ["offering__course__code"]
        indexes = [models.Index(fields=["student", "is_published"])]

    def __str__(self):
        return f"{self.student.student_number} {self.offering.course.code}: {self.grade}"


class AmendmentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending approval"
    APPLIED = "APPLIED", "Approved and applied"
    REJECTED = "REJECTED", "Rejected"


class ResultAmendment(TimeStampedModel):
    course_result = models.ForeignKey(CourseResult, on_delete=models.PROTECT, related_name="amendments")
    original_marks = models.JSONField(help_text="{component_id: score} before the change")
    new_marks = models.JSONField(help_text="{component_id: score} requested")
    original_total = models.DecimalField(max_digits=5, decimal_places=2)
    original_grade = models.CharField(max_length=4)
    new_total = models.DecimalField(max_digits=5, decimal_places=2)
    new_grade = models.CharField(max_length=4)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=AmendmentStatus.choices, default=AmendmentStatus.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class ResitSheet(TimeStampedModel):
    """
    A resit (supplementary) examination for one course offering whose results are published.

    Eligible failing students are listed as entries; those who register sit the resit, the
    assigned lecturer enters scores, and the sheet goes through the same submit / approve /
    publish workflow as ordinary results before any published grade changes.
    """

    offering = models.OneToOneField("academics.CourseOffering", on_delete=models.PROTECT, related_name="resit_sheet")
    status = models.CharField(max_length=10, choices=SheetStatus.choices, default=SheetStatus.DRAFT, db_index=True)
    registration_deadline = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    last_comment = models.TextField(blank=True)

    def __str__(self):
        return f"Resit: {self.offering}"

    @property
    def is_editable(self):
        return self.status in EDITABLE_SHEET_STATUSES


class ResitEntryStatus(models.TextChoices):
    ELIGIBLE = "ELIGIBLE", "Eligible"
    REGISTERED = "REGISTERED", "Registered"
    NOT_TAKEN = "NOT_TAKEN", "Not taken"
    COMPLETED = "COMPLETED", "Completed"


class ResitEntry(TimeStampedModel):
    sheet = models.ForeignKey(ResitSheet, on_delete=models.CASCADE, related_name="entries")
    course_result = models.ForeignKey(CourseResult, on_delete=models.PROTECT, related_name="resit_entries")
    status = models.CharField(max_length=12, choices=ResitEntryStatus.choices, default=ResitEntryStatus.ELIGIBLE)
    score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)], help_text="Resit examination score out of 100.",
    )
    original_total = models.DecimalField(max_digits=5, decimal_places=2)
    original_grade = models.CharField(max_length=4)
    new_total = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    new_grade = models.CharField(max_length=4, blank=True)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    registered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["course_result__student__last_name", "course_result__student__first_name"]
        constraints = [models.UniqueConstraint(fields=["sheet", "course_result"], name="one_resit_entry_per_result")]

    def __str__(self):
        return f"{self.course_result} resit ({self.status})"
