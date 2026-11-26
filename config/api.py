"""Combined v1 API router for all platforms."""

import json
from typing import TYPE_CHECKING
from typing import Any

from django.shortcuts import render
from ninja import NinjaAPI
from ninja.openapi.docs import Swagger

from chzzk.api import api as chzzk_router
from core.base_url import build_absolute_uri
from core.discord_embed import DEFAULT_ACCENT_COLOR
from core.discord_embed import embed_buttons
from kick.api import api as kick_router
from twitch.api import api as twitch_router

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


class TTVDropsSwaggerDocs(Swagger):
    """Swagger UI docs page with SEO and Discord component-embed context."""

    def render_page(
        self,
        request: HttpRequest,
        api: NinjaAPI,
        **kwargs: str,
    ) -> HttpResponse:
        """Render the Swagger UI page with page metadata.

        Args:
            request: The incoming HTTP request.
            api: The NinjaAPI instance being documented.
            **kwargs: Path parameters used to resolve the OpenAPI schema URL.

        Returns:
            HttpResponse: The rendered documentation page.
        """
        self.settings["url"] = self.get_openapi_url(api, kwargs)
        context: dict[str, Any] = {
            "swagger_settings": json.dumps(self.settings, indent=1),
            "api": api,
            "add_csrf": False,
            "page_title": "TTVDrops API v1 Documentation - Twitch, Kick and Chzzk Drops API",
            "page_description": (
                "Interactive documentation for the TTVDrops API v1. Browse endpoints "
                "for Twitch, Kick and Chzzk drop campaigns, games, rewards, "
                "organizations and channels. No authentication required."
            ),
            "page_url": build_absolute_uri("/api/v1/docs/"),
            "discord_embed_accent_color": DEFAULT_ACCENT_COLOR,
            "discord_embed_buttons": embed_buttons(
                ("API docs", "/api/v1/docs/"),
                ("OpenAPI schema", "/api/v1/openapi.json"),
                ("Datasets", "/datasets/"),
                ("Twitch Drops", "/twitch/"),
            ),
        }
        return render(request, self.template, context)


api_v1 = NinjaAPI(
    title="TTVDrops API",
    version="1.0.0",
    docs=TTVDropsSwaggerDocs(),
    description=(
        "Track limited-time free rewards across Twitch, Kick, and Chzzk. "
        "All campaign, game, organization, and reward data is available "
        "in structured JSON. No authentication required. "
        "Please be nice and limit requests to at most 1 per second. "
        "The datasets at /datasets/ are a better choice for bulk analysis. "
        "If you need a higher rate limit for a legitimate project, please reach out "
        "via tlovinator@gmail.com or on Discord (TheLovinator)."
    ),
    urls_namespace="api-v1",
    docs_url="docs/",
    servers=[
        {"url": "https://ttvdrops.lovinator.space", "description": "Production server"},
    ],
    openapi_extra={
        "info": {
            "contact": {
                "name": "TheLovinator",
                "email": "tlovinator@gmail.com",
                "url": "https://github.com/TheLovinator1/ttvdrops",
            },
            "license": {
                "name": "CC0 1.0 Universal (data), MIT (code)",
                "url": "https://github.com/TheLovinator1/ttvdrops/blob/main/LICENSE",
            },
            "termsOfService": "https://ttvdrops.lovinator.space/about/",
        },
    },
)

api_v1.add_router("/twitch/", twitch_router, url_name_prefix="twitch-api-v1")
api_v1.add_router("/kick/", kick_router, url_name_prefix="kick-api-v1")
api_v1.add_router("/chzzk/", chzzk_router, url_name_prefix="chzzk-api-v1")

app_name = "api-v1"
urlpatterns = api_v1.urls[0]
