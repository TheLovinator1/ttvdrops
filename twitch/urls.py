from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import path

from twitch import views
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameFeed
from twitch.feeds import OrganizationCampaignFeed
from twitch.feeds import OrganizationFeed
from twitch.feeds import RewardCampaignFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern

app_name = "twitch"

# We have /rss/ that is always the latest, and versioned version to not break users regexes.


rss_feeds_latest: list[URLPattern] = [
    path("rss/campaigns/", DropCampaignFeed(), name="campaign_feed"),
    path("rss/games/", GameFeed(), name="game_feed"),
    path("rss/games/<str:twitch_id>/campaigns/", GameCampaignFeed(), name="game_campaign_feed"),
    path("rss/organizations/", OrganizationFeed(), name="organization_feed"),
    path("rss/organizations/<str:twitch_id>/campaigns/", OrganizationCampaignFeed(), name="organization_campaign_feed"),
    path("rss/reward-campaigns/", RewardCampaignFeed(), name="reward_campaign_feed"),
]

v1_rss_feeds: list[URLPattern] = [
    path("rss/v1/campaigns/", DropCampaignFeed(), name="campaign_feed_v1"),
    path("rss/v1/games/", GameFeed(), name="game_feed_v1"),
    path("rss/v1/games/<str:twitch_id>/campaigns/", GameCampaignFeed(), name="game_campaign_feed_v1"),
    path("rss/v1/organizations/", OrganizationFeed(), name="organization_feed_v1"),
    path(
        "rss/v1/organizations/<str:twitch_id>/campaigns/",
        OrganizationCampaignFeed(),
        name="organization_campaign_feed_v1",
    ),
    path("rss/v1/reward-campaigns/", RewardCampaignFeed(), name="reward_campaign_feed_v1"),
]


urlpatterns: list[URLPattern] = [
    path("", views.dashboard, name="dashboard"),
    path("badges/", views.badge_list_view, name="badge_list"),
    path("badges/<str:set_id>/", views.badge_set_detail_view, name="badge_set_detail"),
    path("campaigns/", views.drop_campaign_list_view, name="campaign_list"),
    path("campaigns/<str:twitch_id>/", views.drop_campaign_detail_view, name="campaign_detail"),
    path("channels/", views.ChannelListView.as_view(), name="channel_list"),
    path("channels/<str:twitch_id>/", views.ChannelDetailView.as_view(), name="channel_detail"),
    path("debug/", views.debug_view, name="debug"),
    path("docs/rss/", views.docs_rss_view, name="docs_rss"),
    path("emotes/", views.emote_gallery_view, name="emote_gallery"),
    path("games/", views.GamesGridView.as_view(), name="game_list"),
    path("games/list/", views.GamesListView.as_view(), name="game_list_simple"),
    path("games/<str:twitch_id>/", views.GameDetailView.as_view(), name="game_detail"),
    path("organizations/", views.org_list_view, name="org_list"),
    path("organizations/<str:twitch_id>/", views.organization_detail_view, name="organization_detail"),
    path("reward-campaigns/", views.reward_campaign_list_view, name="reward_campaign_list"),
    path("reward-campaigns/<str:twitch_id>/", views.reward_campaign_detail_view, name="reward_campaign_detail"),
    path("search/", views.search_view, name="search"),
    *rss_feeds_latest,
    *v1_rss_feeds,
]
