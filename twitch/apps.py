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

        # Register post_save signal handlers that dispatch image download tasks
        # when new Twitch records are created.
        from django.db.models.signals import m2m_changed  # noqa: I001, PLC0415
        from django.db.models.signals import post_save  # noqa: PLC0415

        from twitch.models import DropBenefit  # noqa: PLC0415
        from twitch.models import DropCampaign  # noqa: PLC0415
        from twitch.models import Game  # noqa: PLC0415
        from twitch.models import RewardCampaign  # noqa: PLC0415
        from twitch.signals import on_drop_benefit_saved  # noqa: PLC0415
        from twitch.signals import on_drop_campaign_allow_channels_changed  # noqa: PLC0415
        from twitch.signals import on_drop_campaign_saved  # noqa: PLC0415
        from twitch.signals import on_game_saved  # noqa: PLC0415
        from twitch.signals import on_reward_campaign_saved  # noqa: PLC0415

        post_save.connect(on_game_saved, sender=Game)
        post_save.connect(on_drop_campaign_saved, sender=DropCampaign)
        post_save.connect(on_drop_benefit_saved, sender=DropBenefit)
        post_save.connect(on_reward_campaign_saved, sender=RewardCampaign)
        m2m_changed.connect(
            on_drop_campaign_allow_channels_changed,
            sender=DropCampaign.allow_channels.through,
            dispatch_uid="twitch_drop_campaign_allow_channels_counter_cache",
        )
