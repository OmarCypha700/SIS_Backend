from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.bulk import BulkIdsField

from .models import CAPABILITY_FIELDS, DEFAULT_CAPABILITIES, TRANSCRIPT_ISSUER_ROLES, Appointment, Office, Role, User


def office_summary(appointment):
    return {"id": appointment.id, "office": appointment.office, "title": appointment.title,
            "acting": appointment.acting, "starts_on": appointment.starts_on, "ends_on": appointment.ends_on}


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Your current password is incorrect."})
        validate_new_password(attrs["new_password"], user)
        return attrs


def validate_new_password(password, user, field="new_password"):
    try:
        password_validation.validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: list(exc.messages)}) from exc


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)


class ReasonInputSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class UserBulkSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["deactivate", "activate", "delete"])
    ids = BulkIdsField()
    reason = serializers.CharField(max_length=2000)


class CurrentUserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    capabilities = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    offices = serializers.SerializerMethodField()
    student = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "title", "first_name", "last_name", "full_name", "phone", "role", "role_display",
            "roles", "offices", "capabilities", "must_change_password", "student", "email_alerts", "notification_sound",
        ]
        read_only_fields = [f for f in fields if f not in ("phone", "email_alerts", "notification_sound")]

    @extend_schema_field({"type": "object", "additionalProperties": {"type": "boolean"}})
    def get_capabilities(self, user):
        return user.capabilities()

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_roles(self, user):
        """Own role, Lecturer for staff who can teach, and offices held now. Use this for access, not `role`."""
        return sorted(user.roles)

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_offices(self, user):
        return [office_summary(a) for a in user.current_appointments()]

    @extend_schema_field({"type": "object", "nullable": True})
    def get_student(self, user):
        from apps.students.photos import photo_url

        if user.role != Role.STUDENT:
            return None
        profile = getattr(user, "student_profile", None)
        if profile is None:
            return None
        return {
            "id": profile.id,
            "student_number": profile.student_number,
            "programme": profile.programme.name,
            "programme_code": profile.programme.code,
            "level": profile.current_level.name,
            "status": profile.status,
            "photo_url": photo_url(profile),
            "thumbnail_url": photo_url(profile, "thumb"),
        }


class UserSerializer(serializers.ModelSerializer):
    """Staff account management (administrators only)."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    offices = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False,
                                     help_text="Temporary password; the user must change it on first sign-in.")

    class Meta:
        model = User
        fields = [
            "id", "email", "title", "first_name", "last_name", "full_name", "phone", "staff_id", "role",
            "role_display", "offices", *CAPABILITY_FIELDS, "is_active", "must_change_password", "last_login",
            "date_joined", "password",
        ]
        read_only_fields = ["id", "is_active", "last_login", "date_joined", "must_change_password"]

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_offices(self, user):
        """Offices held now. The flags above are the person's own; offices add their permissions on top."""
        return [office_summary(a) for a in user.current_appointments()]

    def validate_role(self, role):
        if role == Role.STUDENT and (self.instance is None or self.instance.role != Role.STUDENT):
            raise serializers.ValidationError("Student accounts are created from the student records module.")
        if self.instance is not None and self.instance.role == Role.STUDENT and role != Role.STUDENT:
            raise serializers.ValidationError("A student account cannot be converted to a staff account.")
        request = self.context.get("request")
        if self.instance is not None and request and self.instance.pk == request.user.pk and role != self.instance.role:
            raise serializers.ValidationError("You cannot change your own role.")
        return role

    def validate_email(self, email):
        return email.strip().lower()

    def validate(self, attrs):
        if self.instance is None:
            password = attrs.get("password")
            if not password:
                raise serializers.ValidationError({"password": "A temporary password is required."})
            candidate = User(email=attrs.get("email", ""), first_name=attrs.get("first_name", ""),
                             last_name=attrs.get("last_name", ""))
            validate_new_password(password, candidate, field="password")
        elif "password" in attrs:
            raise serializers.ValidationError({"password": "Use the reset-password action to change passwords."})
        role = attrs.get("role", getattr(self.instance, "role", None))
        registrar_now = self.instance is not None and Office.REGISTRAR in self.instance.offices
        if attrs.get("can_issue_transcripts") and role not in TRANSCRIPT_ISSUER_ROLES and not registrar_now:
            raise serializers.ValidationError(
                {"can_issue_transcripts": "Only administrators and registrars can issue official transcripts."})
        if (self.instance is not None and "role" in attrs and role not in TRANSCRIPT_ISSUER_ROLES
                and not registrar_now):
            attrs["can_issue_transcripts"] = False  # moving out of the registry removes the permission
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        role = validated_data.get("role", Role.LECTURER)
        for field, value in DEFAULT_CAPABILITIES.get(role, {}).items():
            validated_data.setdefault(field, value)
        user = User.objects.create_user(password=password, must_change_password=True, **validated_data)
        return user


class AppointmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    user_email = serializers.CharField(source="user.email", read_only=True)
    user_role_display = serializers.CharField(source="user.get_role_display", read_only=True)
    office_display = serializers.CharField(source="get_office_display", read_only=True)
    title = serializers.CharField(read_only=True)
    appointed_by_name = serializers.CharField(source="appointed_by.get_full_name", read_only=True, default=None)
    ended_by_name = serializers.CharField(source="ended_by.get_full_name", read_only=True, default=None)
    state = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.exclude(role=Role.STUDENT))

    class Meta:
        model = Appointment
        fields = ["id", "user", "user_name", "user_email", "user_role_display", "office", "office_display", "title",
                  "acting", "starts_on", "ends_on", "reference", "notes", "appointed_by", "appointed_by_name",
                  "ended_at", "ended_by", "ended_by_name", "end_reason", "created_at", "state"]
        read_only_fields = ["appointed_by", "ended_at", "ended_by", "end_reason", "created_at"]

    def get_state(self, appointment) -> str:
        if appointment.ended_at is not None:
            return "ended"
        if appointment.is_current():
            return "current"
        return "upcoming" if appointment.starts_on > timezone.localdate() else "completed"


class AppointmentUpdateSerializer(serializers.Serializer):
    ends_on = serializers.DateField(required=False, allow_null=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=120)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class AppointmentEndSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)
    deactivate_account = serializers.BooleanField(default=False)


class LecturerSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "staff_id"]


class AdminPasswordResetSerializer(serializers.Serializer):
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)
