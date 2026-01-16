from __future__ import annotations

import datetime
import json
import logging
from collections import OrderedDict
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.core.paginator import EmptyPage
from django.core.paginator import PageNotAnInteger
from django.core.paginator import Paginator
from django.core.serializers import serialize
from django.db.models import Count
from django.db.models import F
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import Subquery
from django.db.models.functions import Trim
from django.db.models.query import QuerySet
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import DetailView
from django.views.generic import ListView
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers.data import JsonLexer

from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django.http import HttpResponse

logger: logging.Logger = logging.getLogger("ttvdrops.views")

MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05


def emote_gallery_view(request: HttpRequest) -> HttpResponse:
    """View to display all emote images (distribution_type='EMOTE'), clickable to their campaign.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered emote gallery page.
    """
    emote_benefits: QuerySet[DropBenefit, DropBenefit] = (
        DropBenefit.objects
        .filter(distribution_type="EMOTE")
        .select_related()
        .prefetch_related(
            Prefetch(
                "drops",
                queryset=TimeBasedDrop.objects.select_related("campaign"),
                to_attr="_emote_drops",
            ),
        )
    )

    emotes: list[dict[str, str | DropCampaign]] = []
    for benefit in emote_benefits:
        # Find the first drop with a campaign for this benefit
        drop: TimeBasedDrop | None = next((d for d in getattr(benefit, "_emote_drops", []) if d.campaign), None)
        if drop and drop.campaign:
            emotes.append({
                "image_url": benefit.image_best_url,
                "campaign": drop.campaign,
            })

    context: dict[str, list[dict[str, Any]]] = {"emotes": emotes}
    return render(request, "twitch/emote_gallery.html", context)


# MARK: /search/
def search_view(request: HttpRequest) -> HttpResponse:
    """Search view for all models.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered search results.
    """
    query: str = request.GET.get("q", "")
    results: dict[str, QuerySet] = {}

    if query:
        if len(query) < MIN_QUERY_LENGTH_FOR_FTS:
            results["organizations"] = Organization.objects.filter(name__istartswith=query)
            results["games"] = Game.objects.filter(Q(name__istartswith=query) | Q(display_name__istartswith=query))
            results["campaigns"] = DropCampaign.objects.filter(
                Q(name__istartswith=query) | Q(description__icontains=query),
            ).select_related("game")
            results["drops"] = TimeBasedDrop.objects.filter(name__istartswith=query).select_related("campaign")
            results["benefits"] = DropBenefit.objects.filter(name__istartswith=query).prefetch_related(
                "drops__campaign",
            )
            results["reward_campaigns"] = RewardCampaign.objects.filter(
                Q(name__istartswith=query) | Q(brand__istartswith=query) | Q(summary__icontains=query),
            ).select_related("game")
            results["badge_sets"] = ChatBadgeSet.objects.filter(set_id__istartswith=query)
            results["badges"] = ChatBadge.objects.filter(
                Q(title__istartswith=query) | Q(description__icontains=query),
            ).select_related("badge_set")
        else:
            # SQLite-compatible text search using icontains
            results["organizations"] = Organization.objects.filter(
                name__icontains=query,
            )
            results["games"] = Game.objects.filter(
                Q(name__icontains=query) | Q(display_name__icontains=query),
            )
            results["campaigns"] = DropCampaign.objects.filter(
                Q(name__icontains=query) | Q(description__icontains=query),
            ).select_related("game")
            results["drops"] = TimeBasedDrop.objects.filter(
                name__icontains=query,
            ).select_related("campaign")
            results["benefits"] = DropBenefit.objects.filter(
                name__icontains=query,
            ).prefetch_related("drops__campaign")
            results["reward_campaigns"] = RewardCampaign.objects.filter(
                Q(name__icontains=query) | Q(brand__icontains=query) | Q(summary__icontains=query),
            ).select_related("game")
            results["badge_sets"] = ChatBadgeSet.objects.filter(set_id__icontains=query)
            results["badges"] = ChatBadge.objects.filter(
                Q(title__icontains=query) | Q(description__icontains=query),
            ).select_related("badge_set")

    return render(
        request,
        "twitch/search_results.html",
        {"query": query, "results": results},
    )


# MARK: /organizations/
def org_list_view(request: HttpRequest) -> HttpResponse:
    """Function-based view for organization list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered organization list page.
    """
    orgs: QuerySet[Organization] = Organization.objects.all().order_by("name")

    # Serialize all organizations
    serialized_orgs: str = serialize(
        "json",
        orgs,
        fields=(
            "twitch_id",
            "name",
            "added_at",
            "updated_at",
        ),
    )
    orgs_data: list[dict] = json.loads(serialized_orgs)

    context: dict[str, Any] = {
        "orgs": orgs,
        "orgs_data": format_and_color_json(orgs_data),
    }

    return render(request, "twitch/org_list.html", context)


# MARK: /organizations/<twitch_id>/
def organization_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Function-based view for organization detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the organization.

    Returns:
        HttpResponse: The rendered organization detail page.

    Raises:
        Http404: If the organization is not found.
    """
    try:
        organization: Organization = Organization.objects.get(twitch_id=twitch_id)
    except Organization.DoesNotExist as exc:
        msg = "No organization found matching the query"
        raise Http404(msg) from exc

    games: QuerySet[Game] = organization.games.all()  # pyright: ignore[reportAttributeAccessIssue]

    serialized_org: str = serialize(
        "json",
        [organization],
        fields=(
            "twitch_id",
            "name",
            "added_at",
            "updated_at",
        ),
    )
    org_data: list[dict] = json.loads(serialized_org)

    if games.exists():
        serialized_games: str = serialize(
            "json",
            games,
            fields=(
                "twitch_id",
                "slug",
                "name",
                "display_name",
                "box_art",
                "added_at",
                "updated_at",
            ),
        )
        games_data: list[dict] = json.loads(serialized_games)
        org_data[0]["fields"]["games"] = games_data

    context: dict[str, Any] = {
        "organization": organization,
        "games": games,
        "org_data": format_and_color_json(org_data[0]),
    }

    return render(request, "twitch/organization_detail.html", context)


# MARK: /campaigns/
def drop_campaign_list_view(request: HttpRequest) -> HttpResponse:
    """Function-based view for drop campaigns list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered campaign list page.
    """
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")
    per_page: int = 100
    queryset: QuerySet[DropCampaign] = DropCampaign.objects.all()

    if game_filter:
        queryset = queryset.filter(game__twitch_id=game_filter)

    queryset = queryset.prefetch_related("game__owners").order_by("-start_at")

    # Optionally filter by status (active, upcoming, expired)
    now = timezone.now()
    if status_filter == "active":
        queryset = queryset.filter(start_at__lte=now, end_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(start_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(end_at__lt=now)

    paginator = Paginator(queryset, per_page)
    page = request.GET.get("page") or 1
    try:
        campaigns = paginator.page(page)
    except PageNotAnInteger:
        campaigns = paginator.page(1)
    except EmptyPage:
        campaigns = paginator.page(paginator.num_pages)

    context: dict[str, Any] = {
        "campaigns": campaigns,
        "games": Game.objects.all().order_by("display_name"),
        "status_options": ["active", "upcoming", "expired"],
        "now": now,
        "selected_game": game_filter or "",
        "selected_per_page": per_page,
        "selected_status": status_filter or "",
    }
    return render(request, "twitch/campaign_list.html", context)


def format_and_color_json(data: dict[str, Any] | list[dict] | str) -> str:
    """Format and color a JSON string for HTML display.

    Args:
        data: Either a dictionary, list of dictionaries, or a JSON string to format.

    Returns:
        str: The formatted code with HTML styles.
    """
    if isinstance(data, (dict, list)):
        formatted_code: str = json.dumps(data, indent=4)
    else:
        formatted_code = data
    return highlight(formatted_code, JsonLexer(), HtmlFormatter())


def _enhance_drops_with_context(drops: QuerySet[TimeBasedDrop], now: datetime.datetime) -> list[dict[str, Any]]:
    """Helper to enhance drops with countdown and context.

    Args:
        drops: QuerySet of TimeBasedDrop objects.
        now: Current datetime.

    Returns:
        List of dicts with drop, local_start, local_end, timezone_name, and countdown_text.
    """
    enhanced: list[dict[str, Any]] = []
    for drop in drops:
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
        enhanced.append({
            "drop": drop,
            "local_start": drop.start_at,
            "local_end": drop.end_at,
            "timezone_name": "UTC",
            "countdown_text": countdown_text,
        })
    return enhanced


# MARK: /campaigns/<twitch_id>/
def drop_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # noqa: PLR0914
    """Function-based view for a drop campaign detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the campaign.

    Returns:
        HttpResponse: The rendered campaign detail page.

    Raises:
        Http404: If the campaign is not found.
    """
    try:
        campaign: DropCampaign = DropCampaign.objects.prefetch_related("game__owners").get(
            twitch_id=twitch_id,
        )
    except DropCampaign.DoesNotExist as exc:
        msg = "No campaign found matching the query"
        raise Http404(msg) from exc

    drops: QuerySet[TimeBasedDrop] = (
        TimeBasedDrop.objects
        .filter(campaign=campaign)
        .select_related("campaign")
        .prefetch_related("benefits")
        .order_by("required_minutes_watched")
    )

    serialized_campaign = serialize(
        "json",
        [campaign],
        fields=(
            "twitch_id",
            "name",
            "description",
            "details_url",
            "account_link_url",
            "image_url",
            "start_at",
            "end_at",
            "allow_is_enabled",
            "operation_name",
            "game",
            "created_at",
            "updated_at",
        ),
    )
    campaign_data = json.loads(serialized_campaign)

    if drops.exists():
        badge_benefit_names: set[str] = {
            benefit.name
            for drop in drops
            for benefit in drop.benefits.all()
            if benefit.distribution_type == "BADGE" and benefit.name
        }
        badge_descriptions_by_title: dict[str, str] = dict(
            ChatBadge.objects.filter(title__in=badge_benefit_names).values_list("title", "description"),
        )

        serialized_drops = serialize(
            "json",
            drops,
            fields=(
                "twitch_id",
                "name",
                "required_minutes_watched",
                "required_subs",
                "start_at",
                "end_at",
                "added_at",
                "updated_at",
            ),
        )
        drops_data: list[dict[str, Any]] = json.loads(serialized_drops)

        for i, drop in enumerate(drops):
            drop_benefits: list[DropBenefit] = list(drop.benefits.all())
            if drop_benefits:
                serialized_benefits = serialize(
                    "json",
                    drop_benefits,
                    fields=(
                        "twitch_id",
                        "name",
                        "image_asset_url",
                        "added_at",
                        "updated_at",
                        "created_at",
                        "entitlement_limit",
                        "is_ios_available",
                        "distribution_type",
                    ),
                )
                benefits_data = json.loads(serialized_benefits)

                for benefit_data in benefits_data:
                    fields: dict[str, Any] = benefit_data.get("fields", {})
                    if fields.get("distribution_type") != "BADGE":
                        continue

                    # DropBenefit doesn't have a description field; fetch it from ChatBadge when possible.
                    if fields.get("description"):
                        continue

                    badge_description: str | None = badge_descriptions_by_title.get(fields.get("name", ""))
                    if badge_description:
                        fields["description"] = badge_description

                drops_data[i]["fields"]["benefits"] = benefits_data

        campaign_data[0]["fields"]["drops"] = drops_data

    now: datetime.datetime = timezone.now()
    enhanced_drops: list[dict[str, Any]] = _enhance_drops_with_context(drops, now)
    # Attach awarded_badge to each drop in enhanced_drops
    for enhanced_drop in enhanced_drops:
        drop = enhanced_drop["drop"]
        awarded_badge = None
        for benefit in drop.benefits.all():
            if benefit.distribution_type == "BADGE":
                awarded_badge: ChatBadge | None = ChatBadge.objects.filter(title=benefit.name).first()
                break
        enhanced_drop["awarded_badge"] = awarded_badge

    context: dict[str, Any] = {
        "campaign": campaign,
        "now": now,
        "drops": enhanced_drops,
        "campaign_data": format_and_color_json(campaign_data[0]),
        "owners": list(campaign.game.owners.all()),
        "allowed_channels": campaign.allow_channels.all().order_by("display_name"),
    }

    return render(request, "twitch/campaign_detail.html", context)


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
        """Add additional context data.

        Games are grouped by their owning organization.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with games grouped by organization.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        now: datetime.datetime = timezone.now()

        games_with_campaigns: QuerySet[Game] = (
            Game.objects
            .filter(drop_campaigns__isnull=False)
            .prefetch_related("owners")
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

        games_by_org: defaultdict[Organization, list[dict[str, Game]]] = defaultdict(list)
        for game in games_with_campaigns:
            for org in game.owners.all():
                games_by_org[org].append({"game": game})

        context["games_by_org"] = OrderedDict(
            sorted(games_by_org.items(), key=lambda item: item[0].name),
        )

        return context


# MARK: /games/<twitch_id>/
class GameDetailView(DetailView):
    """Detail view for a game."""

    model = Game
    template_name = "twitch/game_detail.html"
    context_object_name = "game"
    lookup_field = "twitch_id"

    def get_object(self, queryset: QuerySet[Game] | None = None) -> Game:
        """Get the game object using twitch_id as the primary key lookup.

        Args:
            queryset: Optional queryset to use.

        Returns:
            Game: The game object.

        Raises:
            Http404: If the game is not found.
        """
        if queryset is None:
            queryset = self.get_queryset()

        # Use twitch_id as the lookup field since it's the primary key
        twitch_id = self.kwargs.get("twitch_id")
        try:
            game = queryset.get(twitch_id=twitch_id)
        except Game.DoesNotExist as exc:
            msg = "No game found matching the query"
            raise Http404(msg) from exc

        return game

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired
                campaigns. Expired campaigns are filtered based on
                either end date or status.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        game: Game = self.get_object()  # pyright: ignore[reportAssignmentType]

        now: datetime.datetime = timezone.now()
        # For each drop, find awarded badge (distribution_type BADGE)
        drop_awarded_badges: dict[str, ChatBadge] = {}
        drops: QuerySet[TimeBasedDrop, TimeBasedDrop] = TimeBasedDrop.objects.filter(
            campaign__game=game,
        ).prefetch_related("benefits")

        for drop in drops:
            for benefit in drop.benefits.all():
                if benefit.distribution_type == "BADGE":
                    # Find badge by title
                    badge: ChatBadge | None = ChatBadge.objects.filter(title=benefit.name).first()
                    if badge:
                        drop_awarded_badges[drop.twitch_id] = badge

        all_campaigns: QuerySet[DropCampaign] = (
            DropCampaign.objects
            .filter(game=game)
            .prefetch_related("game__owners")
            .prefetch_related(
                Prefetch(
                    "time_based_drops",
                    queryset=TimeBasedDrop.objects.prefetch_related(
                        Prefetch(
                            "benefits",
                            queryset=DropBenefit.objects.order_by("name"),
                        ),
                    ),
                ),
            )
            .order_by("-end_at")
        )

        active_campaigns: list[DropCampaign] = [
            campaign
            for campaign in all_campaigns
            if campaign.start_at is not None
            and campaign.start_at <= now
            and campaign.end_at is not None
            and campaign.end_at >= now
        ]
        active_campaigns.sort(
            key=lambda c: c.end_at if c.end_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC),
        )

        upcoming_campaigns: list[DropCampaign] = [
            campaign for campaign in all_campaigns if campaign.start_at is not None and campaign.start_at > now
        ]

        upcoming_campaigns.sort(
            key=lambda c: c.start_at if c.start_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC),
        )

        expired_campaigns: list[DropCampaign] = [
            campaign for campaign in all_campaigns if campaign.end_at is not None and campaign.end_at < now
        ]

        serialized_game: str = serialize(
            "json",
            [game],
            fields=(
                "twitch_id",
                "slug",
                "name",
                "display_name",
                "box_art",
                "owner",
                "added_at",
                "updated_at",
            ),
        )
        game_data: list[dict[str, Any]] = json.loads(serialized_game)

        if all_campaigns.exists():
            serialized_campaigns = serialize(
                "json",
                all_campaigns,
                fields=(
                    "twitch_id",
                    "name",
                    "description",
                    "details_url",
                    "account_link_url",
                    "image_url",
                    "start_at",
                    "end_at",
                    "allow_is_enabled",
                    "allow_channels",
                    "game",
                    "operation_name",
                    "added_at",
                    "updated_at",
                ),
            )
            campaigns_data: list[dict[str, Any]] = json.loads(
                serialized_campaigns,
            )
            game_data[0]["fields"]["campaigns"] = campaigns_data

        context.update(
            {
                "active_campaigns": active_campaigns,
                "upcoming_campaigns": upcoming_campaigns,
                "expired_campaigns": expired_campaigns,
                "owners": list(game.owners.all()),
                "drop_awarded_badges": drop_awarded_badges,
                "now": now,
                "game_data": format_and_color_json(game_data[0]),
            },
        )

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
        DropCampaign.objects
        .filter(start_at__lte=now, end_at__gte=now)
        .select_related("game")
        .prefetch_related("game__owners")
        .prefetch_related(
            "allow_channels",
        )
        .order_by("-start_at")
    )

    # Preserve insertion order (newest campaigns first). Group by game so games with multiple owners
    # don't render duplicate campaign cards.
    campaigns_by_game: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for campaign in active_campaigns:
        game: Game = campaign.game
        game_id: str = game.twitch_id

        if game_id not in campaigns_by_game:
            campaigns_by_game[game_id] = {
                "name": game.display_name,
                "box_art": game.box_art_best_url,
                "owners": list(game.owners.all()),
                "campaigns": [],
            }

        campaigns_by_game[game_id]["campaigns"].append(campaign)

    # Get active reward campaigns (Quest rewards)
    active_reward_campaigns: QuerySet[RewardCampaign] = (
        RewardCampaign.objects
        .filter(starts_at__lte=now, ends_at__gte=now)
        .select_related("game")
        .order_by("-starts_at")
    )

    return render(
        request,
        "twitch/dashboard.html",
        {
            "active_campaigns": active_campaigns,
            "campaigns_by_game": campaigns_by_game,
            "active_reward_campaigns": active_reward_campaigns,
            "now": now,
        },
    )


# MARK: /reward-campaigns/
def reward_campaign_list_view(request: HttpRequest) -> HttpResponse:
    """Function-based view for reward campaigns list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered reward campaigns list page.
    """
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")
    per_page: int = 100
    queryset: QuerySet[RewardCampaign] = RewardCampaign.objects.all()

    if game_filter:
        queryset = queryset.filter(game__twitch_id=game_filter)

    queryset = queryset.select_related("game").order_by("-starts_at")

    # Optionally filter by status (active, upcoming, expired)
    now = timezone.now()
    if status_filter == "active":
        queryset = queryset.filter(starts_at__lte=now, ends_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(starts_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(ends_at__lt=now)

    paginator = Paginator(queryset, per_page)
    page = request.GET.get("page") or 1
    try:
        reward_campaigns = paginator.page(page)
    except PageNotAnInteger:
        reward_campaigns = paginator.page(1)
    except EmptyPage:
        reward_campaigns = paginator.page(paginator.num_pages)

    context: dict[str, Any] = {
        "reward_campaigns": reward_campaigns,
        "games": Game.objects.all().order_by("display_name"),
        "status_options": ["active", "upcoming", "expired"],
        "now": now,
        "selected_game": game_filter or "",
        "selected_per_page": per_page,
        "selected_status": status_filter or "",
    }
    return render(request, "twitch/reward_campaign_list.html", context)


# MARK: /reward-campaigns/<twitch_id>/
def reward_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Function-based view for a reward campaign detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the reward campaign.

    Returns:
        HttpResponse: The rendered reward campaign detail page.

    Raises:
        Http404: If the reward campaign is not found.
    """
    try:
        reward_campaign: RewardCampaign = RewardCampaign.objects.select_related("game").get(
            twitch_id=twitch_id,
        )
    except RewardCampaign.DoesNotExist as exc:
        msg = "No reward campaign found matching the query"
        raise Http404(msg) from exc

    serialized_campaign = serialize(
        "json",
        [reward_campaign],
        fields=(
            "twitch_id",
            "name",
            "brand",
            "summary",
            "instructions",
            "external_url",
            "about_url",
            "reward_value_url_param",
            "starts_at",
            "ends_at",
            "is_sitewide",
            "game",
            "added_at",
            "updated_at",
        ),
    )
    campaign_data: list[dict[str, Any]] = json.loads(serialized_campaign)

    now: datetime.datetime = timezone.now()

    context: dict[str, Any] = {
        "reward_campaign": reward_campaign,
        "now": now,
        "campaign_data": format_and_color_json(campaign_data[0]),
        "is_active": reward_campaign.is_active,
    }

    return render(request, "twitch/reward_campaign_detail.html", context)


# MARK: /debug/
def debug_view(request: HttpRequest) -> HttpResponse:
    """Debug view showing potentially broken or inconsistent data.

    Returns:
        HttpResponse: Rendered debug template or redirect if unauthorized.
    """
    now: datetime.datetime = timezone.now()

    # Games with no assigned owner organization
    games_without_owner: QuerySet[Game] = Game.objects.filter(
        owners__isnull=True,
    ).order_by("display_name")

    # Campaigns with missing or obviously broken images
    broken_image_campaigns: QuerySet[DropCampaign] = DropCampaign.objects.filter(
        Q(image_url__isnull=True) | Q(image_url__exact="") | ~Q(image_url__startswith="http"),
    ).select_related("game")

    # Benefits with missing images
    broken_benefit_images: QuerySet[DropBenefit] = DropBenefit.objects.annotate(
        trimmed_url=Trim("image_asset_url"),
    ).filter(
        Q(image_asset_url__isnull=True) | Q(trimmed_url__exact="") | ~Q(image_asset_url__startswith="http"),
    )

    # Time-based drops without any benefits
    drops_without_benefits: QuerySet[TimeBasedDrop] = TimeBasedDrop.objects.filter(
        benefits__isnull=True,
    ).select_related(
        "campaign__game",
    )

    # Campaigns with invalid dates (start after end or missing either)
    invalid_date_campaigns: QuerySet[DropCampaign] = DropCampaign.objects.filter(
        Q(start_at__gt=F("end_at")) | Q(start_at__isnull=True) | Q(end_at__isnull=True),
    ).select_related("game")

    # Duplicate campaign names per game.
    # We retrieve the game's name for user-friendly display.
    duplicate_name_campaigns = (
        DropCampaign.objects
        .values("game__display_name", "name", "game__twitch_id")
        .annotate(name_count=Count("twitch_id"))
        .filter(name_count__gt=1)
        .order_by("game__display_name", "name")
    )

    # Campaigns currently active but image missing
    active_missing_image: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(start_at__lte=now, end_at__gte=now)
        .filter(
            Q(image_url__isnull=True) | Q(image_url__exact="") | ~Q(image_url__startswith="http"),
        )
        .select_related("game")
    )

    # Distinct GraphQL operation names used to fetch campaigns with counts
    operation_names_with_counts: list[dict[str, Any]] = list(
        DropCampaign.objects
        .annotate(trimmed_op=Trim("operation_name"))
        .filter(~Q(trimmed_op__exact=""))
        .values("trimmed_op")
        .annotate(count=Count("twitch_id"))
        .order_by("trimmed_op"),
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
        "operation_names_with_counts": operation_names_with_counts,
    }

    return render(
        request,
        "twitch/debug.html",
        context,
    )


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
            "title": "All Organizations",
            "description": "Latest organizations added to TTVDrops",
            "url": "/rss/organizations/",
        },
        {
            "title": "All Games",
            "description": "Latest games added to TTVDrops",
            "url": "/rss/games/",
        },
        {
            "title": "All Drop Campaigns",
            "description": "Latest drop campaigns across all games",
            "url": "/rss/campaigns/",
        },
        {
            "title": "All Reward Campaigns",
            "description": "Latest reward campaigns (Quest rewards) on Twitch",
            "url": "/rss/reward-campaigns/",
        },
    ]

    # Get sample game and organization for examples
    sample_game = Game.objects.first()
    sample_org = Organization.objects.first()

    return render(
        request,
        "twitch/docs_rss.html",
        {
            "feeds": feeds,
            "sample_game": sample_game,
            "sample_org": sample_org,
        },
    )


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

        # Count directly from the through table for maximum efficiency
        # This avoids unnecessary JOINs and GROUP BY operations
        campaign_count_subquery = (
            DropCampaign.allow_channels.through.objects
            .filter(channel_id=OuterRef("pk"))
            .values("channel_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        return queryset.annotate(campaign_count=Subquery(campaign_count_subquery)).order_by("-campaign_count", "name")

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


# MARK: /channels/<twitch_id>/
class ChannelDetailView(DetailView):
    """Detail view for a channel."""

    model = Channel
    template_name = "twitch/channel_detail.html"
    context_object_name = "channel"
    lookup_field = "twitch_id"

    def get_object(self, queryset: QuerySet[Channel] | None = None) -> Channel:
        """Get the channel object using twitch_id as the primary key lookup.

        Args:
            queryset: Optional queryset to use.

        Returns:
            Channel: The channel object.

        Raises:
            Http404: If the channel is not found.
        """
        if queryset is None:
            queryset = self.get_queryset()

        twitch_id = self.kwargs.get("twitch_id")
        try:
            channel = queryset.get(twitch_id=twitch_id)
        except Channel.DoesNotExist as exc:
            msg = "No channel found matching the query"
            raise Http404(msg) from exc

        return channel

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired campaigns.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        channel: Channel = self.get_object()  # pyright: ignore[reportAssignmentType]

        now: datetime.datetime = timezone.now()
        all_campaigns: QuerySet[DropCampaign] = (
            DropCampaign.objects
            .filter(allow_channels=channel)
            .prefetch_related("game__owners")
            .prefetch_related(
                Prefetch(
                    "time_based_drops",
                    queryset=TimeBasedDrop.objects.prefetch_related(
                        Prefetch(
                            "benefits",
                            queryset=DropBenefit.objects.order_by("name"),
                        ),
                    ),
                ),
            )
            .order_by("-start_at")
        )

        active_campaigns: list[DropCampaign] = [
            campaign
            for campaign in all_campaigns
            if campaign.start_at is not None
            and campaign.start_at <= now
            and campaign.end_at is not None
            and campaign.end_at >= now
        ]
        active_campaigns.sort(
            key=lambda c: c.end_at if c.end_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC),
        )

        upcoming_campaigns: list[DropCampaign] = [
            campaign for campaign in all_campaigns if campaign.start_at is not None and campaign.start_at > now
        ]
        upcoming_campaigns.sort(
            key=lambda c: c.start_at if c.start_at is not None else datetime.datetime.max.replace(tzinfo=datetime.UTC),
        )

        expired_campaigns: list[DropCampaign] = [
            campaign for campaign in all_campaigns if campaign.end_at is not None and campaign.end_at < now
        ]

        serialized_channel = serialize(
            "json",
            [channel],
            fields=(
                "twitch_id",
                "name",
                "display_name",
                "added_at",
                "updated_at",
            ),
        )
        channel_data = json.loads(serialized_channel)

        if all_campaigns.exists():
            serialized_campaigns = serialize(
                "json",
                all_campaigns,
                fields=(
                    "twitch_id",
                    "name",
                    "description",
                    "details_url",
                    "account_link_url",
                    "image_url",
                    "start_at",
                    "end_at",
                    "added_at",
                    "updated_at",
                ),
            )
            campaigns_data = json.loads(serialized_campaigns)
            channel_data[0]["fields"]["campaigns"] = campaigns_data

        context.update(
            {
                "active_campaigns": active_campaigns,
                "upcoming_campaigns": upcoming_campaigns,
                "expired_campaigns": expired_campaigns,
                "now": now,
                "channel_data": format_and_color_json(channel_data[0]),
            },
        )

        return context


# MARK: /badges/
def badge_list_view(request: HttpRequest) -> HttpResponse:
    """List view for chat badge sets.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered badge list page.
    """
    badge_sets: QuerySet[ChatBadgeSet] = (
        ChatBadgeSet.objects
        .all()
        .prefetch_related(
            Prefetch(
                "badges",
                queryset=ChatBadge.objects.order_by("badge_id"),
            ),
        )
        .order_by("set_id")
    )

    # Group badges by set for easier display
    badge_data: list[dict[str, Any]] = [
        {
            "set": badge_set,
            "badges": list(badge_set.badges.all()),  # pyright: ignore[reportAttributeAccessIssue]
        }
        for badge_set in badge_sets
    ]

    context: dict[str, Any] = {
        "badge_sets": badge_sets,
        "badge_data": badge_data,
    }

    return render(request, "twitch/badge_list.html", context)


# MARK: /badges/<set_id>/
def badge_set_detail_view(request: HttpRequest, set_id: str) -> HttpResponse:
    """Detail view for a specific badge set.

    Args:
        request: The HTTP request.
        set_id: The ID of the badge set.

    Returns:
        HttpResponse: The rendered badge set detail page.

    Raises:
        Http404: If the badge set is not found.
    """
    try:
        badge_set: ChatBadgeSet = ChatBadgeSet.objects.prefetch_related(
            Prefetch(
                "badges",
                queryset=ChatBadge.objects.order_by("badge_id"),
            ),
        ).get(set_id=set_id)
    except ChatBadgeSet.DoesNotExist as exc:
        msg = "No badge set found matching the query"
        raise Http404(msg) from exc

    badges: QuerySet[ChatBadge] = badge_set.badges.all()  # pyright: ignore[reportAttributeAccessIssue]

    # Attach award_campaigns attribute to each badge for template use
    for badge in badges:
        benefits: QuerySet[DropBenefit, DropBenefit] = DropBenefit.objects.filter(
            distribution_type="BADGE",
            name=badge.title,
        )
        campaigns: QuerySet[DropCampaign, DropCampaign] = DropCampaign.objects.filter(
            time_based_drops__benefits__in=benefits,
        ).distinct()
        badge.award_campaigns = list(campaigns)  # pyright: ignore[reportAttributeAccessIssue]

    # Serialize for JSON display
    serialized_set = serialize(
        "json",
        [badge_set],
        fields=(
            "set_id",
            "added_at",
            "updated_at",
        ),
    )
    set_data: list[dict[str, Any]] = json.loads(serialized_set)

    if badges.exists():
        serialized_badges = serialize(
            "json",
            badges,
            fields=(
                "badge_id",
                "image_url_1x",
                "image_url_2x",
                "image_url_4x",
                "title",
                "description",
                "click_action",
                "click_url",
                "added_at",
                "updated_at",
            ),
        )
        badges_data: list[dict[str, Any]] = json.loads(serialized_badges)
        set_data[0]["fields"]["badges"] = badges_data

    context: dict[str, Any] = {
        "badge_set": badge_set,
        "badges": badges,
        "set_data": format_and_color_json(set_data[0]),
    }

    return render(request, "twitch/badge_set_detail.html", context)
