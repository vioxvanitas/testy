import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Safe by default: DEBUG is OFF unless explicitly enabled (set DJANGO_DEBUG=True in your local .env).
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not DEBUG and (not SECRET_KEY or SECRET_KEY.startswith("replace-with")):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a long random value (DEBUG is off).")
if DEBUG and not SECRET_KEY:
    SECRET_KEY = "development-only-insecure-key"

ALLOWED_HOSTS = [x.strip() for x in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()]

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "axes",
    "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ForcePasswordChangeMiddleware",
    "axes.middleware.AxesMiddleware",  # keep last
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug", "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages",
        "core.context_processors.navigation",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

if os.getenv("DB_NAME"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---- login throttling (django-axes): lock a username+IP pair after repeated failures
AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "django.contrib.auth.backends.ModelBackend"]
AXES_FAILURE_LIMIT = int(os.getenv("AXES_FAILURE_LIMIT", "5"))
AXES_COOLOFF_TIME = timedelta(minutes=int(os.getenv("AXES_COOLOFF_MINUTES", "15")))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "registration/locked.html"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"       # run `python manage.py collectstatic` on deploy
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# ---- enrollment rules
# Percent of the assessed total that must be paid before an enrollment can be marked "Enrolled".
ENROLLMENT_MIN_PAYMENT_PERCENT = int(os.getenv("ENROLLMENT_MIN_PAYMENT_PERCENT", "50"))

# ---- production hardening (only when DEBUG is off)
if not DEBUG:
    SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL", "True").lower() == "true"
    SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
    CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
    SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_SSL_REDIRECT
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_HTTPONLY = True
    X_FRAME_OPTIONS = "DENY"
    if os.getenv("DJANGO_BEHIND_PROXY", "False").lower() == "true":
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
