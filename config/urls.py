from typing import TYPE_CHECKING

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include
from django.urls import path

from core import views as core_views

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

urlpatterns: list[URLPattern | URLResolver] = [
    path(route="sitemap.xml", view=core_views.sitemap_view, name="sitemap"),
    path(
        route="sitemap-static.xml",
        view=core_views.sitemap_static_view,
        name="sitemap-static",
    ),
    path(
        route="sitemap-twitch-channels.xml",
        view=core_views.sitemap_twitch_channels_view,
        name="sitemap-twitch-channels",
    ),
    path(
        route="sitemap-twitch-drops.xml",
        view=core_views.sitemap_twitch_drops_view,
        name="sitemap-twitch-drops",
    ),
    path(
        route="sitemap-twitch-others.xml",
        view=core_views.sitemap_twitch_others_view,
        name="sitemap-twitch-others",
    ),
    path(
        route="sitemap-kick.xml",
        view=core_views.sitemap_kick_view,
        name="sitemap-kick",
    ),
    path(
        route="sitemap-youtube.xml",
        view=core_views.sitemap_youtube_view,
        name="sitemap-youtube",
    ),
    # Core app
    path(route="", view=include("core.urls", namespace="core")),
    # Twitch app
    path(route="twitch/", view=include("twitch.urls", namespace="twitch")),
    # Kick app
    path(route="kick/", view=include("kick.urls", namespace="kick")),
    # Chzzk app
    path(route="chzzk/", view=include("chzzk.urls", namespace="chzzk")),
    # YouTube app
    path(route="youtube/", view=include("youtube.urls", namespace="youtube")),
]

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if not settings.TESTING:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns = [*urlpatterns, *debug_toolbar_urls()]
