from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import path

from twitch import views
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameFeed
from twitch.feeds import OrganizationCampaignFeed
from twitch.feeds import OrganizationFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern

app_name = "twitch"

urlpatterns: list[URLPattern] = [
    path("", views.dashboard, name="dashboard"),
    path("campaigns/", views.drop_campaign_list_view, name="campaign_list"),
    path("campaigns/<str:twitch_id>/", views.drop_campaign_detail_view, name="campaign_detail"),
    path("channels/", views.ChannelListView.as_view(), name="channel_list"),
    path("channels/<str:twitch_id>/", views.ChannelDetailView.as_view(), name="channel_detail"),
    path("debug/", views.debug_view, name="debug"),
    path("docs/rss/", views.docs_rss_view, name="docs_rss"),
    path("emotes/", views.emote_gallery_view, name="emote_gallery"),
    path("games/", views.GamesGridView.as_view(), name="game_list"),
    path("games/<str:twitch_id>/", views.GameDetailView.as_view(), name="game_detail"),
    path("games/list/", views.GamesListView.as_view(), name="game_list_simple"),
    path("organizations/", views.org_list_view, name="org_list"),
    path("organizations/<str:twitch_id>/", views.organization_detail_view, name="organization_detail"),
    path("rss/campaigns/", DropCampaignFeed(), name="campaign_feed"),
    path("rss/games/", GameFeed(), name="game_feed"),
    path("rss/games/<str:twitch_id>/campaigns/", GameCampaignFeed(), name="game_campaign_feed"),
    path("rss/organizations/", OrganizationFeed(), name="organization_feed"),
    path("rss/organizations/<str:twitch_id>/campaigns/", OrganizationCampaignFeed(), name="organization_campaign_feed"),
    path("search/", views.search_view, name="search"),
]
