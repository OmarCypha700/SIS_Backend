"""
Shared settings for every environment.

Environment-specific values come from environment variables (or a local .env file);
see .env.example for the full list.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "common",
    "apps.institutions",
    "apps.accounts",
    "apps.audit",
    "apps.academics",
    "apps.students",
    "apps.registration",
    "apps.results",
    "apps.progression",
    "apps.notifications",
    "apps.transcripts",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "common.middleware.RequestContextMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "common.middleware.PasswordChangeRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------
# SQLite by default; set DATABASE_URL to use PostgreSQL.
if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db("DATABASE_URL")}
    DATABASES["default"].setdefault("CONN_MAX_AGE", env.int("DB_CONN_MAX_AGE", default=60))
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
            "OPTIONS": {
                # Take the write lock at the start of each transaction so concurrent
                # registrations queue instead of failing half-way with "database is locked".
                "transaction_mode": "IMMEDIATE",
                "timeout": 20,
            },
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("TIME_ZONE", default="Africa/Accra")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
# Not web-accessible: transcript signature and seal images. Keep it out of MEDIA_ROOT and back it up.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private-media")))
MAX_UPLOAD_IMAGE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_PHOTO_BYTES = 10 * 1024 * 1024  # student photos straight from a camera; stored at ~30 KB
MAX_UPLOAD_CSV_BYTES = 5 * 1024 * 1024  # CSV and Excel imports
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=False)

# --- Sessions, CSRF, CORS -------------------------------------------------
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=8 * 60 * 60)
SESSION_SAVE_EVERY_REQUEST = True  # sliding expiry
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "sis_sessionid"
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_HTTPONLY = False  # the frontend reads it to send X-CSRFToken
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CORS_URLS_REGEX = r"^/api/.*$"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- Email ----------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@example.edu")
# Brevo (production): EMAIL_BACKEND=anymail.backends.brevo.EmailBackend and BREVO_API_KEY.
# A short timeout keeps a slow mail API from holding a web worker (and the browser) for 30 s.
ANYMAIL = {"BREVO_API_KEY": env("BREVO_API_KEY", default=""), "REQUESTS_TIMEOUT": (3.05, 10)}
# Daily sending allowance shared by every email this installation sends (Brevo free plan: 300).
# Alerts may only use EMAIL_DAILY_LIMIT - EMAIL_SECURITY_RESERVE so password resets always get through.
EMAIL_DAILY_LIMIT = env.int("EMAIL_DAILY_LIMIT", default=300)
EMAIL_SECURITY_RESERVE = env.int("EMAIL_SECURITY_RESERVE", default=60)

# --- Web push (VAPID). Generate with: python manage.py generate_vapid_keys ---
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_SUBJECT = env("VAPID_SUBJECT", default="")
# Browsers' push services. Subscriptions pointing anywhere else are refused, so the server never
# posts to an address a user chose. Entries starting with "." match any subdomain.
PUSH_ALLOWED_HOSTS = env.list("PUSH_ALLOWED_HOSTS", default=[
    "fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
    ".push.apple.com", ".notify.windows.com", ".wns.windows.com",
])
PUSH_MAX_DEVICES_PER_USER = 10
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2

# --- Cache (used by throttling) -------------------------------------------
# Set CACHE_LOCATION (a writable folder) in production so throttles are shared by every web worker.
CACHE_LOCATION = env("CACHE_LOCATION", default="")
CACHES = {"default": (
    {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": CACHE_LOCATION}
    if CACHE_LOCATION else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
)}

# --- REST framework -------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    # Proxies in front of Django that append to X-Forwarded-For (1 = PythonAnywhere's own; 2 when the
    # frontend's rewrites forward requests). Without this, a client could pick its own throttle key.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=1),
    "DEFAULT_THROTTLE_RATES": {
        "login_email": env("THROTTLE_LOGIN_EMAIL", default="20/hour"),
        "password_reset_email": env("THROTTLE_PASSWORD_RESET_EMAIL", default="3/hour"),
        "push": "30/hour",
        "login": env("THROTTLE_LOGIN", default="10/min"),
        "password_reset": env("THROTTLE_PASSWORD_RESET", default="5/hour"),
        "password_change": "10/hour",
        "transcript_verify": env("THROTTLE_TRANSCRIPT_VERIFY", default="30/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Core SIS API",
    "DESCRIPTION": (
        "School Management System API. Authentication uses Django session cookies. "
        "Call GET /api/v1/auth/csrf/ first, then send the csrftoken cookie value as the "
        "X-CSRFToken header on every POST/PUT/PATCH/DELETE request."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # The docs page runs on the app's own origin (through the frontend proxy), so only
    # administrators can open it and the Swagger UI files come from a fixed, never-changing release.
    "SERVE_PERMISSIONS": ["common.permissions.IsAdministrator"],
    "SWAGGER_UI_DIST": "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14",
    "SWAGGER_UI_FAVICON_HREF": "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/favicon-32x32.png",
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "StudentStatusEnum": "apps.students.models.StudentStatus",
        "PeriodStatusEnum": "apps.academics.models.PeriodStatus",
        "OfferingStatusEnum": "apps.academics.models.OfferingStatus",
        "RegistrationStatusEnum": "apps.registration.models.RegistrationStatus",
        "CourseRegistrationStatusEnum": "apps.registration.models.CourseRegistrationStatus",
        "SheetStatusEnum": "apps.results.models.SheetStatus",
        "AmendmentStatusEnum": "apps.results.models.AmendmentStatus",
        "StandingEnum": "apps.progression.models.Standing",
        "PlacementEnum": "apps.progression.models.Placement",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}
