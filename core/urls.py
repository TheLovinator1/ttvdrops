from typing import TYPE_CHECKING

from django.urls import path

from core import views
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

app_name = "core"


urlpatterns: list[URLPattern | URLResolver] = [
    # /
    path("", views.dashboard, name="dashboard"),
    # /search/
    path("search/", views.search_view, name="search"),
    # /debug/
    path("debug/", views.debug_view, name="debug"),
    # /datasets/
    path("datasets/", views.dataset_backups_view, name="dataset_backups"),
    # /datasets/download/<relative_path>/
    path(
        "datasets/download/<path:relative_path>/",
        views.dataset_backup_download_view,
        name="dataset_backup_download",
    ),
    # /docs/rss/
    path("docs/rss/", views.docs_rss_view, name="docs_rss"),
    # RSS feeds
    # /rss/campaigns/ - all active campaigns
    path("rss/campaigns/", DropCampaignFeed(), name="campaign_feed"),
    # /rss/games/ - newly added games
    path("rss/games/", GameFeed(), name="game_feed"),
    # /rss/games/<twitch_id>/campaigns/ - active campaigns for a specific game
    path(
        "rss/games/<str:twitch_id>/campaigns/",
        views.GameCampaignFeed(),
        name="game_campaign_feed",
    ),
    # /rss/organizations/ - newly added organizations
    path(
        "rss/organizations/",
        views.OrganizationRSSFeed(),
        name="organization_feed",
    ),
    # /rss/reward-campaigns/ - all active reward campaigns
    path(
        "rss/reward-campaigns/",
        views.RewardCampaignFeed(),
        name="reward_campaign_feed",
    ),
    # Atom feeds (added alongside RSS to preserve backward compatibility)
    path("atom/campaigns/", views.DropCampaignAtomFeed(), name="campaign_feed_atom"),
    path("atom/games/", views.GameAtomFeed(), name="game_feed_atom"),
    path(
        "atom/games/<str:twitch_id>/campaigns/",
        views.GameCampaignAtomFeed(),
        name="game_campaign_feed_atom",
    ),
    path(
        "atom/organizations/",
        views.OrganizationAtomFeed(),
        name="organization_feed_atom",
    ),
    path(
        "atom/reward-campaigns/",
        views.RewardCampaignAtomFeed(),
        name="reward_campaign_feed_atom",
    ),
    # Discord feeds (Atom feeds with Discord relative timestamps)
    path(
        "discord/campaigns/",
        views.DropCampaignDiscordFeed(),
        name="campaign_feed_discord",
    ),
    path("discord/games/", views.GameDiscordFeed(), name="game_feed_discord"),
    path(
        "discord/games/<str:twitch_id>/campaigns/",
        views.GameCampaignDiscordFeed(),
        name="game_campaign_feed_discord",
    ),
    path(
        "discord/organizations/",
        views.OrganizationDiscordFeed(),
        name="organization_feed_discord",
    ),
    path(
        "discord/reward-campaigns/",
        views.RewardCampaignDiscordFeed(),
        name="reward_campaign_feed_discord",
    ),
]
