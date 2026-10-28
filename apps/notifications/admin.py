from django.contrib import admin

from .models import Notification, OutgoingMessage, PushSubscription


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "recipient", "kind", "created_at", "read_at"]
    list_filter = ["kind"]
    search_fields = ["title", "recipient__email"]


@admin.register(OutgoingMessage)
class OutgoingMessageAdmin(admin.ModelAdmin):
    list_display = ["channel", "category", "to", "status", "attempts", "created_at", "sent_at"]
    list_filter = ["channel", "category", "status"]
    readonly_fields = [f.name for f in OutgoingMessage._meta.fields]


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["user", "user_agent", "created_at", "last_success_at", "failures"]
