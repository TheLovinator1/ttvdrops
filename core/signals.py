import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models.signals import m2m_changed
from django.db.models.signals import post_delete
from django.db.models.signals import post_save

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward
from core.cache import bump_version
from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from kick.models import KickUser
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import Model

logger: logging.Logger = logging.getLogger("ttvdrops.cache")


def _bump_after_commit(*names: str) -> None:
    """Bump versions once the surrounding transaction commits."""
    unique: tuple[str, ...] = tuple(name for name in dict.fromkeys(names) if name)
    if not unique:
        return
    transaction.on_commit(lambda: bump_version(*unique))


def _foreign_key_id(instance: Model, field: str) -> int | None:
    """Return the raw database id of a foreign key.

    Django exposes ``<field>_id`` attributes at runtime, but type checkers do
    not know about attributes generated for model fields.
    """
    value: int | None = getattr(instance, f"{field}_id", None)
    return value


def _related_value(model: type[Model], pk: int | None, field: str) -> str | None:
    """Return ``field`` for ``model`` with primary key ``pk``, if it exists."""
    if pk is None:
        return None
    try:
        value = model.objects.filter(pk=pk).values_list(field, flat=True).first()
    except Exception as exc:  # ruff: ignore[blind-except]
        logger.debug("Page cache related lookup failed: %s", exc)
        return None
    return str(value) if value is not None else None


# MARK: Twitch handlers
def _twitch_campaign_changed(
    sender: type[Model],
    instance: DropCampaign,
    **kwargs: object,
) -> None:
    """Invalidate a Twitch campaign and every Twitch list page."""
    if kwargs.get("raw"):
        return
    _bump_after_commit("twitch", f"twitch:campaign:{instance.twitch_id}")


def _twitch_drop_changed(
    sender: type[Model],
    instance: TimeBasedDrop,
    **kwargs: object,
) -> None:
    """Invalidate the campaign that owns a time-based drop."""
    if kwargs.get("raw"):
        return
    campaign_id = _related_value(
        DropCampaign,
        _foreign_key_id(instance, "campaign"),
        "twitch_id",
    )
    _bump_after_commit(
        "twitch",
        f"twitch:campaign:{campaign_id}" if campaign_id else "",
    )


def _twitch_reward_campaign_changed(
    sender: type[Model],
    instance: RewardCampaign,
    **kwargs: object,
) -> None:
    """Invalidate a Twitch reward campaign and every Twitch list page."""
    if kwargs.get("raw"):
        return
    _bump_after_commit("twitch", f"twitch:reward:{instance.twitch_id}")


def _twitch_reward_changed(
    sender: type[Model],
    instance: Reward,
    **kwargs: object,
) -> None:
    """Invalidate the reward campaign that owns a reward."""
    if kwargs.get("raw"):
        return
    twitch_id = _related_value(
        RewardCampaign,
        _foreign_key_id(instance, "reward_campaign"),
        "twitch_id",
    )
    _bump_after_commit(
        "twitch",
        f"twitch:reward:{twitch_id}" if twitch_id else "",
    )


def _twitch_shared_changed(
    sender: type[Model],
    instance: Model,
    **kwargs: object,
) -> None:
    """Invalidate Twitch pages that aggregate shared data."""
    if kwargs.get("raw"):
        return
    _bump_after_commit("twitch", "twitch:shared")


def _twitch_campaign_m2m_changed(
    sender: type[Model],
    instance: Model,
    *,
    reverse: bool,
    **kwargs: object,
) -> None:
    """Invalidate a campaign after its channel allowlist changes."""
    if reverse or not isinstance(instance, DropCampaign):
        _bump_after_commit("twitch", "twitch:shared")
        return
    _bump_after_commit("twitch", f"twitch:campaign:{instance.twitch_id}")


def _twitch_shared_m2m_changed(
    sender: type[Model],
    instance: Model,
    **kwargs: object,
) -> None:
    """Invalidate aggregated Twitch pages after a shared M2M change."""
    _bump_after_commit("twitch", "twitch:shared")


# MARK: Kick handlers
def _kick_campaign_changed(
    sender: type[Model],
    instance: KickDropCampaign,
    **kwargs: object,
) -> None:
    """Invalidate a Kick campaign and every Kick list page."""
    if kwargs.get("raw"):
        return
    _bump_after_commit("kick", f"kick:campaign:{instance.kick_id}")


def _kick_reward_changed(
    sender: type[Model],
    instance: KickReward,
    **kwargs: object,
) -> None:
    """Invalidate the Kick campaign that owns a reward."""
    if kwargs.get("raw"):
        return
    kick_id = _related_value(
        KickDropCampaign,
        _foreign_key_id(instance, "campaign"),
        "kick_id",
    )
    _bump_after_commit("kick", f"kick:campaign:{kick_id}" if kick_id else "")


def _kick_shared_changed(
    sender: type[Model],
    instance: Model,
    **kwargs: object,
) -> None:
    """Invalidate Kick pages that aggregate shared data."""
    if kwargs.get("raw"):
        return
    _bump_after_commit("kick", "kick:shared")


def _kick_campaign_m2m_changed(
    sender: type[Model],
    instance: Model,
    *,
    reverse: bool,
    **kwargs: object,
) -> None:
    """Invalidate a Kick campaign after its channel list changes."""
    if reverse or not isinstance(instance, KickDropCampaign):
        _bump_after_commit("kick", "kick:shared")
        return
    _bump_after_commit("kick", f"kick:campaign:{instance.kick_id}")


# MARK: Chzzk handlers
def _chzzk_campaign_changed(
    sender: type[Model],
    instance: ChzzkCampaign,
    **kwargs: object,
) -> None:
    """Invalidate a Chzzk campaign and every Chzzk list page."""
    if kwargs.get("raw"):
        return
    _bump_after_commit("chzzk", f"chzzk:campaign:{instance.campaign_no}")


def _chzzk_reward_changed(
    sender: type[Model],
    instance: ChzzkReward,
    **kwargs: object,
) -> None:
    """Invalidate the Chzzk campaign that owns a reward."""
    if kwargs.get("raw"):
        return
    campaign_no = _related_value(
        ChzzkCampaign,
        _foreign_key_id(instance, "campaign"),
        "campaign_no",
    )
    _bump_after_commit(
        "chzzk",
        f"chzzk:campaign:{campaign_no}" if campaign_no else "",
    )


def _connect_model(
    model: type[Model],
    handler: Callable[..., None],
) -> None:
    """Connect save/delete handlers for one model."""
    label: str = model._meta.label_lower  # ruff: ignore[private-member-access]
    post_save.connect(
        handler,
        sender=model,
        dispatch_uid=f"page-cache-save:{label}:{handler.__name__}",
    )
    post_delete.connect(
        handler,
        sender=model,
        dispatch_uid=f"page-cache-delete:{label}:{handler.__name__}",
    )


def connect() -> None:
    """Connect every cache invalidation signal."""
    _connect_model(DropCampaign, _twitch_campaign_changed)
    _connect_model(TimeBasedDrop, _twitch_drop_changed)
    _connect_model(RewardCampaign, _twitch_reward_campaign_changed)
    _connect_model(Reward, _twitch_reward_changed)
    for model in (
        DropBenefit,
        DropBenefitEdge,
        Game,
        Organization,
        Channel,
        ChatBadge,
        ChatBadgeSet,
    ):
        _connect_model(model, _twitch_shared_changed)

    _connect_model(KickDropCampaign, _kick_campaign_changed)
    _connect_model(KickReward, _kick_reward_changed)
    for kick_model in (
        KickCategory,
        KickOrganization,
        KickChannel,
        KickUser,
    ):
        _connect_model(kick_model, _kick_shared_changed)

    _connect_model(ChzzkCampaign, _chzzk_campaign_changed)
    _connect_model(ChzzkReward, _chzzk_reward_changed)

    m2m_changed.connect(
        _twitch_campaign_m2m_changed,
        sender=DropCampaign.allow_channels.through,
        dispatch_uid="page-cache-m2m:twitch:allow-channels",
    )
    m2m_changed.connect(
        _twitch_shared_m2m_changed,
        sender=Game.owners.through,
        dispatch_uid="page-cache-m2m:twitch:game-owners",
    )
    m2m_changed.connect(
        _twitch_shared_m2m_changed,
        sender=DropBenefitEdge,
        dispatch_uid="page-cache-m2m:twitch:drop-benefits",
    )
    m2m_changed.connect(
        _kick_campaign_m2m_changed,
        sender=KickDropCampaign.channels.through,
        dispatch_uid="page-cache-m2m:kick:channels",
    )
