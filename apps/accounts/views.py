from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.db.models import Prefetch, Q
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django_filters import rest_framework as django_filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from common.throttles import LoginEmailThrottle, PasswordResetEmailThrottle
from rest_framework.views import APIView

from apps.audit.services import diff, log_action, snapshot
from apps.notifications import services as notifications
from common.bulk import run_bulk
from common.exceptions import DomainError
from common.importing import preview_payload, records_from_confirm, records_from_upload
from common.permissions import RoleBasedPermission
from common.tabular import requested_format, table_response

from . import importing
from . import services as account_services

from . import appointments as appointment_services
from .models import CAPABILITY_FIELDS, Appointment, Role, User
from .serializers import (
    AppointmentEndSerializer,
    AppointmentSerializer,
    AppointmentUpdateSerializer,
    AdminPasswordResetSerializer, CurrentUserSerializer, LecturerSummarySerializer, LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer, PasswordResetRequestSerializer, ReasonInputSerializer, UserBulkSerializer,
    UserSerializer, validate_new_password,
)

AUDITED_USER_FIELDS = ["role", "is_active", "email", *CAPABILITY_FIELDS]


def _me_queryset_user(user):
    return User.objects.select_related("student_profile__programme", "student_profile__current_level").get(pk=user.pk)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: None}, summary="Set the CSRF cookie")
    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle, LoginEmailThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses=CurrentUserSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, email=serializer.validated_data["email"].lower(),
                            password=serializer.validated_data["password"])
        if user is None:
            # Same message for unknown email, wrong password and disabled account.
            raise DomainError("Invalid email or password.", code="invalid_credentials")
        login(request, user)
        return Response(CurrentUserSerializer(_me_queryset_user(user)).data)


class LogoutView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=CurrentUserSerializer)
    def get(self, request):
        return Response(CurrentUserSerializer(_me_queryset_user(request.user)).data)

    @extend_schema(request=CurrentUserSerializer, responses=CurrentUserSerializer)
    def patch(self, request):
        serializer = CurrentUserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(CurrentUserSerializer(_me_queryset_user(request.user)).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_change"

    @extend_schema(request=PasswordChangeSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password", "updated_at"])
        update_session_auth_hash(request, user)
        log_action("auth.password_changed", actor=user, instance=user, summary="Changed own password")
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle, PasswordResetEmailThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetRequestSerializer, responses={200: None})
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"].lower(), is_active=True).first()
        queued = notifications.request_password_reset_email(user) if user is not None else []
        if queued:
            log_action("auth.password_reset_requested", actor=None, instance=user, summary="Password reset requested")
        # Always the same response, and the email goes out after it, so neither the body nor the
        # response time shows whether an account exists.
        response = Response({"detail": "If an account exists for that email, a reset link has been sent."})
        return notifications.send_after_response(response, queued)


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetConfirmSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(data["uid"])), is_active=True)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            user = None
        if user is None or not default_token_generator.check_token(user, data["token"]):
            raise DomainError("This reset link is invalid or has expired.", code="invalid_token")
        validate_new_password(data["new_password"], user)
        user.set_password(data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password", "updated_at"])
        log_action("auth.password_reset_completed", actor=user, instance=user, summary="Password reset completed")
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                  mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Staff account management. Student accounts are managed through /students/."""

    serializer_class = UserSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN,)
    write_roles = (Role.ADMIN,)
    action_roles = {"lecturers": (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)}  # others: ADMIN
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["role", "is_active"]
    search_fields = ["email", "first_name", "last_name", "staff_id"]
    ordering_fields = ["last_name", "email", "role", "date_joined", "last_login"]

    def get_queryset(self):
        return (User.objects.exclude(role=Role.STUDENT).order_by("last_name", "first_name")
                .prefetch_related(Prefetch("appointments", queryset=Appointment.objects.current(),
                                           to_attr="_current_appointments")))

    def perform_create(self, serializer):
        user = serializer.save()
        log_action("user.created", actor=self.request.user, instance=user,
                   summary=f"Created {user.get_role_display()} account {user.email}",
                   after=snapshot(user, fields=AUDITED_USER_FIELDS))

    def perform_update(self, serializer):
        before = snapshot(serializer.instance, fields=AUDITED_USER_FIELDS)
        user = serializer.save()
        before, after = diff(before, snapshot(user, fields=AUDITED_USER_FIELDS))
        if after:
            log_action("user.updated", actor=self.request.user, instance=user,
                       summary=f"Updated account {user.email}", before=before, after=after)

    def _set_active(self, request, active):
        user = self.get_object()
        if user.pk == request.user.pk and not active:
            raise DomainError("You cannot deactivate your own account.")
        user.is_active = active
        user.save(update_fields=["is_active", "updated_at"])
        log_action("user.activated" if active else "user.deactivated", actor=request.user, instance=user,
                   summary=f"{'Activated' if active else 'Deactivated'} {user.email}",
                   reason=request.data.get("reason", ""))
        return Response(self.get_serializer(user).data)

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"], url_path="deletion-check")
    def deletion_check(self, request, pk=None):
        blockers = account_services.staff_deletion_blockers(self.get_object(), request.user)
        return Response({"can_delete": not blockers, "blockers": blockers})

    @extend_schema(request=ReasonInputSerializer, responses={204: None})
    @action(detail=True, methods=["post"], url_path="delete-record")
    def delete_record(self, request, pk=None):
        """Permanently remove an account created in error. Refused once it has been used."""
        payload = ReasonInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        account_services.delete_staff_account(self.get_object(), reason=payload.validated_data["reason"],
                                              actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=UserBulkSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"])
    def bulk(self, request):
        payload = UserBulkSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        action_name, reason, actor = data["action"], data["reason"], request.user

        def set_active(user, active):
            if user.pk == actor.pk and not active:
                raise DomainError("You cannot deactivate your own account.")
            if user.is_active == active:
                raise DomainError("Already active." if active else "Already disabled.")
            user.is_active = active
            user.save(update_fields=["is_active", "updated_at"])
            log_action("user.activated" if active else "user.deactivated", actor=actor, instance=user, reason=reason,
                       summary=f"{'Activated' if active else 'Deactivated'} {user.email}")

        operations = {
            "deactivate": lambda u: set_active(u, False),
            "activate": lambda u: set_active(u, True),
            "delete": lambda u: account_services.delete_staff_account(u, reason=reason, actor=actor),
        }
        outcome = run_bulk(self.get_queryset(), data["ids"], operations[action_name],
                           label=lambda u: f"{u.get_full_name()} <{u.email}>")
        return Response(outcome)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._set_active(request, True)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @extend_schema(request=AdminPasswordResetSerializer, responses={204: None})
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        serializer = AdminPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validate_new_password(serializer.validated_data["new_password"], user)
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password", "updated_at"])
        log_action("user.password_set_by_admin", actor=request.user, instance=user,
                   summary=f"Set a temporary password for {user.email}")
        return Response(status=status.HTTP_204_NO_CONTENT)

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
        send = str(request.data.get("send_setup_emails", "true")).lower() != "false"
        mode = request.data.get("password_mode") or None
        created, credentials = importing.import_staff(rows, actor=request.user, send_emails=send, password_mode=mode)
        emailed = (mode or ("email" if send else "none")) == "email"
        # Set-up emails count against the daily email allowance; any that did not fit wait in the queue.
        waiting = notifications.pending_security_emails(created) if emailed else 0
        # Credentials are returned once for printing and never stored.
        return Response({"created": len(created), "emails_sent": emailed, "emails_waiting": waiting,
                         "credentials": credentials}, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("file_format", str)], responses={200: OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path="import/template")
    def import_template(self, request):
        return table_response("staff-import-template", importing.COLUMNS, importing.TEMPLATE_ROWS, requested_format(request))

    @action(detail=False, methods=["get"])
    def lecturers(self, request):
        # Registrars and results officers can teach too.
        queryset = User.objects.teaching_staff().order_by("last_name", "first_name")
        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(last_name__icontains=search) | Q(first_name__icontains=search) | Q(email__icontains=search)
            )
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(LecturerSummarySerializer(page, many=True).data)


class AppointmentFilter(django_filters.FilterSet):
    current = django_filters.BooleanFilter(method="filter_current")

    class Meta:
        model = Appointment
        fields = ["user", "office", "acting"]

    def filter_current(self, queryset, name, value):
        current = Appointment.objects.current().values("pk")
        return queryset.filter(pk__in=current) if value else queryset.exclude(pk__in=current)


class AppointmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                         viewsets.GenericViewSet):
    """
    Who holds which office, and when. Administrators record appointments; they are never deleted,
    so the list is the history of every office holder.
    """

    serializer_class = AppointmentSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN,)
    write_roles = (Role.ADMIN,)
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = AppointmentFilter
    search_fields = ["user__first_name", "user__last_name", "user__email", "reference"]
    ordering_fields = ["starts_on", "ends_on", "created_at"]

    def get_queryset(self):
        return Appointment.objects.select_related("user", "appointed_by", "ended_by")

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = appointment_services.appoint(
            data["user"], office=data["office"], starts_on=data["starts_on"], ends_on=data.get("ends_on"),
            acting=data.get("acting", False), reference=data.get("reference", ""), notes=data.get("notes", ""),
            actor=self.request.user)

    @extend_schema(request=AppointmentUpdateSerializer, responses=AppointmentSerializer)
    def partial_update(self, request, pk=None):
        payload = AppointmentUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        appointment = appointment_services.update_appointment(self.get_object(), actor=request.user,
                                                              **payload.validated_data)
        return Response(self.get_serializer(self.get_queryset().get(pk=appointment.pk)).data)

    @extend_schema(request=AppointmentEndSerializer, responses=AppointmentSerializer)
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        """End now (reshuffle, or relieved of the post). Optionally disable the account during an inquiry."""
        payload = AppointmentEndSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        appointment = appointment_services.end_appointment(self.get_object(), actor=request.user,
                                                           **payload.validated_data)
        return Response(self.get_serializer(self.get_queryset().get(pk=appointment.pk)).data)
