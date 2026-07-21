"""Combined v1 API router for all platforms."""

from ninja import NinjaAPI

from kick.api import api as kick_router
from twitch.api import api as twitch_router

api_v1 = NinjaAPI(
    title="TTVDrops API",
    version="1.0.0",
    urls_namespace="api-v1",
    docs_url="docs/",
)

api_v1.add_router("/twitch/", twitch_router, url_name_prefix="twitch-api-v1")
api_v1.add_router("/kick/", kick_router, url_name_prefix="kick-api-v1")

app_name = "api-v1"
urlpatterns = api_v1.urls[0]
