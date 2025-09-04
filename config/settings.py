from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from platformdirs import user_data_dir

logger: logging.Logger = logging.getLogger("ttvdrops.settings")

load_dotenv(verbose=True)

DEBUG: bool = os.getenv(key="DEBUG", default="True").lower() == "true"


def get_data_dir() -> Path:
    r"""Get the directory where the application data will be stored.

    This directory is created if it does not exist.

    Returns:
        Path: The directory where the application data will be stored.

            For example, on Windows, it might be:
            `C:\Users\lovinator\AppData\Roaming\TheLovinator\TTVDrops`

            In this directory, the SQLite database file will be stored as `db.sqlite3`.
    """
    data_dir: str = user_data_dir(
        appname="TTVDrops",
        appauthor="TheLovinator",
        roaming=True,
        ensure_exists=True,
    )
    return Path(data_dir)


DATA_DIR: Path = get_data_dir()

ADMINS: list[tuple[str, str]] = [("Joakim Hellsén", "tlovinator@gmail.com")]
AUTH_USER_MODEL = "accounts.User"
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "config.urls"
SECRET_KEY: str = os.getenv("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    logger.error("DJANGO_SECRET_KEY environment variable is not set.")
    sys.exit(1)

DEFAULT_FROM_EMAIL: str | None = os.getenv(key="EMAIL_HOST_USER", default=None)
EMAIL_HOST: str = os.getenv(key="EMAIL_HOST", default="smtp.gmail.com")
EMAIL_HOST_PASSWORD: str | None = os.getenv(key="EMAIL_HOST_PASSWORD", default=None)
EMAIL_HOST_USER: str | None = os.getenv(key="EMAIL_HOST_USER", default=None)
EMAIL_PORT: int = int(os.getenv(key="EMAIL_PORT", default="587"))
EMAIL_SUBJECT_PREFIX = "[TTVDrops] "
EMAIL_TIMEOUT: int = int(os.getenv(key="EMAIL_TIMEOUT", default="10"))
EMAIL_USE_LOCALTIME = True
EMAIL_USE_TLS: bool = os.getenv(key="EMAIL_USE_TLS", default="True").lower() == "true"
EMAIL_USE_SSL: bool = os.getenv(key="EMAIL_USE_SSL", default="False").lower() == "true"
SERVER_EMAIL: str | None = os.getenv(key="EMAIL_HOST_USER", default=None)

LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"
LOGOUT_REDIRECT_URL = "/"

ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_AUTHENTICATION_METHOD = "username"
ACCOUNT_EMAIL_REQUIRED = False

MEDIA_ROOT: Path = DATA_DIR / "media"
MEDIA_ROOT.mkdir(exist_ok=True)
MEDIA_URL = "/media/"

STATIC_ROOT: Path = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
STATIC_URL = "static/"
STATICFILES_DIRS: list[Path] = [BASE_DIR / "static"]

TIME_ZONE = "UTC"
WSGI_APPLICATION = "config.wsgi.application"

if DEBUG:
    INTERNAL_IPS: list[str] = ["127.0.0.1", "localhost"]

if not DEBUG:
    ALLOWED_HOSTS: list[str] = ["ttvdrops.lovinator.space"]

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": True,
        },
        "django.utils.autoreload": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
    },
}

INSTALLED_APPS: list[str] = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "twitch.apps.TwitchConfig",
]

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]


TEMPLATES: list[dict[str, str | list[Path] | bool | dict[str, list[str] | list[tuple[str, list[str]]]]]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# PostgreSQL configuration (preferred when env vars provided)
DATABASES: dict[str, dict[str, Any]] = {  # pyright: ignore[reportInvalidTypeForm]
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB"),
        "USER": os.getenv("POSTGRES_USER", "ttvdrops"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "ttvdrops"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}


AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

TESTING: bool = "test" in sys.argv or "PYTEST_VERSION" in os.environ

if not TESTING:
    DEBUG_TOOLBAR_CONFIG: dict[str, str] = {"ROOT_TAG_EXTRA_ATTRS": "hx-preserve"}
    INSTALLED_APPS = [  # pyright: ignore[reportConstantRedefinition]
        "django_watchfiles",
        *INSTALLED_APPS,
        "debug_toolbar",
        "django_browser_reload",
    ]
    MIDDLEWARE = [  # pyright: ignore[reportConstantRedefinition]
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        *MIDDLEWARE,
        "django_browser_reload.middleware.BrowserReloadMiddleware",
    ]
