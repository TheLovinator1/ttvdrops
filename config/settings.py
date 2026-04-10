import logging
import os
import sys
from pathlib import Path
from typing import Any

import sentry_sdk
from celery.schedules import crontab
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
TESTING: bool = (
    env_bool(key="TESTING", default=False)
    or "test" in sys.argv
    or "PYTEST_VERSION" in os.environ
)


def get_data_dir() -> Path:
    r"""Get the directory where the application data will be stored.

    This directory is created if it does not exist.

    Returns:
        Path: The directory where the application data will be stored.

            For example, on Windows, it might be:
            `C:\Users\lovinator\AppData\Roaming\TheLovinator\TTVDrops`

            In this directory, application data such as media and static files will be stored.
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
STATIC_URL = "/static/"
STATICFILES_DIRS: list[Path] = [BASE_DIR / "static"]

TIME_ZONE = "UTC"
WSGI_APPLICATION = "config.wsgi.application"

TTVDROPS_PENDING_DIR: str = os.getenv("TTVDROPS_PENDING_DIR", "")

INTERNAL_IPS: list[str] = []
if DEBUG:
    INTERNAL_IPS = ["127.0.0.1", "localhost"]  # pyright: ignore[reportConstantRedefinition]

ALLOWED_HOSTS: list[str] = [".localhost", "127.0.0.1", "[::1]", "testserver"]
if not DEBUG:
    ALLOWED_HOSTS = ["ttvdrops.lovinator.space"]  # pyright: ignore[reportConstantRedefinition]

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"level": "DEBUG", "class": "logging.StreamHandler"}},
    "loggers": {
        "": {"handlers": ["console"], "level": "INFO", "propagate": True},
        "ttvdrops": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.utils.autoreload": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
    },
}

INSTALLED_APPS: list[str] = [
    # Django built-in apps
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Internal apps
    "chzzk.apps.ChzzkConfig",
    "core.apps.CoreConfig",
    "kick.apps.KickConfig",
    "twitch.apps.TwitchConfig",
    "youtube.apps.YoutubeConfig",
    # Third-party apps
    "django_celery_results",
    "django_celery_beat",
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
                "core.context_processors.base_url",
            ],
        },
    },
]


def configure_databases(*, testing: bool, base_dir: Path) -> dict[str, dict[str, Any]]:
    """Configure Django databases based on environment variables and testing mode.

    Args:
        testing (bool): Whether the application is running in testing mode.
        base_dir (Path): The base directory of the project, used for SQLite file location.

    Returns:
        dict[str, dict[str, Any]]: The DATABASES setting for Django.
    """
    use_sqlite: bool = env_bool("USE_SQLITE", default=False)

    if testing:
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
        }
    if use_sqlite:
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(base_dir / "db.sqlite3"),
            },
        }
    # Default: PostgreSQL
    return {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "ttvdrops"),
            "USER": os.getenv("POSTGRES_USER", "ttvdrops"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": env_int("POSTGRES_PORT", 5432),
            "CONN_MAX_AGE": env_int("CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": env_bool("CONN_HEALTH_CHECKS", default=True),
            "OPTIONS": {"connect_timeout": env_int("DB_CONNECT_TIMEOUT", 10)},
        },
    }


DATABASES: dict[str, dict[str, Any]] = configure_databases(
    testing=TESTING,
    base_dir=BASE_DIR,
)

if DEBUG or TESTING:
    INSTALLED_APPS.append("zeal")
    MIDDLEWARE.append("zeal.middleware.zeal_middleware")

if not TESTING:
    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar", "silk"]
    MIDDLEWARE = [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        "silk.middleware.SilkyMiddleware",
        *MIDDLEWARE,
    ]

    if not DEBUG:
        sentry_sdk.init(
            dsn="https://1aa1ac672090fb795783de0e90a2b19f@o4505228040339456.ingest.us.sentry.io/4511055670738944",
            send_default_pii=True,
            enable_logs=True,
            traces_sample_rate=1.0,
            profile_session_sample_rate=1.0,
            profile_lifecycle="trace",
        )

REDIS_URL_CACHE: str = os.getenv(
    key="REDIS_URL_CACHE",
    default="redis://localhost:6379/0",
)
REDIS_URL_CELERY: str = os.getenv(
    key="REDIS_URL_CELERY",
    default="redis://localhost:6379/1",
)

CACHES: dict[str, dict[str, str]] = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL_CACHE,
    },
}

CELERY_BROKER_URL: str = REDIS_URL_CELERY
CELERY_RESULT_BACKEND = "django-db"
CELERY_RESULT_EXTENDED = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_SOFT_TIME_LIMIT: int = 3600  # warn at 1 h
CELERY_TASK_TIME_LIMIT: int = 3900  # hard-kill at 1 h 5 min

CELERY_TASK_ROUTES: dict[str, dict[str, str]] = {
    "twitch.tasks.scan_pending_twitch_files": {"queue": "imports"},
    "twitch.tasks.import_twitch_file": {"queue": "imports"},
    "twitch.tasks.download_game_image": {"queue": "image-downloads"},
    "twitch.tasks.download_campaign_image": {"queue": "image-downloads"},
    "twitch.tasks.download_benefit_image": {"queue": "image-downloads"},
    "twitch.tasks.download_reward_campaign_image": {"queue": "image-downloads"},
    "twitch.tasks.download_all_images": {"queue": "image-downloads"},
    "twitch.tasks.import_chat_badges": {"queue": "api-fetches"},
    "twitch.tasks.backup_database": {"queue": "default"},
    "kick.tasks.import_kick_drops": {"queue": "api-fetches"},
    "chzzk.tasks.discover_chzzk_campaigns": {"queue": "api-fetches"},
    "chzzk.tasks.import_chzzk_campaign_task": {"queue": "imports"},
    "core.tasks.submit_indexnow_task": {"queue": "default"},
}

CELERY_BEAT_SCHEDULE: dict[str, Any] = {
    # Scan for new Twitch JSON drops every 10 seconds.
    "scan-pending-twitch-files": {
        "task": "twitch.tasks.scan_pending_twitch_files",
        "schedule": 10.0,
        "options": {"queue": "imports"},
    },
    # Import Kick drops from the API (:01, :16, :31, :46 each hour).
    "import-kick-drops": {
        "task": "kick.tasks.import_kick_drops",
        "schedule": crontab(minute="1,16,31,46"),
        "options": {"queue": "api-fetches"},
    },
    # Backup database nightly at 02:15.
    "backup-database": {
        "task": "twitch.tasks.backup_database",
        "schedule": crontab(hour=2, minute=15),
        "options": {"queue": "default"},
    },
    # Discover new Chzzk campaigns every 2 hours at minute 0.
    "discover-chzzk-campaigns": {
        "task": "chzzk.tasks.discover_chzzk_campaigns",
        "schedule": crontab(minute=0, hour="*/2"),
        "options": {"queue": "api-fetches"},
    },
    # Weekly full image refresh (Sunday 04:00 UTC).
    "download-all-images-weekly": {
        "task": "twitch.tasks.download_all_images",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),
        "options": {"queue": "image-downloads"},
    },
    # Weekly chat badge refresh (Sunday 03:00 UTC).
    "import-chat-badges-weekly": {
        "task": "twitch.tasks.import_chat_badges",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
        "options": {"queue": "api-fetches"},
    },
}

# Define BASE_URL for dynamic URL generation
BASE_URL: str = "https://ttvdrops.lovinator.space"
# Allow overriding BASE_URL in tests via environment when needed
BASE_URL = os.getenv("BASE_URL", BASE_URL)
