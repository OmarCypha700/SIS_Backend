from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Institution Administrator"
    REGISTRAR = "REGISTRAR", "Registrar"
    LECTURER = "LECTURER", "Lecturer"
    RESULTS_OFFICER = "RESULTS_OFFICER", "Results Officer"
    STUDENT = "STUDENT", "Student"


STAFF_ROLES = (Role.ADMIN, Role.REGISTRAR, Role.LECTURER, Role.RESULTS_OFFICER)
ACADEMIC_ADMIN_ROLES = (Role.ADMIN, Role.REGISTRAR)
# Staff who can be assigned courses and then act as the lecturer for those courses.
TEACHING_ROLES = (Role.LECTURER, Role.REGISTRAR, Role.RESULTS_OFFICER)
# Roles that see every course, not only the ones they teach.
OVERSIGHT_ROLES = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)


class Office(models.TextChoices):
    """Offices a staff member can be appointed to for a period, on top of their own role."""

    REGISTRAR = "REGISTRAR", "Registrar"
    RESULTS_OFFICER = "RESULTS_OFFICER", "Results Officer"

# Privileged result capabilities. They are explicit per-user flags so an institution can
# separate approval from publication; these are only the defaults applied at creation.
CAPABILITY_FIELDS = (
    "can_approve_results",
    "can_publish_results",
    "can_request_amendments",
    "can_approve_amendments",
    "can_issue_transcripts",
)
# Only these roles may ever be given the transcript permission.
TRANSCRIPT_ISSUER_ROLES = ("ADMIN", "REGISTRAR")
DEFAULT_CAPABILITIES = {
    Role.ADMIN: {"can_approve_results": False, "can_publish_results": True,
                 "can_request_amendments": False, "can_approve_amendments": True, "can_issue_transcripts": True},
    # Registrars issue official transcripts only when an administrator allows it.
    Role.REGISTRAR: {"can_approve_results": False, "can_publish_results": True,
                     "can_request_amendments": False, "can_approve_amendments": True, "can_issue_transcripts": False},
    Role.RESULTS_OFFICER: {"can_approve_results": True, "can_publish_results": False,
                           "can_request_amendments": True, "can_approve_amendments": False},
    Role.LECTURER: {"can_approve_results": False, "can_publish_results": False,
                    "can_request_amendments": True, "can_approve_amendments": False},
    Role.STUDENT: {},
}
# What holding an office grants, for as long as the appointment lasts. Transcript issuing is never
# automatic: an administrator still allows it person by person.
OFFICE_CAPABILITIES = {
    office: {field: value for field, value in DEFAULT_CAPABILITIES[office].items()
             if value and field != "can_issue_transcripts"}
    for office in Office.values
}


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        role = extra_fields.get("role", Role.STUDENT)
        for field, value in DEFAULT_CAPABILITIES.get(role, {}).items():
            extra_fields.setdefault(field, value)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def with_capability(self, field):
        """Active staff holding a permission, themselves or through an office they hold now."""
        offices = [office for office, granted in OFFICE_CAPABILITIES.items() if granted.get(field)]
        held = Appointment.objects.current().filter(office__in=offices).values("user")
        return (self.filter(is_active=True).exclude(role=Role.STUDENT)
                .filter(models.Q(**{field: True}) | models.Q(pk__in=held)))

    def holding(self, *roles):
        """Active people whose own role is one of `roles`, or who currently hold it as an office."""
        held = Appointment.objects.current().filter(office__in=roles).values("user")
        return self.filter(is_active=True).filter(models.Q(role__in=roles) | models.Q(pk__in=held))

    def teaching_staff(self):
        return self.filter(is_active=True, role__in=TEACHING_ROLES)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    title = models.CharField(max_length=20, blank=True, help_text="e.g. Dr., Prof., Mrs.")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    staff_id = models.CharField(max_length=40, blank=True, null=True, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT, db_index=True)

    can_approve_results = models.BooleanField(default=False)
    can_publish_results = models.BooleanField(default=False)
    can_request_amendments = models.BooleanField(default=False)
    can_approve_amendments = models.BooleanField(default=False)
    can_issue_transcripts = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    # is_staff only grants access to Django's own /django-admin/ site, never to the API.
    is_staff = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    # Notification preferences. Security email (password reset) is always sent.
    email_alerts = models.BooleanField(default=True, help_text="Email me when something needs my action (staff).")
    notification_sound = models.BooleanField(default=True, help_text="Play a sound for new notifications in the app.")
    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.get_full_name()} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def get_full_name(self):
        return " ".join(part for part in [self.title, self.first_name, self.last_name] if part)

    def get_short_name(self):
        return self.first_name

    @property
    def is_student(self):
        return self.role == Role.STUDENT

    # --- Offices held through appointments ------------------------------------------------
    # Computed once per request (the user object lives for one request), so an appointment that
    # ends or is revoked takes effect on the person's next request.

    def current_appointments(self):
        cached = self.__dict__.get("_current_appointments")
        if cached is None:
            if self.pk is None or not self.is_active or self.role == Role.STUDENT:
                cached = []
            else:
                cached = list(Appointment.objects.current().filter(user=self))
            self.__dict__["_current_appointments"] = cached
        return cached

    def forget_appointments(self):
        self.__dict__.pop("_current_appointments", None)

    @property
    def offices(self):
        return sorted({a.office for a in self.current_appointments()})

    @property
    def roles(self):
        """The person's own role, plus Lecturer for staff who can teach, plus the offices they hold now."""
        roles = {self.role}
        if self.role in TEACHING_ROLES:
            roles.add(Role.LECTURER)
        roles.update(self.offices)
        return frozenset(roles)

    def has_any_role(self, *roles):
        return not self.roles.isdisjoint(roles)

    @property
    def teaches_only(self):
        """A lecturer with no office: sees only the courses they are assigned to."""
        return Role.LECTURER in self.roles and not self.has_any_role(*OVERSIGHT_ROLES)

    def capacity_codes(self):
        """How the person is acting right now, for the audit log: e.g. ["LECTURER", "ACTING_RESULTS_OFFICER"]."""
        codes = [self.role] if self.role else []
        for appointment in sorted(self.current_appointments(), key=lambda a: a.office):
            code = f"ACTING_{appointment.office}" if appointment.acting else appointment.office
            if code not in codes and appointment.office != self.role:
                codes.append(code)
        return codes

    def has_capability(self, field):
        """The person's own permission, or one that comes with an office they currently hold."""
        if getattr(self, field, False):
            return True
        return any(OFFICE_CAPABILITIES.get(office, {}).get(field) for office in self.offices)

    def capabilities(self):
        return {field: self.has_capability(field) for field in CAPABILITY_FIELDS}


class AppointmentQuerySet(models.QuerySet):
    def current(self, on=None):
        today = on or timezone.localdate()
        return self.filter(ended_at__isnull=True, starts_on__lte=today).filter(
            models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=today))


class Appointment(models.Model):
    """
    A staff member holding an office (Registrar, Results Officer) for a period.

    Access comes from the person's own role plus the offices they currently hold, so a tutor appointed
    Registrar keeps teaching, and loses registry access the moment the appointment ends. Appointments
    are never deleted: ended ones are the history of who held which office, and when.
    """

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="appointments")
    office = models.CharField(max_length=20, choices=Office.choices)
    acting = models.BooleanField(default=False, help_text="Covering for the substantive holder.")
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True, help_text="Last day in office; empty while open-ended.")
    reference = models.CharField(max_length=120, blank=True, help_text="e.g. appointment letter number.")
    notes = models.TextField(blank=True)
    appointed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    end_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppointmentQuerySet.as_manager()

    class Meta:
        ordering = ["-starts_on", "-id"]
        indexes = [models.Index(fields=["office", "starts_on"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=models.F("starts_on")),
                                   name="appointment_ends_after_start"),
        ]

    def __str__(self):
        prefix = "Acting " if self.acting else ""
        return f"{prefix}{self.get_office_display()}: {self.user.get_full_name()} from {self.starts_on}"

    @property
    def title(self):
        return f"{'Acting ' if self.acting else ''}{self.get_office_display()}"

    def is_current(self, on=None):
        today = on or timezone.localdate()
        return (self.ended_at is None and self.starts_on <= today
                and (self.ends_on is None or self.ends_on >= today))
