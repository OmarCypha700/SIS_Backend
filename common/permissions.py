"""
Role-based access control for API views.

Views declare which roles may perform which actions:

    read_roles = (Role.ADMIN, Role.REGISTRAR)
    write_roles = (Role.ADMIN,)
    action_roles = {"export": (Role.ADMIN,)}

Anything not declared is denied. Object-level restrictions (a student seeing only their
own record, a lecturer seeing only assigned offerings) are applied in get_queryset so
that out-of-scope identifiers return 404 rather than leaking existence.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission


def is_active_user(user):
    return bool(user and user.is_authenticated and user.is_active)


def has_role(user, *roles):
    return is_active_user(user) and user.role in roles


class RoleBasedPermission(BasePermission):
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not is_active_user(user):
            return False
        action = getattr(view, "action", None)
        action_roles = getattr(view, "action_roles", {}) or {}
        if action in action_roles:
            roles = action_roles[action]
        elif request.method in SAFE_METHODS:
            roles = getattr(view, "read_roles", ())
        else:
            roles = getattr(view, "write_roles", ())
        return user.role in roles
