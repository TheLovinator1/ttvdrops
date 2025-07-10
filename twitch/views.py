from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import DetailView, ListView

from twitch.models import DropCampaign, Game, TimeBasedDrop

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse


class DropCampaignListView(ListView):
    """List view for drop campaigns."""

    model = DropCampaign
    template_name = "twitch/campaign_list.html"
    context_object_name = "campaigns"

    def get_queryset(self) -> QuerySet[DropCampaign]:
        """Get queryset of drop campaigns.

        Returns:
            QuerySet: Filtered drop campaigns.
        """
        queryset = super().get_queryset()
        status_filter = self.request.GET.get("status")
        game_filter = self.request.GET.get("game")

        # Filter by status if provided
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter by game if provided
        if game_filter:
            queryset = queryset.filter(game__id=game_filter)

        return queryset.select_related("game", "owner").order_by("-start_at")

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context = super().get_context_data(**kwargs)

        # Add games for filtering
        context["games"] = Game.objects.all()

        # Add status options for filtering
        context["status_options"] = [status[0] for status in DropCampaign.STATUS_CHOICES]

        # Add selected filters
        context["selected_status"] = self.request.GET.get("status", "")
        context["selected_game"] = self.request.GET.get("game", "")

        # Current time for active campaign highlighting
        context["now"] = timezone.now()

        return context


class DropCampaignDetailView(DetailView):
    """Detail view for a drop campaign."""

    model = DropCampaign
    template_name = "twitch/campaign_detail.html"
    context_object_name = "campaign"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context = super().get_context_data(**kwargs)

        # Add drops for this campaign with benefits preloaded
        context["drops"] = (
            TimeBasedDrop.objects.filter(campaign=self.get_object())
            .select_related("campaign")
            .prefetch_related("benefits")
            .order_by("required_minutes_watched")
        )

        # Current time for active campaign highlighting
        context["now"] = timezone.now()

        return context


class GameListView(ListView):
    """List view for games."""

    model = Game
    template_name = "twitch/game_list.html"
    context_object_name = "games"

    def get_queryset(self) -> QuerySet[Game]:
        """Get queryset of games, annotated with campaign counts to avoid N+1 queries."""
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

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data."""
        context = super().get_context_data(**kwargs)
        # Use annotated counts directly, no extra queries
        context["games_with_counts"] = [
            {
                "game": game,
                "campaign_count": getattr(game, "campaign_count", 0),
                "active_count": getattr(game, "active_count", 0),
            }
            for game in context["games"]
        ]
        return context


class GameDetailView(DetailView):
    """Detail view for a game."""

    model = Game
    template_name = "twitch/game_detail.html"
    context_object_name = "game"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context = super().get_context_data(**kwargs)
        game = self.get_object()

        # Get all campaigns for this game
        now = timezone.now()

        # Active campaigns
        active_campaigns = (
            DropCampaign.objects.filter(
                game=game,
                start_at__lte=now,
                end_at__gte=now,
                status="ACTIVE",
            )
            .select_related("owner")
            .order_by("end_at")
        )

        # Upcoming campaigns
        upcoming_campaigns = (
            DropCampaign.objects.filter(game=game, start_at__gt=now, status="UPCOMING").select_related("owner").order_by("start_at")
        )

        # Past campaigns (show all campaigns for this game)
        expired_campaigns = DropCampaign.objects.filter(game=game).select_related("owner").order_by("-end_at")

        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
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
    # Get active campaigns
    now = timezone.now()
    active_campaigns = DropCampaign.objects.filter(start_at__lte=now, end_at__gte=now, status="ACTIVE").select_related("game", "owner")

    return render(
        request,
        "twitch/dashboard.html",
        {
            "active_campaigns": active_campaigns,
            "now": now,
        },
    )
