from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Core application configuration."""

    name = "core"

    def ready(self) -> None:
        """Register cache invalidation signals."""
        from core import signals  # ruff: ignore[import-outside-top-level]

        signals.connect()
