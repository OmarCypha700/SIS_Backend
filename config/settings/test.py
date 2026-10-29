from .dev import *  # noqa: F401,F403
from .dev import BASE_DIR, DATABASES

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"]["OPTIONS"] = {}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = BASE_DIR / ".test-media"
PRIVATE_MEDIA_ROOT = BASE_DIR / ".test-private"
