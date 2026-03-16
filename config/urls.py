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
    # Core app
    path(route="", view=include("core.urls", namespace="core")),
    # Twitch app
    path(route="twitch/", view=include("twitch.urls", namespace="twitch")),
    # Kick app
    path(route="kick/", view=include("kick.urls", namespace="kick")),
]

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if not settings.TESTING:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += [path("silk/", include("silk.urls", namespace="silk"))]
    urlpatterns = [*urlpatterns, *debug_toolbar_urls()]
