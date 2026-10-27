from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.accounts.models import Role, User
from common.serializers import ModelCleanMixin, UpperCaseCharField

from .models import (
    AcademicYear, Course, CourseOffering, Department, LecturerAssignment, Level, PeriodStatus, Programme,
    ProgrammeCourse, Semester,
)


class ClosedPeriodGuardMixin:
    """Closed academic periods are historical: only their status may change."""

    def validate(self, attrs):
        if self.instance is not None and self.instance.status == PeriodStatus.CLOSED:
            changed = {key for key, value in attrs.items() if getattr(self.instance, key, None) != value}
            if changed - {"status"}:
                raise serializers.ValidationError("This period is closed. Re-open it before editing its details.")
        return super().validate(attrs)


class AcademicYearSerializer(ClosedPeriodGuardMixin, ModelCleanMixin, serializers.ModelSerializer):
    semester_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = AcademicYear
        fields = ["id", "name", "start_date", "end_date", "status", "semester_count", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class SemesterSerializer(ClosedPeriodGuardMixin, ModelCleanMixin, serializers.ModelSerializer):
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    registration_open = serializers.SerializerMethodField()
    amendments_open = serializers.SerializerMethodField()

    class Meta:
        model = Semester
        fields = [
            "id", "academic_year", "academic_year_name", "name", "term_number", "start_date", "end_date",
            "registration_start", "registration_end", "add_drop_deadline", "result_entry_deadline",
            "result_publication_date", "status", "is_current", "registration_open", "amendments_open",
            "created_at", "updated_at",
        ]
        read_only_fields = ["is_current", "created_at", "updated_at"]

    def get_registration_open(self, obj) -> bool:
        return obj.registration_is_open()

    def get_amendments_open(self, obj) -> bool:
        return obj.amendments_are_open()

    def validate_academic_year(self, year):
        if year.status == PeriodStatus.CLOSED:
            raise serializers.ValidationError("Cannot add or move semesters into a closed academic year.")
        return year


class DepartmentSerializer(serializers.ModelSerializer):
    code = UpperCaseCharField(max_length=20, validators=[UniqueValidator(Department.objects.all(), message="This code is already in use.")])
    class Meta:
        model = Department
        fields = ["id", "code", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class LevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Level
        fields = ["id", "name", "order"]


class ProgrammeSerializer(ModelCleanMixin, serializers.ModelSerializer):
    code = UpperCaseCharField(max_length=20, validators=[UniqueValidator(Programme.objects.all(), message="This code is already in use.")])
    department_name = serializers.CharField(source="department.name", read_only=True)
    award_type_display = serializers.CharField(source="get_award_type_display", read_only=True)
    student_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Programme
        fields = [
            "id", "code", "name", "department", "department_name", "award_type", "award_type_display",
            "duration_years", "levels", "required_credits", "min_credits_per_semester",
            "max_credits_per_semester", "is_active", "student_count", "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class CourseSerializer(serializers.ModelSerializer):
    code = UpperCaseCharField(max_length=20, validators=[UniqueValidator(Course.objects.all(), message="This code is already in use.")])
    department_name = serializers.CharField(source="department.name", read_only=True)
    prerequisite_codes = serializers.SlugRelatedField(source="prerequisites", slug_field="code", many=True, read_only=True)

    class Meta:
        model = Course
        fields = [
            "id", "code", "title", "description", "credits", "department", "department_name", "course_type",
            "prerequisites", "prerequisite_codes", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {"prerequisites": {"required": False}}

    def validate_prerequisites(self, prerequisites):
        if self.instance is None:
            return prerequisites
        if any(course.pk == self.instance.pk for course in prerequisites):
            raise serializers.ValidationError("A course cannot be its own prerequisite.")
        # Reject cycles: none of the new prerequisites may (transitively) require this course.
        seen, frontier = set(), [course.pk for course in prerequisites]
        while frontier:
            current = frontier.pop()
            if current == self.instance.pk:
                raise serializers.ValidationError("These prerequisites would create a circular requirement.")
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(Course.prerequisites.through.objects.filter(from_course_id=current)
                            .values_list("to_course_id", flat=True))
        return prerequisites


class ProgrammeCourseSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    course_credits = serializers.IntegerField(source="course.credits", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)

    class Meta:
        model = ProgrammeCourse
        fields = [
            "id", "programme", "course", "course_code", "course_title", "course_credits", "level", "level_name",
            "term_number", "category",
        ]
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=ProgrammeCourse.objects.all(), fields=["programme", "course"],
                message="This course is already part of the programme.",
            )
        ]

    def validate(self, attrs):
        programme = attrs.get("programme") or self.instance.programme
        level = attrs.get("level") or self.instance.level
        if programme.levels.exists() and not programme.levels.filter(pk=level.pk).exists():
            raise serializers.ValidationError({"level": "This level is not part of the programme's level structure."})
        return attrs


class LecturerAssignmentSerializer(serializers.ModelSerializer):
    lecturer_name = serializers.CharField(source="lecturer.get_full_name", read_only=True)
    lecturer_email = serializers.CharField(source="lecturer.email", read_only=True)

    class Meta:
        model = LecturerAssignment
        fields = ["id", "lecturer", "lecturer_name", "lecturer_email", "is_coordinator"]


class CourseOfferingSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    credits = serializers.IntegerField(source="course.credits", read_only=True)
    department_name = serializers.CharField(source="course.department.name", read_only=True)
    semester_name = serializers.CharField(source="semester.__str__", read_only=True)
    assignments = LecturerAssignmentSerializer(many=True, read_only=True)
    enrolled_count = serializers.IntegerField(read_only=True, required=False)
    result_status = serializers.SerializerMethodField()

    class Meta:
        model = CourseOffering
        fields = [
            "id", "course", "course_code", "course_title", "credits", "department_name", "semester", "semester_name",
            "programmes", "levels", "capacity", "status", "notes", "assignments", "enrolled_count",
            "result_status", "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=CourseOffering.objects.all(), fields=["course", "semester"],
                message="This course is already offered in the selected semester.",
            )
        ]

    def get_result_status(self, obj) -> str | None:
        sheet = getattr(obj, "result_sheet", None)
        return sheet.status if sheet else None

    def validate_semester(self, semester):
        if semester.status == PeriodStatus.CLOSED:
            raise serializers.ValidationError("Cannot add offerings to a closed semester.")
        return semester

    def validate(self, attrs):
        if self.instance is not None and "semester" in attrs and attrs["semester"] != self.instance.semester:
            if self.instance.registrations.exists():
                raise serializers.ValidationError({"semester": "Students are registered; the semester cannot change."})
        return attrs


class LecturerAssignmentInputSerializer(serializers.Serializer):
    lecturer = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role=Role.LECTURER, is_active=True))
    is_coordinator = serializers.BooleanField(default=False)


class AssignLecturersSerializer(serializers.Serializer):
    lecturers = LecturerAssignmentInputSerializer(many=True)

    def validate_lecturers(self, items):
        ids = [item["lecturer"].pk for item in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each lecturer may only be listed once.")
        return items


class GenerateOfferingsSerializer(serializers.Serializer):
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.exclude(status=PeriodStatus.CLOSED))
    programme = serializers.PrimaryKeyRelatedField(queryset=Programme.objects.filter(is_active=True), required=False)
