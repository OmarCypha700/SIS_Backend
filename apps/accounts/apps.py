from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        from . import devices
        from .models import SignInMethod

        def signed_in(sender, request, user, **kwargs):
            if request is not None and hasattr(request, "session"):
                devices.record_sign_in(request, user, getattr(request, "sign_in_method", SignInMethod.PASSWORD))

        def signed_out(sender, request, user, **kwargs):
            if request is not None:
                devices.forget_sign_in(request)

        user_logged_in.connect(signed_in, dispatch_uid="accounts.record_sign_in")
        user_logged_out.connect(signed_out, dispatch_uid="accounts.forget_sign_in")
