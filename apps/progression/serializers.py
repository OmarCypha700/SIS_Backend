from rest_framework import serializers

from .models import Placement, ProgressionDecision, ProgressionReview, Standing


class ProgressionReviewSerializer(serializers.ModelSerializer):
    semester_name = serializers.CharField(source="semester.__str__", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    generated_by_name = serializers.CharField(source="generated_by.get_full_name", read_only=True, default=None)
    applied_by_name = serializers.CharField(source="applied_by.get_full_name", read_only=True, default=None)
    decision_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = ProgressionReview
        fields = ["id", "semester", "semester_name", "status", "status_display", "is_final_term", "regulation_snapshot",
                  "generated_by_name", "applied_by_name", "applied_at", "decision_count", "created_at", "updated_at"]


class ProgressionDecisionSerializer(serializers.ModelSerializer):
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    programme_code = serializers.CharField(source="programme.code", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)
    overridden_by_name = serializers.CharField(source="overridden_by.get_full_name", read_only=True, default=None)
    is_overridden = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProgressionDecision
        fields = ["id", "student", "student_number", "student_name", "programme_code", "level_name", "semester_gpa",
                  "cgpa", "credits_earned_year", "outstanding_failed_credits", "results_complete",
                  "proposed_standing", "proposed_placement", "reasons", "final_standing", "final_placement",
                  "override_reason", "overridden_by_name", "is_overridden", "excluded", "applied"]
        read_only_fields = fields


class GenerateReviewSerializer(serializers.Serializer):
    semester = serializers.IntegerField()


class OverrideSerializer(serializers.Serializer):
    standing = serializers.ChoiceField(choices=Standing.choices)
    placement = serializers.ChoiceField(choices=Placement.choices)
    reason = serializers.CharField(max_length=2000)


class ExcludeSerializer(serializers.Serializer):
    excluded = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
