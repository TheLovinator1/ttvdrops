from typing import TYPE_CHECKING

from django.db.models.query import QuerySet
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeText
from django.views import generic

from chzzk import models
from twitch.feeds import TTVDropsBaseFeed

if TYPE_CHECKING:
    import datetime

    from django.http import HttpResponse
    from django.http.request import HttpRequest
    from pytest_django.asserts import QuerySet


def dashboard_view(request: HttpRequest) -> HttpResponse:
    """View function for the dashboard page showing all the active chzzk campaigns.

    Args:
        request (HttpRequest): The incoming HTTP request.

    Returns:
        HttpResponse: The HTTP response containing the rendered dashboard page.
    """
    active_campaigns: QuerySet[models.ChzzkCampaign, models.ChzzkCampaign] = (
        models.ChzzkCampaign.objects
        .filter(end_date__gte=timezone.now())
        .exclude(state="TESTING")
        .order_by("-start_date")
    )
    return render(
        request=request,
        template_name="chzzk/dashboard.html",
        context={
            "active_campaigns": active_campaigns,
        },
    )


class CampaignListView(generic.ListView):
    """List view showing all chzzk campaigns."""

    model = models.ChzzkCampaign
    template_name = "chzzk/campaign_list.html"
    context_object_name = "campaigns"
    paginate_by = 25


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
    rewards: QuerySet[models.ChzzkReward, models.ChzzkReward] = campaign.rewards.all()  # pyright: ignore[reportAttributeAccessIssue]
    return render(
        request=request,
        template_name="chzzk/campaign_detail.html",
        context={
            "campaign": campaign,
            "rewards": rewards,
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
        return models.ChzzkCampaign.objects.filter(raw_json__isnull=False).order_by(
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
        if getattr(item, "image_url", ""):
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="320" height="180" />',
                    item.image_url,
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
