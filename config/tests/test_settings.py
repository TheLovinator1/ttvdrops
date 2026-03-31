import importlib
import os
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.contrib.sessions.models import Session

from config import settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Generator
    from pathlib import Path
    from types import ModuleType


@pytest.fixture
def reload_settings_module() -> Generator[Callable[..., ModuleType]]:
    """Reload ``config.settings`` with temporary environment overrides.

    Yields:
        Callable[..., settings]: Function that reloads the settings module using
            provided environment overrides.
    """
    original_env: dict[str, str] = os.environ.copy()

    @contextmanager
    def temporary_env(env: dict[str, str]) -> Generator[None]:
        previous_env: dict[str, str] = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(previous_env)

    def _reload(**env_overrides: str | None) -> ModuleType:
        env: dict[str, str] = os.environ.copy()
        env.setdefault(
            "DJANGO_SECRET_KEY",
            original_env.get("DJANGO_SECRET_KEY", "test-secret-key"),
        )

        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value

        with temporary_env(env):
            # If another test removed the module from sys.modules (we do that in a
            # few tests to simulate startup failure), importlib.reload() will
            # raise. Import afresh in that case so the fixture is resilient.
            if "config.settings" in sys.modules:
                return importlib.reload(sys.modules["config.settings"])
            return importlib.import_module("config.settings")

    yield _reload

    with temporary_env(original_env):
        # Ensure `config.settings` is restored even if some tests removed the
        # module from sys.modules (several tests intentionally pop it). Use
        # importlib.import_module to (re)import and then reload to execute
        # module-level initialization under the original environment.
        importlib.reload(importlib.import_module("config.settings"))


def test_env_bool_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_bool should treat common truthy strings as True."""
    truthy_values: list[str] = ["1", "true", "yes", "y", "on", "TrUe", " YES "]
    for value in truthy_values:
        monkeypatch.setenv("FEATURE_FLAG", value)
        assert settings.env_bool("FEATURE_FLAG") is True


def test_env_bool_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_bool should fall back to the provided default when unset."""
    monkeypatch.delenv("MISSING_FLAG", raising=False)
    assert settings.env_bool("MISSING_FLAG", default=False) is False
    assert settings.env_bool("MISSING_FLAG", default=True) is True


def test_env_int_parses_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_int should parse integers from the environment."""
    monkeypatch.setenv("MAX_COUNT", "5")
    assert settings.env_int("MAX_COUNT", 1) == 5


def test_env_int_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_int should return the fallback when unset."""
    monkeypatch.delenv("MAX_COUNT", raising=False)
    assert settings.env_int("MAX_COUNT", 3) == 3


def test_get_data_dir_uses_platformdirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """get_data_dir should use platformdirs and create the directory."""
    fake_dir: Path = tmp_path / "data_dir"

    def fake_user_data_dir(**_: str) -> str:
        fake_dir.mkdir(parents=True, exist_ok=True)
        return str(fake_dir)

    monkeypatch.setattr(settings, "user_data_dir", fake_user_data_dir)

    path: Path = settings.get_data_dir()

    assert path == fake_dir
    assert path.exists() is True
    assert path.is_dir() is True


def test_allowed_hosts_when_debug_false(
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """When DEBUG is false, ALLOWED_HOSTS should use the production host."""
    reloaded: ModuleType = reload_settings_module(DEBUG="false")

    assert reloaded.DEBUG is False
    assert reloaded.ALLOWED_HOSTS == ["ttvdrops.lovinator.space"]


def test_allowed_hosts_when_debug_true(
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """When DEBUG is true, development hostnames should be allowed."""
    reloaded: ModuleType = reload_settings_module(DEBUG="1")

    assert reloaded.DEBUG is True
    assert reloaded.ALLOWED_HOSTS == [".localhost", "127.0.0.1", "[::1]", "testserver"]


def test_debug_defaults_true_when_missing(
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """DEBUG should default to True when the environment variable is missing."""
    reloaded: ModuleType = reload_settings_module(DEBUG=None)

    assert reloaded.DEBUG is True


def test_sessions_app_installed() -> None:
    """Sessions app should be registered when session middleware is enabled."""
    assert "django.contrib.sessions" in settings.INSTALLED_APPS


@pytest.mark.django_db
def test_session_table_exists() -> None:
    """The sessions table should be available in the database."""
    Session.objects.count()


def test_env_bool_falsey_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_bool should treat common falsey strings as False."""
    falsy_values: list[str] = ["0", "false", "no", "n", "off", "", "  "]
    for value in falsy_values:
        monkeypatch.setenv("FEATURE_FLAG", value)
        assert settings.env_bool("FEATURE_FLAG") is False


def test_env_int_invalid_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_int should raise ValueError for non-integer strings."""
    monkeypatch.setenv("MAX_COUNT", "not-an-int")
    with pytest.raises(ValueError, match="invalid literal for int"):
        settings.env_int("MAX_COUNT", 1)


def test_testing_true_when_sys_argv_contains_test(
    monkeypatch: pytest.MonkeyPatch,
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """TESTING should be true when 'test' is present in sys.argv."""
    monkeypatch.setattr("sys.argv", ["manage.py", "test"])
    reloaded: ModuleType = reload_settings_module()

    assert reloaded.TESTING is True


def test_testing_true_when_pytest_version_set(
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """TESTING should be true when PYTEST_VERSION is set in the env."""
    reloaded: ModuleType = reload_settings_module(PYTEST_VERSION="7.0.0")

    assert reloaded.TESTING is True


def test_media_and_static_directories_created_on_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """Importing settings should create MEDIA_ROOT and STATIC_ROOT under DATA_DIR."""
    fake_dir: Path = tmp_path / "app_data"

    def fake_user_data_dir(**_: str) -> str:
        fake_dir.mkdir(parents=True, exist_ok=True)
        return str(fake_dir)

    monkeypatch.setattr("platformdirs.user_data_dir", fake_user_data_dir)
    reloaded: ModuleType = reload_settings_module()

    assert fake_dir == reloaded.DATA_DIR
    assert reloaded.MEDIA_ROOT.exists() is True
    assert reloaded.STATIC_ROOT.exists() is True


def test_missing_secret_key_causes_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing DJANGO_SECRET_KEY should abort startup with SystemExit."""
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    # Prevent load_dotenv from repopulating values from the repository .env file
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)

    # Remove the cached module so the import executes module-level code freshly
    sys.modules.pop("config.settings", None)

    with pytest.raises(SystemExit):
        __import__("config.settings")


def test_email_settings_from_env(
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """EMAIL_* values should be read from the environment and cast correctly."""
    reloaded: ModuleType = reload_settings_module(
        EMAIL_HOST="smtp.example.com",
        EMAIL_PORT="1025",
        EMAIL_USE_TLS="0",
        EMAIL_USE_SSL="1",
        EMAIL_HOST_USER="me@example.com",
        EMAIL_HOST_PASSWORD="s3cret",
        EMAIL_TIMEOUT="3",
    )

    assert reloaded.EMAIL_HOST == "smtp.example.com"
    assert reloaded.EMAIL_PORT == 1025
    assert reloaded.EMAIL_USE_TLS is False
    assert reloaded.EMAIL_USE_SSL is True
    assert reloaded.EMAIL_HOST_USER == "me@example.com"
    assert reloaded.EMAIL_HOST_PASSWORD == "s3cret"
    assert reloaded.EMAIL_TIMEOUT == 3
    # DEFAULT_FROM_EMAIL and SERVER_EMAIL mirror EMAIL_HOST_USER in settings.py
    assert reloaded.DEFAULT_FROM_EMAIL == "me@example.com"
    assert reloaded.SERVER_EMAIL == "me@example.com"


def test_database_settings_when_use_sqlite_enabled(
    monkeypatch: pytest.MonkeyPatch,
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """When USE_SQLITE=1 and not testing, DATABASES should use SQLite file."""
    monkeypatch.setattr("sys.argv", ["manage.py", "runserver"])
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.setenv("USE_SQLITE", "1")

    reloaded: ModuleType = reload_settings_module(
        TESTING=None,
        PYTEST_VERSION=None,
        POSTGRES_DB="prod_db",
        POSTGRES_USER="prod_user",
        POSTGRES_PASSWORD="secret",
        POSTGRES_HOST="db.host",
        POSTGRES_PORT="5433",
        CONN_MAX_AGE="120",
        CONN_HEALTH_CHECKS="0",
    )

    assert reloaded.TESTING is False
    db_cfg: dict[str, Any] = reloaded.DATABASES["default"]
    assert db_cfg["ENGINE"] == "django.db.backends.sqlite3"
    # Should use a file, not in-memory
    assert db_cfg["NAME"].endswith("db.sqlite3")


def test_database_settings_when_not_testing(
    monkeypatch: pytest.MonkeyPatch,
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """When not running tests, DATABASES should use the Postgres configuration."""
    # Ensure the module believes it's not running tests and USE_SQLITE is unset
    monkeypatch.setattr("sys.argv", ["manage.py", "runserver"])
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.setenv("USE_SQLITE", "0")

    reloaded: ModuleType = reload_settings_module(
        TESTING=None,
        PYTEST_VERSION=None,
        POSTGRES_DB="prod_db",
        POSTGRES_USER="prod_user",
        POSTGRES_PASSWORD="secret",
        POSTGRES_HOST="db.host",
        POSTGRES_PORT="5433",
        CONN_MAX_AGE="120",
        CONN_HEALTH_CHECKS="0",
    )

    assert reloaded.TESTING is False
    db_cfg: dict[str, Any] = reloaded.DATABASES["default"]
    assert db_cfg["ENGINE"] == "django.db.backends.postgresql"
    assert db_cfg["NAME"] == "prod_db"
    assert db_cfg["USER"] == "prod_user"
    assert db_cfg["PASSWORD"] == "secret"
    assert db_cfg["HOST"] == "db.host"
    assert db_cfg["PORT"] == 5433
    assert db_cfg["CONN_MAX_AGE"] == 120
    assert db_cfg["CONN_HEALTH_CHECKS"] is False


def test_debug_tools_installed_only_when_not_testing(
    monkeypatch: pytest.MonkeyPatch,
    reload_settings_module: Callable[..., ModuleType],
) -> None:
    """`debug_toolbar` and `silk` are only added when not TESTING."""
    # Not testing -> tools should be present
    monkeypatch.setattr("sys.argv", ["manage.py", "runserver"])
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    not_testing: ModuleType = reload_settings_module(TESTING=None, PYTEST_VERSION=None)
    assert "debug_toolbar" in not_testing.INSTALLED_APPS
    assert "silk" in not_testing.INSTALLED_APPS

    # Testing -> tools should not be present
    testing: ModuleType = reload_settings_module(PYTEST_VERSION="7.0.0")
    assert "debug_toolbar" not in testing.INSTALLED_APPS
    assert "silk" not in testing.INSTALLED_APPS


def test_logging_configuration_structure() -> None:
    """Basic logging structure and levels should be present in LOGGING."""
    assert settings.LOGGING["handlers"]["console"]["class"] == "logging.StreamHandler"
    assert settings.LOGGING["loggers"]["ttvdrops"]["level"] == "DEBUG"
    assert settings.LOGGING["loggers"][""]["level"] == "INFO"
