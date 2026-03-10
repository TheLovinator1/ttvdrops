import io
import logging
from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.db.models.fields.files import FieldFile

if TYPE_CHECKING:
    from collections.abc import Callable


class TwitchConfig(AppConfig):
    """Django app configuration for the Twitch app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "twitch"

    def ready(self) -> None:  # noqa: D102
        logger: logging.Logger = logging.getLogger("ttvdrops.apps")

        # Patch FieldFile.open to swallow FileNotFoundError and provide
        # an empty in-memory file-like object so image dimension
        # calculations don't crash when the on-disk file was removed.
        try:
            orig_open: Callable[..., FieldFile] = FieldFile.open

            def _safe_open(self: FieldFile, mode: str = "rb") -> FieldFile:
                try:
                    return orig_open(self, mode)
                except FileNotFoundError:
                    # Provide an empty BytesIO so subsequent dimension checks
                    # read harmlessly and return (None, None).
                    self._file = io.BytesIO(b"")  # pyright: ignore[reportAttributeAccessIssue]
                    return self

            FieldFile.open = _safe_open
        except (AttributeError, TypeError) as exc:
            logger.debug("Failed to patch FieldFile.open: %s", exc)
