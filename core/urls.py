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
from twitch.feeds import GameRewardCampaignAtomFeed
from twitch.feeds import GameRewardCampaignDiscordFeed
from twitch.feeds import GameRewardCampaignFeed
from twitch.feeds import OrganizationAtomFeed
from twitch.feeds import OrganizationDiscordFeed
from twitch.feeds import OrganizationRSSFeed
from twitch.feeds import RewardCampaignAtomFeed
from twitch.feeds import RewardCampaignDiscordFeed
from twitch.feeds import RewardCampaignFeed
from twitch.feeds import SitewideRewardCampaignAtomFeed
from twitch.feeds import SitewideRewardCampaignDiscordFeed
from twitch.feeds import SitewideRewardCampaignFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

app_name = "core"


urlpatterns: list[URLPattern | URLResolver] = [
    # /
    path(
        route="",
        view=dashboard,
        name="dashboard",
    ),
    # /search/
    path(
        route="search/",
        view=search_view,
        name="search",
    ),
    # /debug/
    path(
        route="debug/",
        view=debug_view,
        name="debug",
    ),
    # /datasets/
    path(
        route="datasets/",
        view=dataset_backups_view,
        name="dataset_backups",
    ),
    # /datasets/download/<relative_path>/
    path(
        route="datasets/download/<path:relative_path>/",
        view=dataset_backup_download_view,
        name="dataset_backup_download",
    ),
    # /docs/rss/
    path(
        route="docs/rss/",
        view=docs_rss_view,
        name="docs_rss",
    ),
    # RSS feeds
    # /rss/campaigns/ - all active campaigns
    path(
        route="rss/campaigns/",
        view=DropCampaignFeed(),
        name="campaign_feed",
    ),
    # /rss/games/ - newly added games
    path(
        route="rss/games/",
        view=GameFeed(),
        name="game_feed",
    ),
    # /rss/games/<twitch_id>/campaigns/ - active campaigns for a specific game
    path(
        route="rss/games/<str:twitch_id>/campaigns/",
        view=GameCampaignFeed(),
        name="game_campaign_feed",
    ),
    # /rss/organizations/ - newly added organizations
    path(
        route="rss/organizations/",
        view=OrganizationRSSFeed(),
        name="organization_feed",
    ),
    # /rss/games/<twitch_id>/rewards/ - active reward campaigns for a specific game
    path(
        route="rss/games/<str:twitch_id>/rewards/",
        view=GameRewardCampaignFeed(),
        name="game_reward_feed",
    ),
    # /rss/reward-campaigns/ - all active reward campaigns
    path(
        route="rss/reward-campaigns/",
        view=RewardCampaignFeed(),
        name="reward_campaign_feed",
    ),
    # /rss/sitewide/rewards/ - site-wide reward campaigns
    path(
        route="rss/sitewide/rewards/",
        view=SitewideRewardCampaignFeed(),
        name="sitewide_reward_feed",
    ),
    # Atom feeds (added alongside RSS to preserve backward compatibility)
    path(
        route="atom/campaigns/",
        view=DropCampaignAtomFeed(),
        name="campaign_feed_atom",
    ),
    path(
        route="atom/games/",
        view=GameAtomFeed(),
        name="game_feed_atom",
    ),
    path(
        route="atom/games/<str:twitch_id>/campaigns/",
        view=GameCampaignAtomFeed(),
        name="game_campaign_feed_atom",
    ),
    path(
        route="atom/organizations/",
        view=OrganizationAtomFeed(),
        name="organization_feed_atom",
    ),
    path(
        route="atom/games/<str:twitch_id>/rewards/",
        view=GameRewardCampaignAtomFeed(),
        name="game_reward_feed_atom",
    ),
    path(
        route="atom/reward-campaigns/",
        view=RewardCampaignAtomFeed(),
        name="reward_campaign_feed_atom",
    ),
    # /atom/sitewide/rewards/ - site-wide reward campaigns (Atom)
    path(
        route="atom/sitewide/rewards/",
        view=SitewideRewardCampaignAtomFeed(),
        name="sitewide_reward_feed_atom",
    ),
    # /discord/sitewide/rewards/ - site-wide reward campaigns (Discord)
    path(
        route="discord/sitewide/rewards/",
        view=SitewideRewardCampaignDiscordFeed(),
        name="sitewide_reward_feed_discord",
    ),
    # Discord feeds (Atom feeds with Discord relative timestamps)
    path(
        route="discord/campaigns/",
        view=DropCampaignDiscordFeed(),
        name="campaign_feed_discord",
    ),
    path(
        route="discord/games/",
        view=GameDiscordFeed(),
        name="game_feed_discord",
    ),
    path(
        route="discord/games/<str:twitch_id>/campaigns/",
        view=GameCampaignDiscordFeed(),
        name="game_campaign_feed_discord",
    ),
    path(
        route="discord/organizations/",
        view=OrganizationDiscordFeed(),
        name="organization_feed_discord",
    ),
    path(
        route="discord/games/<str:twitch_id>/rewards/",
        view=GameRewardCampaignDiscordFeed(),
        name="game_reward_feed_discord",
    ),
    path(
        route="discord/reward-campaigns/",
        view=RewardCampaignDiscordFeed(),
        name="reward_campaign_feed_discord",
    ),
]
