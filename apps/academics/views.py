from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django_filters import rest_framework as django_filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import STAFF_ROLES, Role
from apps.audit.mixins import AuditedModelMixin
from apps.audit.services import log_action
from apps.notifications import events as notify_events
from common.exceptions import DomainError
from common.importing import preview_payload, records_from_confirm, records_from_upload
from common.permissions import RoleBasedPermission
from common.tabular import requested_format, table_response

from . import importing

from .models import (
    AcademicYear, Course, CourseOffering, Department, LecturerAssignment, Level, OfferingStatus, Programme,
    ProgrammeCourse, Semester,
)
from .serializers import (
    AcademicYearSerializer, AssignLecturersSerializer, CourseOfferingSerializer, CourseSerializer,
    DepartmentSerializer, GenerateOfferingsSerializer, LevelSerializer, ProgrammeCourseSerializer,
    ProgrammeSerializer, SemesterSerializer,
)

ADMIN_ONLY = (Role.ADMIN,)
ACADEMIC_ADMINS = (Role.ADMIN, Role.REGISTRAR)


class AcademicViewSet(AuditedModelMixin, viewsets.ModelViewSet):
    permission_classes = [RoleBasedPermission]
    read_roles = STAFF_ROLES
    write_roles = ADMIN_ONLY


class AcademicYearViewSet(AcademicViewSet):
    serializer_class = AcademicYearSerializer
    filterset_fields = ["status"]
    search_fields = ["name"]
    ordering_fields = ["start_date", "name"]
    audit_name = "academic_year"

    def get_queryset(self):
        return AcademicYear.objects.annotate(semester_count=Count("semesters")).order_by("-start_date")


class SemesterFilter(django_filters.FilterSet):
    class Meta:
        model = Semester
        fields = ["academic_year", "status", "is_current"]


class SemesterViewSet(AcademicViewSet):
    serializer_class = SemesterSerializer
    filterset_class = SemesterFilter
    search_fields = ["name", "academic_year__name"]
    ordering_fields = ["start_date", "term_number"]
    write_roles = ACADEMIC_ADMINS
    action_roles = {"destroy": ADMIN_ONLY, "set_current": ADMIN_ONLY}
    audit_name = "semester"

    def get_queryset(self):
        return Semester.objects.select_related("academic_year")

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="set-current")
    @transaction.atomic
    def set_current(self, request, pk=None):
        semester = self.get_object()
        previous = Semester.objects.filter(is_current=True).exclude(pk=semester.pk).first()
        Semester.objects.filter(is_current=True).exclude(pk=semester.pk).update(is_current=False)
        semester.is_current = True
        semester.save(update_fields=["is_current", "updated_at"])
        log_action("semester.set_current", actor=request.user, instance=semester,
                   summary=f"Set current semester to {semester}",
                   before={"current_semester": previous.pk if previous else None},
                   after={"current_semester": semester.pk})
        return Response(self.get_serializer(semester).data)


class DepartmentViewSet(AcademicViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name"]
    audit_name = "department"


class LevelViewSet(AcademicViewSet):
    queryset = Level.objects.all()
    serializer_class = LevelSerializer
    read_roles = (*STAFF_ROLES, Role.STUDENT)
    pagination_class = None
    ordering_fields = ["order"]
    audit_name = "level"


class ProgrammeViewSet(AcademicViewSet):
    serializer_class = ProgrammeSerializer
    filterset_fields = ["department", "award_type", "is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name"]
    audit_name = "programme"

    def get_queryset(self):
        return (Programme.objects.select_related("department").prefetch_related("levels")
                .annotate(student_count=Count("students", filter=Q(students__status="ACTIVE"))).order_by("name"))


class CourseViewSet(AcademicViewSet):
    serializer_class = CourseSerializer
    action_roles = {"import_preview": ADMIN_ONLY, "import_confirm": ADMIN_ONLY, "import_template": ADMIN_ONLY}
    filterset_fields = ["department", "course_type", "is_active"]
    search_fields = ["code", "title"]
    ordering_fields = ["code", "title", "credits"]
    audit_name = "course"

    def get_queryset(self):
        return Course.objects.select_related("department").prefetch_related("prerequisites")

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}},
                   responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="import/preview", parser_classes=[MultiPartParser, FormParser])
    def import_preview(self, request):
        records = records_from_upload(request, max_rows=2000, required_columns=importing.REQUIRED)
        return Response(preview_payload(importing.validate_rows(records)))

    @extend_schema(request={"application/json": {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object"}}}}},
                   responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="import/confirm")
    def import_confirm(self, request):
        rows = records_from_confirm(request, max_rows=2000)
        created, placements = importing.import_courses(rows, actor=request.user)
        return Response({"created": created, "placements": placements}, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("file_format", str)], responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path="import/template")
    def import_template(self, request):
        return table_response("course-import-template", importing.COLUMNS, importing.TEMPLATE_ROWS, requested_format(request))


class ProgrammeCourseViewSet(AcademicViewSet):
    serializer_class = ProgrammeCourseSerializer
    filterset_fields = ["programme", "level", "term_number", "category", "course"]
    search_fields = ["course__code", "course__title"]
    ordering_fields = ["level__order", "term_number", "course__code"]
    audit_name = "programme_course"

    def get_queryset(self):
        return ProgrammeCourse.objects.select_related("course", "level", "programme")


class CourseOfferingFilter(django_filters.FilterSet):
    programme = django_filters.NumberFilter(field_name="programmes", distinct=True)
    level = django_filters.NumberFilter(field_name="levels", distinct=True)
    department = django_filters.NumberFilter(field_name="course__department")
    lecturer = django_filters.NumberFilter(field_name="assignments__lecturer", distinct=True)
    current = django_filters.BooleanFilter(field_name="semester__is_current")

    class Meta:
        model = CourseOffering
        fields = ["semester", "course", "status"]


class CourseOfferingViewSet(AcademicViewSet):
    serializer_class = CourseOfferingSerializer
    filterset_class = CourseOfferingFilter
    search_fields = ["course__code", "course__title"]
    ordering_fields = ["course__code", "course__title", "enrolled_count"]
    write_roles = ACADEMIC_ADMINS
    audit_name = "course_offering"

    def get_queryset(self):
        queryset = (
            CourseOffering.objects.select_related("course__department", "semester__academic_year", "result_sheet")
            .prefetch_related(Prefetch("assignments", queryset=LecturerAssignment.objects.select_related("lecturer")),
                              "programmes", "levels")
            .annotate(enrolled_count=Count("registrations", filter=Q(registrations__status="ACTIVE"), distinct=True))
            .order_by("course__code", "id")
        )
        user = self.request.user
        if getattr(user, "teaches_only", False):
            # Lecturers only ever see offerings they are assigned to.
            queryset = queryset.filter(assignments__lecturer=user)
        return queryset

    @extend_schema(request=AssignLecturersSerializer, responses=CourseOfferingSerializer)
    @action(detail=True, methods=["put"], url_path="lecturers")
    @transaction.atomic
    def assign_lecturers(self, request, pk=None):
        offering = self.get_object()
        serializer = AssignLecturersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = sorted(offering.assignments.values_list("lecturer_id", flat=True))
        offering.assignments.all().delete()
        LecturerAssignment.objects.bulk_create([
            LecturerAssignment(offering=offering, lecturer=item["lecturer"], is_coordinator=item["is_coordinator"])
            for item in serializer.validated_data["lecturers"]
        ])
        after = sorted(item["lecturer"].pk for item in serializer.validated_data["lecturers"])
        log_action("course_offering.lecturers_assigned", actor=request.user, instance=offering,
                   summary=f"Updated lecturers for {offering}", before={"lecturers": before},
                   after={"lecturers": after})
        notify_events.lecturers_assigned(offering, sorted(set(after) - set(before)), request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=offering.pk)).data)

    @extend_schema(request=GenerateOfferingsSerializer)
    @action(detail=False, methods=["post"])
    @transaction.atomic
    def generate(self, request):
        """Create offerings for every curriculum course scheduled in the semester's term."""
        serializer = GenerateOfferingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        semester = serializer.validated_data["semester"]
        links = ProgrammeCourse.objects.filter(term_number=semester.term_number, course__is_active=True,
                                               programme__is_active=True).select_related("course", "level")
        if serializer.validated_data.get("programme"):
            links = links.filter(programme=serializer.validated_data["programme"])
        if not links.exists():
            raise DomainError("No curriculum courses are scheduled for this term number.")
        created = 0
        for link in links:
            offering, was_created = CourseOffering.objects.get_or_create(
                course=link.course, semester=semester, defaults={"status": OfferingStatus.OPEN}
            )
            offering.programmes.add(link.programme_id)
            offering.levels.add(link.level_id)
            created += int(was_created)
        log_action("course_offering.generated", actor=request.user, instance=semester,
                   summary=f"Generated {created} offerings for {semester}")
        return Response({"created": created, "linked": links.count()})


class CurrentPeriodView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=SemesterSerializer)
    def get(self, request):
        semester = Semester.objects.select_related("academic_year").filter(is_current=True).first()
        if semester is None:
            return Response({"semester": None})
        return Response({"semester": SemesterSerializer(semester).data})
