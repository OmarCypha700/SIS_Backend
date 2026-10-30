"""Throttles keyed on the account being targeted rather than the caller's address."""
import hashlib

from rest_framework.throttling import SimpleRateThrottle


class EmailRateThrottle(SimpleRateThrottle):
    """
    Limits attempts against one email address however many addresses the caller rotates through.
    Subclasses set `scope`; requests without an email are left to the per-address throttle.
    """

    def get_cache_key(self, request, view):
        email = request.data.get("email") if hasattr(request, "data") else None
        if not isinstance(email, str) or not email.strip():
            return None
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LoginEmailThrottle(EmailRateThrottle):
    scope = "login_email"


class PasswordResetEmailThrottle(EmailRateThrottle):
    scope = "password_reset_email"
