"""Dispatch Bluesky posts when a new drop campaign is created."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save

if TYPE_CHECKING:
    from django.db.models import Model

logger: logging.Logger = logging.getLogger("ttvdrops.bluesky")


def _drop_identity(instance: Model) -> tuple[str, str] | None:
    label: str = instance._meta.label_lower  # ruff:ignore[private-member-access]
    if label == "twitch.dropcampaign":
        return "twitch", str(instance.twitch_id)  # pyright:ignore[reportAttributeAccessIssue]
    if label == "kick.kickdropcampaign":
        return "kick", str(instance.kick_id)  # pyright:ignore[reportAttributeAccessIssue]
    return None


def _enqueue(platform: str, drop_id: str) -> None:
    from core.tasks import publish_bluesky_post  # ruff:ignore[import-outside-top-level]

    try:
        # Importers may still be saving related rewards/channels after the
        # campaign row is created; a short countdown lets them finish first.
        publish_bluesky_post.apply_async(
            args=[platform, drop_id],
            countdown=10,
        )
    except Exception:  # ruff:ignore[blind-except]
        logger.debug("Could not queue Bluesky post for %s:%s", platform, drop_id)


def on_campaign_saved(
    sender: Any,  # ruff:ignore[any-type]
    instance: Model,
    created: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    **kwargs: Any,  # ruff:ignore[any-type]
) -> None:
    """Queue a Bluesky post for newly created campaign records."""
    if not created or not settings.BLUESKY_ENABLED:
        return

    from core.bluesky import bluesky_posts_suppressed  # ruff:ignore[import-outside-top-level]

    if bluesky_posts_suppressed():
        return

    identity: tuple[str, str] | None = _drop_identity(instance)
    if identity is None:
        return
    platform, drop_id = identity
    transaction.on_commit(lambda: _enqueue(platform, drop_id))


def connect() -> None:
    """Register the Bluesky post_save handlers for all drop platforms."""
    from kick.models import KickDropCampaign  # ruff:ignore[import-outside-top-level]
    from twitch.models import DropCampaign  # ruff:ignore[import-outside-top-level]

    for model in (DropCampaign, KickDropCampaign):
        label: str = model._meta.label_lower  # ruff:ignore[private-member-access]
        post_save.connect(
            on_campaign_saved,
            sender=model,
            dispatch_uid=f"bluesky-post:{label}",
        )
