from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuration class for the 'core' Django application.

    Attributes:
        default_auto_field (str): Specifies the type of auto-created primary key field to use for models in this app.
        name (str): The full Python path to the application.
        verbose_name (str): A human-readable name for the application.
    """

    default_auto_field: str = "django.db.models.BigAutoField"
    name = "core"
    verbose_name: str = "Core Application"
