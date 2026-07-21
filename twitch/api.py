from __future__ import annotations

import datetime  # ruff:ignore[typing-only-standard-library-import]
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router
from ninja import Schema

from twitch.models import Channel
from twitch.models import ChatBadgeSet
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.utils import normalize_twitch_box_art_url

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest

    from twitch.models import ChatBadge
    from twitch.models import DropBenefit
    from twitch.models import TimeBasedDrop

V1StatusFilter = Literal["active", "upcoming", "expired", "unknown"]

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


@dataclass(frozen=True)
class V1Page[ModelT: models.Model]:
    """Typed slice metadata for v1 list responses."""

    items: list[ModelT]
    total: int
    page: int
    page_size: int


class V1PaginationSchema(Schema):
    """Common pagination fields for v1 list responses."""

    total: int
    page: int
    page_size: int


class V1OrganizationSummarySchema(Schema):
    """Compact Twitch organization representation."""

    twitch_id: str
    name: str


class V1GameSummarySchema(Schema):
    """Compact Twitch game representation."""

    twitch_id: str
    slug: str
    name: str
    display_name: str
    box_art_url: str
    organizations: list[V1OrganizationSummarySchema]
    campaign_count: int
    active_campaign_count: int


class V1OrganizationSchema(V1OrganizationSummarySchema):
    """Twitch organization response."""

    added_at: datetime.datetime
    updated_at: datetime.datetime


class V1GameSchema(V1GameSummarySchema):
    """Twitch game response."""

    added_at: datetime.datetime
    updated_at: datetime.datetime


class V1ChannelSummarySchema(Schema):
    """Compact Twitch channel representation."""

    twitch_id: str
    name: str
    display_name: str


class V1ChannelSchema(V1ChannelSummarySchema):
    """Twitch channel response."""

    allowed_campaign_count: int
    added_at: datetime.datetime
    updated_at: datetime.datetime


class V1DropBenefitSchema(Schema):
    """Twitch drop benefit response."""

    twitch_id: str
    name: str
    image_url: str
    distribution_type: str
    created_at: datetime.datetime | None
    entitlement_limit: int
    is_ios_available: bool


class V1TimeBasedDropSchema(Schema):
    """Twitch time-based drop response."""

    twitch_id: str
    name: str
    required_minutes_watched: int | None
    required_subs: int
    start_at: datetime.datetime | None
    end_at: datetime.datetime | None
    benefits: list[V1DropBenefitSchema]


class V1DropCampaignSummarySchema(Schema):
    """Compact Twitch drop campaign representation."""

    twitch_id: str
    name: str
    description: str
    status: str
    image_url: str
    details_url: str
    account_link_url: str
    start_at: datetime.datetime | None
    end_at: datetime.datetime | None
    game: V1GameSummarySchema
    allow_is_enabled: bool
    is_fully_imported: bool
    added_at: datetime.datetime
    updated_at: datetime.datetime


class V1DropCampaignDetailSchema(V1DropCampaignSummarySchema):
    """Twitch drop campaign detail response."""

    operation_names: list[str]
    allowed_channels: list[V1ChannelSummarySchema]
    drops: list[V1TimeBasedDropSchema]


class V1OrganizationDetailSchema(V1OrganizationSchema):
    """Twitch organization detail response."""

    games: list[V1GameSummarySchema]
    campaigns: list[V1DropCampaignDetailSchema]


class V1GameDetailSchema(V1GameSchema):
    """Twitch game detail response."""

    campaigns: list[V1DropCampaignSummarySchema]
    reward_campaigns: list[V1RewardCampaignSchema]


class V1ChannelDetailSchema(V1ChannelSchema):
    """Twitch channel detail response."""

    campaigns: list[V1DropCampaignSummarySchema]


class V1RewardCampaignSchema(Schema):
    """Twitch reward campaign response."""

    twitch_id: str
    name: str
    brand: str
    status: str
    computed_status: str
    summary: str
    instructions: str
    external_url: str
    reward_value_url_param: str
    about_url: str
    image_url: str
    is_sitewide: bool
    starts_at: datetime.datetime | None
    ends_at: datetime.datetime | None
    game: V1GameSummarySchema | None
    added_at: datetime.datetime
    updated_at: datetime.datetime


class V1ChatBadgeSchema(Schema):
    """Twitch chat badge response."""

    badge_id: str
    image_url_1x: str
    image_url_2x: str
    image_url_4x: str
    title: str
    description: str
    click_action: str | None
    click_url: str | None


class V1ChatBadgeSetSchema(Schema):
    """Twitch chat badge set response."""

    set_id: str
    badges: list[V1ChatBadgeSchema]
    added_at: datetime.datetime
    updated_at: datetime.datetime


class V1DropCampaignListSchema(V1PaginationSchema):
    """Paginated Twitch drop campaign list."""

    items: list[V1DropCampaignSummarySchema]


class V1GameListSchema(V1PaginationSchema):
    """Paginated Twitch game list."""

    items: list[V1GameSchema]


class V1OrganizationListSchema(V1PaginationSchema):
    """Paginated Twitch organization list."""

    items: list[V1OrganizationSchema]


class V1ChannelListSchema(V1PaginationSchema):
    """Paginated Twitch channel list."""

    items: list[V1ChannelSchema]


class V1RewardCampaignListSchema(V1PaginationSchema):
    """Paginated Twitch reward campaign list."""

    items: list[V1RewardCampaignSchema]


class V1ChatBadgeSetListSchema(V1PaginationSchema):
    """Paginated Twitch chat badge set list."""

    items: list[V1ChatBadgeSetSchema]


api = Router()


def _paginate[ModelT: models.Model](
    queryset: QuerySet[ModelT, ModelT],
    *,
    page: int,
    page_size: int,
) -> V1Page[ModelT]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    offset = (page - 1) * page_size
    return V1Page(
        items=list(queryset[offset : offset + page_size]),
        page=page,
        page_size=page_size,
        total=queryset.count(),
    )


def _campaign_status(
    start_at: datetime.datetime | None,
    end_at: datetime.datetime | None,
    now: datetime.datetime,
) -> str:
    if start_at and end_at:
        if start_at <= now <= end_at:
            return "active"
        if start_at > now:
            return "upcoming"
        return "expired"
    return "unknown"


def _apply_status_filter[ModelT: models.Model](
    queryset: QuerySet[ModelT, ModelT],
    status: V1StatusFilter | None,
    now: datetime.datetime,
    *,
    start_field: str,
    end_field: str,
) -> QuerySet[ModelT, ModelT]:
    if status == "active":
        return queryset.filter(**{f"{start_field}__lte": now, f"{end_field}__gte": now})
    if status == "upcoming":
        return queryset.filter(**{f"{start_field}__gt": now})
    if status == "expired":
        return queryset.filter(**{f"{end_field}__lt": now})
    if status == "unknown":
        return queryset.filter(
            models.Q(**{f"{start_field}__isnull": True})
            | models.Q(**{f"{end_field}__isnull": True}),
        )
    return queryset


def _serialize_organization_summary(org: Organization) -> V1OrganizationSummarySchema:
    return V1OrganizationSummarySchema(twitch_id=org.twitch_id, name=org.name)


def _serialize_organization(org: Organization) -> V1OrganizationSchema:
    return V1OrganizationSchema(
        twitch_id=org.twitch_id,
        name=org.name,
        added_at=org.added_at,
        updated_at=org.updated_at,
    )


def _game_campaign_count(game: Game) -> int:
    return DropCampaign.objects.filter(game=game).count()


def _game_active_campaign_count(game: Game, now: datetime.datetime) -> int:
    return DropCampaign.objects.filter(
        game=game,
        start_at__lte=now,
        end_at__gte=now,
    ).count()


def _game_box_art_url(game: Game) -> str:
    deferred_fields: set[str] = game.get_deferred_fields()
    local_image_fields = {"box_art_file", "box_art_width", "box_art_height"}
    if deferred_fields.isdisjoint(local_image_fields):
        return game.box_art_best_url
    if "box_art" not in deferred_fields:
        return normalize_twitch_box_art_url(game.box_art or "")
    return ""


def _campaign_summary_queryset(
    queryset: QuerySet[DropCampaign, DropCampaign],
) -> QuerySet[DropCampaign, DropCampaign]:
    return (
        queryset
        .select_related("game")
        .only(
            "twitch_id",
            "name",
            "description",
            "details_url",
            "account_link_url",
            "image_url",
            "image_file",
            "image_width",
            "image_height",
            "start_at",
            "end_at",
            "allow_is_enabled",
            "is_fully_imported",
            "added_at",
            "updated_at",
            "game",
            "game__twitch_id",
            "game__name",
            "game__display_name",
            "game__slug",
            "game__box_art",
            "game__box_art_file",
            "game__box_art_width",
            "game__box_art_height",
        )
        .prefetch_related("game__owners")
    )


def _channel_list_queryset(search_query: str | None) -> QuerySet[Channel, Channel]:
    queryset = Channel.objects.only(
        "twitch_id",
        "name",
        "display_name",
        "allowed_campaign_count",
        "added_at",
        "updated_at",
    )
    normalized_query = (search_query or "").strip()
    if normalized_query:
        queryset = queryset.filter(
            models.Q(name__icontains=normalized_query)
            | models.Q(display_name__icontains=normalized_query),
        )
    return queryset.annotate(
        campaign_count=models.F("allowed_campaign_count"),
    ).order_by("-campaign_count", "name")


def _serialize_game_summary(
    game: Game,
    now: datetime.datetime,
) -> V1GameSummarySchema:
    return V1GameSummarySchema(
        twitch_id=game.twitch_id,
        slug=game.slug,
        name=game.name,
        display_name=game.display_name,
        box_art_url=_game_box_art_url(game),
        organizations=[
            _serialize_organization_summary(org)
            for org in getattr(game, "owners_for_detail", game.owners.all())
        ],
        campaign_count=_game_campaign_count(game),
        active_campaign_count=_game_active_campaign_count(game, now),
    )


def _serialize_game(game: Game, now: datetime.datetime) -> V1GameSchema:
    return V1GameSchema(
        twitch_id=game.twitch_id,
        slug=game.slug,
        name=game.name,
        display_name=game.display_name,
        box_art_url=_game_box_art_url(game),
        organizations=[
            _serialize_organization_summary(org)
            for org in getattr(game, "owners_for_detail", game.owners.all())
        ],
        campaign_count=_game_campaign_count(game),
        active_campaign_count=_game_active_campaign_count(game, now),
        added_at=game.added_at,
        updated_at=game.updated_at,
    )


def _serialize_channel_summary(channel: Channel) -> V1ChannelSummarySchema:
    return V1ChannelSummarySchema(
        twitch_id=channel.twitch_id,
        name=channel.name,
        display_name=channel.display_name,
    )


def _serialize_channel(channel: Channel) -> V1ChannelSchema:
    return V1ChannelSchema(
        twitch_id=channel.twitch_id,
        name=channel.name,
        display_name=channel.display_name,
        allowed_campaign_count=channel.allowed_campaign_count,
        added_at=channel.added_at,
        updated_at=channel.updated_at,
    )


def _serialize_benefit(benefit: DropBenefit) -> V1DropBenefitSchema:
    return V1DropBenefitSchema(
        twitch_id=benefit.twitch_id,
        name=benefit.name,
        image_url=benefit.image_best_url,
        distribution_type=benefit.distribution_type,
        created_at=benefit.created_at,
        entitlement_limit=benefit.entitlement_limit,
        is_ios_available=benefit.is_ios_available,
    )


def _serialize_drop(drop: TimeBasedDrop) -> V1TimeBasedDropSchema:
    return V1TimeBasedDropSchema(
        twitch_id=drop.twitch_id,
        name=drop.name,
        required_minutes_watched=drop.required_minutes_watched,
        required_subs=drop.required_subs,
        start_at=drop.start_at,
        end_at=drop.end_at,
        benefits=[_serialize_benefit(benefit) for benefit in drop.benefits.all()],
    )


def _serialize_campaign_summary(
    campaign: DropCampaign,
    now: datetime.datetime,
) -> V1DropCampaignSummarySchema:
    return V1DropCampaignSummarySchema(
        twitch_id=campaign.twitch_id,
        name=campaign.name,
        description=campaign.description,
        status=_campaign_status(campaign.start_at, campaign.end_at, now),
        image_url=campaign.listing_image_url,
        details_url=campaign.details_url,
        account_link_url=campaign.account_link_url,
        start_at=campaign.start_at,
        end_at=campaign.end_at,
        game=_serialize_game_summary(campaign.game, now),
        allow_is_enabled=campaign.allow_is_enabled,
        is_fully_imported=campaign.is_fully_imported,
        added_at=campaign.added_at,
        updated_at=campaign.updated_at,
    )


def _serialize_campaign_detail(
    campaign: DropCampaign,
    now: datetime.datetime,
) -> V1DropCampaignDetailSchema:
    return V1DropCampaignDetailSchema(
        twitch_id=campaign.twitch_id,
        name=campaign.name,
        description=campaign.description,
        status=_campaign_status(campaign.start_at, campaign.end_at, now),
        image_url=campaign.listing_image_url,
        details_url=campaign.details_url,
        account_link_url=campaign.account_link_url,
        start_at=campaign.start_at,
        end_at=campaign.end_at,
        game=_serialize_game_summary(campaign.game, now),
        allow_is_enabled=campaign.allow_is_enabled,
        is_fully_imported=campaign.is_fully_imported,
        added_at=campaign.added_at,
        updated_at=campaign.updated_at,
        operation_names=campaign.operation_names,
        allowed_channels=[
            _serialize_channel_summary(channel)
            for channel in getattr(
                campaign,
                "channels_ordered",
                campaign.allow_channels.all(),
            )
        ],
        drops=[_serialize_drop(drop) for drop in campaign.time_based_drops.all()],  # pyright: ignore[reportAttributeAccessIssue]
    )


def _serialize_reward_campaign(
    campaign: RewardCampaign,
    now: datetime.datetime,
) -> V1RewardCampaignSchema:
    return V1RewardCampaignSchema(
        twitch_id=campaign.twitch_id,
        name=campaign.name,
        brand=campaign.brand,
        status=campaign.status,
        computed_status=_campaign_status(campaign.starts_at, campaign.ends_at, now),
        summary=campaign.summary,
        instructions=campaign.instructions,
        external_url=campaign.external_url,
        reward_value_url_param=campaign.reward_value_url_param,
        about_url=campaign.about_url,
        image_url=campaign.image_best_url,
        is_sitewide=campaign.is_sitewide,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        game=_serialize_game_summary(campaign.game, now) if campaign.game else None,
        added_at=campaign.added_at,
        updated_at=campaign.updated_at,
    )


def _serialize_badge(badge: ChatBadge) -> V1ChatBadgeSchema:
    return V1ChatBadgeSchema(
        badge_id=badge.badge_id,
        image_url_1x=badge.image_url_1x,
        image_url_2x=badge.image_url_2x,
        image_url_4x=badge.image_url_4x,
        title=badge.title,
        description=badge.description,
        click_action=badge.click_action,
        click_url=badge.click_url,
    )


def _serialize_badge_set(badge_set: ChatBadgeSet) -> V1ChatBadgeSetSchema:
    return V1ChatBadgeSetSchema(
        set_id=badge_set.set_id,
        badges=[_serialize_badge(badge) for badge in badge_set.badges.all()],  # pyright: ignore[reportAttributeAccessIssue]
        added_at=badge_set.added_at,
        updated_at=badge_set.updated_at,
    )


@api.get("/campaigns/", response=V1DropCampaignListSchema)
def list_campaigns(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    game: str | None = None,
    status: V1StatusFilter | None = None,
) -> V1DropCampaignListSchema:
    """Return paginated Twitch drop campaigns."""
    now = timezone.now()
    queryset = DropCampaign.for_campaign_list(now, game_twitch_id=game, status=status)
    page_data = _paginate(queryset, page=page, page_size=page_size)
    return V1DropCampaignListSchema(
        total=page_data.total,
        page=page_data.page,
        page_size=page_data.page_size,
        items=[
            _serialize_campaign_summary(campaign, now) for campaign in page_data.items
        ],
    )


@api.get("/campaigns/{twitch_id}/", response=V1DropCampaignDetailSchema)
def get_campaign(request: HttpRequest, twitch_id: str) -> V1DropCampaignDetailSchema:
    """Return one Twitch drop campaign.

    Raises:
        Http404: if not found
    """
    try:
        campaign = DropCampaign.for_detail_view(twitch_id)
    except DropCampaign.DoesNotExist as exc:
        msg = "Campaign not found"
        raise Http404(msg) from exc
    return _serialize_campaign_detail(campaign, timezone.now())


@api.get("/games/", response=V1GameListSchema)
def list_games(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> V1GameListSchema:
    """Return paginated Twitch games."""
    now = timezone.now()
    queryset = Game.with_campaign_counts(now).order_by("display_name")
    page_data = _paginate(queryset, page=page, page_size=page_size)
    return V1GameListSchema(
        total=page_data.total,
        page=page_data.page,
        page_size=page_data.page_size,
        items=[_serialize_game(game, now) for game in page_data.items],
    )


@api.get("/games/{twitch_id}/", response=V1GameDetailSchema)
def get_game(request: HttpRequest, twitch_id: str) -> V1GameDetailSchema:
    """Return one Twitch game."""
    game = get_object_or_404(Game.for_detail_view(), twitch_id=twitch_id)
    campaigns = _campaign_summary_queryset(
        DropCampaign.objects.filter(game=game),
    ).order_by("-end_at")
    now = timezone.now()
    reward_campaigns = list(
        RewardCampaign.objects
        .filter(game=game)
        .select_related("game")
        .prefetch_related("game__owners")
        .order_by("-starts_at"),
    )
    return V1GameDetailSchema(
        twitch_id=game.twitch_id,
        slug=game.slug,
        name=game.name,
        display_name=game.display_name,
        box_art_url=_game_box_art_url(game),
        organizations=[
            _serialize_organization_summary(org)
            for org in getattr(game, "owners_for_detail", game.owners.all())
        ],
        campaign_count=_game_campaign_count(game),
        active_campaign_count=_game_active_campaign_count(game, now),
        added_at=game.added_at,
        updated_at=game.updated_at,
        campaigns=[
            _serialize_campaign_summary(campaign, now) for campaign in campaigns
        ],
        reward_campaigns=[
            _serialize_reward_campaign(rc, now) for rc in reward_campaigns
        ],
    )


@api.get("/organizations/", response=V1OrganizationListSchema)
def list_organizations(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> V1OrganizationListSchema:
    """Return paginated Twitch organizations."""
    page_data = _paginate(
        Organization.objects.all().order_by("name"),
        page=page,
        page_size=page_size,
    )
    return V1OrganizationListSchema(
        total=page_data.total,
        page=page_data.page,
        page_size=page_data.page_size,
        items=[_serialize_organization(org) for org in page_data.items],
    )


@api.get("/organizations/{twitch_id}/", response=V1OrganizationDetailSchema)
def get_organization(
    request: HttpRequest,
    twitch_id: str,
) -> V1OrganizationDetailSchema:
    """Return one Twitch organization."""
    org = get_object_or_404(Organization.for_detail_view(), twitch_id=twitch_id)
    now = timezone.now()
    campaigns = (
        DropCampaign.objects
        .filter(game__owners=org)
        .select_related("game")
        .prefetch_related(
            "game__owners",
            "allow_channels",
            "time_based_drops__benefits",
        )
        .order_by("-start_at")
        .distinct()
    )
    return V1OrganizationDetailSchema(
        twitch_id=org.twitch_id,
        name=org.name,
        added_at=org.added_at,
        updated_at=org.updated_at,
        games=[
            _serialize_game_summary(game, now)
            for game in getattr(org, "games_for_detail", org.games.all())  # pyright: ignore[reportAttributeAccessIssue]
        ],
        campaigns=[_serialize_campaign_detail(campaign, now) for campaign in campaigns],
    )


@api.get("/channels/", response=V1ChannelListSchema)
def list_channels(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
) -> V1ChannelListSchema:
    """Return paginated Twitch channels."""
    page_data = _paginate(
        _channel_list_queryset(search),
        page=page,
        page_size=page_size,
    )
    return V1ChannelListSchema(
        total=page_data.total,
        page=page_data.page,
        page_size=page_data.page_size,
        items=[_serialize_channel(channel) for channel in page_data.items],
    )


@api.get("/channels/{twitch_id}/", response=V1ChannelDetailSchema)
def get_channel(request: HttpRequest, twitch_id: str) -> V1ChannelDetailSchema:
    """Return one Twitch channel."""
    channel = get_object_or_404(Channel.for_detail_view(), twitch_id=twitch_id)
    campaigns = _campaign_summary_queryset(
        DropCampaign.objects.filter(allow_channels=channel),
    ).order_by("-start_at")
    now = timezone.now()
    return V1ChannelDetailSchema(
        twitch_id=channel.twitch_id,
        name=channel.name,
        display_name=channel.display_name,
        allowed_campaign_count=channel.allowed_campaign_count,
        added_at=channel.added_at,
        updated_at=channel.updated_at,
        campaigns=[
            _serialize_campaign_summary(campaign, now) for campaign in campaigns
        ],
    )


@api.get("/reward-campaigns/", response=V1RewardCampaignListSchema)
def list_reward_campaigns(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    game: str | None = None,
    status: V1StatusFilter | None = None,
) -> V1RewardCampaignListSchema:
    """Return paginated Twitch reward campaigns."""
    now = timezone.now()
    queryset = RewardCampaign.objects.select_related("game").prefetch_related(
        "game__owners",
    )
    if game:
        queryset = queryset.filter(game__twitch_id=game)
    queryset = _apply_status_filter(
        queryset.order_by("-starts_at"),
        status,
        now,
        start_field="starts_at",
        end_field="ends_at",
    )
    page_data = _paginate(queryset, page=page, page_size=page_size)
    return V1RewardCampaignListSchema(
        total=page_data.total,
        page=page_data.page,
        page_size=page_data.page_size,
        items=[
            _serialize_reward_campaign(campaign, now) for campaign in page_data.items
        ],
    )


@api.get("/reward-campaigns/{twitch_id}/", response=V1RewardCampaignSchema)
def get_reward_campaign(
    request: HttpRequest,
    twitch_id: str,
) -> V1RewardCampaignSchema:
    """Return one Twitch reward campaign."""
    campaign = get_object_or_404(
        RewardCampaign.objects.select_related("game").prefetch_related(
            "game__owners",
        ),
        twitch_id=twitch_id,
    )
    return _serialize_reward_campaign(campaign, timezone.now())


@api.get("/badges/", response=V1ChatBadgeSetListSchema)
def list_badges(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> V1ChatBadgeSetListSchema:
    """Return paginated Twitch chat badge sets."""
    page_data = _paginate(
        ChatBadgeSet.for_list_view(),
        page=page,
        page_size=page_size,
    )
    return V1ChatBadgeSetListSchema(
        total=page_data.total,
        page=page_data.page,
        page_size=page_data.page_size,
        items=[_serialize_badge_set(badge_set) for badge_set in page_data.items],
    )


@api.get("/badges/{set_id}/", response=V1ChatBadgeSetSchema)
def get_badge_set(request: HttpRequest, set_id: str) -> V1ChatBadgeSetSchema:
    """Return one Twitch chat badge set."""
    badge_set = get_object_or_404(ChatBadgeSet.for_list_view(), set_id=set_id)
    return _serialize_badge_set(badge_set)
