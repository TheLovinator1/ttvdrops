from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include
from django.urls import path

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

urlpatterns: list[URLResolver] | list[URLPattern | URLResolver] = [  # type: ignore[assignment]
    path(route="", view=include("twitch.urls", namespace="twitch")),
]

if not settings.TESTING:
    # Import debug_toolbar lazily to avoid ImportError when not installed in testing environments
    from debug_toolbar.toolbar import debug_toolbar_urls  # pyright: ignore[reportMissingTypeStubs]

    urlpatterns = [
        *urlpatterns,
        *debug_toolbar_urls(),
    ]

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
