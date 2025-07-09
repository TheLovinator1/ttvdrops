from __future__ import annotations

from django.urls import path

from twitch import views

app_name = "twitch"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("campaigns/", views.DropCampaignListView.as_view(), name="campaign_list"),
    path("campaigns/<str:pk>/", views.DropCampaignDetailView.as_view(), name="campaign_detail"),
]
