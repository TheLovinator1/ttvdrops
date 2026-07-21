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

    def ready(self) -> None:  # ruff:ignore[undocumented-public-method]
        logger: logging.Logger = logging.getLogger("ttvdrops.apps")

        # Patch FieldFile.open to swallow FileNotFoundError and provide
        # an empty in-memory file-like object so image dimension
        # calculations don't crash when the on-disk file was removed.
        if hasattr(FieldFile, "open"):
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
        else:
            logger.debug("FieldFile has no 'open' attribute; skipping patch")

        # Register post_save signal handlers that dispatch image download tasks
        # when new Twitch records are created.
        from django.db.models.signals import m2m_changed  # ruff:ignore[unsorted-imports, import-outside-top-level]
        from django.db.models.signals import post_save  # ruff:ignore[import-outside-top-level]

        from twitch.models import DropBenefit  # ruff:ignore[import-outside-top-level]
        from twitch.models import DropCampaign  # ruff:ignore[import-outside-top-level]
        from twitch.models import Game  # ruff:ignore[import-outside-top-level]
        from twitch.models import RewardCampaign  # ruff:ignore[import-outside-top-level]
        from twitch.signals import on_drop_benefit_saved  # ruff:ignore[import-outside-top-level]
        from twitch.signals import on_drop_campaign_allow_channels_changed  # ruff:ignore[import-outside-top-level]
        from twitch.signals import on_drop_campaign_saved  # ruff:ignore[import-outside-top-level]
        from twitch.signals import on_game_saved  # ruff:ignore[import-outside-top-level]
        from twitch.signals import on_reward_campaign_saved  # ruff:ignore[import-outside-top-level]

        post_save.connect(on_game_saved, sender=Game)
        post_save.connect(on_drop_campaign_saved, sender=DropCampaign)
        post_save.connect(on_drop_benefit_saved, sender=DropBenefit)
        post_save.connect(on_reward_campaign_saved, sender=RewardCampaign)
        m2m_changed.connect(
            on_drop_campaign_allow_channels_changed,
            sender=DropCampaign.allow_channels.through,
            dispatch_uid="twitch_drop_campaign_allow_channels_counter_cache",
        )
