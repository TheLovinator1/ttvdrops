from __future__ import annotations

from django.urls import path

from twitch import views

app_name = "twitch"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("campaigns/", views.DropCampaignListView.as_view(), name="campaign_list"),
    path("campaigns/<str:pk>/", views.DropCampaignDetailView.as_view(), name="campaign_detail"),
    path("games/", views.GameListView.as_view(), name="game_list"),
    path("games/<str:pk>/", views.GameDetailView.as_view(), name="game_detail"),
    path("games/<str:game_id>/subscribe/", views.subscribe_game_notifications, name="subscribe_notifications"),
    path("organizations/", views.OrgListView.as_view(), name="org_list"),
    path("organizations/<str:pk>/", views.OrgDetailView.as_view(), name="organization_detail"),
    path("organizations/<str:org_id>/subscribe/", views.subscribe_org_notifications, name="subscribe_org_notifications"),
]
