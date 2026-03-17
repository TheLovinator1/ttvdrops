from typing import TYPE_CHECKING

from django.urls import path

from core.views import dashboard
from core.views import dataset_backup_download_view
from core.views import dataset_backups_view
from core.views import debug_view
from core.views import docs_rss_view
from core.views import search_view
from twitch.feeds import DropCampaignAtomFeed
from twitch.feeds import DropCampaignDiscordFeed
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameAtomFeed
from twitch.feeds import GameCampaignAtomFeed
from twitch.feeds import GameCampaignDiscordFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameDiscordFeed
from twitch.feeds import GameFeed
from twitch.feeds import OrganizationAtomFeed
from twitch.feeds import OrganizationDiscordFeed
from twitch.feeds import OrganizationRSSFeed
from twitch.feeds import RewardCampaignAtomFeed
from twitch.feeds import RewardCampaignDiscordFeed
from twitch.feeds import RewardCampaignFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

app_name = "core"


urlpatterns: list[URLPattern | URLResolver] = [
    # /
    path("", dashboard, name="dashboard"),
    # /search/
    path("search/", search_view, name="search"),
    # /debug/
    path("debug/", debug_view, name="debug"),
    # /datasets/
    path("datasets/", dataset_backups_view, name="dataset_backups"),
    # /datasets/download/<relative_path>/
    path(
        "datasets/download/<path:relative_path>/",
        dataset_backup_download_view,
        name="dataset_backup_download",
    ),
    # /docs/rss/
    path("docs/rss/", docs_rss_view, name="docs_rss"),
    # RSS feeds
    # /rss/campaigns/ - all active campaigns
    path("rss/campaigns/", DropCampaignFeed(), name="campaign_feed"),
    # /rss/games/ - newly added games
    path("rss/games/", GameFeed(), name="game_feed"),
    # /rss/games/<twitch_id>/campaigns/ - active campaigns for a specific game
    path(
        "rss/games/<str:twitch_id>/campaigns/",
        GameCampaignFeed(),
        name="game_campaign_feed",
    ),
    # /rss/organizations/ - newly added organizations
    path(
        "rss/organizations/",
        OrganizationRSSFeed(),
        name="organization_feed",
    ),
    # /rss/reward-campaigns/ - all active reward campaigns
    path(
        "rss/reward-campaigns/",
        RewardCampaignFeed(),
        name="reward_campaign_feed",
    ),
    # Atom feeds (added alongside RSS to preserve backward compatibility)
    path("atom/campaigns/", DropCampaignAtomFeed(), name="campaign_feed_atom"),
    path("atom/games/", GameAtomFeed(), name="game_feed_atom"),
    path(
        "atom/games/<str:twitch_id>/campaigns/",
        view=GameCampaignAtomFeed(),
        name="game_campaign_feed_atom",
    ),
    path(
        "atom/organizations/",
        OrganizationAtomFeed(),
        name="organization_feed_atom",
    ),
    path(
        "atom/reward-campaigns/",
        RewardCampaignAtomFeed(),
        name="reward_campaign_feed_atom",
    ),
    # Discord feeds (Atom feeds with Discord relative timestamps)
    path(
        "discord/campaigns/",
        DropCampaignDiscordFeed(),
        name="campaign_feed_discord",
    ),
    path("discord/games/", GameDiscordFeed(), name="game_feed_discord"),
    path(
        "discord/games/<str:twitch_id>/campaigns/",
        GameCampaignDiscordFeed(),
        name="game_campaign_feed_discord",
    ),
    path(
        "discord/organizations/",
        OrganizationDiscordFeed(),
        name="organization_feed_discord",
    ),
    path(
        "discord/reward-campaigns/",
        RewardCampaignDiscordFeed(),
        name="reward_campaign_feed_discord",
    ),
]
