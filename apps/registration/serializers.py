from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import Semester
from apps.students.models import Student

from .models import CourseRegistration, RegistrationEvent, SemesterRegistration


class CourseRegistrationSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="offering.course.code", read_only=True)
    course_title = serializers.CharField(source="offering.course.title", read_only=True)
    credits = serializers.IntegerField(source="offering.course.credits", read_only=True)

    class Meta:
        model = CourseRegistration
        fields = ["id", "offering", "course_code", "course_title", "credits", "category", "is_retake", "status",
                  "created_at", "dropped_at"]


class RegistrationEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True, default=None)
    course_code = serializers.CharField(source="offering.course.code", read_only=True, default=None)
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = RegistrationEvent
        fields = ["id", "action", "action_display", "course_code", "actor_name", "on_behalf", "reason", "created_at"]


class SemesterRegistrationListSerializer(serializers.ModelSerializer):
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    semester_name = serializers.CharField(source="semester.__str__", read_only=True)
    programme_code = serializers.CharField(source="programme.code", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    course_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = SemesterRegistration
        fields = ["id", "student", "student_number", "student_name", "semester", "semester_name", "programme_code",
                  "level_name", "status", "status_display", "total_credits", "course_count", "submitted_at",
                  "requires_approval_reason", "review_comment", "reviewed_at"]


class SemesterRegistrationDetailSerializer(SemesterRegistrationListSerializer):
    courses = serializers.SerializerMethodField()
    events = RegistrationEventSerializer(many=True, read_only=True)

    class Meta(SemesterRegistrationListSerializer.Meta):
        fields = SemesterRegistrationListSerializer.Meta.fields + ["courses", "events"]

    @extend_schema_field(CourseRegistrationSerializer(many=True))
    def get_courses(self, obj):
        courses = [c for c in obj.courses.all() if c.status == "ACTIVE" or self.context.get("include_dropped")]
        return CourseRegistrationSerializer(courses, many=True).data


class SubmitRegistrationSerializer(serializers.Serializer):
    offering_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=40)
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all(), required=False)


class AdminRegistrationSerializer(SubmitRegistrationSerializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    reason = serializers.CharField(max_length=2000)


class ReviewSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000)
