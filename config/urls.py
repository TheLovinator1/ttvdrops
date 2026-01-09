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

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
