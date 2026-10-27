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

# Privileged result capabilities. They are explicit per-user flags so an institution can
# separate approval from publication; these are only the defaults applied at creation.
CAPABILITY_FIELDS = (
    "can_approve_results",
    "can_publish_results",
    "can_request_amendments",
    "can_approve_amendments",
)
DEFAULT_CAPABILITIES = {
    Role.ADMIN: {"can_approve_results": False, "can_publish_results": True,
                 "can_request_amendments": False, "can_approve_amendments": True},
    Role.REGISTRAR: {"can_approve_results": False, "can_publish_results": True,
                     "can_request_amendments": False, "can_approve_amendments": True},
    Role.RESULTS_OFFICER: {"can_approve_results": True, "can_publish_results": False,
                           "can_request_amendments": True, "can_approve_amendments": False},
    Role.LECTURER: {"can_approve_results": False, "can_publish_results": False,
                    "can_request_amendments": True, "can_approve_amendments": False},
    Role.STUDENT: {},
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

    is_active = models.BooleanField(default=True)
    # is_staff only grants access to Django's own /django-admin/ site, never to the API.
    is_staff = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
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

    def capabilities(self):
        return {field: getattr(self, field) for field in CAPABILITY_FIELDS}
