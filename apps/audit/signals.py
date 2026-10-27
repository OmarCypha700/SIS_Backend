from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import log_action


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action("auth.login", actor=user, instance=user, summary="Signed in")


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_action("auth.logout", actor=user, instance=user, summary="Signed out")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # Only the attempted identifier is kept; never the password.
    attempted = str(credentials.get("email") or credentials.get("username") or "")[:254]
    log_action("auth.login_failed", entity_type="accounts.user", summary="Failed sign-in",
               metadata={"attempted_email": attempted})
