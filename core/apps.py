from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Core application configuration."""

    name = "core"

    def ready(self) -> None:
        """Register cache invalidation and Bluesky posting signals."""
        from core import signals  # ruff: ignore[import-outside-top-level]
        from core import social_signals  # ruff: ignore[import-outside-top-level]

        signals.connect()
        social_signals.connect()
