from __future__ import annotations

from typing import TYPE_CHECKING

from debug_toolbar.toolbar import debug_toolbar_urls  # pyright: ignore[reportMissingTypeStubs]
from django.conf import settings
from django.contrib import admin
from django.urls import path

if TYPE_CHECKING:
    from django.urls.resolvers import URLResolver

urlpatterns: list[URLResolver] = [
    path(route="admin/", view=admin.site.urls),
]

if not settings.TESTING:
    urlpatterns = [*urlpatterns, *debug_toolbar_urls()]
