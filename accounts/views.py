from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import CreateView

from accounts.forms import CustomUserCreationForm
from accounts.models import User
from twitch.models import Game, NotificationSubscription

if TYPE_CHECKING:
    from django.forms import BaseModelForm
    from django.http import HttpRequest, HttpResponse


class CustomLoginView(LoginView):
    """Custom login view with better styling."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self) -> str:
        """Redirect to the dashboard after successful login.

        Returns:
            str: URL to redirect to after successful login.
        """
        return reverse_lazy("twitch:dashboard")


class CustomLogoutView(LogoutView):
    """Custom logout view."""

    next_page = reverse_lazy("twitch:dashboard")
    http_method_names = ["get", "post", "options"]

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Allow GET requests for logout.

        Args:
            request: The HTTP request object.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: Response after logout.
        """
        return self.post(request, *args, **kwargs)


class SignUpView(CreateView):
    """User registration view."""

    model = User
    form_class = CustomUserCreationForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("twitch:dashboard")

    def form_valid(self, form: BaseModelForm) -> HttpResponse:
        """Login the user after successful registration.

        Args:
            form: The validated user creation form.

        Returns:
            HttpResponse: Response after successful form processing.
        """
        response = super().form_valid(form)
        login(self.request, self.object)  # type: ignore[attr-defined]
        return response


@login_required
def profile_view(request: HttpRequest) -> HttpResponse:
    """User profile view.

    Args:
        request: The HTTP request object.

    Returns:
        HttpResponse: Rendered profile template.
    """
    subscriptions = NotificationSubscription.objects.filter(user=request.user)  # type: ignore[misc]

    # Direct game subscriptions
    direct_game_subscriptions = subscriptions.filter(game_id__isnull=False)

    # Organization subscriptions
    org_subscriptions = subscriptions.filter(organization_id__isnull=False)

    # Get games from organization subscriptions (inherited games)
    inherited_games = Game.objects.filter(owner__in=org_subscriptions.values("organization"))

    # Create mapping of inherited game IDs to their organization names
    inherited_game_org_map = {}
    for game in inherited_games:
        if game.owner:
            inherited_game_org_map[game.id] = game.owner.name

    # Combine direct and inherited game IDs
    direct_game_ids = direct_game_subscriptions.values_list("game_id", flat=True)
    inherited_game_ids = inherited_games.values_list("id", flat=True)
    all_game_ids = set(direct_game_ids) | set(inherited_game_ids)

    # Get all games (direct + inherited)
    game_subscriptions_combined = Game.objects.filter(id__in=all_game_ids)

    # Get direct game IDs for template indication
    direct_game_ids = list(direct_game_subscriptions.values_list("game_id", flat=True))

    # Get all games (direct + inherited) with inheritance info
    games_with_inheritance = []
    for game in game_subscriptions_combined:
        is_inherited = game.id not in direct_game_ids
        inherited_from = inherited_game_org_map.get(game.id) if is_inherited else None
        games_with_inheritance.append({
            "game": game,
            "is_inherited": is_inherited,
            "inherited_from": inherited_from,
        })

    return render(
        request,
        "accounts/profile.html",
        {
            "user": request.user,
            "games_with_inheritance": games_with_inheritance,
            "org_subscriptions": org_subscriptions,
        },
    )
