from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "kind", "kind_display", "title", "body", "link", "is_read", "read_at", "created_at"]
        read_only_fields = fields

    def get_is_read(self, obj) -> bool:
        return obj.read_at is not None


class PushKeysSerializer(serializers.Serializer):
    p256dh = serializers.CharField(max_length=200)
    auth = serializers.CharField(max_length=100)


class PushSubscribeSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=1000)
    keys = PushKeysSerializer()

    def validate_endpoint(self, value):
        if not value.startswith("https://"):
            raise serializers.ValidationError("Push endpoints must use HTTPS.")
        return value


class PushUnsubscribeSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=1000)
