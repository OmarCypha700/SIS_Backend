"""
Production settings. Every value that matters must be supplied explicitly through the
environment; the application refuses to start otherwise.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=False)
if DEBUG:
    raise ImproperlyConfigured("DJANGO_DEBUG must be False in production.")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY or SECRET_KEY == "change-me" or len(SECRET_KEY) < 40:
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY (at least 40 characters).")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to the explicit host names of this deployment.")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
