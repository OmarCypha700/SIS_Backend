from rest_framework import serializers

from common.serializers import ModelCleanMixin

from .models import AcademicRegulation, InstitutionProfile
from .services import render_format

BRANDING_FIELDS = ["name", "short_name", "motto", "logo", "primary_color", "country"]


class InstitutionBrandingSerializer(serializers.ModelSerializer):
    """Public subset used by the sign-in page and application shell."""

    class Meta:
        model = InstitutionProfile
        fields = BRANDING_FIELDS


class InstitutionProfileSerializer(serializers.ModelSerializer):
    # The signature and seal are private files, managed through /institution/transcript-images/.
    has_transcript_signature = serializers.SerializerMethodField()
    has_transcript_seal = serializers.SerializerMethodField()

    class Meta:
        model = InstitutionProfile
        exclude = ["id", "transcript_signature", "transcript_seal"]
        read_only_fields = ["created_at", "updated_at"]

    def get_has_transcript_signature(self, obj) -> bool:
        return bool(obj.transcript_signature)

    def get_has_transcript_seal(self, obj) -> bool:
        return bool(obj.transcript_seal)

    def validate_transcript_serial_format(self, value):
        if "{SEQ" not in value:
            raise serializers.ValidationError("The format must include a {SEQ} token, e.g. {SEQ:06}.")
        try:
            render_format(value, short="X", year=2026, programme="P", seq=1)
        except (KeyError, ValueError, IndexError) as exc:
            raise serializers.ValidationError("Unknown token in format. Use {SHORT}, {YEAR}, {PROG} and {SEQ}.") from exc
        return value

    def validate_student_number_format(self, value):
        if "{SEQ" not in value:
            raise serializers.ValidationError("The format must include a {SEQ} token, e.g. {SEQ:04}.")
        from .services import render_student_number
        try:
            render_student_number(value, short="X", year=2025, programme="P", seq=1)
        except (KeyError, ValueError, IndexError) as exc:
            raise serializers.ValidationError("Unknown token in format.") from exc
        return value

    def validate_timezone(self, value):
        import zoneinfo
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError("Unknown time zone.")
        return value


class AcademicRegulationSerializer(ModelCleanMixin, serializers.ModelSerializer):
    class Meta:
        model = AcademicRegulation
        exclude = ["id"]
        read_only_fields = ["created_at", "updated_at"]
