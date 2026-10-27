from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "role", "is_active"]
    list_filter = ["role", "is_active"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("title", "first_name", "last_name", "phone", "staff_id", "role")}),
        ("Result capabilities", {"fields": ("can_approve_results", "can_publish_results", "can_request_amendments",
                                            "can_approve_amendments")}),
        ("Status", {"fields": ("is_active", "must_change_password", "is_staff", "is_superuser")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "role",
                                                              "password1", "password2")}),)
