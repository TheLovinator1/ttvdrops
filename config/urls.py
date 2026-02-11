from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include
from django.urls import path

from twitch import views as twitch_views

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

urlpatterns: list[URLPattern | URLResolver] = [
    path("sitemap.xml", twitch_views.sitemap_view, name="sitemap"),
    path("robots.txt", twitch_views.robots_txt_view, name="robots"),
    path(route="", view=include("twitch.urls", namespace="twitch")),
]

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

if not settings.TESTING:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += [path("silk/", include("silk.urls", namespace="silk"))]
    urlpatterns = [*urlpatterns, *debug_toolbar_urls()]
