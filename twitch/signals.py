from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count

logger = logging.getLogger("ttvdrops.signals")


def _dispatch(task_fn: Any, pk: int) -> None:  # noqa: ANN401
    """Dispatch a Celery task, logging rather than raising when the broker is unavailable."""
    try:
        task_fn.delay(pk)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not dispatch %s(%d) — broker may be unavailable.",
            task_fn.name,
            pk,
        )


def on_game_saved(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:  # noqa: ANN401, FBT001
    """Dispatch a box-art download task when a new Game is created."""
    if created:
        from twitch.tasks import download_game_image  # noqa: PLC0415

        _dispatch(download_game_image, instance.pk)


def on_drop_campaign_saved(
    sender: Any,  # noqa: ANN401
    instance: Any,  # noqa: ANN401
    created: bool,  # noqa: FBT001
    **kwargs: Any,  # noqa: ANN401
) -> None:
    """Dispatch an image download task when a new DropCampaign is created."""
    if created:
        from twitch.tasks import download_campaign_image  # noqa: PLC0415

        _dispatch(download_campaign_image, instance.pk)


def on_drop_benefit_saved(
    sender: Any,  # noqa: ANN401
    instance: Any,  # noqa: ANN401
    created: bool,  # noqa: FBT001
    **kwargs: Any,  # noqa: ANN401
) -> None:
    """Dispatch an image download task when a new DropBenefit is created."""
    if created:
        from twitch.tasks import download_benefit_image  # noqa: PLC0415

        _dispatch(download_benefit_image, instance.pk)


def on_reward_campaign_saved(
    sender: Any,  # noqa: ANN401
    instance: Any,  # noqa: ANN401
    created: bool,  # noqa: FBT001
    **kwargs: Any,  # noqa: ANN401
) -> None:
    """Dispatch an image download task when a new RewardCampaign is created."""
    if created:
        from twitch.tasks import download_reward_campaign_image  # noqa: PLC0415

        _dispatch(download_reward_campaign_image, instance.pk)


def _refresh_allowed_campaign_counts(channel_ids: set[int]) -> None:
    """Recompute and persist cached campaign counters for the given channels."""
    if not channel_ids:
        return

    from twitch.models import Channel  # noqa: PLC0415
    from twitch.models import DropCampaign  # noqa: PLC0415

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


def on_drop_campaign_allow_channels_changed(  # noqa: PLR0913, PLR0917
    sender: Any,  # noqa: ANN401
    instance: Any,  # noqa: ANN401
    action: str,
    reverse: bool,  # noqa: FBT001
    model: Any,  # noqa: ANN401
    pk_set: set[int] | None,
    **kwargs: Any,  # noqa: ANN401
) -> None:
    """Keep Channel.allowed_campaign_count in sync for allow_channels M2M changes."""
    if action == "pre_clear" and not reverse:
        # post_clear does not expose removed channel IDs; snapshot before clearing.
        instance._pre_clear_channel_ids = set(  # pyright: ignore[reportAttributeAccessIssue]  # noqa: SLF001
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
