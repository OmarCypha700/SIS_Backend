from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import log_action
from common.exceptions import DomainError

from . import devices
from .models import SignedInDevice


class SignedInDeviceSerializer(serializers.ModelSerializer):
    browser = serializers.SerializerMethodField()
    system = serializers.SerializerMethodField()
    method_display = serializers.CharField(source="get_method_display", read_only=True)
    current = serializers.SerializerMethodField()
    notifications = serializers.SerializerMethodField()

    class Meta:
        model = SignedInDevice
        fields = ["id", "browser", "system", "method", "method_display", "ip_address", "signed_in_at", "last_seen_at",
                  "current", "notifications"]

    def get_browser(self, device) -> str:
        return devices.describe(device.user_agent)[0]

    def get_system(self, device) -> str:
        return devices.describe(device.user_agent)[1]

    def get_current(self, device) -> bool:
        return device.pk == self.context.get("current_id")

    def get_notifications(self, device) -> bool:
        """Whether this device receives push notifications."""
        return bool(device.push_subscriptions.all())


def _label(device):
    browser, system = devices.describe(device.user_agent)
    return f"{browser} on {system}"


class SignedInDeviceListView(APIView):
    """Where you are signed in now. This device is marked `current`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=SignedInDeviceSerializer(many=True))
    def get(self, request):
        current_id = devices.current_device_id(request)
        rows = devices.live_devices(request.user)
        return Response(SignedInDeviceSerializer(rows, many=True, context={"current_id": current_id}).data)


class SignedInDeviceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, pk):
        """Sign out one of your other devices now. It also stops receiving notifications."""
        device = SignedInDevice.objects.filter(pk=pk, user=request.user).first()
        if device is None:
            raise DomainError("That sign-in has already ended.", status_code=404, code="not_found")
        if device.pk == devices.current_device_id(request):
            raise DomainError("This is the device you are using. Use Sign out instead.", code="current_device")
        devices.sign_out_device(device)
        log_action("auth.device_signed_out", actor=request.user, instance=request.user,
                   summary=f"Signed out {_label(device)}")
        return Response(status=status.HTTP_204_NO_CONTENT)


class SignOutOtherDevicesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: {"type": "object", "properties": {"signed_out": {"type": "integer"}}}})
    def post(self, request):
        """Sign out everywhere except this device, and stop notifications on the others."""
        count = devices.sign_out_everywhere(request.user, keep_device_id=devices.current_device_id(request))
        log_action("auth.signed_out_other_devices", actor=request.user, instance=request.user,
                   summary=f"Signed out of {count} other device{'' if count == 1 else 's'}")
        return Response({"signed_out": count})
