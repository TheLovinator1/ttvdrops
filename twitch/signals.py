from __future__ import annotations

import logging
from typing import Any

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
