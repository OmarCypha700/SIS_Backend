import datetime

from rest_framework import serializers

from apps.academics.models import Level, Programme
from common.bulk import BulkIdsField

from .models import Gender, Student, StudentProgrammeHistory, StudentStatus
from .photos import photo_url


def _validate_placement(programme, level):
    if programme and level and programme.levels.exists() and not programme.levels.filter(pk=level.pk).exists():
        raise serializers.ValidationError({"current_level": f"{level} is not a level of {programme.code}."})


class PhotoUrlsMixin(serializers.Serializer):
    """Versioned API addresses of the student's photo; None when there is no photo."""

    photo_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    def get_photo_url(self, obj) -> str | None:
        return photo_url(obj)

    def get_thumbnail_url(self, obj) -> str | None:
        return photo_url(obj, "thumb")


class StudentSerializer(PhotoUrlsMixin, serializers.ModelSerializer):
    """Full record for registry staff."""

    full_name = serializers.CharField(read_only=True)
    programme_code = serializers.CharField(source="programme.code", read_only=True)
    programme_name = serializers.CharField(source="programme.name", read_only=True)
    level_name = serializers.CharField(source="current_level.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    student_number = serializers.CharField(required=False, allow_blank=True, max_length=40)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False,
                                     help_text="Optional initial password. Without one the student uses password reset.")

    class Meta:
        model = Student
        fields = [
            "id", "student_number", "first_name", "middle_name", "last_name", "full_name", "date_of_birth",
            "gender", "email", "phone", "residential_address", "emergency_contact_name", "emergency_contact_phone",
            "emergency_contact_relationship", "admission_date", "admission_year", "entry_academic_year",
            "programme", "programme_code", "programme_name", "current_level", "level_name", "status",
            "status_display", "academic_standing", "photo_url", "thumbnail_url", "is_archived", "is_active",
            "password", "created_at", "updated_at",
        ]
        read_only_fields = ["is_archived", "academic_standing", "created_at", "updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None and not isinstance(self.instance, (list, tuple)) and hasattr(self.instance, "pk"):
            # Identity and placement changes go through dedicated, audited actions.
            for name in ("student_number", "programme", "current_level", "status", "password"):
                fields[name].read_only = True
        return fields

    def validate_email(self, email):
        email = email.strip().lower()
        from apps.accounts.models import User
        users = User.objects.filter(email=email)
        if self.instance is not None:
            users = users.exclude(pk=self.instance.user_id)
        if users.exists():
            raise serializers.ValidationError("This email address is already in use.")
        return email

    def validate_student_number(self, value):
        value = value.strip().upper()
        if value and Student.objects.filter(student_number=value).exists():
            raise serializers.ValidationError("This student number is already in use.")
        return value

    def validate_date_of_birth(self, value):
        if value and value >= datetime.date.today():
            raise serializers.ValidationError("Date of birth must be in the past.")
        return value

    def validate(self, attrs):
        programme = attrs.get("programme") or getattr(self.instance, "programme", None)
        level = attrs.get("current_level") or getattr(self.instance, "current_level", None)
        _validate_placement(programme, level)
        if programme and not programme.is_active and self.instance is None:
            raise serializers.ValidationError({"programme": "This programme is not active."})
        return attrs


class StudentSummarySerializer(PhotoUrlsMixin, serializers.ModelSerializer):
    """Limited view for lecturers and results officers: no contact or personal details."""

    full_name = serializers.CharField(read_only=True)
    programme_code = serializers.CharField(source="programme.code", read_only=True)
    level_name = serializers.CharField(source="current_level.name", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "student_number", "full_name", "first_name", "last_name", "email", "programme_code",
                  "level_name", "status", "photo_url", "thumbnail_url"]
        read_only_fields = fields


SELF_EDITABLE_FIELDS = {"phone", "residential_address", "emergency_contact_name", "emergency_contact_phone",
                        "emergency_contact_relationship"}


class StudentSelfSerializer(PhotoUrlsMixin, serializers.ModelSerializer):
    """A student's own record; only contact details are editable."""

    full_name = serializers.CharField(read_only=True)
    programme_code = serializers.CharField(source="programme.code", read_only=True)
    programme_name = serializers.CharField(source="programme.name", read_only=True)
    level_name = serializers.CharField(source="current_level.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Student
        fields = [
            "id", "student_number", "first_name", "middle_name", "last_name", "full_name", "date_of_birth",
            "gender", "email", "phone", "residential_address", "emergency_contact_name", "emergency_contact_phone",
            "emergency_contact_relationship", "admission_year", "programme_code", "programme_name", "level_name",
            "status", "status_display", "photo_url", "thumbnail_url",
        ]
        read_only_fields = [f for f in fields if f not in SELF_EDITABLE_FIELDS]


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=StudentStatus.choices)
    reason = serializers.CharField(max_length=2000)


class PlacementChangeSerializer(serializers.Serializer):
    programme = serializers.PrimaryKeyRelatedField(queryset=Programme.objects.filter(is_active=True))
    current_level = serializers.PrimaryKeyRelatedField(queryset=Level.objects.all())
    reason = serializers.CharField(max_length=2000)

    def validate(self, attrs):
        _validate_placement(attrs["programme"], attrs["current_level"])
        return attrs


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class StudentBulkSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["deactivate", "activate", "change_status", "delete"])
    ids = BulkIdsField()
    reason = serializers.CharField(max_length=2000)
    status = serializers.ChoiceField(choices=StudentStatus.choices, required=False)

    def validate(self, attrs):
        if attrs["action"] == "change_status" and not attrs.get("status"):
            raise serializers.ValidationError({"status": "Choose the new status."})
        return attrs


class StudentHistorySerializer(serializers.ModelSerializer):
    from_programme_code = serializers.CharField(source="from_programme.code", read_only=True, default=None)
    to_programme_code = serializers.CharField(source="to_programme.code", read_only=True, default=None)
    from_level_name = serializers.CharField(source="from_level.name", read_only=True, default=None)
    to_level_name = serializers.CharField(source="to_level.name", read_only=True, default=None)
    changed_by_name = serializers.CharField(source="changed_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = StudentProgrammeHistory
        fields = ["id", "change_type", "from_programme_code", "to_programme_code", "from_level_name",
                  "to_level_name", "from_status", "to_status", "reason", "changed_by_name", "created_at"]


class StudentImportRowSerializer(serializers.Serializer):
    """Validates one CSV row. Column names match the import template."""

    student_number = serializers.CharField(required=False, allow_blank=True, max_length=40)
    first_name = serializers.CharField(max_length=100)
    middle_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    last_name = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    gender = serializers.ChoiceField(choices=Gender.choices, required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    programme_code = serializers.SlugRelatedField(slug_field="code", queryset=Programme.objects.filter(is_active=True))
    level = serializers.SlugRelatedField(slug_field="name", queryset=Level.objects.all())
    admission_year = serializers.IntegerField(min_value=1950, max_value=2100)
    admission_date = serializers.DateField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=StudentStatus.choices, required=False, default=StudentStatus.ACTIVE)

    def to_internal_value(self, data):
        cleaned = {k: v for k, v in data.items() if not (v == "" and k in {"date_of_birth", "admission_date", "status"})}
        if cleaned.get("programme_code"):
            cleaned["programme_code"] = cleaned["programme_code"].strip().upper()
        if cleaned.get("gender"):
            cleaned["gender"] = cleaned["gender"].strip().upper()[:1]
        return super().to_internal_value(cleaned)

    def validate(self, attrs):
        try:
            _validate_placement(attrs["programme_code"], attrs["level"])
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"level": exc.detail["current_level"]}) from exc
        attrs["email"] = attrs["email"].lower()
        attrs["student_number"] = (attrs.get("student_number") or "").upper()
        return attrs


IMPORT_COLUMNS = [
    "student_number", "first_name", "middle_name", "last_name", "email", "phone", "gender", "date_of_birth",
    "programme_code", "level", "admission_year", "admission_date", "status",
]
