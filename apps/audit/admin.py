from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor_email", "action", "entity_type", "entity_id", "summary"]
    list_filter = ["action", "entity_type"]
    search_fields = ["actor_email", "summary", "entity_id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
