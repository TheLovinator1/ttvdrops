from __future__ import annotations

import datetime  # ruff: ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING
from typing import Literal

from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router
from ninja import Schema

from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickUser

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest

    from kick.models import KickReward

CampaignStatus = Literal["active", "upcoming", "expired", "unknown"]
CampaignStatusFilter = Literal["active", "upcoming", "expired", "unknown"]
VALID_STATUS_FILTERS: frozenset[str] = frozenset({
    "active",
    "upcoming",
    "expired",
    "unknown",
})
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

api = Router()


# MARK: Schemas
class V1StatsSchema(Schema):
    """Aggregate counts for Kick drops."""

    total_campaigns: int
    active: int
    upcoming: int
    expired: int
    partial_dates: int
    total_organizations: int
    total_categories: int
    total_channels: int


class V1CategorySchema(Schema):
    """Kick category (game) nested in a campaign."""

    kick_id: int
    name: str
    slug: str
    image_url: str


class V1OrganizationSummarySchema(Schema):
    """Kick organization nested in a campaign."""

    kick_id: str
    name: str
    logo_url: str
    url: str


class V1UserSchema(Schema):
    """Kick user nested in a channel."""

    kick_id: int
    username: str
    profile_picture: str


class V1ChannelSchema(Schema):
    """Kick channel participating in a drop campaign."""

    kick_id: int
    slug: str
    url: str
    user: V1UserSchema | None


class V1RewardSchema(Schema):
    """Reward earned from a Kick drop campaign."""

    kick_id: str
    name: str
    image_url: str
    required_minutes_watched: int


class V1CampaignSummarySchema(Schema):
    """Compact Kick drop campaign for list views."""

    kick_id: str
    name: str
    status: str
    image_url: str
    starts_at: datetime.datetime | None
    ends_at: datetime.datetime | None
    category: V1CategorySchema | None
    organization: V1OrganizationSummarySchema | None
    reward_count: int
    is_fully_imported: bool


class V1CampaignDetailSchema(V1CampaignSummarySchema):
    """Full Kick drop campaign detail."""

    connect_url: str
    url: str
    channels: list[V1ChannelSchema]
    rewards: list[V1RewardSchema]
    added_at: datetime.datetime | None
    updated_at: datetime.datetime | None


class V1PaginationSchema(Schema):
    """Common pagination fields."""

    total: int
    page: int
    page_size: int


class V1CampaignListSchema(V1PaginationSchema):
    """Paginated campaign list."""

    items: list[V1CampaignDetailSchema]


class V1OrganizationListItemSchema(Schema):
    """Kick organization in a paginated list."""

    kick_id: str
    name: str
    logo_url: str
    url: str
    campaign_count: int
    active_campaign_count: int


class V1OrganizationListSchema(V1PaginationSchema):
    """Paginated organization list."""

    items: list[V1OrganizationListItemSchema]


class V1OrganizationDetailSchema(Schema):
    """Kick organization with its campaigns."""

    kick_id: str
    name: str
    logo_url: str
    url: str
    campaign_count: int
    active_campaign_count: int
    campaigns: list[V1CampaignSummarySchema]


class V1CategoryListItemSchema(Schema):
    """Kick category (game) in a paginated list."""

    kick_id: int
    name: str
    slug: str
    image_url: str
    campaign_count: int
    active_campaign_count: int


class V1CategoryListSchema(V1PaginationSchema):
    """Paginated category list."""

    items: list[V1CategoryListItemSchema]


class V1CategoryDetailSchema(Schema):
    """Kick category (game) with its campaigns."""

    kick_id: int
    name: str
    slug: str
    image_url: str
    campaign_count: int
    active_campaign_count: int
    campaigns: list[V1CampaignSummarySchema]


# MARK: Helpers


def _paginate[ModelT: models.Model](
    queryset: QuerySet[ModelT, ModelT],
    *,
    page: int,
    page_size: int,
) -> tuple[list[ModelT], int, int, int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    offset = (page - 1) * page_size
    total = queryset.count()
    items = list(queryset[offset : offset + page_size]) if offset < total else []
    return items, total, page, page_size


def _campaign_status(
    campaign: KickDropCampaign,
    now: datetime.datetime,
) -> CampaignStatus:
    if campaign.starts_at and campaign.ends_at:
        if campaign.starts_at <= now <= campaign.ends_at:
            return "active"
        if campaign.starts_at > now:
            return "upcoming"
        return "expired"
    return "unknown"


def _apply_status_filter(
    queryset: QuerySet[KickDropCampaign],
    status: str | None,
    now: datetime.datetime,
) -> QuerySet[KickDropCampaign]:
    if status == "active":
        return queryset.filter(starts_at__lte=now, ends_at__gte=now)
    if status == "upcoming":
        return queryset.filter(starts_at__gt=now, ends_at__isnull=False)
    if status == "expired":
        return queryset.filter(starts_at__lte=now, ends_at__lt=now)
    if status == "unknown":
        return queryset.filter(
            models.Q(starts_at__isnull=True) | models.Q(ends_at__isnull=True),
        )
    return queryset


# MARK: Serializers
def _serialize_category(category: KickCategory | None) -> V1CategorySchema | None:
    if category is None:
        return None
    return V1CategorySchema(
        kick_id=category.kick_id,
        name=category.name,
        slug=category.slug,
        image_url=category.image_url,
    )


def _serialize_organization_summary(
    organization: KickOrganization | None,
) -> V1OrganizationSummarySchema | None:
    if organization is None:
        return None
    return V1OrganizationSummarySchema(
        kick_id=organization.kick_id,
        name=organization.name,
        logo_url=organization.logo_url,
        url=organization.url,
    )


def _serialize_user(user: KickUser | None) -> V1UserSchema | None:
    if user is None:
        return None
    return V1UserSchema(
        kick_id=user.kick_id,
        username=user.username,
        profile_picture=user.profile_picture,
    )


def _serialize_channel(channel: KickChannel) -> V1ChannelSchema:
    return V1ChannelSchema(
        kick_id=channel.kick_id,
        slug=channel.slug,
        url=channel.channel_url,
        user=_serialize_user(channel.user),
    )


def _serialize_reward(reward: KickReward) -> V1RewardSchema:
    return V1RewardSchema(
        kick_id=reward.kick_id,
        name=reward.name,
        image_url=reward.full_image_url,
        required_minutes_watched=reward.required_units,
    )


def _serialize_campaign_summary(
    campaign: KickDropCampaign,
    now: datetime.datetime,
) -> V1CampaignSummarySchema:
    return V1CampaignSummarySchema(
        kick_id=campaign.kick_id,
        name=campaign.name,
        status=_campaign_status(campaign, now),
        image_url=campaign.image_url,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        category=_serialize_category(campaign.category),
        organization=_serialize_organization_summary(campaign.organization),
        reward_count=campaign.rewards.count(),  # pyright: ignore[reportAttributeAccessIssue]
        is_fully_imported=campaign.is_fully_imported,
    )


def _serialize_campaign(
    campaign: KickDropCampaign,
    now: datetime.datetime,
) -> V1CampaignDetailSchema:
    return V1CampaignDetailSchema(
        kick_id=campaign.kick_id,
        name=campaign.name,
        status=_campaign_status(campaign, now),
        image_url=campaign.image_url,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        category=_serialize_category(campaign.category),
        organization=_serialize_organization_summary(campaign.organization),
        reward_count=campaign.rewards.count(),  # pyright: ignore[reportAttributeAccessIssue]
        is_fully_imported=campaign.is_fully_imported,
        connect_url=campaign.connect_url,
        url=campaign.url,
        channels=[_serialize_channel(channel) for channel in campaign.channels.all()],
        rewards=[
            _serialize_reward(reward)
            for reward in campaign.rewards.all()  # pyright: ignore[reportAttributeAccessIssue]
        ],
        added_at=campaign.added_at,
        updated_at=campaign.updated_at,
    )


# MARK: Endpoints


@api.get("/", response=V1StatsSchema)
def stats(request: HttpRequest) -> V1StatsSchema:
    """Return aggregate counts for Kick drops."""
    now: datetime.datetime = timezone.now()
    total_campaigns: int = KickDropCampaign.objects.filter(
        is_fully_imported=True,
    ).count()
    active: int = KickDropCampaign.objects.filter(
        is_fully_imported=True,
        starts_at__lte=now,
        ends_at__gte=now,
    ).count()
    upcoming: int = KickDropCampaign.objects.filter(
        is_fully_imported=True,
        starts_at__gt=now,
        ends_at__isnull=False,
    ).count()
    expired: int = KickDropCampaign.objects.filter(
        is_fully_imported=True,
        starts_at__lte=now,
        ends_at__lt=now,
    ).count()

    return V1StatsSchema(
        total_campaigns=total_campaigns,
        active=active,
        upcoming=upcoming,
        expired=expired,
        partial_dates=total_campaigns - active - upcoming - expired,
        total_organizations=KickOrganization.objects.count(),
        total_categories=KickCategory.objects.count(),
        total_channels=KickChannel.objects.count(),
    )


@api.get("/campaigns/", response=V1CampaignListSchema)
def list_campaigns(  # ruff:ignore[too-many-positional-arguments]
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: CampaignStatusFilter | None = None,
    game: int | None = None,
    organization: str | None = None,
    search: str | None = None,
) -> V1CampaignListSchema:
    """Return paginated Kick drop campaigns."""
    now: datetime.datetime = timezone.now()
    queryset: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .filter(is_fully_imported=True)
        .select_related("category", "organization")
        .prefetch_related("channels__user", "rewards")
        .order_by("-starts_at", "kick_id")
    )

    if game is not None:
        queryset = queryset.filter(category__kick_id=game)
    if organization is not None:
        queryset = queryset.filter(organization__kick_id=organization)
    if search is not None:
        queryset = queryset.filter(name__icontains=search)

    queryset = _apply_status_filter(queryset, status, now)

    items, total, current_page, current_page_size = _paginate(
        queryset,
        page=page,
        page_size=page_size,
    )

    return V1CampaignListSchema(
        total=total,
        page=current_page,
        page_size=current_page_size,
        items=[_serialize_campaign(campaign, now) for campaign in items],
    )


@api.get("/campaigns/{kick_id}/", response=V1CampaignDetailSchema)
def get_campaign(
    request: HttpRequest,
    kick_id: str,
) -> V1CampaignDetailSchema:
    """Return a single Kick drop campaign.

    Raises:
        Http404: If the campaign does not exist or is not fully imported.
    """
    try:
        campaign: KickDropCampaign = (
            KickDropCampaign.objects
            .filter(is_fully_imported=True)
            .select_related("category", "organization")
            .prefetch_related("channels__user", "rewards")
            .get(kick_id=kick_id)
        )
    except KickDropCampaign.DoesNotExist as exc:
        msg = "Campaign not found"
        raise Http404(msg) from exc

    return _serialize_campaign(campaign, timezone.now())


@api.get("/games/", response=V1CategoryListSchema)
def list_games(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
) -> V1CategoryListSchema:
    """Return paginated Kick categories (games)."""
    now: datetime.datetime = timezone.now()

    queryset: QuerySet[KickCategory] = KickCategory.objects.annotate(
        campaign_count=models.Count(
            "campaigns",
            filter=models.Q(campaigns__is_fully_imported=True),
        ),
        active_campaign_count=models.Count(
            "campaigns",
            filter=models.Q(
                campaigns__is_fully_imported=True,
                campaigns__starts_at__lte=now,
                campaigns__ends_at__gte=now,
            ),
        ),
    ).order_by("name")

    if search is not None:
        queryset = queryset.filter(name__icontains=search)

    items, total, current_page, current_page_size = _paginate(
        queryset,
        page=page,
        page_size=page_size,
    )

    return V1CategoryListSchema(
        total=total,
        page=current_page,
        page_size=current_page_size,
        items=[
            V1CategoryListItemSchema(
                kick_id=cat.kick_id,
                name=cat.name,
                slug=cat.slug,
                image_url=cat.image_url,
                campaign_count=cat.campaign_count,  # pyright: ignore[reportAttributeAccessIssue]
                active_campaign_count=cat.active_campaign_count,  # pyright: ignore[reportAttributeAccessIssue]
            )
            for cat in items
        ],
    )


@api.get("/games/{kick_id}/", response=V1CategoryDetailSchema)
def get_game(
    request: HttpRequest,
    kick_id: int,
) -> V1CategoryDetailSchema:
    """Return a single Kick category (game) with its campaigns."""
    category: KickCategory = get_object_or_404(KickCategory, kick_id=kick_id)
    now: datetime.datetime = timezone.now()
    campaigns: list[KickDropCampaign] = list(
        category.campaigns  # pyright: ignore[reportAttributeAccessIssue]
        .filter(is_fully_imported=True)
        .select_related("organization")
        .prefetch_related("rewards")
        .order_by("-starts_at"),
    )

    return V1CategoryDetailSchema(
        kick_id=category.kick_id,
        name=category.name,
        slug=category.slug,
        image_url=category.image_url,
        campaign_count=len(campaigns),
        active_campaign_count=sum(
            1 for c in campaigns if _campaign_status(c, now) == "active"
        ),
        campaigns=[_serialize_campaign_summary(c, now) for c in campaigns],
    )


@api.get("/organizations/", response=V1OrganizationListSchema)
def list_organizations(
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
) -> V1OrganizationListSchema:
    """Return paginated Kick organizations."""
    now: datetime.datetime = timezone.now()

    queryset: QuerySet[KickOrganization] = KickOrganization.objects.annotate(
        campaign_count=models.Count(
            "campaigns",
            filter=models.Q(campaigns__is_fully_imported=True),
        ),
        active_campaign_count=models.Count(
            "campaigns",
            filter=models.Q(
                campaigns__is_fully_imported=True,
                campaigns__starts_at__lte=now,
                campaigns__ends_at__gte=now,
            ),
        ),
    ).order_by("name")

    if search is not None:
        queryset = queryset.filter(name__icontains=search)

    items, total, current_page, current_page_size = _paginate(
        queryset,
        page=page,
        page_size=page_size,
    )

    return V1OrganizationListSchema(
        total=total,
        page=current_page,
        page_size=current_page_size,
        items=[
            V1OrganizationListItemSchema(
                kick_id=org.kick_id,
                name=org.name,
                logo_url=org.logo_url,
                url=org.url,
                campaign_count=org.campaign_count,  # pyright: ignore[reportAttributeAccessIssue]
                active_campaign_count=org.active_campaign_count,  # pyright: ignore[reportAttributeAccessIssue]
            )
            for org in items
        ],
    )


@api.get("/organizations/{kick_id}/", response=V1OrganizationDetailSchema)
def get_organization(
    request: HttpRequest,
    kick_id: str,
) -> V1OrganizationDetailSchema:
    """Return a single Kick organization with its campaigns."""
    organization: KickOrganization = get_object_or_404(
        KickOrganization,
        kick_id=kick_id,
    )
    now: datetime.datetime = timezone.now()
    campaigns: list[KickDropCampaign] = list(
        organization.campaigns  # pyright: ignore[reportAttributeAccessIssue]
        .filter(is_fully_imported=True)
        .select_related("category")
        .prefetch_related("rewards")
        .order_by("-starts_at"),
    )

    return V1OrganizationDetailSchema(
        kick_id=organization.kick_id,
        name=organization.name,
        logo_url=organization.logo_url,
        url=organization.url,
        campaign_count=len(campaigns),
        active_campaign_count=sum(
            1 for c in campaigns if _campaign_status(c, now) == "active"
        ),
        campaigns=[_serialize_campaign_summary(c, now) for c in campaigns],
    )
