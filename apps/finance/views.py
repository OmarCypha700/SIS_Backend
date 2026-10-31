from django.db.models import Exists, OuterRef, Q, Subquery
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.academics.models import Semester
from apps.accounts.models import Role
from apps.institutions.models import InstitutionProfile
from apps.students.models import StatusPolicy, Student
from common.bulk import BulkIdsField
from common.exceptions import DomainError
from common.importing import records_from_upload
from common.permissions import RoleBasedPermission
from common.tabular import table_response

from . import services
from .models import FinancialClearance

FINANCE = (Role.ADMIN, Role.ACCOUNTS_OFFICER)


class SetClearanceSerializer(serializers.Serializer):
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    students = BulkIdsField()
    cleared = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255,
                                 help_text="Receipt number, payment plan or sponsor; required when revoking.")


def _semester(value):
    if value in (None, "", "current"):
        semester = Semester.objects.select_related("academic_year").filter(is_current=True).first()
        if semester is None:
            raise DomainError("No current semester has been set.", code="no_current_semester", status_code=404)
        return semester
    try:
        return Semester.objects.select_related("academic_year").get(pk=int(value))
    except (Semester.DoesNotExist, ValueError):
        raise DomainError("Semester not found.", code="not_found", status_code=404) from None


class FinancialClearanceViewSet(viewsets.GenericViewSet):
    """
    The Accounts Office's list of students and their clearance for a semester. Only identity,
    programme, level and status are shown: no results, contact details or photos.
    """

    serializer_class = SetClearanceSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = (*FINANCE, Role.REGISTRAR)
    write_roles = FINANCE
    action_roles = {"template": (*FINANCE, Role.REGISTRAR)}
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _students(self, request, semester):
        allowed = StatusPolicy.objects.filter(can_register=True).values_list("status", flat=True)
        clearance = FinancialClearance.objects.filter(student=OuterRef("pk"), semester=semester)
        queryset = (Student.objects.filter(is_archived=False, status__in=allowed)
                    .select_related("programme", "current_level")
                    .annotate(cleared=Exists(clearance.filter(cleared=True)),
                              clearance_note=Subquery(clearance.values("note")[:1]),
                              decided_at=Subquery(clearance.values("decided_at")[:1]))
                    .order_by("programme__code", "current_level__order", "last_name", "first_name"))
        params = request.query_params
        for key, field in (("programme", "programme_id"), ("level", "current_level_id")):
            if params.get(key):
                queryset = queryset.filter(**{field: params[key]})
        if params.get("cleared") in ("true", "false"):
            queryset = queryset.filter(cleared=params["cleared"] == "true")
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(Q(student_number__icontains=term) | Q(first_name__icontains=term)
                                       | Q(last_name__icontains=term))
        return queryset

    @extend_schema(parameters=[OpenApiParameter(name, str) for name in
                               ("semester", "programme", "level", "cleared", "search")],
                   responses=OpenApiTypes.OBJECT)
    def list(self, request):
        semester = _semester(request.query_params.get("semester"))
        queryset = self._students(request, semester)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response([
            {"id": s.id, "student_number": s.student_number, "full_name": s.full_name,
             "programme_code": s.programme.code, "level_name": s.current_level.name, "status": s.status,
             "cleared": s.cleared, "note": s.clearance_note or "", "decided_at": s.decided_at}
            for s in page
        ])

    @extend_schema(parameters=[OpenApiParameter("semester", str)], responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        semester = _semester(request.query_params.get("semester"))
        students = self._students(request, semester)
        total = students.count()
        cleared = students.filter(cleared=True).count()
        return Response({"semester": {"id": semester.id, "name": str(semester)}, "total": total, "cleared": cleared,
                         "not_cleared": total - cleared,
                         "required": InstitutionProfile.load().require_financial_clearance})

    @extend_schema(request=SetClearanceSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="set")
    def set_clearance(self, request):
        """Clear or revoke the selected students for the semester."""
        payload = SetClearanceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        students = list(Student.objects.filter(pk__in=data["students"]))
        changed = services.set_clearance(students, data["semester"], cleared=data["cleared"],
                                         note=data.get("note", ""), actor=request.user)
        return Response({"changed": len(changed), "selected": len(students)})

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {
        "file": {"type": "string", "format": "binary"}, "semester": {"type": "integer"}}}},
        responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="import")
    def import_file(self, request):
        """Spreadsheet from the accounting system: student_number, optional cleared (yes/no) and note."""
        semester = _semester(request.data.get("semester"))
        records = records_from_upload(request, max_rows=5000, required_columns=("student_number",))
        return Response(services.import_rows(records, semester, actor=request.user))

    @extend_schema(parameters=[OpenApiParameter("file_format", str)], responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"])
    def template(self, request):
        return table_response("clearance-import-template", ["student_number", "cleared", "note"],
                              [["NCHT/26/0001", "yes", "Receipt 004512"], ["NCHT/26/0002", "no", "Balance outstanding"]],
                              request.query_params.get("file_format", "csv"))
