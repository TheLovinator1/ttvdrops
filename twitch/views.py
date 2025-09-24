from __future__ import annotations

import datetime
import json
import logging
from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.serializers import serialize
from django.db.models import Count, F, Prefetch, Q
from django.db.models.functions import Trim
from django.db.models.query import QuerySet
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import DetailView, ListView
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers.data import JsonLexer

from twitch.models import Channel, DropBenefit, DropCampaign, Game, NotificationSubscription, Organization, TimeBasedDrop

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse
    from django.http.response import HttpResponseRedirect

logger: logging.Logger = logging.getLogger(__name__)

MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05


# MARK: /search/
def search_view(request: HttpRequest) -> HttpResponse:
    """Search view for all models.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered search results.
    """
    query = request.GET.get("q", "")
    results = {}

    if query:
        if len(query) < MIN_QUERY_LENGTH_FOR_FTS:
            results["organizations"] = Organization.objects.filter(name__istartswith=query)
            results["games"] = Game.objects.filter(Q(name__istartswith=query) | Q(display_name__istartswith=query))
            results["campaigns"] = DropCampaign.objects.filter(Q(name__istartswith=query) | Q(description__icontains=query)).select_related("game")
            results["drops"] = TimeBasedDrop.objects.filter(name__istartswith=query).select_related("campaign")
            results["benefits"] = DropBenefit.objects.filter(name__istartswith=query)
        else:
            search_query = SearchQuery(query)

            # Search Organizations
            org_vector = SearchVector("name")
            org_results = Organization.objects.annotate(rank=SearchRank(org_vector, search_query)).filter(rank__gte=MIN_SEARCH_RANK).order_by("-rank")
            results["organizations"] = org_results

            # Search Games
            game_vector = SearchVector("name", "display_name")
            game_results = Game.objects.annotate(rank=SearchRank(game_vector, search_query)).filter(rank__gte=MIN_SEARCH_RANK).order_by("-rank")
            results["games"] = game_results

            # Search DropCampaigns
            campaign_vector = SearchVector("name", "description")
            campaign_results = (
                DropCampaign.objects.annotate(rank=SearchRank(campaign_vector, search_query))
                .filter(rank__gte=MIN_SEARCH_RANK)
                .select_related("game")
                .order_by("-rank")
            )
            results["campaigns"] = campaign_results

            # Search TimeBasedDrops
            drop_vector = SearchVector("name")
            drop_results = (
                TimeBasedDrop.objects.annotate(rank=SearchRank(drop_vector, search_query))
                .filter(rank__gte=MIN_SEARCH_RANK)
                .select_related("campaign")
                .order_by("-rank")
            )
            results["drops"] = drop_results

            # Search DropBenefits
            benefit_vector = SearchVector("name")
            benefit_results = DropBenefit.objects.annotate(rank=SearchRank(benefit_vector, search_query)).filter(rank__gte=MIN_SEARCH_RANK).order_by("-rank")
            results["benefits"] = benefit_results

    return render(request, "twitch/search_results.html", {"query": query, "results": results})


# MARK: /organizations/
class OrgListView(ListView):
    """List view for organization."""

    model = Organization
    template_name = "twitch/org_list.html"
    context_object_name = "orgs"


# MARK: /organizations/<pk>/
class OrgDetailView(DetailView):
    """Detail view for organization."""

    model = Organization
    template_name = "twitch/organization_detail.html"
    context_object_name = "organization"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context = super().get_context_data(**kwargs)
        organization: Organization = self.object

        user = self.request.user
        if not user.is_authenticated:
            subscription: NotificationSubscription | None = None
        else:
            subscription = NotificationSubscription.objects.filter(user=user, organization=organization).first()

        games: QuerySet[Game, Game] = organization.games.all()  # pyright: ignore[reportAttributeAccessIssue]

        serialized_org = serialize(
            "json",
            [organization],
            fields=("name",),
        )
        org_data = json.loads(serialized_org)

        if games.exists():
            serialized_games = serialize(
                "json",
                games,
                fields=("slug", "name", "display_name", "box_art"),
            )
            games_data = json.loads(serialized_games)
            org_data[0]["fields"]["games"] = games_data

        pretty_org_data = json.dumps(org_data[0], indent=4)

        context.update({
            "subscription": subscription,
            "games": games,
            "org_data": pretty_org_data,
        })

        return context


# MARK: /campaigns/
class DropCampaignListView(ListView):
    """List view for drop campaigns."""

    model = DropCampaign
    template_name = "twitch/campaign_list.html"
    context_object_name = "campaigns"
    paginate_by = 100

    def get_queryset(self) -> QuerySet[DropCampaign]:
        """Get queryset of drop campaigns.

        Returns:
            QuerySet: Filtered drop campaigns.
        """
        queryset: QuerySet[DropCampaign] = super().get_queryset()
        game_filter: str | None = self.request.GET.get("game")

        if game_filter:
            queryset = queryset.filter(game__id=game_filter)

        return queryset.select_related("game__owner").order_by("-start_at")

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)

        context["games"] = Game.objects.all().order_by("display_name")
        context["status_options"] = ["active", "upcoming", "expired"]
        context["now"] = timezone.now()
        context["selected_game"] = str(self.request.GET.get(key="game", default=""))
        context["selected_per_page"] = self.paginate_by
        context["selected_status"] = self.request.GET.get(key="status", default="")

        return context


def format_and_color_json(code: str) -> str:
    """Format and color a JSON string for HTML display.

    Args:
        code: The code string to format.

    Returns:
        str: The formatted code with HTML styles.
    """
    formatted_code: str = json.dumps(code, indent=4)
    return highlight(formatted_code, JsonLexer(), HtmlFormatter())


# MARK: /campaigns/<pk>/
class DropCampaignDetailView(DetailView):
    """Detail view for a drop campaign."""

    model = DropCampaign
    template_name = "twitch/campaign_detail.html"
    context_object_name = "campaign"

    def get_object(self, queryset: QuerySet[DropCampaign] | None = None) -> DropCampaign:
        """Get the campaign object with related data prefetched.

        Args:
            queryset: Optional queryset to use.

        Returns:
            DropCampaign: The campaign object with prefetched relations.
        """
        if queryset is None:
            queryset = self.get_queryset()

        queryset = queryset.select_related("game__owner")

        return super().get_object(queryset=queryset)

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:  # noqa: PLR0914
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        campaign = context["campaign"]
        drops: QuerySet[TimeBasedDrop, TimeBasedDrop] = (
            TimeBasedDrop.objects.filter(campaign=campaign).select_related("campaign").prefetch_related("benefits").order_by("required_minutes_watched")
        )

        serialized_campaign = serialize(
            "json",
            [campaign],
            fields=(
                "name",
                "description",
                "details_url",
                "account_link_url",
                "image_url",
                "start_at",
                "end_at",
                "is_account_connected",
                "game",
                "created_at",
                "updated_at",
            ),
        )
        campaign_data = json.loads(serialized_campaign)

        if drops.exists():
            serialized_drops = serialize(
                "json",
                drops,
                fields=(
                    "name",
                    "required_minutes_watched",
                    "required_subs",
                    "start_at",
                    "end_at",
                ),
            )
            drops_data = json.loads(serialized_drops)

            for i, drop in enumerate(drops):
                benefits = drop.benefits.all()
                if benefits.exists():
                    serialized_benefits = serialize(
                        "json",
                        benefits,
                        fields=("name", "image_asset_url"),
                    )
                    benefits_data = json.loads(serialized_benefits)
                    drops_data[i]["fields"]["benefits"] = benefits_data

            campaign_data[0]["fields"]["drops"] = drops_data

        # Enhance drops with additional context data
        enhanced_drops = []
        now: datetime.datetime = timezone.now()
        for drop in drops:
            # Ensure benefits are loaded
            benefits = list(drop.benefits.all())

            # Calculate countdown text
            if drop.end_at and drop.end_at > now:
                time_diff: datetime.timedelta = drop.end_at - now
                days: int = time_diff.days
                hours, remainder = divmod(time_diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)

                if days > 0:
                    countdown_text: str = f"{days}d {hours}h {minutes}m"
                elif hours > 0:
                    countdown_text = f"{hours}h {minutes}m"
                elif minutes > 0:
                    countdown_text = f"{minutes}m {seconds}s"
                else:
                    countdown_text = f"{seconds}s"
            elif drop.start_at and drop.start_at > now:
                countdown_text = "Not started"
            else:
                countdown_text = "Expired"

            enhanced_drop: dict[str, str | datetime.datetime | TimeBasedDrop] = {
                "drop": drop,
                "local_start": drop.start_at,
                "local_end": drop.end_at,
                "timezone_name": "UTC",
                "countdown_text": countdown_text,
            }
            enhanced_drops.append(enhanced_drop)

        context["now"] = now
        context["drops"] = enhanced_drops
        context["campaign_data"] = format_and_color_json(campaign_data[0])
        context["owner"] = campaign.game.owner
        context["allowed_channels"] = campaign.allow_channels.all().order_by("display_name")

        return context


# MARK: /games/
class GamesGridView(ListView):
    """List view for games grouped by organization."""

    model = Game
    template_name = "twitch/games_grid.html"
    context_object_name = "games"

    def get_queryset(self) -> QuerySet[Game]:
        """Get queryset of all games, annotated with campaign counts.

        Returns:
            QuerySet: Annotated games queryset.
        """
        now: datetime.datetime = timezone.now()
        return (
            super()
            .get_queryset()
            .annotate(
                campaign_count=Count("drop_campaigns", distinct=True),
                active_count=Count(
                    "drop_campaigns",
                    filter=Q(
                        drop_campaigns__start_at__lte=now,
                        drop_campaigns__end_at__gte=now,
                    ),
                    distinct=True,
                ),
            )
            .order_by("display_name")
        )

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data with games grouped by their owning organization in a highly optimized manner.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with games grouped by organization.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        now: datetime.datetime = timezone.now()

        games_with_campaigns: QuerySet[Game, Game] = (
            Game.objects.filter(drop_campaigns__isnull=False)
            .select_related("owner")
            .annotate(
                campaign_count=Count("drop_campaigns", distinct=True),
                active_count=Count(
                    "drop_campaigns",
                    filter=Q(
                        drop_campaigns__start_at__lte=now,
                        drop_campaigns__end_at__gte=now,
                    ),
                    distinct=True,
                ),
            )
            .order_by("owner__name", "display_name")
        )

        games_by_org: defaultdict[Organization, list[dict[str, Game]]] = defaultdict(list)
        for game in games_with_campaigns:
            if game.owner:
                games_by_org[game.owner].append({"game": game})

        context["games_by_org"] = OrderedDict(sorted(games_by_org.items(), key=lambda item: item[0].name))

        return context


# MARK: /games/<pk>/
class GameDetailView(DetailView):
    """Detail view for a game."""

    model = Game
    template_name = "twitch/game_detail.html"
    context_object_name = "game"

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired campaigns.
                 Expired campaigns are filtered based on either end date or status.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        game: Game = self.get_object()

        user = self.request.user
        if not user.is_authenticated:
            subscription: NotificationSubscription | None = None
        else:
            subscription = NotificationSubscription.objects.filter(user=user, game=game).first()

        now: datetime.datetime = timezone.now()
        all_campaigns: QuerySet[DropCampaign, DropCampaign] = (
            DropCampaign.objects.filter(game=game)
            .select_related("game__owner")
            .prefetch_related(
                Prefetch(
                    "time_based_drops", queryset=TimeBasedDrop.objects.prefetch_related(Prefetch("benefits", queryset=DropBenefit.objects.order_by("name")))
                )
            )
            .order_by("-end_at")
        )

        active_campaigns: list[DropCampaign] = [
            campaign
            for campaign in all_campaigns
            if campaign.start_at is not None and campaign.start_at <= now and campaign.end_at is not None and campaign.end_at >= now
        ]
        active_campaigns.sort(key=lambda c: c.end_at if c.end_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC))

        upcoming_campaigns: list[DropCampaign] = [campaign for campaign in all_campaigns if campaign.start_at is not None and campaign.start_at > now]

        upcoming_campaigns.sort(key=lambda c: c.start_at if c.start_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC))

        expired_campaigns: list[DropCampaign] = [campaign for campaign in all_campaigns if campaign.end_at is not None and campaign.end_at < now]

        # Add unique sorted benefits to each campaign object
        for campaign in all_campaigns:
            benefits_dict = {}  # Use dict to track unique benefits by ID
            for drop in campaign.time_based_drops.all():  # type: ignore[attr-defined]
                for benefit in drop.benefits.all():
                    benefits_dict[benefit.id] = benefit
            # Sort benefits by name and attach to campaign
            campaign.sorted_benefits = sorted(benefits_dict.values(), key=lambda b: b.name)  # type: ignore[attr-defined]

        serialized_game = serialize(
            "json",
            [game],
            fields=(
                "slug",
                "name",
                "display_name",
                "box_art",
                "owner",
            ),
        )
        game_data = json.loads(serialized_game)

        if all_campaigns.exists():
            serialized_campaigns = serialize(
                "json",
                all_campaigns,
                fields=(
                    "name",
                    "description",
                    "details_url",
                    "account_link_url",
                    "image_url",
                    "start_at",
                    "end_at",
                    "is_account_connected",
                ),
            )
            campaigns_data = json.loads(serialized_campaigns)
            game_data[0]["fields"]["campaigns"] = campaigns_data

        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "subscription": subscription,
            "owner": game.owner,
            "now": now,
            "game_data": format_and_color_json(game_data[0]),
        })

        return context


# MARK: /
def dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard view showing active campaigns and progress.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered dashboard template.
    """
    now: datetime.datetime = timezone.now()
    active_campaigns: QuerySet[DropCampaign] = (
        DropCampaign.objects.filter(start_at__lte=now, end_at__gte=now)
        .select_related("game__owner")
        .prefetch_related(
            "allow_channels",
        )
    )

    campaigns_by_org_game: dict[str, Any] = {}

    for campaign in active_campaigns:
        owner: Organization | None = campaign.game.owner

        org_id: str = owner.id if owner else "unknown"
        org_name: str = owner.name if owner else "Unknown"
        game_id: str = campaign.game.id
        game_name: str = campaign.game.display_name

        if org_id not in campaigns_by_org_game:
            campaigns_by_org_game[org_id] = {"name": org_name, "games": {}}

        if game_id not in campaigns_by_org_game[org_id]["games"]:
            campaigns_by_org_game[org_id]["games"][game_id] = {
                "name": game_name,
                "box_art": campaign.game.box_art_best_url,
                "campaigns": [],
            }

        campaigns_by_org_game[org_id]["games"][game_id]["campaigns"].append(campaign)

    sorted_campaigns_by_org_game: dict[str, Any] = {
        org_id: campaigns_by_org_game[org_id] for org_id in sorted(campaigns_by_org_game.keys(), key=lambda k: campaigns_by_org_game[k]["name"])
    }

    for org_data in sorted_campaigns_by_org_game.values():
        org_data["games"] = {game_id: org_data["games"][game_id] for game_id in sorted(org_data["games"].keys(), key=lambda k: org_data["games"][k]["name"])}

    return render(
        request,
        "twitch/dashboard.html",
        {
            "active_campaigns": active_campaigns,
            "campaigns_by_org_game": sorted_campaigns_by_org_game,
            "now": now,
        },
    )


# MARK: /debug/
@login_required
def debug_view(request: HttpRequest) -> HttpResponse:
    """Debug view showing potentially broken or inconsistent data.

    Only staff users may access this endpoint.

    Returns:
        HttpResponse: Rendered debug template or redirect if unauthorized.
    """
    now = timezone.now()

    # Games with no assigned owner organization
    games_without_owner: QuerySet[Game] = Game.objects.filter(owner__isnull=True).order_by("display_name")

    # Campaigns with missing or obviously broken images (empty or not starting with http)
    broken_image_campaigns: QuerySet[DropCampaign] = DropCampaign.objects.filter(
        Q(image_url__isnull=True) | Q(image_url__exact="") | ~Q(image_url__startswith="http")
    ).select_related("game")

    # Benefits with missing images
    broken_benefit_images: QuerySet[DropBenefit] = DropBenefit.objects.annotate(trimmed_url=Trim("image_asset_url")).filter(
        Q(image_asset_url__isnull=True) | Q(trimmed_url__exact="") | ~Q(image_asset_url__startswith="http")
    )

    # Time-based drops without any benefits
    drops_without_benefits: QuerySet[TimeBasedDrop] = TimeBasedDrop.objects.filter(benefits__isnull=True).select_related("campaign__game")

    # Campaigns with invalid dates (start after end or missing either)
    invalid_date_campaigns: QuerySet[DropCampaign] = DropCampaign.objects.filter(
        Q(start_at__gt=F("end_at")) | Q(start_at__isnull=True) | Q(end_at__isnull=True)
    ).select_related("game")

    # Duplicate campaign names per game. We retrieve the game's name for user-friendly display.
    duplicate_name_campaigns = (
        DropCampaign.objects.values("game_id", "game__display_name", "name")
        .annotate(name_count=Count("id"))
        .filter(name_count__gt=1)
        .order_by("game__display_name", "name")
    )

    # Campaigns currently active but image missing
    active_missing_image: QuerySet[DropCampaign] = (
        DropCampaign.objects.filter(start_at__lte=now, end_at__gte=now)
        .filter(Q(image_url__isnull=True) | Q(image_url__exact="") | ~Q(image_url__startswith="http"))
        .select_related("game")
    )

    context: dict[str, Any] = {
        "now": now,
        "games_without_owner": games_without_owner,
        "broken_image_campaigns": broken_image_campaigns,
        "broken_benefit_images": broken_benefit_images,
        "drops_without_benefits": drops_without_benefits,
        "invalid_date_campaigns": invalid_date_campaigns,
        "duplicate_name_campaigns": duplicate_name_campaigns,
        "active_missing_image": active_missing_image,
    }

    return render(request, "twitch/debug.html", context)


# MARK: /games/<pk>/subscribe/
@login_required
def subscribe_game_notifications(request: HttpRequest, game_id: str) -> HttpResponseRedirect:
    """Update Game notification for a user.

    Args:
        request: The HTTP request.
        game_id: The game we are updating.

    Returns:
        Redirect back to the twitch:game_detail.
    """
    game: Game = get_object_or_404(Game, pk=game_id)
    if request.method == "POST":
        notify_found = bool(request.POST.get("notify_found"))
        notify_live = bool(request.POST.get("notify_live"))

        subscription, created = NotificationSubscription.objects.get_or_create(user=request.user, game=game)

        changes = []
        if not created:
            if subscription.notify_found != notify_found:
                changes.append(f"{'Enabled' if notify_found else 'Disabled'} notification when drop is found")
            if subscription.notify_live != notify_live:
                changes.append(f"{'Enabled' if notify_live else 'Disabled'} notification when drop is farmable")

        subscription.notify_found = notify_found
        subscription.notify_live = notify_live
        subscription.save()

        if created:
            message = f"You have subscribed to notifications for {game.display_name}"
        elif changes:
            message = "\n".join(changes)
        else:
            message = ""

        messages.success(request, message)
        return redirect("twitch:game_detail", pk=game.id)

    messages.warning(request, "Only POST is available for this view.")
    return redirect("twitch:game_detail", pk=game.id)


# MARK: /organizations/<pk>/subscribe/
@login_required
def subscribe_org_notifications(request: HttpRequest, org_id: str) -> HttpResponseRedirect:
    """Update Organization notification for a user.

    Args:
        request: The HTTP request.
        org_id: The org we are updating.

    Returns:
        Redirect back to the twitch:organization_detail.
    """
    organization: Organization = get_object_or_404(Organization, pk=org_id)

    if request.method == "POST":
        notify_found = bool(request.POST.get("notify_found"))
        notify_live = bool(request.POST.get("notify_live"))

        subscription, created = NotificationSubscription.objects.get_or_create(user=request.user, organization=organization)

        changes = []
        if not created:
            if subscription.notify_found != notify_found:
                changes.append(f"{'Enabled' if notify_found else 'Disabled'} notification when drop is found")
            if subscription.notify_live != notify_live:
                changes.append(f"{'Enabled' if notify_live else 'Disabled'} notification when drop is farmable")

        subscription.notify_found = notify_found
        subscription.notify_live = notify_live
        subscription.save()

        if created:
            message = f"You have subscribed to notifications for this {organization.name}"
        elif changes:
            message = "\n".join(changes)
        else:
            message = ""

        messages.success(request, message)
        return redirect("twitch:organization_detail", pk=organization.id)

    messages.warning(request, "Only POST is available for this view.")
    return redirect("twitch:organization_detail", pk=organization.id)


# MARK: /games/list/
class GamesListView(GamesGridView):
    """List view for games in simple list format."""

    template_name = "twitch/games_list.html"


# MARK: /docs/rss/
def docs_rss_view(request: HttpRequest) -> HttpResponse:
    """View for /docs/rss that lists all available RSS feeds.

    Args:
        request: The HTTP request object.

    Returns:
        Rendered HTML response with list of RSS feeds.
    """
    feeds: list[dict[str, str]] = [
        {
            "title": "Organizations",
            "description": "Latest organizations",
            "url": "/rss/organizations/",
        },
        {
            "title": "Games",
            "description": "Latest games",
            "url": "/rss/games/",
        },
        {
            "title": "Drop Campaigns",
            "description": "Latest drop campaigns",
            "url": "/rss/campaigns/",
        },
    ]
    return render(request, "twitch/docs_rss.html", {"feeds": feeds})


# MARK: /channels/
class ChannelListView(ListView):
    """List view for channels."""

    model = Channel
    template_name = "twitch/channel_list.html"
    context_object_name = "channels"
    paginate_by = 200

    def get_queryset(self) -> QuerySet[Channel]:
        """Get queryset of channels.

        Returns:
            QuerySet: Filtered channels.
        """
        queryset: QuerySet[Channel] = super().get_queryset()
        search_query: str | None = self.request.GET.get("search")

        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(display_name__icontains=search_query))

        return queryset.annotate(campaign_count=Count("allowed_campaigns", distinct=True)).order_by("-campaign_count", "name")

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        context["search_query"] = self.request.GET.get("search", "")
        return context


# MARK: /channels/<pk>/
class ChannelDetailView(DetailView):
    """Detail view for a channel."""

    model = Channel
    template_name = "twitch/channel_detail.html"
    context_object_name = "channel"

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired campaigns for this channel.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        channel: Channel = self.get_object()

        now: datetime.datetime = timezone.now()
        all_campaigns: QuerySet[DropCampaign, DropCampaign] = (
            DropCampaign.objects.filter(allow_channels=channel)
            .select_related("game__owner")
            .prefetch_related(
                Prefetch(
                    "time_based_drops",
                    queryset=TimeBasedDrop.objects.prefetch_related(
                        Prefetch("benefits", queryset=DropBenefit.objects.order_by("name")),
                    ),
                )
            )
            .order_by("-start_at")
        )

        active_campaigns: list[DropCampaign] = [
            campaign
            for campaign in all_campaigns
            if campaign.start_at is not None and campaign.start_at <= now and campaign.end_at is not None and campaign.end_at >= now
        ]
        active_campaigns.sort(key=lambda c: c.end_at if c.end_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC))

        upcoming_campaigns: list[DropCampaign] = [campaign for campaign in all_campaigns if campaign.start_at is not None and campaign.start_at > now]
        upcoming_campaigns.sort(key=lambda c: c.start_at if c.start_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC))

        expired_campaigns: list[DropCampaign] = [campaign for campaign in all_campaigns if campaign.end_at is not None and campaign.end_at < now]

        # Add unique sorted benefits to each campaign object
        for campaign in all_campaigns:
            benefits_dict = {}  # Use dict to track unique benefits by ID
            for drop in campaign.time_based_drops.all():  # type: ignore[attr-defined]
                for benefit in drop.benefits.all():
                    benefits_dict[benefit.id] = benefit
            # Sort benefits by name and attach to campaign
            campaign.sorted_benefits = sorted(benefits_dict.values(), key=lambda b: b.name)  # type: ignore[attr-defined]

        serialized_channel = serialize(
            "json",
            [channel],
            fields=(
                "name",
                "display_name",
            ),
        )
        channel_data = json.loads(serialized_channel)

        if all_campaigns.exists():
            serialized_campaigns = serialize(
                "json",
                all_campaigns,
                fields=(
                    "name",
                    "description",
                    "details_url",
                    "account_link_url",
                    "image_url",
                    "start_at",
                    "end_at",
                    "is_account_connected",
                ),
            )
            campaigns_data = json.loads(serialized_campaigns)
            channel_data[0]["fields"]["campaigns"] = campaigns_data

        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "now": now,
            "channel_data": format_and_color_json(channel_data[0]),
        })

        return context
