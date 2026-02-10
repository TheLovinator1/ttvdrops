from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.contrib.sessions.models import Session

from config import settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Generator
    from collections.abc import Iterator
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
    def temporary_env(env: dict[str, str]) -> Iterator[None]:
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
        env.setdefault("DJANGO_SECRET_KEY", original_env.get("DJANGO_SECRET_KEY", "test-secret-key"))

        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value

        with temporary_env(env):
            return importlib.reload(settings)

    yield _reload

    with temporary_env(original_env):
        importlib.reload(settings)


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


def test_get_data_dir_uses_platformdirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_allowed_hosts_when_debug_false(reload_settings_module: Callable[..., ModuleType]) -> None:
    """When DEBUG is false, ALLOWED_HOSTS should use the production host."""
    reloaded: ModuleType = reload_settings_module(DEBUG="false")

    assert reloaded.DEBUG is False
    assert reloaded.ALLOWED_HOSTS == ["ttvdrops.lovinator.space"]


def test_allowed_hosts_when_debug_true(reload_settings_module: Callable[..., ModuleType]) -> None:
    """When DEBUG is true, development hostnames should be allowed."""
    reloaded: ModuleType = reload_settings_module(DEBUG="1")

    assert reloaded.DEBUG is True
    assert reloaded.ALLOWED_HOSTS == [".localhost", "127.0.0.1", "[::1]", "testserver"]


def test_debug_defaults_true_when_missing(reload_settings_module: Callable[..., ModuleType]) -> None:
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
