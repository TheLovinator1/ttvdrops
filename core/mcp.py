"""MCP server exposing the TTVDrops archive to AI clients.

The server is mounted at ``/mcp`` and offers tools for searching drop
campaigns, rewards, games, organizations, and dates across Twitch, Kick, and
Chzzk. All data served here is already public through the website and the v1
API, so the server accepts unauthenticated requests.
"""

from __future__ import annotations

import datetime
import logging
from typing import Annotated
from typing import Any
from typing import Literal

import msgspec
from django.db.models import Count
from django.db.models import Q
from django.db.models import QuerySet
from django.http import HttpRequest  # ruff: ignore[typing-only-third-party-import]
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.dateparse import parse_datetime
from django_mcpz.server import MCPServer
from django_mcpz.server import ToolError
from django_mcpz.server import public

from chzzk.models import ChzzkCampaign
from core.base_url import build_absolute_uri
from kick.models import KickCategory
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization

logger: logging.Logger = logging.getLogger("ttvdrops.mcp")

PlatformFilter = Literal["all", "twitch", "kick", "chzzk"]
StatusFilter = Literal["any", "active", "upcoming", "expired", "unknown"]
GamePlatformFilter = Literal["all", "twitch", "kick"]

MAX_LIMIT = 100
DEFAULT_LIMIT = 20

_ISO_DATE_HELP = (
    "An ISO 8601 date (YYYY-MM-DD) or datetime (for example "
    "2026-11-18T15:30:00Z). A date means the whole day in the site's timezone."
)


# MARK: Schemas
class DropSummary(msgspec.Struct):
    """A drop campaign from Twitch, Kick, or Chzzk."""

    platform: str
    id: str
    name: str
    game: str | None = None
    organization: str | None = None
    status: str = "unknown"
    starts_at: str | None = None
    ends_at: str | None = None
    url: str = ""
    reward_count: int = 0


class DropsResult(msgspec.Struct):
    """A list of drop campaigns matching a search."""

    count: int
    items: list[DropSummary]


class RewardSummary(msgspec.Struct):
    """A reward that can be earned from a drop campaign."""

    name: str
    requirement: str | None = None
    image_url: str = ""


class DropDetail(msgspec.Struct):
    """Full details for one drop campaign."""

    platform: str
    id: str
    name: str
    description: str = ""
    game: str | None = None
    organization: str | None = None
    status: str = "unknown"
    starts_at: str | None = None
    ends_at: str | None = None
    url: str = ""
    image_url: str = ""
    channels: list[str] = msgspec.field(default_factory=list)
    rewards: list[RewardSummary] = msgspec.field(default_factory=list)


class GameSummary(msgspec.Struct):
    """A game or category tracked by the site."""

    platform: str
    id: str
    name: str
    slug: str = ""
    campaign_count: int = 0
    active_campaign_count: int = 0
    url: str = ""


class GamesResult(msgspec.Struct):
    """A list of games or categories matching a search."""

    count: int
    items: list[GameSummary]


class OrganizationSummary(msgspec.Struct):
    """An organization or publisher tracked by the site."""

    platform: str
    id: str
    name: str
    logo_url: str = ""
    campaign_count: int = 0
    active_campaign_count: int = 0
    url: str = ""


class OrganizationsResult(msgspec.Struct):
    """A list of organizations matching a search."""

    count: int
    items: list[OrganizationSummary]


class PlatformStats(msgspec.Struct):
    """Campaign counts for one platform."""

    campaigns: int
    active: int
    upcoming: int
    expired: int
    unknown: int


class StatsResult(msgspec.Struct):
    """Campaign counts for every platform, plus a combined total."""

    total: int
    twitch: PlatformStats
    kick: PlatformStats
    chzzk: PlatformStats


class SearchDropsParams(msgspec.Struct):
    """Filters for searching drop campaigns across platforms."""

    query: Annotated[
        str | None,
        msgspec.Meta(
            description=(
                "Free-text search over campaign names, descriptions, games, "
                "categories, and organizations."
            ),
        ),
    ] = None
    platform: Annotated[
        PlatformFilter,
        msgspec.Meta(description="Platform to search, or 'all'."),
    ] = "all"
    status: Annotated[
        StatusFilter,
        msgspec.Meta(
            description=(
                "Campaign status: 'active' (running now), 'upcoming', "
                "'expired', 'unknown' (missing dates), or 'any'."
            ),
        ),
    ] = "any"
    game: Annotated[
        str | None,
        msgspec.Meta(
            description="Game or category name, slug, or platform ID to filter by.",
        ),
    ] = None
    organization: Annotated[
        str | None,
        msgspec.Meta(
            description="Organization or publisher name or platform ID to filter by.",
        ),
    ] = None
    starts_after: Annotated[
        str | None,
        msgspec.Meta(
            description=f"When the campaign starts at or after. {_ISO_DATE_HELP}",
        ),
    ] = None
    starts_before: Annotated[
        str | None,
        msgspec.Meta(
            description=f"When the campaign starts at or before. {_ISO_DATE_HELP}",
        ),
    ] = None
    ends_after: Annotated[
        str | None,
        msgspec.Meta(
            description=f"When the campaign ends at or after. {_ISO_DATE_HELP}",
        ),
    ] = None
    ends_before: Annotated[
        str | None,
        msgspec.Meta(
            description=f"When the campaign ends at or before. {_ISO_DATE_HELP}",
        ),
    ] = None
    limit: Annotated[
        int,
        msgspec.Meta(
            ge=1,
            le=MAX_LIMIT,
            description=f"Maximum number of campaigns to return (1-{MAX_LIMIT}).",
        ),
    ] = DEFAULT_LIMIT


class DropsBetweenDatesParams(msgspec.Struct):
    """Filters for listing campaigns running during a date range."""

    start_date: Annotated[
        str,
        msgspec.Meta(description=f"Start of the range. {_ISO_DATE_HELP}"),
    ]
    end_date: Annotated[
        str,
        msgspec.Meta(description=f"End of the range, inclusive. {_ISO_DATE_HELP}"),
    ]
    platform: Annotated[
        PlatformFilter,
        msgspec.Meta(description="Platform to search, or 'all'."),
    ] = "all"
    limit: Annotated[
        int,
        msgspec.Meta(
            ge=1,
            le=MAX_LIMIT,
            description=f"Maximum number of campaigns to return (1-{MAX_LIMIT}).",
        ),
    ] = 50


class GetDropParams(msgspec.Struct):
    """Identifies one drop campaign."""

    platform: Annotated[
        Literal["twitch", "kick", "chzzk"],
        msgspec.Meta(description="Platform the campaign belongs to."),
    ]
    id: Annotated[
        str,
        msgspec.Meta(
            description=(
                "Platform campaign ID: a Twitch campaign ID, a Kick campaign "
                "ULID, or a Chzzk campaign number."
            ),
        ),
    ]


class SearchGamesParams(msgspec.Struct):
    """Filters for searching games and categories."""

    query: Annotated[
        str | None,
        msgspec.Meta(description="Free-text search over game and category names."),
    ] = None
    platform: Annotated[
        GamePlatformFilter,
        msgspec.Meta(description="Platform to search, or 'all'."),
    ] = "all"
    limit: Annotated[
        int,
        msgspec.Meta(
            ge=1,
            le=MAX_LIMIT,
            description=f"Maximum number of games to return (1-{MAX_LIMIT}).",
        ),
    ] = DEFAULT_LIMIT


class SearchOrganizationsParams(msgspec.Struct):
    """Filters for searching organizations and publishers."""

    query: Annotated[
        str | None,
        msgspec.Meta(description="Free-text search over organization names."),
    ] = None
    platform: Annotated[
        GamePlatformFilter,
        msgspec.Meta(description="Platform to search, or 'all'."),
    ] = "all"
    limit: Annotated[
        int,
        msgspec.Meta(
            ge=1,
            le=MAX_LIMIT,
            description=f"Maximum number of organizations to return (1-{MAX_LIMIT}).",
        ),
    ] = DEFAULT_LIMIT


# MARK: Server
server = MCPServer(
    name="ttvdrops",
    version="1.0.0",
    title="TTVDrops",
    description=(
        "Search the TTVDrops archive of limited-time free rewards across "
        "Twitch, Kick, and Chzzk."
    ),
    website_url=build_absolute_uri("/"),
    instructions=(
        "Use search_drops to find drop campaigns by text, platform, status, "
        "game, organization, or date. Use list_drops_between_dates to answer "
        "questions about what is running during a period. Use get_drop for "
        "the rewards and watch requirements of one campaign. Dates are ISO "
        "8601 and all timestamps are UTC unless an offset is given."
    ),
    auth=public,
)

# MARK: Helpers


def _parse_bound(value: str | None, *, end_of_day: bool) -> datetime.datetime | None:
    """Parse an ISO 8601 date or datetime into an aware datetime.

    Returns:
        The parsed datetime, or None when no value was given.

    Raises:
        ToolError: If the value is not a valid ISO 8601 date or datetime.
    """
    if not value:
        return None

    parsed: datetime.datetime | None = parse_datetime(value)
    if parsed is None:
        parsed_date: datetime.date | None = parse_date(value)
        if parsed_date is None:
            msg = (
                f"Invalid date {value!r}. Use ISO 8601, such as 2026-11-18 "
                "or 2026-11-18T15:30:00Z."
            )
            raise ToolError(
                msg,
            )
        parsed = datetime.datetime.combine(
            parsed_date,
            datetime.time.max if end_of_day else datetime.time.min,
        )

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _campaign_status(
    starts_at: datetime.datetime | None,
    ends_at: datetime.datetime | None,
    now: datetime.datetime,
) -> str:
    """Return the active, upcoming, expired, or unknown status of a campaign."""
    if starts_at is None or ends_at is None:
        return "unknown"
    if starts_at <= now <= ends_at:
        return "active"
    if starts_at > now:
        return "upcoming"
    return "expired"


def _apply_status_filter(
    queryset: QuerySet[Any],
    status: StatusFilter,
    now: datetime.datetime,
    *,
    start_field: str,
    end_field: str,
) -> QuerySet[Any]:
    """Apply a campaign status filter to a queryset.

    Returns:
        The filtered queryset.
    """
    if status == "active":
        return queryset.filter(
            **{f"{start_field}__lte": now, f"{end_field}__gte": now},
        )
    if status == "upcoming":
        return queryset.filter(**{f"{start_field}__gt": now})
    if status == "expired":
        return queryset.filter(**{f"{end_field}__lt": now})
    if status == "unknown":
        return queryset.filter(
            Q(**{f"{start_field}__isnull": True}) | Q(**{f"{end_field}__isnull": True}),
        )
    return queryset


def _apply_date_bounds(
    queryset: QuerySet[Any],
    *,
    start_field: str,
    end_field: str,
    starts_after: datetime.datetime | None,
    starts_before: datetime.datetime | None,
    ends_after: datetime.datetime | None,
    ends_before: datetime.datetime | None,
) -> QuerySet[Any]:
    """Apply independent lower and upper bounds to a campaign's dates.

    Returns:
        The filtered queryset.
    """
    filters: dict[str, datetime.datetime] = {}
    if starts_after is not None:
        filters[f"{start_field}__gte"] = starts_after
    if starts_before is not None:
        filters[f"{start_field}__lte"] = starts_before
    if ends_after is not None:
        filters[f"{end_field}__gte"] = ends_after
    if ends_before is not None:
        filters[f"{end_field}__lte"] = ends_before
    return queryset.filter(**filters) if filters else queryset


def _iso(value: datetime.datetime | None) -> str | None:
    """Format a datetime as an ISO 8601 string.

    Returns:
        The ISO 8601 string, or None when no datetime was given.
    """
    return value.isoformat() if value is not None else None


def _absolute(path: str) -> str:
    """Return an absolute URL for a site path."""
    return build_absolute_uri(path)


def _first_owner_name(game: Game) -> str | None:
    """Return the first owner organization's name without an extra query."""
    owners = getattr(game, "owners_for_detail", None)
    if owners is None:
        owners = list(game.owners.all())  # pyright: ignore[reportAttributeAccessIssue]
    return owners[0].name if owners else None


def _watch_requirement(minutes: int | None, subs: int | None) -> str | None:
    """Describe the watch or subscription requirement for a reward.

    Returns:
        A human-readable requirement, or None when there is none.
    """
    parts: list[str] = []
    if minutes:
        parts.append(f"Watch {minutes} minutes")
    if subs:
        parts.append(f"Requires {subs} subscription{'s' if subs != 1 else ''}")
    return " and ".join(parts) if parts else None


def _sort_drops(items: list[DropSummary], *, newest_first: bool) -> list[DropSummary]:
    """Sort campaigns by start date, keeping campaigns without dates last.

    Returns:
        The sorted campaign list.
    """
    dated = [item for item in items if item.starts_at]
    undated = [item for item in items if not item.starts_at]
    dated.sort(key=lambda item: item.starts_at or "", reverse=newest_first)
    return [*dated, *undated]


def _twitch_drop_summaries(
    params: SearchDropsParams,
    *,
    overlaps: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> list[DropSummary]:
    """Search Twitch drop campaigns and return compact summaries.

    Returns:
        Matching campaigns as summaries.
    """
    now: datetime.datetime = timezone.now()
    starts_after = _parse_bound(params.starts_after, end_of_day=False)
    starts_before = _parse_bound(params.starts_before, end_of_day=True)
    ends_after = _parse_bound(params.ends_after, end_of_day=False)
    ends_before = _parse_bound(params.ends_before, end_of_day=True)

    queryset = (
        DropCampaign.objects
        .filter(is_fully_imported=True)
        .select_related("game")
        .prefetch_related("game__owners")
        .annotate(
            reward_count=Count("time_based_drops__benefits", distinct=True),
        )
    )

    if params.query:
        queryset = queryset.filter(
            Q(name__icontains=params.query)
            | Q(description__icontains=params.query)
            | Q(game__display_name__icontains=params.query)
            | Q(game__name__icontains=params.query),
        )
    if params.game:
        queryset = queryset.filter(
            Q(game__display_name__icontains=params.game)
            | Q(game__name__icontains=params.game)
            | Q(game__slug__iexact=params.game)
            | Q(game__twitch_id=params.game),
        )
    if params.organization:
        queryset = queryset.filter(
            Q(game__owners__name__icontains=params.organization)
            | Q(game__owners__twitch_id=params.organization),
        ).distinct()

    if overlaps is not None:
        range_start, range_end = overlaps
        queryset = queryset.filter(start_at__lte=range_end, end_at__gte=range_start)
    else:
        queryset = _apply_status_filter(
            queryset,
            params.status,
            now,
            start_field="start_at",
            end_field="end_at",
        )
        queryset = _apply_date_bounds(
            queryset,
            start_field="start_at",
            end_field="end_at",
            starts_after=starts_after,
            starts_before=starts_before,
            ends_after=ends_after,
            ends_before=ends_before,
        )

    order = "start_at" if overlaps is not None else "-start_at"
    campaigns = list(queryset.order_by(order)[: params.limit])
    return [
        DropSummary(
            platform="twitch",
            id=campaign.twitch_id,  # pyright: ignore[reportAttributeAccessIssue]
            name=campaign.name,  # pyright: ignore[reportAttributeAccessIssue]
            game=campaign.game.get_game_name,  # pyright: ignore[reportAttributeAccessIssue]
            organization=_first_owner_name(campaign.game),  # pyright: ignore[reportAttributeAccessIssue]
            status=_campaign_status(campaign.start_at, campaign.end_at, now),  # pyright: ignore[reportAttributeAccessIssue]
            starts_at=_iso(campaign.start_at),  # pyright: ignore[reportAttributeAccessIssue]
            ends_at=_iso(campaign.end_at),  # pyright: ignore[reportAttributeAccessIssue]
            url=_absolute(reverse("twitch:campaign_detail", args=[campaign.twitch_id])),  # pyright: ignore[reportAttributeAccessIssue]
            reward_count=int(getattr(campaign, "reward_count", 0) or 0),
        )
        for campaign in campaigns
    ]


def _kick_drop_summaries(
    params: SearchDropsParams,
    *,
    overlaps: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> list[DropSummary]:
    """Search Kick drop campaigns and return compact summaries.

    Returns:
        Matching campaigns as summaries.
    """
    now: datetime.datetime = timezone.now()
    starts_after = _parse_bound(params.starts_after, end_of_day=False)
    starts_before = _parse_bound(params.starts_before, end_of_day=True)
    ends_after = _parse_bound(params.ends_after, end_of_day=False)
    ends_before = _parse_bound(params.ends_before, end_of_day=True)

    queryset = (
        KickDropCampaign.objects
        .filter(is_fully_imported=True)
        .select_related("category", "organization")
        .annotate(reward_count=Count("rewards", distinct=True))
    )

    if params.query:
        queryset = queryset.filter(
            Q(name__icontains=params.query)
            | Q(category__name__icontains=params.query)
            | Q(organization__name__icontains=params.query),
        )
    if params.game:
        game_filters = Q(category__name__icontains=params.game) | Q(
            category__slug__iexact=params.game,
        )
        if params.game.isdigit():
            game_filters |= Q(category__kick_id=int(params.game))
        queryset = queryset.filter(game_filters)
    if params.organization:
        queryset = queryset.filter(
            Q(organization__name__icontains=params.organization)
            | Q(organization__kick_id=params.organization),
        )

    if overlaps is not None:
        range_start, range_end = overlaps
        queryset = queryset.filter(
            starts_at__lte=range_end,
            ends_at__gte=range_start,
        )
    else:
        queryset = _apply_status_filter(
            queryset,
            params.status,
            now,
            start_field="starts_at",
            end_field="ends_at",
        )
        queryset = _apply_date_bounds(
            queryset,
            start_field="starts_at",
            end_field="ends_at",
            starts_after=starts_after,
            starts_before=starts_before,
            ends_after=ends_after,
            ends_before=ends_before,
        )

    order = "starts_at" if overlaps is not None else "-starts_at"
    campaigns = list(queryset.order_by(order)[: params.limit])
    return [
        DropSummary(
            platform="kick",
            id=campaign.kick_id,
            name=campaign.name,
            game=campaign.category.name if campaign.category else None,
            organization=campaign.organization.name if campaign.organization else None,
            status=_campaign_status(campaign.starts_at, campaign.ends_at, now),
            starts_at=_iso(campaign.starts_at),
            ends_at=_iso(campaign.ends_at),
            url=_absolute(reverse("kick:campaign_detail", args=[campaign.kick_id])),
            reward_count=int(getattr(campaign, "reward_count", 0) or 0),
        )
        for campaign in campaigns
    ]


def _chzzk_drop_summaries(
    params: SearchDropsParams,
    *,
    overlaps: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> list[DropSummary]:
    """Search Chzzk drop campaigns and return compact summaries.

    Returns:
        Matching campaigns as summaries.
    """
    now: datetime.datetime = timezone.now()
    starts_after = _parse_bound(params.starts_after, end_of_day=False)
    starts_before = _parse_bound(params.starts_before, end_of_day=True)
    ends_after = _parse_bound(params.ends_after, end_of_day=False)
    ends_before = _parse_bound(params.ends_before, end_of_day=True)

    queryset = ChzzkCampaign.objects.exclude(state="TESTING").annotate(
        reward_count=Count("rewards", distinct=True),
    )

    if params.query:
        queryset = queryset.filter(
            Q(title__icontains=params.query)
            | Q(description__icontains=params.query)
            | Q(category_value__icontains=params.query),
        )
    if params.game:
        queryset = queryset.filter(
            Q(category_value__icontains=params.game)
            | Q(category_id__iexact=params.game),
        )
    if params.organization:
        queryset = queryset.filter(service_id__icontains=params.organization)

    if overlaps is not None:
        range_start, range_end = overlaps
        queryset = queryset.filter(
            start_date__lte=range_end,
            end_date__gte=range_start,
        )
    else:
        queryset = _apply_status_filter(
            queryset,
            params.status,
            now,
            start_field="start_date",
            end_field="end_date",
        )
        queryset = _apply_date_bounds(
            queryset,
            start_field="start_date",
            end_field="end_date",
            starts_after=starts_after,
            starts_before=starts_before,
            ends_after=ends_after,
            ends_before=ends_before,
        )

    order = "start_date" if overlaps is not None else "-start_date"
    campaigns = list(queryset.order_by(order)[: params.limit])
    return [
        DropSummary(
            platform="chzzk",
            id=str(campaign.campaign_no),
            name=campaign.title,
            game=campaign.category_value or campaign.category_id,
            organization=None,
            status=_campaign_status(campaign.start_date, campaign.end_date, now),
            starts_at=_iso(campaign.start_date),
            ends_at=_iso(campaign.end_date),
            url=_absolute(
                reverse("chzzk:campaign_detail", args=[campaign.campaign_no]),
            ),
            reward_count=int(getattr(campaign, "reward_count", 0) or 0),
        )
        for campaign in campaigns
    ]


def _search_drop_summaries(
    params: SearchDropsParams,
    *,
    overlaps: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> list[DropSummary]:
    """Search the selected platforms and merge their campaigns.

    Returns:
        The merged campaigns, limited and ordered by start date.
    """
    results: list[DropSummary] = []
    if params.platform in {"all", "twitch"}:
        results.extend(_twitch_drop_summaries(params, overlaps=overlaps))
    if params.platform in {"all", "kick"}:
        results.extend(_kick_drop_summaries(params, overlaps=overlaps))
    if params.platform in {"all", "chzzk"}:
        results.extend(_chzzk_drop_summaries(params, overlaps=overlaps))

    newest_first = overlaps is None
    return _sort_drops(results, newest_first=newest_first)[: params.limit]


# MARK: Tools
@server.tool(
    description=(
        "Search drop campaigns across Twitch, Kick, and Chzzk by text, "
        "platform, status, game, organization, and start/end dates. Returns "
        "campaign dates, links, and reward counts. Use this to find drops "
        "matching a game, a keyword, or a time window."
    ),
    read_only=True,
)
def search_drops(request: HttpRequest, params: SearchDropsParams) -> DropsResult:
    """Search drop campaigns across every supported platform.

    Returns:
        The matching campaigns.
    """
    items = _search_drop_summaries(params)
    return DropsResult(count=len(items), items=items)


@server.tool(
    description=(
        "List drop campaigns that are running at any point between two "
        "dates. The range is inclusive, and campaigns that start before the "
        "range or end after it are included when they overlap it. Results "
        "are ordered by start date."
    ),
    read_only=True,
)
def list_drops_between_dates(
    request: HttpRequest,
    params: DropsBetweenDatesParams,
) -> DropsResult:
    """List campaigns that overlap an inclusive date range.

    Returns:
        The matching campaigns.

    Raises:
        ToolError: If either date is missing or the range is reversed.
    """
    range_start = _parse_bound(params.start_date, end_of_day=False)
    range_end = _parse_bound(params.end_date, end_of_day=True)
    if range_start is None or range_end is None:
        msg = "Both start_date and end_date are required."
        raise ToolError(msg)
    if range_start > range_end:
        msg = "start_date must be before or equal to end_date."
        raise ToolError(msg)

    search = SearchDropsParams(platform=params.platform, limit=params.limit)
    items = _search_drop_summaries(search, overlaps=(range_start, range_end))
    return DropsResult(count=len(items), items=items)


@server.tool(
    description=(
        "Get full details for one drop campaign, including its rewards, "
        "watch requirements, dates, channels, and links. Pass the platform "
        "and the campaign ID returned by search_drops."
    ),
    read_only=True,
)
def get_drop(request: HttpRequest, params: GetDropParams) -> DropDetail:
    """Return full details for one campaign."""
    return load_drop_detail(params.platform, params.id)


def load_drop_detail(platform: str, drop_id: str) -> DropDetail:
    """Return full details for a campaign on any supported platform.

    Raises:
        ValueError: If the platform is not supported.
    """
    if platform == "twitch":
        return _get_twitch_drop(drop_id)
    if platform == "kick":
        return _get_kick_drop(drop_id)
    if platform == "chzzk":
        return _get_chzzk_drop(drop_id)
    msg = f"Unknown drop platform: {platform!r}"
    raise ValueError(msg)


def _get_twitch_drop(campaign_id: str) -> DropDetail:
    """Return details for a Twitch drop campaign.

    Returns:
        The campaign details.

    Raises:
        ToolError: If the campaign does not exist.
    """
    try:
        campaign = DropCampaign.for_detail_view(campaign_id)  # pyright: ignore[reportCallIssue, reportArgumentType]
    except DropCampaign.DoesNotExist as exc:
        msg = f"No Twitch drop campaign with id {campaign_id!r}."
        raise ToolError(msg) from exc

    rewards: list[RewardSummary] = []
    seen_benefits: set[str] = set()
    for drop in campaign.time_based_drops.all():
        for benefit in drop.benefits.all():
            if benefit.twitch_id in seen_benefits:
                continue
            seen_benefits.add(benefit.twitch_id)
            rewards.append(
                RewardSummary(
                    name=benefit.name,
                    requirement=_watch_requirement(
                        drop.required_minutes_watched,
                        drop.required_subs,
                    ),
                    image_url=benefit.image_best_url,
                ),
            )

    channels = getattr(campaign, "channels_ordered", None)
    if channels is None:
        channels = list(campaign.allow_channels.all())

    owners = getattr(campaign.game, "owners_for_detail", None)
    if owners is None:
        owners = list(campaign.game.owners.all())

    return DropDetail(
        platform="twitch",
        id=campaign.twitch_id,
        name=campaign.name,
        description=campaign.description,
        game=campaign.game.get_game_name,
        organization=owners[0].name if owners else None,
        status=_campaign_status(campaign.start_at, campaign.end_at, timezone.now()),
        starts_at=_iso(campaign.start_at),
        ends_at=_iso(campaign.end_at),
        url=_absolute(reverse("twitch:campaign_detail", args=[campaign.twitch_id])),
        image_url=campaign.image_best_url,
        channels=[channel.display_name or channel.name for channel in channels],
        rewards=rewards,
    )


def _get_kick_drop(campaign_id: str) -> DropDetail:
    """Return details for a Kick drop campaign.

    Returns:
        The campaign details.

    Raises:
        ToolError: If the campaign does not exist.
    """
    try:
        campaign = (
            KickDropCampaign.objects
            .select_related("category", "organization")
            .prefetch_related("rewards", "channels__user")
            .get(kick_id=campaign_id)
        )
    except KickDropCampaign.DoesNotExist as exc:
        msg = f"No Kick drop campaign with id {campaign_id!r}."
        raise ToolError(msg) from exc

    rewards = [
        RewardSummary(
            name=reward.name,
            requirement=_watch_requirement(reward.required_units, None),
            image_url=reward.full_image_url,
        )
        for reward in campaign.rewards.all()
    ]

    return DropDetail(
        platform="kick",
        id=campaign.kick_id,
        name=campaign.name,
        description="",
        game=campaign.category.name if campaign.category else None,
        organization=campaign.organization.name if campaign.organization else None,
        status=_campaign_status(campaign.starts_at, campaign.ends_at, timezone.now()),
        starts_at=_iso(campaign.starts_at),
        ends_at=_iso(campaign.ends_at),
        url=_absolute(reverse("kick:campaign_detail", args=[campaign.kick_id])),
        image_url=campaign.image_url,
        channels=[
            channel.slug or str(channel.kick_id) for channel in campaign.channels.all()
        ],
        rewards=rewards,
    )


def _get_chzzk_drop(campaign_id: str) -> DropDetail:
    """Return details for a Chzzk drop campaign.

    Returns:
        The campaign details.

    Raises:
        ToolError: If the campaign ID is not numeric or the campaign is missing.
    """
    try:
        campaign_no = int(campaign_id)
    except ValueError as exc:
        msg = f"Chzzk campaign IDs are numbers, not {campaign_id!r}."
        raise ToolError(
            msg,
        ) from exc

    try:
        campaign = ChzzkCampaign.objects.prefetch_related("rewards").get(
            campaign_no=campaign_no,
        )
    except ChzzkCampaign.DoesNotExist as exc:
        msg = f"No Chzzk drop campaign with id {campaign_id!r}."
        raise ToolError(
            msg,
        ) from exc

    rewards = [
        RewardSummary(
            name=reward.title,
            requirement=_watch_requirement(reward.condition_for_minutes, None),
            image_url=reward.image_url,
        )
        for reward in campaign.rewards.all()
    ]

    return DropDetail(
        platform="chzzk",
        id=str(campaign.campaign_no),
        name=campaign.title,
        description=campaign.description,
        game=campaign.category_value or campaign.category_id,
        organization=None,
        status=_campaign_status(
            campaign.start_date,
            campaign.end_date,
            timezone.now(),
        ),
        starts_at=_iso(campaign.start_date),
        ends_at=_iso(campaign.end_date),
        url=_absolute(
            reverse("chzzk:campaign_detail", args=[campaign.campaign_no]),
        ),
        image_url=campaign.image_url,
        channels=[],
        rewards=rewards,
    )


@server.tool(
    description=(
        "Search tracked games and categories across Twitch and Kick, with "
        "total and currently active campaign counts. Use this to resolve a "
        "game name before searching for its drops."
    ),
    read_only=True,
)
def search_games(request: HttpRequest, params: SearchGamesParams) -> GamesResult:
    """Search games and categories across Twitch and Kick.

    Returns:
        The matching games and categories.
    """
    now: datetime.datetime = timezone.now()
    items: list[GameSummary] = []

    if params.platform in {"all", "twitch"}:
        games = Game.objects.annotate(
            campaign_count=Count(
                "drop_campaigns",
                filter=Q(drop_campaigns__is_fully_imported=True),
                distinct=True,
            ),
            active_campaign_count=Count(
                "drop_campaigns",
                filter=Q(
                    drop_campaigns__is_fully_imported=True,
                    drop_campaigns__start_at__lte=now,
                    drop_campaigns__end_at__gte=now,
                ),
                distinct=True,
            ),
        ).order_by("display_name")
        if params.query:
            games = games.filter(
                Q(display_name__icontains=params.query)
                | Q(name__icontains=params.query)
                | Q(slug__icontains=params.query)
                | Q(twitch_id=params.query),
            )
        items.extend(
            GameSummary(
                platform="twitch",
                id=game.twitch_id,  # pyright: ignore[reportAttributeAccessIssue]
                name=game.get_game_name,  # pyright: ignore[reportAttributeAccessIssue]
                slug=game.slug,  # pyright: ignore[reportAttributeAccessIssue]
                campaign_count=int(game.campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
                active_campaign_count=int(game.active_campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
                url=_absolute(reverse("twitch:game_detail", args=[game.twitch_id])),  # pyright: ignore[reportAttributeAccessIssue]
            )
            for game in games[: params.limit]
        )

    if params.platform in {"all", "kick"}:
        categories = KickCategory.objects.annotate(
            campaign_count=Count(
                "campaigns",
                filter=Q(campaigns__is_fully_imported=True),
                distinct=True,
            ),
            active_campaign_count=Count(
                "campaigns",
                filter=Q(
                    campaigns__is_fully_imported=True,
                    campaigns__starts_at__lte=now,
                    campaigns__ends_at__gte=now,
                ),
                distinct=True,
            ),
        ).order_by("name")
        if params.query:
            category_filters = Q(name__icontains=params.query) | Q(
                slug__icontains=params.query,
            )
            if params.query.isdigit():
                category_filters |= Q(kick_id=int(params.query))
            categories = categories.filter(category_filters)
        items.extend(
            GameSummary(
                platform="kick",
                id=str(category.kick_id),
                name=category.name,
                slug=category.slug,
                campaign_count=int(category.campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
                active_campaign_count=int(category.active_campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
                url=_absolute(
                    reverse("kick:game_detail", args=[category.kick_id]),
                ),
            )
            for category in categories[: params.limit]
        )

    items.sort(key=lambda item: item.name.casefold())
    return GamesResult(count=len(items), items=items)


def _twitch_organization_summaries(
    params: SearchOrganizationsParams,
    now: datetime.datetime,
) -> list[OrganizationSummary]:
    """Search Twitch organizations.

    Returns:
        Matching organizations as summaries.
    """
    organizations = Organization.objects.annotate(
        campaign_count=Count(
            "games__drop_campaigns",
            filter=Q(games__drop_campaigns__is_fully_imported=True),
            distinct=True,
        ),
        active_campaign_count=Count(
            "games__drop_campaigns",
            filter=Q(
                games__drop_campaigns__is_fully_imported=True,
                games__drop_campaigns__start_at__lte=now,
                games__drop_campaigns__end_at__gte=now,
            ),
            distinct=True,
        ),
    ).order_by("name")
    if params.query:
        organizations = organizations.filter(
            Q(name__icontains=params.query) | Q(twitch_id=params.query),
        )
    return [
        OrganizationSummary(
            platform="twitch",
            id=organization.twitch_id,  # pyright: ignore[reportAttributeAccessIssue]
            name=organization.name,  # pyright: ignore[reportAttributeAccessIssue]
            campaign_count=int(organization.campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
            active_campaign_count=int(organization.active_campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
            url=_absolute(
                reverse("twitch:organization_detail", args=[organization.twitch_id]),  # pyright: ignore[reportAttributeAccessIssue]
            ),
        )
        for organization in organizations[: params.limit]
    ]


def _kick_organization_summaries(
    params: SearchOrganizationsParams,
    now: datetime.datetime,
) -> list[OrganizationSummary]:
    """Search Kick organizations.

    Returns:
        Matching organizations as summaries.
    """
    organizations = KickOrganization.objects.annotate(
        campaign_count=Count(
            "campaigns",
            filter=Q(campaigns__is_fully_imported=True),
            distinct=True,
        ),
        active_campaign_count=Count(
            "campaigns",
            filter=Q(
                campaigns__is_fully_imported=True,
                campaigns__starts_at__lte=now,
                campaigns__ends_at__gte=now,
            ),
            distinct=True,
        ),
    ).order_by("name")
    if params.query:
        organizations = organizations.filter(
            Q(name__icontains=params.query) | Q(kick_id=params.query),
        )
    return [
        OrganizationSummary(
            platform="kick",
            id=organization.kick_id,
            name=organization.name,
            logo_url=organization.logo_url,
            campaign_count=int(organization.campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
            active_campaign_count=int(organization.active_campaign_count),  # pyright: ignore[reportAttributeAccessIssue]
            url=_absolute(
                reverse("kick:organization_detail", args=[organization.kick_id]),
            ),
        )
        for organization in organizations[: params.limit]
    ]


@server.tool(
    description=(
        "Search organizations and publishers tracked for Twitch and Kick "
        "drops, with total and currently active campaign counts."
    ),
    read_only=True,
)
def search_organizations(
    request: HttpRequest,
    params: SearchOrganizationsParams,
) -> OrganizationsResult:
    """Search organizations across Twitch and Kick.

    Returns:
        The matching organizations.
    """
    now: datetime.datetime = timezone.now()
    items: list[OrganizationSummary] = []
    if params.platform in {"all", "twitch"}:
        items.extend(_twitch_organization_summaries(params, now))
    if params.platform in {"all", "kick"}:
        items.extend(_kick_organization_summaries(params, now))

    items.sort(key=lambda item: item.name.casefold())
    return OrganizationsResult(count=len(items), items=items)


def _platform_stats(
    queryset: QuerySet[Any],
    now: datetime.datetime,
    *,
    start_field: str,
    end_field: str,
) -> PlatformStats:
    """Count campaigns by status for one platform.

    Returns:
        The campaign counts for the platform.
    """
    counts: dict[str, int] = queryset.aggregate(
        campaigns=Count("pk"),
        active=Count(
            "pk",
            filter=Q(**{f"{start_field}__lte": now, f"{end_field}__gte": now}),
        ),
        upcoming=Count("pk", filter=Q(**{f"{start_field}__gt": now})),
        expired=Count("pk", filter=Q(**{f"{end_field}__lt": now})),
        unknown=Count(
            "pk",
            filter=(
                Q(**{f"{start_field}__isnull": True})
                | Q(**{f"{end_field}__isnull": True})
            ),
        ),
    )
    return PlatformStats(**counts)


@server.tool(
    description=(
        "Get an overview of the archive: how many drop campaigns exist on "
        "each platform, and how many are active, upcoming, expired, or have "
        "unknown dates."
    ),
    read_only=True,
)
def get_stats(request: HttpRequest) -> StatsResult:
    """Return campaign counts for every platform.

    Returns:
        The per-platform and total campaign counts.
    """
    now: datetime.datetime = timezone.now()
    twitch = _platform_stats(
        DropCampaign.objects.filter(is_fully_imported=True),
        now,
        start_field="start_at",
        end_field="end_at",
    )
    kick = _platform_stats(
        KickDropCampaign.objects.filter(is_fully_imported=True),
        now,
        start_field="starts_at",
        end_field="ends_at",
    )
    chzzk = _platform_stats(
        ChzzkCampaign.objects.exclude(state="TESTING"),
        now,
        start_field="start_date",
        end_field="end_date",
    )
    return StatsResult(
        total=twitch.campaigns + kick.campaigns + chzzk.campaigns,
        twitch=twitch,
        kick=kick,
        chzzk=chzzk,
    )
