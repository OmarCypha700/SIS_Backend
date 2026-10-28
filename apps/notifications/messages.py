"""
Security email templates. They are rendered when the email is actually sent, so the single-use
link is fresh even when the email waited in the queue, and no token is ever stored in the outbox.
"""
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def _reset_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?uid={uid}&token={token}"


def _hours():
    return settings.PASSWORD_RESET_TIMEOUT // 3600


def password_reset(user):
    return ("Reset your password",
            f"Hello {user.first_name},\n\nUse the link below to choose a new password. "
            f"It expires in {_hours()} hours.\n\n{_reset_link(user)}\n\n"
            "If you did not request this, you can ignore this email.")


def account_setup(user):
    return ("Your staff account is ready",
            f"Hello {user.first_name},\n\nAn account has been created for you as {user.get_role_display()}.\n"
            f"Choose your password using the link below (it expires in {_hours()} hours):\n\n"
            f"{_reset_link(user)}\n\nYour sign-in email is {user.email}.")


TEMPLATES = {"password_reset": password_reset, "account_setup": account_setup}


def render(template, user):
    return TEMPLATES[template](user)
