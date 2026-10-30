from django.conf import settings
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import Role
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission

from . import budget, delivery, services
from .models import Channel, Kind, MessageStatus, Notification, OutgoingMessage, PushSubscription
from .serializers import NotificationSerializer, PushSubscribeSerializer, PushUnsubscribeSerializer


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The signed-in user's own notifications. Other people's notifications return 404."""

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation has no user
            return Notification.objects.none()
        queryset = services.own(self.request.user)
        if self.request.query_params.get("unread") in ("1", "true"):
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @extend_schema(parameters=[OpenApiParameter("unread", bool)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        # Every open app polls this, so it also sends a few queued push/email messages.
        delivery.drain_on_poll()
        return Response({"unread": services.unread_count(request.user)})

    @extend_schema(request=None, responses=NotificationSerializer)
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(NotificationSerializer(notification).data)

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        updated = services.own(request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": updated, "unread": 0})


class PushPublicKeyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response({"enabled": delivery.push_enabled(), "public_key": settings.VAPID_PUBLIC_KEY or None})


class PushSubscribeView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "push"

    @extend_schema(request=PushSubscribeSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        if not delivery.push_enabled():
            raise DomainError("Push notifications are not set up on this server.", code="push_disabled",
                              status_code=409)
        payload = PushSubscribeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        # Upsert by endpoint: a shared device that changes hands belongs to whoever subscribed last.
        subscription, _ = PushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={"user": request.user, "p256dh": data["keys"]["p256dh"], "auth": data["keys"]["auth"],
                      "user_agent": request.headers.get("User-Agent", "")[:300], "failures": 0},
        )
        # Keep the newest few devices per person; older ones stop receiving pushes.
        stale = (PushSubscription.objects.filter(user=request.user).order_by("-created_at", "-pk")
                 .values_list("pk", flat=True)[settings.PUSH_MAX_DEVICES_PER_USER:])
        PushSubscription.objects.filter(pk__in=list(stale)).delete()
        return Response({"id": subscription.pk}, status=status.HTTP_201_CREATED)


class PushUnsubscribeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PushUnsubscribeSerializer, responses={204: None})
    def post(self, request):
        payload = PushUnsubscribeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        PushSubscription.objects.filter(user=request.user, endpoint=payload.validated_data["endpoint"]).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PushTestView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "push"

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        devices = PushSubscription.objects.filter(user=request.user).count()
        services.notify([request.user], kind=Kind.TEST, title="Test notification",
                        body="Notifications are working on this device.", link="/notifications", email=False)
        return Response({"devices": devices})


class DeliveryStatusView(APIView):
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN,)

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        week_ago = timezone.now() - timezone.timedelta(days=7)
        outbox = OutgoingMessage.objects
        return Response({
            "email": {
                **budget.status(),
                "pending": outbox.filter(channel=Channel.EMAIL, status=MessageStatus.PENDING).count(),
                "failed_last_7_days": outbox.filter(channel=Channel.EMAIL, status=MessageStatus.FAILED,
                                                    updated_at__gte=week_ago).count(),
                "backend": settings.EMAIL_BACKEND.rsplit(".", 2)[-2],
            },
            "push": {
                "enabled": delivery.push_enabled(),
                "devices": PushSubscription.objects.count(),
                "pending": outbox.filter(channel=Channel.PUSH, status=MessageStatus.PENDING).count(),
            },
        })
