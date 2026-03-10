from typing import TYPE_CHECKING

from django.urls import path

from twitch import views
from twitch.feeds import DropCampaignAtomFeed
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameAtomFeed
from twitch.feeds import GameCampaignAtomFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameFeed
from twitch.feeds import OrganizationAtomFeed
from twitch.feeds import OrganizationRSSFeed
from twitch.feeds import RewardCampaignAtomFeed
from twitch.feeds import RewardCampaignFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern

app_name = "twitch"


urlpatterns: list[URLPattern] = [
    path("", views.dashboard, name="dashboard"),
    path("badges/", views.badge_list_view, name="badge_list"),
    path("badges/<str:set_id>/", views.badge_set_detail_view, name="badge_set_detail"),
    path("campaigns/", views.drop_campaign_list_view, name="campaign_list"),
    path(
        "campaigns/<str:twitch_id>/",
        views.drop_campaign_detail_view,
        name="campaign_detail",
    ),
    path("channels/", views.ChannelListView.as_view(), name="channel_list"),
    path(
        "channels/<str:twitch_id>/",
        views.ChannelDetailView.as_view(),
        name="channel_detail",
    ),
    path("debug/", views.debug_view, name="debug"),
    path("datasets/", views.dataset_backups_view, name="dataset_backups"),
    path(
        "datasets/download/<path:relative_path>/",
        views.dataset_backup_download_view,
        name="dataset_backup_download",
    ),
    path("docs/rss/", views.docs_rss_view, name="docs_rss"),
    path("emotes/", views.emote_gallery_view, name="emote_gallery"),
    path("games/", views.GamesGridView.as_view(), name="games_grid"),
    path("games/list/", views.GamesListView.as_view(), name="games_list"),
    path("games/<str:twitch_id>/", views.GameDetailView.as_view(), name="game_detail"),
    path("organizations/", views.org_list_view, name="org_list"),
    path(
        "organizations/<str:twitch_id>/",
        views.organization_detail_view,
        name="organization_detail",
    ),
    path(
        "reward-campaigns/",
        views.reward_campaign_list_view,
        name="reward_campaign_list",
    ),
    path(
        "reward-campaigns/<str:twitch_id>/",
        views.reward_campaign_detail_view,
        name="reward_campaign_detail",
    ),
    path("search/", views.search_view, name="search"),
    path(
        "export/campaigns/csv/",
        views.export_campaigns_csv,
        name="export_campaigns_csv",
    ),
    path(
        "export/campaigns/json/",
        views.export_campaigns_json,
        name="export_campaigns_json",
    ),
    path("export/games/csv/", views.export_games_csv, name="export_games_csv"),
    path("export/games/json/", views.export_games_json, name="export_games_json"),
    path(
        "export/organizations/csv/",
        views.export_organizations_csv,
        name="export_organizations_csv",
    ),
    path(
        "export/organizations/json/",
        views.export_organizations_json,
        name="export_organizations_json",
    ),
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
        GameCampaignAtomFeed(),
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
]
