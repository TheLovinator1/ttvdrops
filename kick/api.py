from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from kick.models import KickDropCampaign

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet
    from django.http import HttpRequest

    from kick.models import KickCategory
    from kick.models import KickChannel
    from kick.models import KickOrganization
    from kick.models import KickReward
    from kick.models import KickUser

CampaignStatus = Literal["active", "upcoming", "expired", "unknown"]
VALID_STATUS_FILTERS: frozenset[str] = frozenset({"active", "upcoming", "expired"})
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def _datetime_to_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _campaign_status(campaign: KickDropCampaign, now: datetime) -> CampaignStatus:
    if campaign.starts_at and campaign.ends_at:
        if campaign.starts_at <= now <= campaign.ends_at:
            return "active"
        if campaign.starts_at > now:
            return "upcoming"
        return "expired"
    return "unknown"


def _serialize_category(category: KickCategory | None) -> dict[str, Any] | None:
    if category is None:
        return None
    return {
        "kick_id": category.kick_id,
        "name": category.name,
        "slug": category.slug,
        "image_url": category.image_url,
    }


def _serialize_organization(
    organization: KickOrganization | None,
) -> dict[str, Any] | None:
    if organization is None:
        return None
    return {
        "kick_id": organization.kick_id,
        "name": organization.name,
        "logo_url": organization.logo_url,
        "url": organization.url,
    }


def _serialize_user(user: KickUser | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "kick_id": user.kick_id,
        "username": user.username,
        "profile_picture": user.profile_picture,
    }


def _serialize_channel(channel: KickChannel) -> dict[str, Any]:
    return {
        "kick_id": channel.kick_id,
        "slug": channel.slug,
        "url": channel.channel_url,
        "user": _serialize_user(channel.user),
    }


def _serialize_reward(reward: KickReward) -> dict[str, Any]:
    return {
        "kick_id": reward.kick_id,
        "name": reward.name,
        "image_url": reward.full_image_url,
        "required_minutes_watched": reward.required_units,
    }


def _serialize_campaign(
    campaign: KickDropCampaign,
    now: datetime,
) -> dict[str, Any]:
    return {
        "kick_id": campaign.kick_id,
        "name": campaign.name,
        "status": _campaign_status(campaign, now),
        "image_url": campaign.image_url,
        "connect_url": campaign.connect_url,
        "url": campaign.url,
        "starts_at": _datetime_to_json(campaign.starts_at),
        "ends_at": _datetime_to_json(campaign.ends_at),
        "category": _serialize_category(campaign.category),
        "organization": _serialize_organization(campaign.organization),
        "channels": [
            _serialize_channel(channel) for channel in campaign.channels.all()
        ],
        "rewards": [_serialize_reward(reward) for reward in campaign.rewards.all()],
        "is_fully_imported": campaign.is_fully_imported,
        "added_at": _datetime_to_json(campaign.added_at),
        "updated_at": _datetime_to_json(campaign.updated_at),
    }


def _parse_integer_query(
    request: HttpRequest,
    name: str,
    default: int,
) -> int | JsonResponse:
    raw_value: str | None = request.GET.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return JsonResponse(
            {
                "detail": [
                    {
                        "type": "int_parsing",
                        "loc": ["query", name],
                        "msg": "Input should be a valid integer",
                    },
                ],
            },
            status=422,
        )


@require_GET
def campaign_list_api(request: HttpRequest) -> JsonResponse:
    """Return a paginated JSON representation of fully imported Kick campaigns."""
    page = _parse_integer_query(request, "page", 1)
    if isinstance(page, JsonResponse):
        return page
    page = max(page, 1)

    page_size = _parse_integer_query(request, "page_size", DEFAULT_PAGE_SIZE)
    if isinstance(page_size, JsonResponse):
        return page_size
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)

    status_filter: str | None = request.GET.get("status")
    if status_filter is not None and status_filter not in VALID_STATUS_FILTERS:
        return JsonResponse(
            {
                "detail": [
                    {
                        "type": "literal_error",
                        "loc": ["query", "status"],
                        "msg": "Input should be 'active', 'upcoming' or 'expired'",
                    },
                ],
            },
            status=422,
        )

    now: datetime = timezone.now()
    queryset: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .filter(is_fully_imported=True)
        .select_related("category", "organization")
        .prefetch_related("channels__user", "rewards")
        .order_by("-starts_at", "kick_id")
    )

    game_filter: int | JsonResponse | None = None
    if request.GET.get("game") is not None:
        game_filter = _parse_integer_query(request, "game", 0)
    if isinstance(game_filter, JsonResponse):
        return game_filter
    if game_filter is not None:
        queryset = queryset.filter(category__kick_id=game_filter)

    if status_filter == "active":
        queryset = queryset.filter(starts_at__lte=now, ends_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(starts_at__gt=now, ends_at__isnull=False)
    elif status_filter == "expired":
        queryset = queryset.filter(starts_at__lte=now, ends_at__lt=now)

    total: int = queryset.count()
    offset: int = (page - 1) * page_size
    campaigns: list[KickDropCampaign] = (
        list(queryset[offset : offset + page_size]) if offset < total else []
    )

    return JsonResponse({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize_campaign(campaign, now) for campaign in campaigns],
    })
