from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.bulk import BulkIdsField

from .models import CAPABILITY_FIELDS, DEFAULT_CAPABILITIES, Role, User


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
    student = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "title", "first_name", "last_name", "full_name", "phone", "role", "role_display",
            "capabilities", "must_change_password", "student", "email_alerts", "notification_sound",
        ]
        read_only_fields = [f for f in fields if f not in ("phone", "email_alerts", "notification_sound")]

    @extend_schema_field({"type": "object", "additionalProperties": {"type": "boolean"}})
    def get_capabilities(self, user):
        return user.capabilities()

    @extend_schema_field({"type": "object", "nullable": True})
    def get_student(self, user):
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
        }


class UserSerializer(serializers.ModelSerializer):
    """Staff account management (administrators only)."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False,
                                     help_text="Temporary password; the user must change it on first sign-in.")

    class Meta:
        model = User
        fields = [
            "id", "email", "title", "first_name", "last_name", "full_name", "phone", "staff_id", "role",
            "role_display", *CAPABILITY_FIELDS, "is_active", "must_change_password", "last_login",
            "date_joined", "password",
        ]
        read_only_fields = ["id", "is_active", "last_login", "date_joined", "must_change_password"]

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
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        role = validated_data.get("role", Role.LECTURER)
        for field, value in DEFAULT_CAPABILITIES.get(role, {}).items():
            validated_data.setdefault(field, value)
        user = User.objects.create_user(password=password, must_change_password=True, **validated_data)
        return user


class LecturerSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "staff_id"]


class AdminPasswordResetSerializer(serializers.Serializer):
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)
