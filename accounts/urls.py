from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import path

from accounts import views

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern

app_name = "accounts"

urlpatterns: list[URLPattern] = [
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("logout/", views.CustomLogoutView.as_view(), name="logout"),
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("profile/", views.profile_view, name="profile"),
]
