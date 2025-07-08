from __future__ import annotations

# Pytest configuration for Django testing
import os

import django
from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.contrib.auth.signals import user_logged_in


def pytest_configure() -> None:
    """Configure Django settings for pytest."""
    if not settings.configured:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()

        # Use faster password hasher for tests
        settings.PASSWORD_HASHERS = [
            "django.contrib.auth.hashers.MD5PasswordHasher",
        ]

        # Disconnect update_last_login signal to avoid unnecessary DB writes
        user_logged_in.disconnect(update_last_login)
