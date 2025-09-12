from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

if TYPE_CHECKING:
    from django.urls.resolvers import URLResolver

urlpatterns: list[URLResolver] = [
    path(route="admin/", view=admin.site.urls),
    path(route="accounts/", view=include("accounts.urls", namespace="accounts")),
    path(route="", view=include("twitch.urls", namespace="twitch")),
]

if not settings.TESTING:
    # Import debug_toolbar lazily to avoid ImportError when not installed in testing environments
    from debug_toolbar.toolbar import debug_toolbar_urls  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]

    urlpatterns = [
        *urlpatterns,
        *debug_toolbar_urls(),
        path("__reload__/", include("django_browser_reload.urls")),
    ]
