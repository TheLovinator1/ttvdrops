"""Combined v1 API router for all platforms."""

from ninja import NinjaAPI

from kick.api import api as kick_router
from twitch.api import api as twitch_router

api_v1 = NinjaAPI(
    title="TTVDrops API",
    version="1.0.0",
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

app_name = "api-v1"
urlpatterns = api_v1.urls[0]
