import json
from collections import OrderedDict
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db.models import Count
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeText
from django.views import generic

from chzzk import models
from core.campaign_timing import campaign_timing_stats
from core.discord_embed import CHZZK_ACCENT_COLOR
from core.discord_embed import embed_buttons
from twitch.feeds import TTVDropsBaseFeed

if TYPE_CHECKING:
    import datetime

    from django.db.models.query import QuerySet
    from django.http import HttpResponse
    from django.http.request import HttpRequest


def dashboard_view(request: HttpRequest) -> HttpResponse:
    """View function for the dashboard page showing all the active chzzk campaigns.

    Args:
        request (HttpRequest): The incoming HTTP request.

    Returns:
        HttpResponse: The HTTP response containing the rendered dashboard page.
    """
    now = timezone.now()
    active_campaigns: QuerySet[models.ChzzkCampaign, models.ChzzkCampaign] = (
        models.ChzzkCampaign.objects
        .filter(end_date__gte=now)
        .exclude(state="TESTING")
        .prefetch_related("rewards")
        .order_by("-start_date")
    )
    counts: dict[str, int] = {
        "active_campaigns": active_campaigns.count(),
        "total_campaigns": models.ChzzkCampaign.objects.exclude(
            state="TESTING",
        ).count(),
        "total_rewards": models.ChzzkReward.objects.count(),
    }
    dashboard_games: OrderedDict[str, dict[str, str]] = OrderedDict()
    for campaign in active_campaigns:
        if campaign.category_id not in dashboard_games:
            dashboard_games[campaign.category_id] = {
                "name": campaign.category_display,
                "url": campaign.category_url,
            }

    return render(
        request=request,
        template_name="chzzk/dashboard.html",
        context={
            "active_campaigns": active_campaigns,
            "dashboard_games": list(dashboard_games.values()),
            "counts": counts,
            "now": now,
            "page_title": "Chzzk Drops - Campaigns, Rewards and Schedule",
            "page_description": (
                "Browse active Chzzk drop campaigns, rewards and schedules."
            ),
            "discord_embed_accent_color": CHZZK_ACCENT_COLOR,
            "discord_embed_buttons": embed_buttons(
                ("Chzzk Drops", "https://chzzk.naver.com/"),
                ("Campaigns", "/chzzk/campaigns/"),
            ),
        },
    )


def _game_list_rows() -> list[dict[str, Any]]:
    """Return GAME categories with campaign counts and representative artwork."""
    now = timezone.now()
    latest_campaign = (
        models.ChzzkCampaign.objects
        .filter(
            category_type=OuterRef("category_type"),
            category_id=OuterRef("category_id"),
        )
        .exclude(state="TESTING")
        .order_by("-start_date")
    )
    queryset = (
        models.ChzzkCampaign.objects
        .filter(category_type__iexact="GAME")
        .exclude(state="TESTING")
        .values("category_type", "category_id")
        .annotate(
            category_value=Max("category_value"),
            campaign_count=Count("id"),
            active_campaign_count=Count(
                "id",
                filter=Q(start_date__lte=now, end_date__gte=now),
            ),
            upcoming_campaign_count=Count(
                "id",
                filter=Q(start_date__gt=now),
            ),
            last_campaign_ended_at=Max(
                "end_date",
                filter=Q(end_date__lt=now),
            ),
            latest_image_url=Subquery(latest_campaign.values("image_url")[:1]),
            latest_image_file=Subquery(latest_campaign.values("image_file")[:1]),
        )
        .order_by("category_value", "category_id")
    )

    rows: list[dict[str, Any]] = []
    for row in queryset:
        category_value: str = row["category_value"] or row["category_id"]
        category_id_display: str = row["category_id"].replace("_", " ")
        category_display: str = (
            f"{category_value} ({category_id_display})"
            if category_id_display and category_id_display != category_value
            else category_value
        )
        image_file: str = row.pop("latest_image_file") or ""
        image_url: str = row.pop("latest_image_url") or ""
        image_best_url: str = (
            f"{settings.MEDIA_URL.rstrip('/')}/{image_file.lstrip('/')}"
            if image_file
            else image_url
        )
        rows.append({
            **row,
            "category_value": category_value,
            "category_id_display": category_id_display,
            "category_display": category_display,
            "category_url": reverse(
                "chzzk:game_detail",
                args=[row["category_id"]],
            ),
            "image_best_url": image_best_url,
        })

    return rows


def game_list_view(request: HttpRequest) -> HttpResponse:
    """Show Chzzk games in a grid layout.

    Returns:
        The rendered Chzzk games grid.
    """
    games: list[dict[str, Any]] = _game_list_rows()
    seo_context: dict[str, Any] = {
        "page_title": "Chzzk Games with Drops, Rewards and Campaigns",
        "page_description": "Games on Chzzk that have drop campaigns.",
        "discord_embed_accent_color": CHZZK_ACCENT_COLOR,
        "discord_embed_buttons": embed_buttons(
            ("Chzzk Drops", "https://chzzk.naver.com/"),
            ("Campaigns", "/chzzk/campaigns/"),
        ),
    }
    return render(
        request,
        "chzzk/category_list.html",
        {
            "games": games,
            **seo_context,
        },
    )


def game_list_table_view(request: HttpRequest) -> HttpResponse:
    """Show Chzzk games in a list layout.

    Returns:
        The rendered Chzzk games list.
    """
    games: list[dict[str, Any]] = _game_list_rows()
    seo_context: dict[str, Any] = {
        "page_title": "Chzzk Games List with Drops, Rewards and Campaigns",
        "page_description": "Games on Chzzk that have drop campaigns.",
        "discord_embed_accent_color": CHZZK_ACCENT_COLOR,
        "discord_embed_buttons": embed_buttons(
            ("Chzzk Drops", "https://chzzk.naver.com/"),
            ("Campaigns", "/chzzk/campaigns/"),
        ),
    }
    return render(
        request,
        "chzzk/category_list_table.html",
        {
            "games": games,
            **seo_context,
        },
    )


def _category_campaigns(
    category_type: str,
    category_id: str,
) -> list[models.ChzzkCampaign]:
    """Return non-testing campaigns for a Chzzk category.

    Returns:
        Campaigns belonging to the category.

    Raises:
        Http404: If the category has no non-testing campaigns.
    """
    campaigns: list[models.ChzzkCampaign] = list(
        models.ChzzkCampaign.objects
        .filter(
            category_type__iexact=category_type,
            category_id=category_id,
        )
        .exclude(state="TESTING")
        .prefetch_related("rewards")
        .order_by("-start_date"),
    )
    if not campaigns:
        msg = "No campaigns found for this category"
        raise Http404(msg)
    return campaigns


def _render_category_detail(
    request: HttpRequest,
    category_type: str,
    category_id: str,
) -> HttpResponse:
    """Render all campaigns belonging to a Chzzk category.

    Returns:
        The rendered category detail response.
    """
    campaigns: list[models.ChzzkCampaign] = _category_campaigns(
        category_type,
        category_id,
    )
    now = timezone.now()
    category_value: str = next(
        (campaign.category_value for campaign in campaigns if campaign.category_value),
        category_id,
    )
    category_id_display: str = category_id.replace("_", " ")
    category_display: str = (
        f"{category_value} ({category_id_display})"
        if category_id_display and category_id_display != category_value
        else category_value
    )
    active_campaigns: list[models.ChzzkCampaign] = [
        campaign
        for campaign in campaigns
        if campaign.start_date <= now <= campaign.end_date
    ]
    upcoming_campaigns: list[models.ChzzkCampaign] = [
        campaign for campaign in campaigns if campaign.start_date > now
    ]
    expired_campaigns: list[models.ChzzkCampaign] = [
        campaign for campaign in campaigns if campaign.end_date < now
    ]
    reward_count: int = sum(len(campaign.rewards.all()) for campaign in campaigns)
    last_campaign_end_at, average_gap_days = campaign_timing_stats(
        (campaign.start_date, campaign.end_date) for campaign in campaigns
    )

    return render(
        request=request,
        template_name="chzzk/category_detail.html",
        context={
            "selected_status": request.GET.get("status", ""),
            "category_type": campaigns[0].category_type,
            "category_id": category_id,
            "category_value": category_value,
            "category_display": category_display,
            "campaigns": campaigns,
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "latest_campaign": campaigns[0],
            "now": now,
            "active_count": len(active_campaigns),
            "upcoming_count": len(upcoming_campaigns),
            "expired_count": len(expired_campaigns),
            "reward_count": reward_count,
            "last_campaign_end_at": last_campaign_end_at,
            "average_gap_days": average_gap_days,
            "page_title": f"{category_display} - Chzzk Drops and Campaigns",
            "page_description": f"Chzzk drop campaigns for {category_display}.",
            "discord_embed_accent_color": CHZZK_ACCENT_COLOR,
            "discord_embed_buttons": embed_buttons(
                ("Chzzk Drops", "https://chzzk.naver.com/"),
                ("Campaigns", "/chzzk/campaigns/"),
            ),
        },
    )


def game_detail_view(request: HttpRequest, category_id: str) -> HttpResponse:
    """Show all Chzzk campaigns for a GAME category.

    Returns:
        The rendered game detail response.
    """
    return _render_category_detail(request, "GAME", category_id)


def category_detail_view(
    request: HttpRequest,
    category_type: str,
    category_id: str,
) -> HttpResponse:
    """Show all Chzzk campaigns for a non-game category.

    Returns:
        The rendered category detail response.
    """
    return _render_category_detail(request, category_type, category_id)


class CampaignListView(generic.ListView):
    """List view showing all chzzk campaigns."""

    model = models.ChzzkCampaign
    template_name = "chzzk/campaign_list.html"
    context_object_name = "campaigns"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[models.ChzzkCampaign, models.ChzzkCampaign]:
        """Return campaigns filtered by the selected status tab."""
        queryset = super().get_queryset().exclude(state="TESTING")
        status: str = self.request.GET.get("status", "")
        now = timezone.now()

        if status == "active":
            queryset = queryset.filter(start_date__lte=now, end_date__gte=now)
        elif status == "upcoming":
            queryset = queryset.filter(start_date__gt=now)
        elif status == "expired":
            queryset = queryset.filter(end_date__lt=now)
        elif status == "ios":
            queryset = queryset.filter(has_ios_based_reward=True)

        return queryset.order_by("-start_date")

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add SEO metadata to the campaign list context.

        Args:
            **kwargs: Additional context data.

        Returns:
            dict[str, Any]: Context data with page title and description.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        context["now"] = timezone.now()
        context["selected_status"] = self.request.GET.get("status", "")
        context["page_title"] = "Chzzk Drop Campaigns, Rewards and Archive"
        context["page_description"] = (
            "Browse all Chzzk drop campaigns, rewards and past campaigns."
        )
        context["discord_embed_accent_color"] = CHZZK_ACCENT_COLOR
        context["discord_embed_buttons"] = embed_buttons(
            ("Chzzk Drops", "https://chzzk.naver.com/"),
            ("Campaigns", "/chzzk/campaigns/"),
        )
        return context


def campaign_detail_view(request: HttpRequest, campaign_no: int) -> HttpResponse:
    """View function for the campaign detail page showing information about a single chzzk campaign.

    Args:
        request (HttpRequest): The incoming HTTP request.
        campaign_no (int): The campaign number of the campaign to display.

    Returns:
        HttpResponse: The HTTP response containing the rendered campaign detail page.
    """
    campaign: models.ChzzkCampaign = get_object_or_404(
        models.ChzzkCampaign,
        campaign_no=campaign_no,
    )
    now = timezone.now()
    if campaign.start_date <= now <= campaign.end_date:
        campaign_status: str = "active"
    elif campaign.start_date > now:
        campaign_status = "upcoming"
    else:
        campaign_status = "expired"

    rewards: QuerySet[models.ChzzkReward, models.ChzzkReward] = campaign.rewards.all()  # pyright: ignore[reportAttributeAccessIssue]
    campaign_progress_percent: int = 0
    if (
        campaign.start_date
        and campaign.end_date
        and campaign.end_date > campaign.start_date
    ):
        if now >= campaign.end_date:
            campaign_progress_percent = 100
        elif now > campaign.start_date:
            elapsed: float = (now - campaign.start_date).total_seconds()
            total: float = (campaign.end_date - campaign.start_date).total_seconds()
            campaign_progress_percent = round(elapsed / total * 100)
    technical_details: dict[str, Any] = {
        "campaign_no": campaign.campaign_no,
        "title": campaign.title,
        "description": campaign.description,
        "image_url": campaign.image_url,
        "image_best_url": campaign.image_best_url,
        "category_type": campaign.category_type,
        "category_id": campaign.category_id,
        "category_value": campaign.category_value,
        "category_display": campaign.category_display,
        "pc_link_url": campaign.pc_link_url,
        "mobile_link_url": campaign.mobile_link_url,
        "service_id": campaign.service_id,
        "state": campaign.state,
        "status": campaign_status,
        "start_date": (
            campaign.start_date.isoformat() if campaign.start_date else None
        ),
        "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
        "has_ios_based_reward": campaign.has_ios_based_reward,
        "drops_campaign_not_started": campaign.drops_campaign_not_started,
        "campaign_reward_type": campaign.campaign_reward_type,
        "reward_type": campaign.reward_type,
        "account_link_url": campaign.account_link_url,
        "scraped_at": campaign.scraped_at.isoformat() if campaign.scraped_at else None,
        "source_api": campaign.source_api,
        "rewards": [
            {
                "reward_no": reward.reward_no,
                "title": reward.title,
                "reward_type": reward.reward_type,
                "campaign_reward_type": reward.campaign_reward_type,
                "condition_type": reward.condition_type,
                "condition_for_minutes": reward.condition_for_minutes,
                "ios_based_reward": reward.ios_based_reward,
                "code_remaining_count": reward.code_remaining_count,
                "image_url": reward.image_url,
                "image_best_url": reward.image_best_url,
            }
            for reward in rewards
        ],
    }

    return render(
        request=request,
        template_name="chzzk/campaign_detail.html",
        context={
            "campaign": campaign,
            "campaign_status": campaign_status,
            "rewards": rewards,
            "now": now,
            "campaign_progress_percent": campaign_progress_percent,
            "technical_details_json": json.dumps(
                technical_details,
                indent=2,
                ensure_ascii=False,
            ),
            "page_title": f"{campaign.title} - Chzzk Drop Campaign and Rewards",
            "page_description": campaign.description or campaign.title,
            "discord_embed_accent_color": CHZZK_ACCENT_COLOR,
            "discord_embed_buttons": embed_buttons(
                ("Campaign page", campaign.pc_link_url),
                ("Chzzk Drops", "https://chzzk.naver.com/"),
                ("All campaigns", "/chzzk/campaigns/"),
            ),
        },
    )


class ChzzkCampaignFeed(TTVDropsBaseFeed):
    """RSS feed for the latest chzzk campaigns."""

    title: str = "chzzk campaigns"
    link: str = "/chzzk/campaigns/"
    description: str = "Latest chzzk campaigns"

    _limit: int | None = None

    def __call__(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Allow an optional 'limit' query parameter to specify the number of items in the feed.

        Args:
            request (HttpRequest): The incoming HTTP request.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: The HTTP response containing the feed.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 50))
            except ValueError, TypeError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> QuerySet[models.ChzzkCampaign, models.ChzzkCampaign]:
        """Return the latest chzzk campaigns with raw_json data, ordered by start date.

        Returns:
            QuerySet: A queryset of ChzzkCampaign objects.
        """
        limit: int = self._limit if self._limit is not None else 50
        return models.ChzzkCampaign.objects.filter(
            Q(raw_json_v1__isnull=False) | Q(raw_json_v2__isnull=False),
        ).order_by(
            "-start_date",
        )[:limit]

    def item_title(self, item: models.ChzzkCampaign) -> str:
        """Return the title of the feed item, which is the campaign title.

        Args:
            item (ChzzkCampaign): The campaign object for the feed item.

        Returns:
            str: The title of the feed item.
        """
        return item.title

    def item_description(self, item: models.ChzzkCampaign) -> SafeText:
        """Return the description of the feed item, which includes the campaign image and description.

        Args:
            item (ChzzkCampaign): The campaign object for the feed item.

        Returns:
            SafeText: The HTML description of the feed item.
        """
        parts: list[SafeText] = []
        image_url: str = getattr(item, "image_best_url", "")
        if image_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="320" height="180" />',
                    self._absolute_url(image_url),
                    item.title,
                ),
            )
        if getattr(item, "description", ""):
            parts.append(format_html("<p>{}</p>", item.description))

        # Link back to the PC detail URL when available
        if getattr(item, "pc_link_url", ""):
            parts.append(
                format_html('<p><a href="{}">Details</a></p>', item.pc_link_url),
            )

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: models.ChzzkCampaign) -> str:
        """Return the URL for the feed item, which is the campaign detail page.

        Args:
            item (ChzzkCampaign): The campaign object for the feed item.

        Returns:
            str: The URL for the feed item.
        """
        return reverse("chzzk:campaign_detail", args=[item.pk])

    def item_pubdate(self, item: models.ChzzkCampaign) -> datetime.datetime:
        """Return the publication date of the feed item, which is the campaign start date.

        Args:
            item (ChzzkCampaign): The campaign object for the feed item.

        Returns:
            datetime.datetime: The publication date of the feed item.
        """
        return getattr(item, "start_date", timezone.now()) or timezone.now()

    def item_updateddate(self, item: models.ChzzkCampaign) -> datetime.datetime:
        """Return the last updated date of the feed item, which is the campaign scraped date.

        Args:
            item (ChzzkCampaign): The campaign object for the feed item.

        Returns:
            datetime.datetime: The last updated date of the feed item.
        """
        return getattr(item, "scraped_at", timezone.now()) or timezone.now()

    def item_author_name(self, item: models.ChzzkCampaign) -> str:
        """Return the author name for the feed item. Since we don't have a specific author, return a default value.

        Args:
            item (ChzzkCampaign): The campaign object for the feed item.

        Returns:
            str: The author name for the feed item.
        """
        return item.category_id or "Unknown Category"

    def feed_url(self) -> str:
        """Return the URL of the feed itself.

        Returns:
            str: The URL of the feed.
        """
        return reverse("chzzk:campaign_feed")
