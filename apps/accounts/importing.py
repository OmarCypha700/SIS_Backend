"""
Staff account import.

Imported accounts get no password. Each new user is emailed a single-use link (valid for
PASSWORD_RESET_TIMEOUT) to choose their own, so no passwords ever pass through spreadsheets.
An administrator can still set a temporary password for anyone who cannot receive email.
"""
from django.db import transaction
from rest_framework import serializers

from apps.audit.services import log_action
from apps.notifications import services as notifications
from common.exceptions import DomainError
from common.importing import row_result, serializer_errors

from .models import DEFAULT_CAPABILITIES, Role, User

COLUMNS = ["email", "title", "first_name", "last_name", "phone", "staff_id", "role"]
REQUIRED = {"email", "first_name", "last_name", "role"}
TEMPLATE_ROWS = [
    ["k.asante@example.edu", "Dr.", "Kwame", "Asante", "0240000000", "STF-001", "LECTURER"],
    ["e.owusu@example.edu", "Mrs.", "Efua", "Owusu", "", "STF-002", "RESULTS_OFFICER"],
]
STAFF_ROLE_CHOICES = [(r.value, r.label) for r in Role if r != Role.STUDENT]
_ROLE_ALIASES = {label.upper(): value for value, label in STAFF_ROLE_CHOICES} | {
    "ADMINISTRATOR": Role.ADMIN, "RESULTS OFFICER": Role.RESULTS_OFFICER, "EXAMS OFFICER": Role.RESULTS_OFFICER,
    "EXAMINATIONS OFFICER": Role.RESULTS_OFFICER, "TUTOR": Role.LECTURER,
}


class StaffRowSerializer(serializers.Serializer):
    email = serializers.EmailField()
    title = serializers.CharField(required=False, allow_blank=True, max_length=20)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    staff_id = serializers.CharField(required=False, allow_blank=True, max_length=40)
    role = serializers.ChoiceField(choices=STAFF_ROLE_CHOICES)

    def to_internal_value(self, data):
        data = dict(data)
        role = (data.get("role") or "").strip().upper()
        data["role"] = _ROLE_ALIASES.get(role, role.replace(" ", "_"))
        return super().to_internal_value(data)

    def validate_email(self, value):
        return value.strip().lower()


def validate_rows(rows):
    parsed = []
    for index, raw in enumerate(rows, start=2):
        serializer = StaffRowSerializer(data=raw)
        if serializer.is_valid():
            parsed.append((index, raw, dict(serializer.validated_data), {}))
        else:
            parsed.append((index, raw, None, serializer_errors(serializer)))
    emails = [d["email"] for _, _, d, _ in parsed if d]
    staff_ids = [d["staff_id"] for _, _, d, _ in parsed if d and d.get("staff_id")]
    existing_emails = set(User.objects.filter(email__in=emails).values_list("email", flat=True))
    existing_ids = set(User.objects.filter(staff_id__in=staff_ids).values_list("staff_id", flat=True))
    seen_email, seen_id, results = {}, {}, []
    for index, raw, data, errors in parsed:
        warnings = []
        if data:
            if data["email"] in existing_emails:
                errors.setdefault("email", []).append("An account with this email already exists.")
            if data["email"] in seen_email:
                errors.setdefault("email", []).append(f"Duplicate of row {seen_email[data['email']]} in this file.")
            seen_email.setdefault(data["email"], index)
            staff_id = data.get("staff_id")
            if staff_id:
                if staff_id in existing_ids:
                    errors.setdefault("staff_id", []).append("This staff ID is already in use.")
                if staff_id in seen_id:
                    errors.setdefault("staff_id", []).append(f"Duplicate of row {seen_id[staff_id]} in this file.")
                seen_id.setdefault(staff_id, index)
            if data["role"] == Role.ADMIN:
                warnings.append("This person will have full administrator access.")
        results.append(row_result(index, raw, data, errors, warnings))
    return results


@transaction.atomic
def import_staff(rows, *, actor, send_emails=True):
    results = validate_rows(rows)
    invalid = [r for r in results if not r["valid"]]
    if invalid:
        raise DomainError(f"{len(invalid)} row(s) have errors. Nothing was imported.", code="import_invalid",
                          errors=[{"row": r["row"], "errors": r["errors"]} for r in invalid])
    created = []
    for result in results:
        data = dict(result["data"])
        data["staff_id"] = data.get("staff_id") or None
        for field, value in DEFAULT_CAPABILITIES.get(data["role"], {}).items():
            data.setdefault(field, value)
        created.append(User.objects.create_user(password=None, **data))
    log_action("user.imported", actor=actor, entity_type="accounts.user",
               summary=f"Imported {len(created)} staff accounts",
               metadata={"emails": [u.email for u in created][:500], "roles": sorted({u.role for u in created})})
    if send_emails:
        # Queued after the rows exist and sent within the daily email allowance; a mail failure
        # never undoes the import. Links are generated when each email is actually sent.
        notifications.send_security_emails(created, "account_setup")
    return created
