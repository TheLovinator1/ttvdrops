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

TRUE_VALUES: set[str] = {"1", "true", "yes", "y", "on"}


def env_bool(key: str, *, default: bool = False) -> bool:
    """Read a boolean from the environment, accepting common truthy values.

    Returns:
        bool: Parsed boolean value or the provided default when unset.
    """
    value: str | None = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def env_int(key: str, default: int) -> int:
    """Read an integer from the environment with a fallback default.

    Returns:
        int: Parsed integer value or the provided default when unset.
    """
    value: str | None = os.getenv(key)
    return int(value) if value is not None else default


DEBUG: bool = env_bool(key="DEBUG", default=True)
TESTING: bool = "test" in sys.argv or "PYTEST_VERSION" in os.environ


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
EMAIL_PORT: int = env_int(key="EMAIL_PORT", default=587)
EMAIL_SUBJECT_PREFIX = "[TTVDrops] "
EMAIL_TIMEOUT: int = env_int(key="EMAIL_TIMEOUT", default=10)
EMAIL_USE_LOCALTIME = True
EMAIL_USE_TLS: bool = env_bool(key="EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL: bool = env_bool(key="EMAIL_USE_SSL", default=False)
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

STATIC_ROOT: Path = DATA_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
STATIC_URL = "static/"
STATICFILES_DIRS: list[Path] = [BASE_DIR / "static"]

TIME_ZONE = "UTC"
WSGI_APPLICATION = "config.wsgi.application"

INTERNAL_IPS: list[str] = []
if DEBUG:
    INTERNAL_IPS = ["127.0.0.1", "localhost"]  # pyright: ignore[reportConstantRedefinition]

ALLOWED_HOSTS: list[str] = [".localhost", "127.0.0.1", "[::1]", "testserver"]
if not DEBUG:
    ALLOWED_HOSTS = ["ttvdrops.lovinator.space"]  # pyright: ignore[reportConstantRedefinition]

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
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "twitch.apps.TwitchConfig",
]

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]


TEMPLATES: list[dict[str, Any]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]


# https://blog.pecar.me/django-sqlite-benchmark
DATABASES: dict[str, dict[str, str | Path | dict[str, str]]] = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "ttvdrops.sqlite3",
        "OPTIONS": {
            "init_command": (
                "PRAGMA foreign_keys = ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA mmap_size = 134217728; PRAGMA journal_size_limit = 27103364; PRAGMA cache_size=2000;"  # noqa: E501
            ),
            "transaction_mode": "IMMEDIATE",
        },
    },
}

if not TESTING:
    INSTALLED_APPS = [
        *INSTALLED_APPS,
        "debug_toolbar",
        "silk",
    ]
    MIDDLEWARE = [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        "silk.middleware.SilkyMiddleware",
        *MIDDLEWARE,
    ]
