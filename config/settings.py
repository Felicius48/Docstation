import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DOCSTATION_DATA", BASE_DIR / "data")).resolve()
for directory in (DATA_DIR, DATA_DIR / "uploads", DATA_DIR / "work", DATA_DIR / "files"):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

DEBUG = os.environ.get("DOCSTATION_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DOCSTATION_SECRET_KEY")
if not SECRET_KEY and os.environ.get("DOCSTATION_WORKER") == "1":
    SECRET_KEY = secrets.token_urlsafe(64)
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DOCSTATION_SECRET_KEY.")
    key_file = DATA_DIR / ".secret"
    try:
        with key_file.open("x") as handle:
            os.chmod(key_file, 0o600)
            handle.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = key_file.read_text().strip()

ALLOWED_HOSTS = os.environ.get("DOCSTATION_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"csrf_console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django.security.csrf": {
            "handlers": ["csrf_console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
if os.environ.get("DOCSTATION_BEHIND_HTTPS_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "documents",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if os.environ.get("DOCSTATION_SERVE_STATIC") == "1":
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    WHITENOISE_MIMETYPES = {".mjs": "text/javascript", ".wasm": "application/wasm"}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "DIRS": [BASE_DIR / "documents" / "templates"],
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "archive.sqlite3",
        "OPTIONS": {"timeout": 15, "transaction_mode": "IMMEDIATE"},
    }
}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{name}"}
    for name in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]
LANGUAGE_CODE = "ru"
TIME_ZONE = os.environ.get("DOCSTATION_TIME_ZONE", "Europe/Samara")
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "workspace"
LOGOUT_REDIRECT_URL = "login"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FILE_UPLOAD_HANDLERS = ["documents.uploads.LimitedUploadHandler"]
FILE_UPLOAD_TEMP_DIR = DATA_DIR / "uploads"
DATA_UPLOAD_MAX_NUMBER_FILES = 1
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024
MAX_FILE_SIZE = 50_000_000
MAX_BATCH_SIZE = 50
MAX_ARCHIVE_BYTES = 10_000_000_000
MAX_PENDING_JOBS = 100
MAX_RESULT_BYTES = 500_000_000
