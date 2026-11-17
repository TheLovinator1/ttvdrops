from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.cache import patch_vary_headers

from core.cache import build_page_key
from core.cache import load_page
from core.cache import log_cache_error
from core.cache import read_versions
from core.cache import render_cached_page
from core.cache import request_bypasses_cache
from core.cache import resolve_route
from core.cache import route_version_names
from core.cache import store_page

if TYPE_CHECKING:
    from collections.abc import Callable
    from re import Match

    from django.http import HttpRequest
    from django.http import HttpResponse

    from core.cache import RouteSpec


class TimezoneMiddleware:
    """Activate the user's timezone from a cookie set by the browser."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the get_response callable."""
        self.get_response: Callable[[HttpRequest], HttpResponse] = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Activate the user's timezone if a valid cookie is present.

        Returns:
            HttpResponse: The response from the next middleware or view.
        """
        tzname: str | None = request.COOKIES.get("timezone")
        if tzname:
            try:
                timezone.activate(ZoneInfo(tzname))
            except KeyError, ValueError, TypeError:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)


class PageCacheMiddleware:
    """Serve cacheable GET responses from the full-page cache.

    This middleware must run after :class:`TimezoneMiddleware` so the active
    timezone matches the timezone that is part of the cache key.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the get_response callable."""
        self.get_response: Callable[[HttpRequest], HttpResponse] = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Serve a cached response or render and store a fresh one.

        Returns:
            HttpResponse: The cached or freshly rendered response.
        """
        if not settings.PAGE_CACHE_ENABLED or request.method != "GET":
            return self.get_response(request)

        route = resolve_route(request.path)
        if route is None:
            return self.get_response(request)

        spec, match = route
        if request_bypasses_cache(request):
            return self._mark_bypass(self.get_response(request))

        key: str | None = None
        cached_response: HttpResponse | None = None
        try:
            key, cached_response = self._read_cache(request, spec, match)
        except Exception as exc:  # ruff: ignore[blind-except]
            log_cache_error("middleware", exc)
            key = None

        if cached_response is not None:
            return cached_response

        response = self.get_response(request)
        if key is None:
            return self._mark_bypass(response)

        stored: bool = False
        try:
            stored = store_page(key, response, spec.ttl)
        except Exception as exc:  # ruff: ignore[blind-except]
            log_cache_error("store", exc)

        if not stored:
            return self._mark_bypass(response)

        if not response.streaming:
            response.headers["X-Cache"] = "MISS"
            if "Cache-Control" not in response.headers:
                response.headers["Cache-Control"] = f"public, max-age={spec.ttl}"
            patch_vary_headers(response, ("Accept-Encoding", "Cookie"))
        return response

    @staticmethod
    def _read_cache(
        request: HttpRequest,
        spec: RouteSpec,
        match: Match[str],
    ) -> tuple[str | None, HttpResponse | None]:
        """Look up a cached response for a request.

        Args:
            request: The incoming request.
            spec: Route policy for the matched page.
            match: Regex match for the route.

        Returns:
            The cache key (if one could be built) and the cached response, if any.
        """
        versions = read_versions(route_version_names(spec, match))
        key = build_page_key(spec, match, request, versions)
        if key is None:
            return None, None
        entry = load_page(key)
        if entry is None:
            return key, None
        return key, render_cached_page(entry, spec, request)

    @staticmethod
    def _mark_bypass(response: HttpResponse) -> HttpResponse:
        """Mark an uncached response for operational visibility.

        Args:
            response: The response from the view.

        Returns:
            The same response, marked as a cache bypass when possible.
        """
        if not response.streaming:
            response.headers["X-Cache"] = "BYPASS"
        return response
