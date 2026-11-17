"""Minimal URLconf used by page cache middleware tests."""

from http import HTTPStatus

from django.http import HttpRequest
from django.http import HttpResponse
from django.urls import path

calls: list[str] = []


def static_view(request: HttpRequest) -> HttpResponse:
    """Return a stable body and record that the view was called."""
    path = request.get_full_path()
    calls.append(path)
    return HttpResponse(f"body:{path}", content_type="text/html")


def campaign_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Stand in for a campaign detail page keyed by ``twitch_id``.

    Args:
        request: The incoming request.
        twitch_id: Campaign identifier from the URL.

    Returns:
        A stable body for the campaign.
    """
    path = request.get_full_path()
    calls.append(path)
    return HttpResponse(f"campaign:{twitch_id}", content_type="text/html")


def missing_view(request: HttpRequest) -> HttpResponse:
    """Return a 404 response for non-200 status tests."""
    path = request.get_full_path()
    calls.append(path)
    return HttpResponse("missing", status=HTTPStatus.NOT_FOUND)


urlpatterns = [
    path("about/", static_view),
    path("search/", static_view),
    path("twitch/", static_view),
    path("twitch/campaigns/<str:twitch_id>/", campaign_view),
    path("twitch/missing/", missing_view),
]
