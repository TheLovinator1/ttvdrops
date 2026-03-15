from typing import TYPE_CHECKING

from django.urls import path

from kick import views
from kick.feeds import KickCampaignAtomFeed
from kick.feeds import KickCampaignDiscordFeed
from kick.feeds import KickCampaignFeed
from kick.feeds import KickCategoryAtomFeed
from kick.feeds import KickCategoryCampaignAtomFeed
from kick.feeds import KickCategoryCampaignDiscordFeed
from kick.feeds import KickCategoryCampaignFeed
from kick.feeds import KickCategoryDiscordFeed
from kick.feeds import KickCategoryFeed
from kick.feeds import KickOrganizationAtomFeed
from kick.feeds import KickOrganizationDiscordFeed
from kick.feeds import KickOrganizationFeed

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

app_name = "kick"

urlpatterns: list[URLPattern | URLResolver] = [
    # /kick/
    path(
        route="",
        view=views.dashboard,
        name="dashboard",
    ),
    # /kick/campaigns/
    path(
        route="campaigns/",
        view=views.campaign_list_view,
        name="campaign_list",
    ),
    # /kick/campaigns/<kick_id>/
    path(
        route="campaigns/<str:kick_id>/",
        view=views.campaign_detail_view,
        name="campaign_detail",
    ),
    # /kick/games/
    path(
        route="games/",
        view=views.category_list_view,
        name="game_list",
    ),
    # /kick/games/<kick_id>/
    path(
        route="games/<int:kick_id>/",
        view=views.category_detail_view,
        name="game_detail",
    ),
    # Legacy category routes kept for backward compatibility
    path(
        route="categories/",
        view=views.category_list_view,
        name="category_list",
    ),
    path(
        route="categories/<int:kick_id>/",
        view=views.category_detail_view,
        name="category_detail",
    ),
    # /kick/organizations/
    path(
        route="organizations/",
        view=views.organization_list_view,
        name="organization_list",
    ),
    # /kick/organizations/<kick_id>/
    path(
        route="organizations/<str:kick_id>/",
        view=views.organization_detail_view,
        name="organization_detail",
    ),
    # RSS feeds
    # /kick/rss/campaigns/ - all active campaigns
    path(
        "rss/campaigns/",
        KickCampaignFeed(),
        name="campaign_feed",
    ),
    # /kick/rss/games/ - newly added games
    path(
        "rss/games/",
        KickCategoryFeed(),
        name="game_feed",
    ),
    # /kick/rss/games/<kick_id>/campaigns/ - active campaigns for a specific game
    path(
        "rss/games/<int:kick_id>/campaigns/",
        KickCategoryCampaignFeed(),
        name="game_campaign_feed",
    ),
    # Legacy category feed routes kept for backward compatibility
    path(
        "rss/categories/",
        KickCategoryFeed(),
        name="category_feed",
    ),
    path(
        "rss/categories/<int:kick_id>/campaigns/",
        KickCategoryCampaignFeed(),
        name="category_campaign_feed",
    ),
    # /kick/rss/organizations/ - newly added organizations
    path(
        "rss/organizations/",
        KickOrganizationFeed(),
        name="organization_feed",
    ),
    # Atom feeds (added alongside RSS to preserve backward compatibility)
    path(
        "atom/campaigns/",
        KickCampaignAtomFeed(),
        name="campaign_feed_atom",
    ),
    path(
        "atom/games/",
        KickCategoryAtomFeed(),
        name="game_feed_atom",
    ),
    path(
        "atom/games/<int:kick_id>/campaigns/",
        KickCategoryCampaignAtomFeed(),
        name="game_campaign_feed_atom",
    ),
    path(
        "atom/categories/",
        KickCategoryAtomFeed(),
        name="category_feed_atom",
    ),
    path(
        "atom/categories/<int:kick_id>/campaigns/",
        KickCategoryCampaignAtomFeed(),
        name="category_campaign_feed_atom",
    ),
    path(
        "atom/organizations/",
        KickOrganizationAtomFeed(),
        name="organization_feed_atom",
    ),
    # Discord feeds (Atom feeds with Discord relative timestamps)
    path(
        "discord/campaigns/",
        KickCampaignDiscordFeed(),
        name="campaign_feed_discord",
    ),
    path(
        "discord/games/",
        KickCategoryDiscordFeed(),
        name="game_feed_discord",
    ),
    path(
        "discord/games/<int:kick_id>/campaigns/",
        KickCategoryCampaignDiscordFeed(),
        name="game_campaign_feed_discord",
    ),
    path(
        "discord/categories/",
        KickCategoryDiscordFeed(),
        name="category_feed_discord",
    ),
    path(
        "discord/categories/<int:kick_id>/campaigns/",
        KickCategoryCampaignDiscordFeed(),
        name="category_campaign_feed_discord",
    ),
    path(
        "discord/organizations/",
        KickOrganizationDiscordFeed(),
        name="organization_feed_discord",
    ),
]
