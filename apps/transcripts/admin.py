from django.contrib import admin

from .models import IssuedTranscript


@admin.register(IssuedTranscript)
class IssuedTranscriptAdmin(admin.ModelAdmin):
    list_display = ["serial", "student", "status", "is_interim", "issued_by", "issued_at", "print_count"]
    list_filter = ["status", "is_interim"]
    search_fields = ["serial", "student__student_number"]
    readonly_fields = [f.name for f in IssuedTranscript._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False  # the register is permanent; revoke instead
