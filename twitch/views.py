from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db.models import Count, Prefetch, Q
from django.db.models.query import QuerySet
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import DetailView, ListView

from twitch.models import DropCampaign, Game, Organization, TimeBasedDrop

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse


class DropCampaignListView(ListView):
    """List view for drop campaigns."""

    model = DropCampaign
    template_name = "twitch/campaign_list.html"
    context_object_name = "campaigns"
    paginate_by = 24  # 24 campaigns per page (6x4 grid on larger screens)

    def get_paginate_by(self, queryset) -> int:
        """Get the pagination size, allowing override via URL parameter.

        Args:
            queryset: The queryset being paginated.

        Returns:
            int: Number of items per page.
        """
        per_page: str | None = self.request.GET.get("per_page")
        if per_page and per_page.isdigit():
            per_page_int = int(per_page)
            # Limit to reasonable values to prevent performance issues
            if per_page_int in {12, 24, 48, 96}:
                return per_page_int
        return self.paginate_by

    def get_queryset(self) -> QuerySet[DropCampaign]:
        """Get queryset of drop campaigns.

        Returns:
            QuerySet: Filtered drop campaigns.
        """
        queryset: QuerySet[DropCampaign] = super().get_queryset()
        status_filter: str | None = self.request.GET.get("status")
        game_filter: str | None = self.request.GET.get("game")

        # Apply filters
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        if game_filter:
            queryset = queryset.filter(game__id=game_filter)

        # Prefetch related objects to reduce queries
        return queryset.select_related("game", "owner").order_by("-start_at")

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context = super().get_context_data(**kwargs)

        # Load all games in a single query instead of multiple queries per game
        context["games"] = Game.objects.all().order_by("display_name")

        # Add status options for filtering
        context["status_options"] = [status[0] for status in DropCampaign.STATUS_CHOICES]

        # Add selected filters
        context["selected_status"] = self.request.GET.get("status", "")
        context["selected_game"] = self.request.GET.get("game", "")

        # Add per_page options and current selection
        context["per_page_options"] = [12, 24, 48, 96]
        per_page: str = self.request.GET.get("per_page", str(self.paginate_by))
        if per_page.isdigit():
            per_page_int = int(per_page)
            if per_page_int in {12, 24, 48, 96}:
                context["selected_per_page"] = per_page_int
            else:
                context["selected_per_page"] = self.paginate_by
        else:
            context["selected_per_page"] = self.paginate_by

        # Current time for active campaign highlighting
        context["now"] = timezone.now()

        return context


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
        # Prefetch all needed related objects in a single query
        if queryset is None:
            queryset = self.get_queryset()

        queryset = queryset.select_related("game", "owner")

        # We don't need to prefetch time_based_drops here since we're fetching them separately in get_context_data
        # with proper ordering and prefetching of benefits

        return super().get_object(queryset=queryset)  # type: ignore[return-value]

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context = super().get_context_data(**kwargs)
        campaign = context["campaign"]  # Get the campaign from context

        # Add drops for this campaign with benefits preloaded in a single efficient query
        context["drops"] = (
            TimeBasedDrop.objects.filter(campaign=campaign)
            .select_related("campaign")
            .prefetch_related("benefits")
            .order_by("required_minutes_watched")
        )

        # Current time for active campaign highlighting
        context["now"] = timezone.now()

        return context


class GameListView(ListView):
    """List view for games grouped by organization."""

    model = Game
    template_name = "twitch/game_list.html"
    context_object_name = "games"

    def get_queryset(self) -> QuerySet[Game]:
        """Get queryset of games, annotated with campaign counts to avoid N+1 queries.

        Returns:
            QuerySet[Game]: Queryset of games with annotations.
        """
        now = timezone.now()
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
                        drop_campaigns__status="ACTIVE",
                    ),
                    distinct=True,
                ),
            )
            .order_by("display_name")
        )

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add additional context data with games grouped by organization.

        Args:
            **kwargs: Additional keyword arguments.

        Returns:
            dict: Context data with games grouped by organization.
        """
        context = super().get_context_data(**kwargs)

        # Create a dictionary to hold games by organization
        games_by_org = {}
        now = timezone.now()

        # Step 1: Get all organizations with games in a single query
        # We'll prefetch the games related to each organization through drop_campaigns
        organizations_with_games = Organization.objects.filter(drop_campaigns__isnull=False).distinct().order_by("name")

        # Step 2: Get all game-organization relationships in a single efficient query
        # This query gets all games with their campaign counts and organization info
        game_org_relations = DropCampaign.objects.values("game_id", "owner_id", "owner__name").annotate(
            campaign_count=Count("id", distinct=True),
            active_count=Count("id", filter=Q(start_at__lte=now, end_at__gte=now, status="ACTIVE"), distinct=True),
        )

        # Step 3: Get all games in a single query with their display names
        all_games = {game.id: game for game in Game.objects.all()}

        # Step 4: Create a mapping of organization_id to organization_name
        org_names = {org.id: org.name for org in organizations_with_games}

        # Step 5: Group games by organization
        game_org_map = {}
        for relation in game_org_relations:
            org_id = relation["owner_id"]
            game_id = relation["game_id"]

            if org_id not in game_org_map:
                game_org_map[org_id] = {}

            if game_id not in game_org_map[org_id]:
                game = all_games.get(game_id)
                if game:
                    game_org_map[org_id][game_id] = {
                        "game": game,
                        "campaign_count": relation["campaign_count"],
                        "active_count": relation["active_count"],
                    }

        # Step 6: Convert the nested dictionary to the format expected by the template
        for org_id, games in game_org_map.items():
            if org_id in org_names:
                # Create an Organization-like object with id and name
                org_obj = type("Organization", (), {"id": org_id, "name": org_names[org_id]})
                games_by_org[org_obj] = list(games.values())

        # Create the flattened games_with_counts for backward compatibility
        games_with_counts = []
        for org_games in games_by_org.values():
            games_with_counts.extend(org_games)

        # Add to context
        context["games_with_counts"] = games_with_counts  # Keep the original list for backward compatibility
        context["games_by_org"] = games_by_org  # Add the new organized structure

        return context


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
        context = super().get_context_data(**kwargs)
        game = self.get_object()

        # Get all campaigns for this game in a single query with prefetching
        now = timezone.now()
        all_campaigns = DropCampaign.objects.filter(game=game).select_related("owner").order_by("-end_at")

        # Filter the campaigns in Python instead of making multiple queries
        active_campaigns = [
            campaign for campaign in all_campaigns if campaign.start_at <= now and campaign.end_at >= now and campaign.status == "ACTIVE"
        ]
        active_campaigns.sort(key=lambda c: c.end_at)  # Sort by end_at ascending

        upcoming_campaigns = [campaign for campaign in all_campaigns if campaign.start_at > now and campaign.status == "UPCOMING"]
        upcoming_campaigns.sort(key=lambda c: c.start_at)  # Sort by start_at ascending

        # Filter for expired campaigns
        expired_campaigns = [campaign for campaign in all_campaigns if campaign.end_at < now or campaign.status == "EXPIRED"]

        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,  # Only include expired campaigns
            "now": now,
        })

        return context


def dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard view showing active campaigns and progress.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered dashboard template.
    """
    # Get active campaigns with prefetching to reduce queries
    now = timezone.now()
    active_campaigns = (
        DropCampaign.objects.filter(start_at__lte=now, end_at__gte=now, status="ACTIVE")
        .select_related("game", "owner")
        # Prefetch the time-based drops with their benefits to avoid N+1 queries
        .prefetch_related(Prefetch("time_based_drops", queryset=TimeBasedDrop.objects.prefetch_related("benefits")))
    )

    return render(
        request,
        "twitch/dashboard.html",
        {
            "active_campaigns": active_campaigns,
            "now": now,
        },
    )
