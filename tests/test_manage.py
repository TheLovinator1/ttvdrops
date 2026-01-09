from __future__ import annotations

import sys
import types
from typing import Never

import pytest

import manage


def test_main_importerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test main raises ImportError if django cannot be imported."""
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "")

    def import_fail(*args, **kwargs) -> Never:
        msg = "No Django"
        raise ImportError(msg)

    monkeypatch.setitem(sys.modules, "django.core.management", None)
    monkeypatch.setattr("builtins.__import__", import_fail)
    with pytest.raises(ImportError) as excinfo:
        manage.main()
    assert "Couldn't import Django" in str(excinfo.value)


def test_main_executes_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test main calls execute_from_command_line with sys.argv."""
    called: dict[str, list[str]] = {}

    def fake_execute(argv: list[str]) -> None:
        called["argv"] = argv

    fake_module = types.SimpleNamespace(execute_from_command_line=fake_execute)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "")
    monkeypatch.setitem(sys.modules, "django.core.management", fake_module)
    original_import = __import__
    monkeypatch.setattr(
        "builtins.__import__",
        lambda name, *a, **kw: fake_module if name == "django.core.management" else original_import(name, *a, **kw),
    )
    test_argv: list[str] = ["manage.py", "check"]
    monkeypatch.setattr(sys, "argv", test_argv)
    manage.main()
    assert called["argv"] == test_argv
