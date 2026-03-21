from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import patch

import pytest
from celery import Celery

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def celery_app() -> Generator[Celery, Any]:
    """Fixture to create a Celery app instance for testing.

    Yields:
        Celery: A Celery app instance configured for testing.
    """
    with patch("os.environ.setdefault") as mock_setenv:  # noqa: F841
        app = Celery("config")
        app.config_from_object("django.conf:settings", namespace="CELERY")
        yield app


def test_celery_app_initialization(celery_app: Celery) -> None:
    """Test that the Celery app is initialized with the correct main module."""
    assert celery_app.main == "config"


def test_celery_app_config(celery_app: Celery) -> None:
    """Test that the Celery app is configured with the correct settings."""
    with patch("celery.Celery.config_from_object") as mock_config:
        celery_app.config_from_object("django.conf:settings", namespace="CELERY")
        mock_config.assert_called_once_with("django.conf:settings", namespace="CELERY")


def test_celery_task_discovery(celery_app: Celery) -> None:
    """Test that the Celery app discovers tasks correctly."""
    with patch("celery.Celery.autodiscover_tasks") as mock_discover:
        celery_app.autodiscover_tasks()
        mock_discover.assert_called_once()
