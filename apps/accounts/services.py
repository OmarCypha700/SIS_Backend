"""
Removing accounts created in error.

Records are normally never deleted: people are deactivated instead, so academic history and the
audit trail stay intact. Deletion is only for a record created by mistake (a duplicate from a bad
import, a typo, a test account) that has never been used and has nothing attached to it.
"""
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from common.exceptions import DomainError

from .models import Role


def account_usage(user):
    """Reasons this login account cannot be deleted (empty when it has never been used)."""
    reasons = []
    if user.last_login:
        reasons.append("The person has signed in.")
    if AuditLog.objects.filter(actor=user).exists():
        reasons.append("The account has recorded activity in the audit log.")
    return reasons


def staff_deletion_blockers(user, actor):
    reasons = []
    if user.pk == actor.pk:
        reasons.append("You cannot delete your own account.")
    if user.role == Role.STUDENT:
        reasons.append("Student accounts are removed from the student record.")
    reasons += account_usage(user)
    if user.lecturer_assignments.exists():
        reasons.append("The person is assigned to teach a course.")
    return reasons


def require_reason(reason):
    if not (reason or "").strip():
        raise DomainError("Give a reason for deleting this record.", code="reason_required")


def delete_or_explain(obj):
    """Delete, turning any remaining database protection into a readable error."""
    try:
        obj.delete()
    except (ProtectedError, RestrictedError):
        raise DomainError("This record is linked to other records and cannot be deleted. Deactivate it instead.",
                          code="has_history", status_code=409) from None


@transaction.atomic
def delete_staff_account(user, *, reason, actor):
    require_reason(reason)
    blockers = staff_deletion_blockers(user, actor)
    if blockers:
        raise DomainError("This account cannot be deleted: " + " ".join(blockers) + " Deactivate it instead.",
                          code="has_history", status_code=409)
    summary = {"email": user.email, "name": user.get_full_name(), "role": user.role, "staff_id": user.staff_id}
    entity_id = user.pk
    delete_or_explain(user)
    log_action("user.deleted", actor=actor, entity_type="accounts.user", entity_id=str(entity_id), reason=reason,
               summary=f"Deleted {summary['email']} (created in error)", before=summary)
