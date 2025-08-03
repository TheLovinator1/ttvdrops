from __future__ import annotations

from django.apps import AppConfig


class TwitchConfig(AppConfig):
    """AppConfig subclass for the 'twitch' application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "twitch"
