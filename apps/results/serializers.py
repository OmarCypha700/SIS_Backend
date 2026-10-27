from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .models import (
    AssessmentComponent, GradeBand, GradingScheme, ResitEntry, ResitSheet, ResultAmendment, ResultSheet,
    ResultSheetEvent, SheetStatus,
    validate_bands,
)

LOCKED_SCHEME_STATUSES = (SheetStatus.SUBMITTED, SheetStatus.APPROVED, SheetStatus.PUBLISHED)


class GradeBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradeBand
        fields = ["id", "letter", "min_score", "max_score", "grade_point", "is_pass", "remark"]
        read_only_fields = ["id"]


class GradingSchemeSerializer(serializers.ModelSerializer):
    bands = GradeBandSerializer(many=True)
    in_use = serializers.SerializerMethodField()

    class Meta:
        model = GradingScheme
        fields = ["id", "name", "description", "round_to_decimals", "is_default", "is_active", "bands", "in_use",
                  "created_at", "updated_at"]
        read_only_fields = ["is_default", "created_at", "updated_at"]

    def get_in_use(self, obj) -> bool:
        return obj.sheets.filter(status__in=LOCKED_SCHEME_STATUSES).exists()

    def validate(self, attrs):
        decimals = attrs.get("round_to_decimals", getattr(self.instance, "round_to_decimals", 0))
        bands = attrs.get("bands")
        if self.instance is not None and self.get_in_use(self.instance) and (
            bands is not None or decimals != self.instance.round_to_decimals
        ):
            raise serializers.ValidationError(
                "This scheme has been used for submitted or published results. Create a new scheme instead.")
        if bands is not None:
            try:
                validate_bands(bands, decimals)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"bands": exc.messages}) from exc
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        bands = validated_data.pop("bands")
        scheme = GradingScheme.objects.create(**validated_data)
        GradeBand.objects.bulk_create([GradeBand(scheme=scheme, **band) for band in bands])
        return scheme

    @transaction.atomic
    def update(self, instance, validated_data):
        bands = validated_data.pop("bands", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        if bands is not None:
            instance.bands.all().delete()
            GradeBand.objects.bulk_create([GradeBand(scheme=instance, **band) for band in bands])
        return instance


class AssessmentComponentSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)

    class Meta:
        model = AssessmentComponent
        fields = ["id", "name", "weight", "max_score", "is_required", "is_examination", "order"]
        read_only_fields = ["order"]


class ComponentsInputSerializer(serializers.Serializer):
    components = AssessmentComponentSerializer(many=True)


class MarkEntrySerializer(serializers.Serializer):
    registration = serializers.IntegerField()
    component = serializers.IntegerField()
    score = serializers.CharField(allow_null=True, allow_blank=True, required=False)


class MarksInputSerializer(serializers.Serializer):
    marks = MarkEntrySerializer(many=True, max_length=20000)
    source = serializers.ChoiceField(choices=["manual", "import"], default="manual",
                                     help_text="Recorded in the audit log.")


class TransitionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class ResultSheetEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True, default=None)

    class Meta:
        model = ResultSheetEvent
        fields = ["id", "from_status", "to_status", "actor_name", "comment", "created_at"]


class ResultSheetListSerializer(serializers.ModelSerializer):
    offering = serializers.IntegerField(source="offering_id", read_only=True)
    course_code = serializers.CharField(source="offering.course.code", read_only=True)
    course_title = serializers.CharField(source="offering.course.title", read_only=True)
    semester_name = serializers.CharField(source="offering.semester.__str__", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    grading_scheme_name = serializers.CharField(source="grading_scheme.name", read_only=True)
    submitted_by_name = serializers.CharField(source="submitted_by.get_full_name", read_only=True, default=None)
    lecturers = serializers.SerializerMethodField()
    student_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = ResultSheet
        fields = ["id", "offering", "course_code", "course_title", "semester_name", "status", "status_display",
                  "grading_scheme", "grading_scheme_name", "submitted_by_name", "submitted_at", "approved_at",
                  "published_at", "last_comment", "lecturers", "student_count", "updated_at"]

    def get_lecturers(self, obj) -> list[str]:
        return [a.lecturer.get_full_name() for a in obj.offering.assignments.all()]


class AmendmentSerializer(serializers.ModelSerializer):
    student_number = serializers.CharField(source="course_result.student.student_number", read_only=True)
    student_name = serializers.CharField(source="course_result.student.full_name", read_only=True)
    course_code = serializers.CharField(source="course_result.offering.course.code", read_only=True)
    semester_name = serializers.CharField(source="course_result.offering.semester.__str__", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.get_full_name", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.get_full_name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ResultAmendment
        fields = ["id", "course_result", "student_number", "student_name", "course_code", "semester_name",
                  "original_marks", "new_marks", "original_total", "original_grade", "new_total", "new_grade",
                  "reason", "status", "status_display", "requested_by_name", "reviewed_by_name", "reviewed_at",
                  "review_comment", "created_at"]
        read_only_fields = fields


class AmendmentRequestSerializer(serializers.Serializer):
    course_result = serializers.IntegerField()
    new_marks = serializers.DictField(child=serializers.CharField(allow_null=True, allow_blank=True))
    reason = serializers.CharField(max_length=4000)


class AmendmentReviewSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class OpenResitSerializer(serializers.Serializer):
    offering = serializers.IntegerField()
    registration_deadline = serializers.DateTimeField(required=False, allow_null=True)


class ResitScoreSerializer(serializers.Serializer):
    entry = serializers.IntegerField()
    score = serializers.CharField(allow_null=True, allow_blank=True, required=False)


class ResitScoresInputSerializer(serializers.Serializer):
    scores = ResitScoreSerializer(many=True, max_length=5000)


class ResitRegisterSerializer(serializers.Serializer):
    entries = serializers.ListField(child=serializers.IntegerField(), allow_empty=False, max_length=5000)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ResitSheetListSerializer(serializers.ModelSerializer):
    offering = serializers.IntegerField(source="offering_id", read_only=True)
    course_code = serializers.CharField(source="offering.course.code", read_only=True)
    course_title = serializers.CharField(source="offering.course.title", read_only=True)
    semester_name = serializers.CharField(source="offering.semester.__str__", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    eligible_count = serializers.IntegerField(read_only=True, required=False)
    registered_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = ResitSheet
        fields = ["id", "offering", "course_code", "course_title", "semester_name", "status", "status_display",
                  "registration_deadline", "eligible_count", "registered_count", "submitted_at", "approved_at",
                  "published_at", "last_comment", "created_at"]


class MyResitSerializer(serializers.ModelSerializer):
    sheet = serializers.IntegerField(source="sheet_id", read_only=True)
    course_code = serializers.CharField(source="sheet.offering.course.code", read_only=True)
    course_title = serializers.CharField(source="sheet.offering.course.title", read_only=True)
    semester_name = serializers.CharField(source="sheet.offering.semester.__str__", read_only=True)
    registration_deadline = serializers.DateTimeField(source="sheet.registration_deadline", read_only=True)
    sheet_status = serializers.CharField(source="sheet.status", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    new_grade = serializers.SerializerMethodField()

    class Meta:
        model = ResitEntry
        fields = ["id", "sheet", "course_code", "course_title", "semester_name", "registration_deadline",
                  "sheet_status", "status", "status_display", "original_total", "original_grade", "new_grade"]

    def get_new_grade(self, obj) -> str | None:
        # The new grade is only revealed once the resit results are published.
        return obj.new_grade if obj.status == "COMPLETED" else None
