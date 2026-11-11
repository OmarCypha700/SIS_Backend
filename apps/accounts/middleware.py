from django.contrib.auth import logout

from . import devices


class SignedInDeviceMiddleware:
    """
    For signed-in requests: ends a sign-in that has lasted longer than the institution allows (7 days
    for students and 24 hours for staff by default, on top of the 8 hours without activity), and keeps
    the device's "last active" time current.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and hasattr(request, "session"):
            if devices.has_run_out(request):
                logout(request)  # the request continues signed out; the app shows the sign-in page
            else:
                devices.touch(request)
        return self.get_response(request)
