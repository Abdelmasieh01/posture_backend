"""
Central configuration for the posture_backend Django project.
Exists to configure SQLite, media uploads, DRF, and Django Channels (Daphne).
Called automatically by Django when the server or a management command starts.
"""

from pathlib import Path
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# Read from environment variables (or a local .env file) with safe defaults for a school demo.
# python-decouple automatically casts DEBUG to a boolean and splits ALLOWED_HOSTS on commas.
SECRET_KEY = config(
    "DJANGO_SECRET_KEY",
    default="demo-insecure-secret-key-for-school-project",
)
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*", cast=Csv())

# "daphne" MUST be first in INSTALLED_APPS so it overrides the standard `runserver`
# management command with an ASGI-capable server that handles both HTTP and WebSockets.
INSTALLED_APPS = [
    "daphne",
    "corsheaders",
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "monitor",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "posture_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Tell Daphne / Channels where the root ASGI application lives
ASGI_APPLICATION = "posture_backend.asgi.application"

# Single-file SQLite database requires zero setup on a Raspberry Pi or laptop
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# InMemoryChannelLayer stores WebSocket groups in a Python dictionary inside this process's RAM.
# This avoids installing Redis, which is ideal for a single-process school demo,
# but means you cannot run multiple worker processes (they wouldn't share memory).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Disable authentication and permissions on API endpoints because the demo runs on a trusted LAN
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "UNAUTHENTICATED_USER": None,
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
# Store all datetimes in UTC in the database to match the Pi's ISO 8601 UTC timestamps
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Local directory and URL prefix for uploaded JPEG posture snapshots
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
