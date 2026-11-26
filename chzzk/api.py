from __future__ import annotations

import datetime  # ruff: ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING
from typing import Literal

from django.db import models
from django.http import Http404
from django.utils import timezone
from ninja import Router
from ninja import Schema

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest

CampaignStatus = Literal["active", "upcoming", "expired", "unknown"]
CampaignStatusFilter = Literal["active", "upcoming", "expired", "unknown"]
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

api = Router()


# MARK: Schemas
class V1StatsSchema(Schema):
    """Aggregate counts for Chzzk drops."""

    total_campaigns: int
    active: int
    upcoming: int
    expired: int
    total_rewards: int


class V1RewardSchema(Schema):
    """Reward earned from a Chzzk drop campaign."""

    reward_no: int
    title: str
    image_url: str
    image_filename: str
    reward_type: str
    campaign_reward_type: str
    condition_type: str
    condition_for_minutes: int
    ios_based_reward: bool
    code_remaining_count: int


class V1CampaignSummarySchema(Schema):
    """Compact Chzzk drop campaign for list views."""

    campaign_no: int
    title: str
    status: str
    image_url: str
    image_filename: str
    category_type: str
    category_id: str
    category_value: str
    reward_type: str
    starts_at: datetime.datetime | None
    ends_at: datetime.datetime | None
    reward_count: int


class V1CampaignDetailSchema(V1CampaignSummarySchema):
    """Full Chzzk drop campaign detail."""

    description: str
    pc_link_url: str
    mobile_link_url: str
    account_link_url: str
    service_id: str
    state: str
    has_ios_based_reward: bool
    drops_campaign_not_started: bool
    campaign_reward_type: str
    source_api: str
    scraped_at: datetime.datetime
    rewards: list[V1RewardSchema]


class V1PaginationSchema(Schema):
    """Common pagination fields."""

    total: int
    page: int
    page_size: int


class V1CampaignListSchema(V1PaginationSchema):
    """Paginated campaign list."""

    items: list[V1CampaignSummarySchema]


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
    campaign: ChzzkCampaign,
    now: datetime.datetime,
) -> CampaignStatus:
    if campaign.start_date and campaign.end_date:
        if campaign.start_date <= now <= campaign.end_date:
            return "active"
        if campaign.start_date > now:
            return "upcoming"
        return "expired"
    return "unknown"


def _apply_status_filter(
    queryset: QuerySet[ChzzkCampaign],
    status: str | None,
    now: datetime.datetime,
) -> QuerySet[ChzzkCampaign]:
    if status == "active":
        return queryset.filter(start_date__lte=now, end_date__gte=now)
    if status == "upcoming":
        return queryset.filter(start_date__gt=now)
    if status == "expired":
        return queryset.filter(end_date__lt=now)
    if status == "unknown":
        return queryset.filter(
            models.Q(start_date__isnull=True) | models.Q(end_date__isnull=True),
        )
    return queryset


def _serialize_reward(reward: ChzzkReward) -> V1RewardSchema:
    return V1RewardSchema(
        reward_no=reward.reward_no,
        title=reward.title,
        image_url=reward.image_best_url,
        image_filename=reward.image_filename,
        reward_type=reward.reward_type,
        campaign_reward_type=reward.campaign_reward_type,
        condition_type=reward.condition_type,
        condition_for_minutes=reward.condition_for_minutes,
        ios_based_reward=reward.ios_based_reward,
        code_remaining_count=reward.code_remaining_count,
    )


def _serialize_campaign_summary(
    campaign: ChzzkCampaign,
    now: datetime.datetime,
) -> V1CampaignSummarySchema:
    return V1CampaignSummarySchema(
        campaign_no=campaign.campaign_no,
        title=campaign.title,
        status=_campaign_status(campaign, now),
        image_url=campaign.image_best_url,
        image_filename=campaign.image_filename,
        category_type=campaign.category_type,
        category_id=campaign.category_id,
        category_value=campaign.category_value,
        reward_type=campaign.reward_type,
        starts_at=campaign.start_date,
        ends_at=campaign.end_date,
        reward_count=campaign.reward_count,  # pyright: ignore[reportAttributeAccessIssue]
    )


def _serialize_campaign_detail(
    campaign: ChzzkCampaign,
    now: datetime.datetime,
) -> V1CampaignDetailSchema:
    rewards: list[ChzzkReward] = list(
        campaign.rewards.all(),  # pyright: ignore[reportAttributeAccessIssue]
    )
    summary: V1CampaignSummarySchema = _serialize_campaign_summary(campaign, now)
    return V1CampaignDetailSchema(
        campaign_no=summary.campaign_no,
        title=summary.title,
        status=summary.status,
        image_url=summary.image_url,
        image_filename=summary.image_filename,
        category_type=summary.category_type,
        category_id=summary.category_id,
        category_value=summary.category_value,
        reward_type=summary.reward_type,
        starts_at=summary.starts_at,
        ends_at=summary.ends_at,
        reward_count=len(rewards),
        description=campaign.description,
        pc_link_url=campaign.pc_link_url,
        mobile_link_url=campaign.mobile_link_url,
        account_link_url=campaign.account_link_url,
        service_id=campaign.service_id,
        state=campaign.state,
        has_ios_based_reward=campaign.has_ios_based_reward,
        drops_campaign_not_started=campaign.drops_campaign_not_started,
        campaign_reward_type=campaign.campaign_reward_type,
        source_api=campaign.source_api,
        scraped_at=campaign.scraped_at,
        rewards=[_serialize_reward(reward) for reward in rewards],
    )


# MARK: Endpoints
@api.get("/", response=V1StatsSchema)
def stats(request: HttpRequest) -> V1StatsSchema:
    """Return aggregate counts for Chzzk drops."""
    now: datetime.datetime = timezone.now()
    queryset: QuerySet[ChzzkCampaign] = ChzzkCampaign.objects.exclude(
        state="TESTING",
    )

    total_campaigns: int = queryset.count()
    active: int = queryset.filter(
        start_date__lte=now,
        end_date__gte=now,
    ).count()
    upcoming: int = queryset.filter(start_date__gt=now).count()
    expired: int = queryset.filter(end_date__lt=now).count()

    return V1StatsSchema(
        total_campaigns=total_campaigns,
        active=active,
        upcoming=upcoming,
        expired=expired,
        total_rewards=ChzzkReward.objects.exclude(
            campaign__state="TESTING",
        ).count(),
    )


@api.get("/campaigns/", response=V1CampaignListSchema)
def list_campaigns(  # ruff:ignore[too-many-positional-arguments]
    request: HttpRequest,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: CampaignStatusFilter | None = None,
    category_type: str | None = None,
    category_id: str | None = None,
    search: str | None = None,
) -> V1CampaignListSchema:
    """Return paginated Chzzk drop campaigns."""
    now: datetime.datetime = timezone.now()
    queryset: QuerySet[ChzzkCampaign] = (
        ChzzkCampaign.objects
        .exclude(state="TESTING")
        .annotate(reward_count=models.Count("rewards"))
        .order_by("-start_date", "campaign_no")
    )

    if category_type is not None:
        queryset = queryset.filter(category_type=category_type)
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)
    if search is not None:
        queryset = queryset.filter(
            models.Q(title__icontains=search)
            | models.Q(description__icontains=search)
            | models.Q(category_value__icontains=search),
        )

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
        items=[_serialize_campaign_summary(campaign, now) for campaign in items],
    )


@api.get("/campaigns/{campaign_no}/", response=V1CampaignDetailSchema)
def get_campaign(
    request: HttpRequest,
    campaign_no: int,
) -> V1CampaignDetailSchema:
    """Return a single Chzzk drop campaign with its rewards.

    Raises:
        Http404: If the campaign does not exist.
    """
    try:
        campaign: ChzzkCampaign = (
            ChzzkCampaign.objects
            .exclude(state="TESTING")
            .annotate(reward_count=models.Count("rewards"))
            .prefetch_related("rewards")
            .get(campaign_no=campaign_no)
        )
    except ChzzkCampaign.DoesNotExist as exc:
        msg = "Campaign not found"
        raise Http404(msg) from exc

    return _serialize_campaign_detail(campaign, timezone.now())
