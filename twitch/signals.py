from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count

logger = logging.getLogger("ttvdrops.signals")


def _dispatch(task_fn: Any, pk: int) -> None:  # ruff:ignore[any-type]
    """Dispatch a Celery task, logging rather than raising when the broker is unavailable."""
    try:
        task_fn.delay(pk)
    except Exception:  # ruff:ignore[blind-except]
        logger.debug(
            "Could not dispatch %s(%d) - broker may be unavailable.",
            task_fn.name,
            pk,
        )


def on_game_saved(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:  # ruff:ignore[any-type, boolean-type-hint-positional-argument]
    """Dispatch a box-art download task when a new Game is created."""
    if created:
        from twitch.tasks import (  # ruff:ignore[import-outside-top-level]
            download_game_image,
        )

        _dispatch(download_game_image, instance.pk)


def on_drop_campaign_saved(
    sender: Any,  # ruff:ignore[any-type]
    instance: Any,  # ruff:ignore[any-type]
    created: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    **kwargs: Any,  # ruff:ignore[any-type]
) -> None:
    """Dispatch an image download task when a new DropCampaign is created."""
    if created:
        from twitch.tasks import (  # ruff:ignore[import-outside-top-level]
            download_campaign_image,
        )

        _dispatch(download_campaign_image, instance.pk)


def on_drop_benefit_saved(
    sender: Any,  # ruff:ignore[any-type]
    instance: Any,  # ruff:ignore[any-type]
    created: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    **kwargs: Any,  # ruff:ignore[any-type]
) -> None:
    """Dispatch an image download task when a new DropBenefit is created."""
    if created:
        from twitch.tasks import (  # ruff:ignore[import-outside-top-level]
            download_benefit_image,
        )

        _dispatch(download_benefit_image, instance.pk)


def on_reward_campaign_saved(
    sender: Any,  # ruff:ignore[any-type]
    instance: Any,  # ruff:ignore[any-type]
    created: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    **kwargs: Any,  # ruff:ignore[any-type]
) -> None:
    """Dispatch an image download task when a new RewardCampaign is created."""
    if created:
        from twitch.tasks import (  # ruff:ignore[import-outside-top-level]
            download_reward_campaign_image,
        )

        _dispatch(download_reward_campaign_image, instance.pk)


def _refresh_allowed_campaign_counts(channel_ids: set[int]) -> None:
    """Recompute and persist cached campaign counters for the given channels."""
    if not channel_ids:
        return

    from twitch.models import Channel  # ruff:ignore[import-outside-top-level]
    from twitch.models import DropCampaign  # ruff:ignore[import-outside-top-level]

    through_model: type[Channel] = DropCampaign.allow_channels.through
    counts_by_channel: dict[int, int] = {
        row["channel_id"]: row["campaign_count"]
        for row in (
            through_model.objects
            .filter(channel_id__in=channel_ids)
            .values("channel_id")
            .annotate(campaign_count=Count("dropcampaign_id"))
        )
    }

    channels = list(
        Channel.objects.filter(pk__in=channel_ids).only("pk", "allowed_campaign_count"),
    )
    for channel in channels:
        channel.allowed_campaign_count = counts_by_channel.get(channel.pk, 0)

    if channels:
        Channel.objects.bulk_update(channels, ["allowed_campaign_count"])


def on_drop_campaign_allow_channels_changed(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]
    sender: Any,  # ruff:ignore[any-type]
    instance: Any,  # ruff:ignore[any-type]
    action: str,
    reverse: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    model: Any,  # ruff:ignore[any-type]
    pk_set: set[int] | None,
    **kwargs: Any,  # ruff:ignore[any-type]
) -> None:
    """Keep Channel.allowed_campaign_count in sync for allow_channels M2M changes."""
    if action == "pre_clear" and not reverse:
        # post_clear does not expose removed channel IDs; snapshot before clearing.
        instance._pre_clear_channel_ids = set(  # pyright: ignore[reportAttributeAccessIssue]  # ruff:ignore[private-member-access]
            instance.allow_channels.values_list("pk", flat=True),  # pyright: ignore[reportAttributeAccessIssue]
        )
        return

    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    channel_ids: set[int] = set()
    if reverse:
        channel_pk: int | None = getattr(instance, "pk", None)
        if isinstance(channel_pk, int):
            channel_ids.add(channel_pk)
    elif action == "post_clear":
        channel_ids = set(getattr(instance, "_pre_clear_channel_ids", set()))
    else:
        channel_ids = set(pk_set or set())

    _refresh_allowed_campaign_counts(channel_ids)
