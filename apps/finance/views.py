import json
from decimal import Decimal

from django.db.models import Count, DecimalField, Exists, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import Semester
from apps.accounts.models import Role
from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from apps.students.models import StatusPolicy, Student
from common import pdf
from common.bulk import BulkIdsField
from common.exceptions import DomainError
from common.importing import records_from_upload
from common.permissions import RoleBasedPermission
from common.tabular import table_response

from . import accounts, fees, imports, paystack, services
from .models import EntrySide, FeeSchedule, FinancialClearance, LedgerEntry, Payment, PaymentSource, PaymentStatus
from .serializers import (
    BillSerializer, CopySchedulesSerializer, EntrySerializer, FeeScheduleSerializer, ImportConfirmSerializer,
    ManualPaymentSerializer,
    MyPaymentSerializer, PaymentSerializer, PayRequestSerializer, ReverseSerializer,
)

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


# --- Fees, student accounts and payments ---------------------------------------------------

def _student_or_404(pk):
    student = (Student.objects.select_related("programme", "current_level", "user").filter(pk=pk).first()
               if str(pk).isdigit() else None)
    if student is None:
        raise DomainError("Student not found.", code="not_found", status_code=404)
    return student


def _requirement(student):
    semester = accounts.current_semester()
    if semester is None:
        return {"billed": False, "meets": False, "balance": accounts.balance(student)}
    return accounts.requirement(student, semester)


def _with_balance(queryset):
    return queryset.annotate(balance=Coalesce(Sum("ledger_entries__amount"), Value(Decimal("0")),
                                              output_field=DecimalField(max_digits=12, decimal_places=2)))


class FeeScheduleViewSet(viewsets.ModelViewSet):
    """Fees per programme, level and period, with their items. Billing charges them to the students."""

    serializer_class = FeeScheduleSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = FINANCE
    write_roles = FINANCE

    def get_queryset(self):
        queryset = (FeeSchedule.objects.select_related("programme", "level", "academic_year", "semester")
                    .prefetch_related("items")
                    .annotate(billed_students=Count("items__entries__student", distinct=True))
                    # The model's default ordering is dropped by the GROUP BY above: page through in a fixed order.
                    .order_by("-academic_year__start_date", "semester__term_number", "programme__code", "level__order",
                              "pk"))
        params = self.request.query_params
        for key in ("academic_year", "semester", "programme", "level"):
            if params.get(key):
                queryset = queryset.filter(**{f"{key}_id": params[key]})
        return queryset

    def perform_destroy(self, instance):
        if LedgerEntry.objects.filter(fee_item__schedule=instance).exists():
            raise DomainError("These fees have been billed to students and cannot be deleted. Reverse the charges on "
                              "the students' accounts first.", code="schedule_billed", status_code=409)
        log_action("finance.fee_schedule_deleted", actor=self.request.user, instance=instance,
                   summary=f"Deleted fees for {instance}")
        instance.delete()

    @extend_schema(request=BillSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        """Preview (dry_run, the default) or charge these fees to every student of the programme and level."""
        payload = BillSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(fees.bill(self.get_object(), actor=request.user, dry_run=payload.validated_data["dry_run"],
                                  student_ids=payload.validated_data.get("students")))

    @extend_schema(request=CopySchedulesSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"])
    def copy(self, request):
        """Copy one academic year's fees to another as a starting point."""
        payload = CopySchedulesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        return Response({"created": fees.copy_schedules(data["source_year"], data["target_year"], actor=request.user)})


class StudentAccountViewSet(viewsets.GenericViewSet):
    """Each student's fees account: balance, statement and the Accounts Office's entries."""

    serializer_class = ManualPaymentSerializer
    queryset = Student.objects.none()  # for the schema; list() and _student_or_404 build the real queries
    lookup_value_regex = r"\d+"
    permission_classes = [RoleBasedPermission]
    read_roles = FINANCE
    write_roles = FINANCE
    parser_classes = [JSONParser]

    @extend_schema(parameters=[OpenApiParameter(n, str) for n in ("programme", "level", "owing", "search", "ordering")],
                   responses=OpenApiTypes.OBJECT, operation_id="student_accounts_list")
    def list(self, request):
        params = request.query_params
        queryset = _with_balance(Student.objects.filter(is_archived=False).select_related("programme", "current_level"))
        for key, field in (("programme", "programme_id"), ("level", "current_level_id")):
            if params.get(key):
                queryset = queryset.filter(**{field: params[key]})
        if params.get("owing") == "true":
            queryset = queryset.filter(balance__gt=0)
        elif params.get("owing") == "false":
            queryset = queryset.filter(balance__lte=0)
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(Q(student_number__icontains=term) | Q(first_name__icontains=term)
                                       | Q(last_name__icontains=term))
        ordering = {"balance": "balance", "-balance": "-balance"}.get(params.get("ordering"))
        queryset = queryset.order_by(*([ordering] if ordering else []), "last_name", "first_name")
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response([
            {"id": s.id, "student_number": s.student_number, "full_name": s.full_name, "programme_code": s.programme.code,
             "level_name": s.current_level.name, "status": s.status, "balance": accounts.money(s.balance)}
            for s in page])

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        student = _student_or_404(pk)
        return Response({
            "student": {"id": student.id, "student_number": student.student_number, "full_name": student.full_name,
                        "programme": student.programme.name, "programme_code": student.programme.code,
                        "level": student.current_level.name, "status": student.status},
            "summary": _requirement(student),
            "statement": accounts.statement(student),
            "payments": PaymentSerializer(student.payments.select_related("recorded_by", "student__programme")[:100],
                                          many=True).data,
        })

    @extend_schema(request=ManualPaymentSerializer, responses=PaymentSerializer)
    @action(detail=True, methods=["post"], url_path="record-payment")
    def record_payment(self, request, pk=None):
        """A payment made at the bank or cashier."""
        student = _student_or_404(pk)
        payload = ManualPaymentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        payment = accounts.record_manual_payment(student, actor=request.user, **payload.validated_data)
        return Response(PaymentSerializer(payment).data, status=201)

    @extend_schema(request=EntrySerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def entry(self, request, pk=None):
        """A waiver or scholarship, a refund, or a correction to the bill."""
        student = _student_or_404(pk)
        payload = EntrySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        entry = accounts.add_entry(student, actor=request.user, **payload.validated_data)
        return Response({"id": entry.id, "balance": accounts.balance(student)}, status=201)

    @extend_schema(request=ReverseSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        """Undo an entry made in error. The original stays on the statement."""
        student = _student_or_404(pk)
        payload = ReverseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        entry = LedgerEntry.objects.filter(pk=payload.validated_data["entry"], student=student).first()
        if entry is None:
            raise DomainError("Entry not found.", code="not_found", status_code=404)
        accounts.reverse_entry(entry, reason=payload.validated_data["reason"], actor=request.user)
        return Response({"balance": accounts.balance(student)})

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="statement-pdf")
    def statement_pdf(self, request, pk=None):
        return _statement_pdf(_student_or_404(pk))


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    """Online and recorded payments, for the Accounts Office."""

    serializer_class = PaymentSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = FINANCE
    write_roles = FINANCE
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = Payment.objects.select_related("student__programme", "recorded_by")
        params = self.request.query_params
        for key in ("status", "source", "student"):
            if params.get(key):
                queryset = queryset.filter(**{key: params[key]})
        for key, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
            value = parse_date(params.get(key) or "")
            if value:
                queryset = queryset.filter(**{lookup: value})
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(Q(reference__icontains=term) | Q(receipt_number__icontains=term)
                                       | Q(student__student_number__icontains=term)
                                       | Q(student__last_name__icontains=term) | Q(student__first_name__icontains=term))
        return queryset

    @extend_schema(request=None, responses=PaymentSerializer)
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """Ask Paystack again about a pending payment."""
        payment = self.get_object()
        if payment.source == PaymentSource.ONLINE and payment.status == PaymentStatus.PENDING:
            payment = paystack.confirm(payment.reference, actor=request.user)
        return Response(PaymentSerializer(payment).data)

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"])
    def receipt(self, request, pk=None):
        return _receipt(self.get_object())

    @extend_schema(parameters=[OpenApiParameter("file_format", str)], responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"])
    def export(self, request):
        rows = [[timezone.localtime(p.paid_at).strftime("%Y-%m-%d %H:%M") if p.paid_at else "", p.receipt_number or "",
                 p.reference, p.student.student_number, p.student.full_name, p.get_source_display(), p.channel,
                 p.amount, p.fee, p.total, p.get_status_display(), p.note]
                for p in self.get_queryset()[:20000]]
        return table_response("payments", ["Paid at", "Receipt", "Reference", "Student number", "Name", "Source",
                                           "Method", "School fees", "Transaction fee", "Total", "Status", "Note"],
                              rows, request.query_params.get("file_format", "csv"), sheet_title="Payments")

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Figures for the Accounts Office dashboard."""
        today = timezone.localdate()
        success = Payment.objects.filter(status=PaymentStatus.SUCCESS)
        owing = _with_balance(Student.objects.filter(is_archived=False)).filter(balance__gt=0)
        stale = timezone.now() - paystack.CHECK_AFTER
        total = lambda qs, field="amount": accounts.money(qs.aggregate(s=Sum(field))["s"] or 0)  # noqa: E731
        return Response({
            "billed": total(LedgerEntry.objects.filter(side=EntrySide.BILL)),
            "collected": total(success),
            "collected_today": total(success.filter(paid_at__date=today)),
            "payments_today": success.filter(paid_at__date=today).count(),
            "outstanding": total(owing, "balance"),
            "students_owing": owing.count(),
            "needs_attention": Payment.objects.filter(Q(status=PaymentStatus.MISMATCH)
                                                      | Q(status=PaymentStatus.PENDING, created_at__lt=stale)).count(),
            "online_enabled": paystack.enabled(),
            "gateway_configured": paystack.configured(),
            "billing_period": InstitutionProfile.load().fee_billing_period,
            "registration_payment_percent": InstitutionProfile.load().registration_payment_percent,
        })

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {
        "file": {"type": "string", "format": "binary"}}}}, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="import/preview")
    def import_preview(self, request):
        """Check a bank statement before recording it: student_number, amount, reference, optional date, method, note."""
        records = records_from_upload(request, max_rows=2000, required_columns=("student_number", "amount", "reference"))
        return Response(imports.preview(records))

    @extend_schema(request=ImportConfirmSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="import/confirm", parser_classes=[JSONParser])
    def import_confirm(self, request):
        """Record the rows ticked in the preview. Every row is checked again; duplicates are never recorded."""
        payload = ImportConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(imports.confirm(payload.validated_data["rows"], actor=request.user))

    @extend_schema(parameters=[OpenApiParameter("file_format", str)], responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path="import-template")
    def import_template(self, request):
        return table_response("payments-import-template", ["student_number", "amount", "reference", "date", "method", "note"],
                              [["NCHT/26/0001", "1500.00", "GCB-0045821", "2026-09-02", "bank", "Paid at Tanoso branch"]],
                              request.query_params.get("file_format", "csv"))


def _receipt(payment):
    if payment.status != PaymentStatus.SUCCESS:
        raise DomainError("A receipt is only available for a successful payment.", code="no_receipt", status_code=409)
    return pdf.pdf_response(pdf.payment_receipt(payment), f"receipt-{payment.receipt_number.replace('/', '-')}.pdf")


def _statement_pdf(student):
    content = pdf.fee_statement(student, accounts.statement(student), {"balance": accounts.balance(student)})
    return pdf.pdf_response(content, f"fees-statement-{student.student_number.replace('/', '-')}.pdf")


class MyFeesViewSet(viewsets.ViewSet):
    """A student's own fees: what they owe, what they need to register, and paying online."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.STUDENT,)
    write_roles = (Role.STUDENT,)

    def _student(self, request):
        student = (Student.objects.select_related("programme", "current_level", "user")
                   .filter(user=request.user).first())
        if student is None:
            raise DomainError("No student record is linked to this account.", code="not_found", status_code=404)
        return student

    def _payment(self, request, reference):
        payment = (Payment.objects.select_related("student__programme", "student__current_level")
                   .filter(reference=reference, student=self._student(request)).first())
        if payment is None:
            raise DomainError("Payment not found.", code="not_found", status_code=404)
        return payment

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def list(self, request):
        student = self._student(request)
        profile = InstitutionProfile.load()
        return Response({
            "summary": _requirement(student),
            "statement": accounts.statement(student),
            "payments": MyPaymentSerializer(student.payments.exclude(status=PaymentStatus.ABANDONED)[:50],
                                            many=True).data,
            "online_enabled": paystack.enabled(profile),
            "fee_percent": profile.payment_fee_percent,
            "billing_period": profile.fee_billing_period,
            "clearance_required": profile.require_financial_clearance,
        })

    @extend_schema(parameters=[OpenApiParameter("amount", str)], responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def quote(self, request):
        """The transaction fee for an amount, shown before paying."""
        return Response(paystack.quote(self._student(request), request.query_params.get("amount", "")))

    @extend_schema(request=PayRequestSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"])
    def pay(self, request):
        payload = PayRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        payment, url = paystack.start_payment(self._student(request), payload.validated_data["amount"],
                                              actor=request.user)
        return Response({"reference": payment.reference, "authorization_url": url, "amount": payment.amount,
                         "fee": payment.fee, "total": payment.total}, status=201)

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path=r"payments/(?P<reference>[A-Za-z0-9\-]{6,64})/verify")
    def verify(self, request, reference=None):
        """Called when the student comes back from Paystack, or presses Check again."""
        payment = self._payment(request, reference)
        if payment.source == PaymentSource.ONLINE and payment.status == PaymentStatus.PENDING:
            payment = paystack.confirm(payment.reference, actor=request.user)
        semester = accounts.current_semester()
        return Response({"payment": MyPaymentSerializer(payment).data, "summary": _requirement(payment.student),
                         "cleared": bool(semester and services.is_cleared(payment.student, semester))})

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path=r"payments/(?P<reference>[A-Za-z0-9\-]{6,64})/receipt")
    def receipt(self, request, reference=None):
        return _receipt(self._payment(request, reference))

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path="statement-pdf")
    def statement_pdf(self, request):
        return _statement_pdf(self._student(request))


@extend_schema(exclude=True)
class PaystackWebhookView(APIView):
    """
    Paystack's server tells us a charge finished. The signature proves it came from Paystack; the
    payment is still confirmed with Paystack's API before anything is credited.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        raw = request.body
        if not paystack.valid_signature(raw, request.headers.get("x-paystack-signature", "")):
            return Response({"detail": "Invalid signature."}, status=401)
        try:
            event = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "Invalid body."}, status=400)
        paystack.handle_webhook(event)
        return Response({"received": True})
