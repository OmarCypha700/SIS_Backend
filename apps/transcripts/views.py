from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.students.models import Student
from common import pdf
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission
from common.tabular import requested_format, table_response

from . import services
from .models import IssuedTranscript
from .serializers import IssuedTranscriptSerializer, IssueTranscriptSerializer, RevokeTranscriptSerializer

REGISTRY = (Role.ADMIN, Role.REGISTRAR)


class TranscriptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    The issuance register. Registry staff can view it; issuing, printing and revoking need the
    `can_issue_transcripts` permission (administrators by default).
    """

    permission_classes = [RoleBasedPermission]
    read_roles = REGISTRY
    write_roles = REGISTRY
    action_roles = {"preview": REGISTRY, "pdf": REGISTRY, "revoke": REGISTRY, "export": REGISTRY}
    serializer_class = IssuedTranscriptSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["issued_at", "serial"]

    def get_queryset(self):
        queryset = IssuedTranscript.objects.select_related("student__programme", "issued_by", "revoked_by")
        params = self.request.query_params
        if params.get("student"):
            queryset = queryset.filter(student_id=params["student"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(
                Q(serial__icontains=term) | Q(student__student_number__icontains=term)
                | Q(student__first_name__icontains=term) | Q(student__last_name__icontains=term)
                | Q(purpose__icontains=term) | Q(verification_code=services.normalise_code(term)))
        return queryset

    @extend_schema(parameters=[OpenApiParameter("student", int), OpenApiParameter("status", str),
                               OpenApiParameter("search", str)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(parameters=[OpenApiParameter("student", int, required=True)], responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"])
    def preview(self, request):
        """What an issue would contain, and whether the current user may issue."""
        student = _student(request.query_params.get("student"))
        return Response({**services.preview(student), "user_can_issue": services.can_issue(request.user)})

    @extend_schema(request=IssueTranscriptSerializer, responses={201: IssuedTranscriptSerializer})
    def create(self, request):
        payload = IssueTranscriptSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        transcript = services.issue(_student(data["student"]), purpose=data["purpose"], signing=data["signing"],
                                    actor=request.user)
        return Response(IssuedTranscriptSerializer(transcript).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """The official PDF. Every download is counted; the second and later copies say 'Reprint'."""
        transcript = services.record_print(self.get_object(), actor=request.user)
        content = pdf.official_transcript(transcript, verify_url=services.verification_url(transcript.verification_code),
                                          copy_number=transcript.print_count)
        filename = f"transcript-{transcript.serial.replace('/', '-')}.pdf"
        return pdf.pdf_response(content, filename)

    @extend_schema(request=RevokeTranscriptSerializer, responses=IssuedTranscriptSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        payload = RevokeTranscriptSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        transcript = services.revoke(self.get_object(), reason=payload.validated_data["reason"], actor=request.user)
        return Response(IssuedTranscriptSerializer(transcript).data)

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"], url_path="record-changed")
    def record_changed(self, request, pk=None):
        return Response({"changed": services.record_changed_since_issue(self.get_object())})

    @extend_schema(parameters=[OpenApiParameter("file_format", str)], responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"])
    def export(self, request):
        rows = [[t.serial, t.student.student_number, t.student.full_name, t.purpose, "Yes" if t.is_interim else "No",
                 t.get_status_display(), t.issued_by.get_full_name(), t.issued_at.strftime("%Y-%m-%d %H:%M"),
                 t.print_count, t.revoke_reason] for t in self.get_queryset()]
        header = ["Serial", "Student number", "Name", "Purpose", "Interim", "Status", "Issued by", "Issued at",
                  "Copies printed", "Revocation reason"]
        return table_response("transcript-register", header, rows, requested_format(request))


def _student(pk):
    student = Student.objects.select_related("programme", "current_level").filter(pk=pk).first() if pk else None
    if student is None:
        raise DomainError("Student not found.", code="not_found", status_code=404)
    return student


class VerifyTranscriptView(APIView):
    """Public check of a transcript by the code printed on it. Rate-limited; codes cannot be guessed."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "transcript_verify"

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request, code):
        transcript = (IssuedTranscript.objects.filter(verification_code=services.normalise_code(code))
                      .select_related("student").first())
        if transcript is None:
            raise DomainError("No transcript matches this code. Check the code printed under the QR code.",
                              code="not_found", status_code=404)
        return Response(services.public_summary(transcript))
