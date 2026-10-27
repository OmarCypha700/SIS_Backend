from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
SECRET_KEY = env("DJANGO_SECRET_KEY", default="") or "dev-only-insecure-key-do-not-use-in-production"
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS", default=["http://localhost:3000", "http://127.0.0.1:3000"]
)
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=True)
