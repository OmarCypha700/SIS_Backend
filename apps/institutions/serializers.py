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
    payment_gateway_configured = serializers.SerializerMethodField()

    class Meta:
        model = InstitutionProfile
        exclude = ["id", "transcript_signature", "transcript_seal"]
        read_only_fields = ["created_at", "updated_at"]

    def to_internal_value(self, data):
        # The settings form is multipart, where a cleared field arrives as "": that means "no cap".
        if hasattr(data, "get") and data.get("payment_fee_cap") == "":
            data = data.copy()
            data["payment_fee_cap"] = None
        return super().to_internal_value(data)

    def get_payment_gateway_configured(self, obj) -> bool:
        from apps.finance import paystack

        return paystack.configured()

    def validate_online_payments_enabled(self, value):
        from apps.finance import paystack

        if value and not paystack.configured():
            raise serializers.ValidationError("Online payment has not been set up on the server yet. Ask your system administrator.")
        return value

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
