from rest_framework import serializers

from .models import IssuedTranscript, SigningMode


class IssuedTranscriptSerializer(serializers.ModelSerializer):
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    programme_code = serializers.CharField(source="student.programme.code", read_only=True)
    issued_by_name = serializers.CharField(source="issued_by.get_full_name", read_only=True)
    revoked_by_name = serializers.CharField(source="revoked_by.get_full_name", read_only=True, default=None)
    signing_display = serializers.CharField(source="get_signing_display", read_only=True)
    cgpa = serializers.SerializerMethodField()
    verification_code = serializers.CharField(source="display_code", read_only=True)

    class Meta:
        model = IssuedTranscript
        fields = [
            "id", "serial", "student", "student_number", "student_name", "programme_code", "purpose", "signing",
            "signing_display", "is_interim", "interim_reason", "status", "cgpa", "issued_by_name", "issued_at",
            "print_count", "last_printed_at", "revoked_by_name", "revoked_at", "revoke_reason", "verification_code",
        ]
        read_only_fields = fields

    def get_cgpa(self, obj) -> str | None:
        return obj.snapshot.get("summary", {}).get("cgpa")


class IssueTranscriptSerializer(serializers.Serializer):
    student = serializers.IntegerField(min_value=1)
    purpose = serializers.CharField(max_length=255)
    signing = serializers.ChoiceField(choices=SigningMode.choices, default=SigningMode.HAND)


class RevokeTranscriptSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)
