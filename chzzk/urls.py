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
