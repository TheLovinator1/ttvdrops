from typing import TYPE_CHECKING

from django.urls import path
from django.views.generic.base import RedirectView

from twitch import views

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

app_name = "twitch"


urlpatterns: list[URLPattern | URLResolver] = [
    # /twitch/
    path("", views.dashboard, name="dashboard"),
    # Redirect old standalone Twitch v1 API URLs to the new combined API
    # These must come before the catch-all below.
    path(
        "api/v1/docs/",
        RedirectView.as_view(
            pattern_name="api-v1:openapi-view",
            permanent=True,
        ),
    ),
    path(
        "api/v1/openapi.json",
        RedirectView.as_view(
            pattern_name="api-v1:openapi-json",
            permanent=True,
        ),
    ),
    path(
        "api/v1/<path:rest>",
        RedirectView.as_view(
            url="/api/v1/twitch/%(rest)s",
            permanent=True,
            query_string=True,
        ),
    ),
    # /twitch/badges/
    path("badges/", views.badge_list_view, name="badge_list"),
    # /twitch/badges/<set_id>/
    path("badges/<str:set_id>/", views.badge_set_detail_view, name="badge_set_detail"),
    # /twitch/campaigns/
    path("campaigns/", views.drop_campaign_list_view, name="campaign_list"),
    # /twitch/campaigns/<twitch_id>/
    path(
        "campaigns/<str:twitch_id>/",
        views.drop_campaign_detail_view,
        name="campaign_detail",
    ),
    # /twitch/channels/
    path("channels/", views.ChannelListView.as_view(), name="channel_list"),
    # /twitch/channels/<twitch_id>/
    path(
        "channels/<str:twitch_id>/",
        views.ChannelDetailView.as_view(),
        name="channel_detail",
    ),
    # /twitch/emotes/
    path("emotes/", views.emote_gallery_view, name="emote_gallery"),
    # /twitch/games/
    path("games/", views.GamesGridView.as_view(), name="games_grid"),
    # /twitch/games/list/
    path("games/list/", views.GamesListView.as_view(), name="games_list"),
    # /twitch/games/<twitch_id>/
    path("games/<str:twitch_id>/", views.GameDetailView.as_view(), name="game_detail"),
    # /twitch/organizations/
    path("organizations/", views.org_list_view, name="org_list"),
    # /twitch/organizations/<twitch_id>/
    path(
        "organizations/<str:twitch_id>/",
        views.organization_detail_view,
        name="organization_detail",
    ),
    # /twitch/reward-campaigns/
    path(
        "reward-campaigns/",
        views.reward_campaign_list_view,
        name="reward_campaign_list",
    ),
    # /twitch/reward-campaigns/<twitch_id>/
    path(
        "reward-campaigns/<str:twitch_id>/",
        views.reward_campaign_detail_view,
        name="reward_campaign_detail",
    ),
    # /twitch/sitewide/
    path(
        "sitewide/",
        views.sitewide_rewards_view,
        name="sitewide_rewards",
    ),
    # /twitch/rewards/no-game/
    path(
        "rewards/no-game/",
        views.game_less_rewards_view,
        name="game_less_rewards",
    ),
    # /twitch/export/campaigns/csv/
    path(
        "export/campaigns/csv/",
        views.export_campaigns_csv,
        name="export_campaigns_csv",
    ),
    # /twitch/export/campaigns/json/
    path(
        "export/campaigns/json/",
        views.export_campaigns_json,
        name="export_campaigns_json",
    ),
    # /twitch/export/games/csv/
    path("export/games/csv/", views.export_games_csv, name="export_games_csv"),
    # /twitch/export/games/json/
    path("export/games/json/", views.export_games_json, name="export_games_json"),
    # /twitch/export/organizations/csv/
    path(
        "export/organizations/csv/",
        views.export_organizations_csv,
        name="export_organizations_csv",
    ),
    # /twitch/export/organizations/json/
    path(
        "export/organizations/json/",
        views.export_organizations_json,
        name="export_organizations_json",
    ),
]
