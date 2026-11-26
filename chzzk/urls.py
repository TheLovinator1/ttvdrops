from typing import TYPE_CHECKING

from django.urls import path

from chzzk import views

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern

app_name = "chzzk"

urlpatterns: list[URLPattern] = [
    # /chzzk/
    path(
        "",
        views.dashboard_view,
        name="dashboard",
    ),
    # /chzzk/games/<category_id>/
    path(
        "games/<str:category_id>/",
        views.game_detail_view,
        name="game_detail",
    ),
    # /chzzk/categories/<category_type>/<category_id>/
    path(
        "categories/<str:category_type>/<str:category_id>/",
        views.category_detail_view,
        name="category_detail",
    ),
    # /chzzk/campaigns/
    path(
        "campaigns/",
        views.CampaignListView.as_view(),
        name="campaign_list",
    ),
    # /chzzk/campaigns/<campaign_no>/
    path(
        "campaigns/<int:campaign_no>/",
        views.campaign_detail_view,
        name="campaign_detail",
    ),
    # /chzzk/rss/campaigns
    path(
        "rss/campaigns",
        views.ChzzkCampaignFeed(),
        name="campaign_feed",
    ),
    # /chzzk/atom/campaigns
    path(
        "atom/campaigns",
        views.ChzzkCampaignFeed(),
        name="campaign_feed_atom",
    ),
    # /chzzk/discord/campaigns
    path(
        "discord/campaigns",
        views.ChzzkCampaignFeed(),
        name="campaign_feed_discord",
    ),
]
