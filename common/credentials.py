"""Temporary passwords for imported accounts, handed to people on printed slips instead of by email."""
import secrets

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

# No 0/O, 1/l/I: slips are read and typed by hand.
_LOWER = "abcdefghijkmnpqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"


def generate_temporary_password():
    """Three readable groups such as `Kp7m-Qx4t-Wb9r` (about 70 bits); passes the password validators."""
    groups = []
    for _ in range(3):
        chars = [secrets.choice(_UPPER), secrets.choice(_LOWER), secrets.choice(_DIGITS), secrets.choice(_LOWER)]
        groups.append("".join(chars))
    return "-".join(groups)


def password_errors(password, *, email, first_name="", last_name=""):
    """Validator messages for an imported password (empty list when acceptable)."""
    from apps.accounts.models import User

    candidate = User(email=email or "", first_name=first_name or "", last_name=last_name or "")
    try:
        password_validation.validate_password(password, candidate)
    except ValidationError as exc:
        return list(exc.messages)
    return []


PASSWORD_MODES = ("generate", "column", "email", "none")
