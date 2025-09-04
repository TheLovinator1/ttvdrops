from __future__ import annotations

from django.urls import path

from twitch import views
from twitch.feeds import (
    DropCampaignFeed,
    GameFeed,
    OrganizationFeed,
)

app_name = "twitch"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("debug/", views.debug_view, name="debug"),
    path("campaigns/", views.DropCampaignListView.as_view(), name="campaign_list"),
    path("campaigns/<str:pk>/", views.DropCampaignDetailView.as_view(), name="campaign_detail"),
    path("games/", views.GamesGridView.as_view(), name="game_list"),
    path("games/list/", views.GamesListView.as_view(), name="game_list_simple"),
    path("games/<str:pk>/", views.GameDetailView.as_view(), name="game_detail"),
    path("games/<str:game_id>/subscribe/", views.subscribe_game_notifications, name="subscribe_notifications"),
    path("organizations/", views.OrgListView.as_view(), name="org_list"),
    path("organizations/<str:pk>/", views.OrgDetailView.as_view(), name="organization_detail"),
    path("organizations/<str:org_id>/subscribe/", views.subscribe_org_notifications, name="subscribe_org_notifications"),
    path("rss/organizations/", OrganizationFeed(), name="organization_feed"),
    path("rss/games/", GameFeed(), name="game_feed"),
    path("rss/campaigns/", DropCampaignFeed(), name="campaign_feed"),
    path("docs/rss/", views.docs_rss_view, name="docs_rss"),
]
