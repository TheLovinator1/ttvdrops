import gzip
import re
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import caches
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.test import RequestFactory
from django.test import TestCase
from django.test import override_settings
from django.urls import get_resolver
from django.urls.resolvers import URLPattern
from django.urls.resolvers import URLResolver

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward
from core.cache import CachedPage
from core.cache import build_page_key
from core.cache import bump_version
from core.cache import client_accepts_gzip
from core.cache import is_excluded
from core.cache import load_page
from core.cache import normalize_query
from core.cache import read_versions
from core.cache import render_cached_page
from core.cache import request_bypasses_cache
from core.cache import resolve_route
from core.cache import route_version_names
from core.cache import should_store
from core.cache import store_page
from core.tests import cache_urls
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from collections.abc import Generator
    from re import Match

    from core.cache import RouteSpec

_CONVERTER_SAMPLES: dict[str, str] = {
    "int": "1",
    "path": "sample",
    "slug": "sample",
    "str": "sample",
    "uuid": "00000000-0000-0000-0000-000000000000",
}


def _iter_route_paths() -> Generator[str]:
    """Yield one sample URL path for every registered Django route."""

    def walk(resolver: URLResolver, prefix: str) -> Generator[str]:
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                route: str = getattr(entry.pattern, "_route", "")
                if not route:
                    regex = getattr(entry.pattern, "regex", None)
                    if regex is not None:
                        route = regex.pattern.removeprefix("^").removesuffix("$")
                yield from walk(entry, prefix + route)
            elif isinstance(entry, URLPattern):
                pattern_route = getattr(entry.pattern, "_route", None)
                if isinstance(pattern_route, str):
                    yield prefix + pattern_route

    yield from walk(get_resolver(), "")


def _sample_path(route: str) -> str:
    """Replace converters in a route with sample values.

    Args:
        route: A URL pattern route such as ``/twitch/<str:name>/``.

    Returns:
        The route with each converter replaced by a sample value.
    """
    pattern = re.compile(
        r"<(?:(?P<converter>[a-zA-Z_]+):)?(?P<name>[a-zA-Z_]+)>",
    )

    def replace(match: re.Match[str]) -> str:
        converter = match.group("converter") or "str"
        return _CONVERTER_SAMPLES.get(converter, "sample")

    return pattern.sub(replace, route)


def _request(
    path: str,
    query: str = "",
    cookies: dict[str, str] | None = None,
) -> HttpRequest:
    """Build a GET request for cache key tests.

    Args:
        path: Request path.
        query: Raw query string, without the leading ``?``.
        cookies: Cookies to attach to the request.

    Returns:
        The constructed request.
    """
    url = f"{path}?{query}" if query else path
    request = RequestFactory().get(url)
    request.COOKIES.update(cookies or {})
    return request


def _resolve(path: str) -> tuple[RouteSpec, Match[str]]:
    """Resolve ``path`` to its cache policy, requiring a route match.

    Args:
        path: URL path to resolve.

    Returns:
        The route policy and its regex match.
    """
    route = resolve_route(path)
    assert route is not None, f"no cache route for {path}"
    return route


def _page_key(
    spec: RouteSpec,
    match: Match[str],
    request: HttpRequest,
    versions: dict[str, str],
) -> str:
    """Build a cache key, requiring the request to be cacheable.

    Args:
        spec: Route policy for the matched page.
        match: Regex match for the route.
        request: The incoming request.
        versions: Current version values for the route's data scopes.

    Returns:
        The cache key.
    """
    key = build_page_key(spec, match, request, versions)
    assert key is not None
    return key


class TestRouteTable:
    """Route classification tests."""

    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("/", "core-dashboard"),
            ("/about/", "about"),
            ("/docs/rss/", "docs-rss"),
            ("/docs/mcp/", "docs-mcp"),
            ("/datasets/", "datasets"),
            ("/youtube/", "youtube"),
            ("/sitemap.xml", "sitemap-index"),
            ("/sitemap-twitch-channels.xml", "sitemap-twitch"),
            ("/sitemap-kick.xml", "sitemap-kick"),
            ("/sitemap-static.xml", "sitemap-static"),
            ("/rss/campaigns/", "twitch-feed"),
            ("/kick/rss/campaigns/", "kick-feed"),
            ("/chzzk/rss/campaigns", "chzzk-feed"),
            ("/twitch/", "twitch"),
            ("/twitch/campaigns/abc/", "twitch-campaign-detail"),
            ("/twitch/reward-campaigns/abc/", "twitch-reward-detail"),
            ("/kick/", "kick"),
            ("/kick/campaigns/abc/", "kick-campaign-detail"),
            ("/chzzk/", "chzzk"),
            ("/chzzk/campaigns/1/", "chzzk-campaign-detail"),
            ("/chzzk/games/list/", "chzzk-game-list"),
            ("/chzzk/games/dokkaebi/", "chzzk-game-detail"),
            (
                "/chzzk/categories/ETC/sci_tech/",
                "chzzk-category-detail",
            ),
            ("/api/v1/twitch/campaigns/", "api-twitch"),
            ("/api/v1/kick/campaigns/", "api-kick"),
            ("/api/v1/chzzk/campaigns/", "api-chzzk"),
        ],
    )
    def test_route_specs(self, path: str, name: str) -> None:
        """Known paths map to the expected cache policy."""
        route = resolve_route(path)
        assert route is not None
        assert route[0].name == name

    @pytest.mark.parametrize(
        "path",
        [
            "/search/",
            "/datasets/download/file.json/",
            "/favicon.ico",
            "/channels/",
            "/games/",
            "/api/v1/docs/",
            "/api/v1/openapi.json",
            "/api/v1/",
            "/.well-known/mcp/server-card.json",
            "/media/file.png",
            "/static/app.css",
            "/__debug__/render_panel/",
        ],
    )
    def test_excluded_paths(self, path: str) -> None:
        """Excluded paths are never cached."""
        assert is_excluded(path)
        assert resolve_route(path) is None

    def test_unknown_path(self) -> None:
        """Unknown paths are not cached."""
        assert resolve_route("/not-a-real-page/") is None

    def test_detail_version_names(self) -> None:
        """Detail routes include shared and per-campaign versions."""
        route = resolve_route("/twitch/campaigns/abc/")
        assert route is not None
        assert route_version_names(*route) == (
            "twitch:shared",
            "twitch:campaign:abc",
        )

    def test_every_registered_route_is_cached_or_excluded(self) -> None:
        """Every route must either have a cache policy or be explicitly excluded."""
        missing: list[str] = []
        for route in _iter_route_paths():
            path = f"/{_sample_path(route).lstrip('/')}"
            if is_excluded(path) or resolve_route(path) is not None:
                continue
            missing.append(path)
        assert not missing


class TestQueryNormalization:
    """Query-string handling tests."""

    def test_empty_query(self) -> None:
        """An empty query is cacheable."""
        assert not normalize_query("")

    def test_sorted_query(self) -> None:
        """Query parameters are sorted for a stable key."""
        assert normalize_query("?page=2&status=active") == "page=2&status=active"

    def test_tracking_parameters_are_dropped(self) -> None:
        """Tracking parameters do not affect the cache key."""
        assert normalize_query("?page=2&utm_source=x&fbclid=y") == "page=2"

    def test_unknown_parameter_bypasses(self) -> None:
        """Unknown parameters disable caching for the request."""
        assert normalize_query("?search=hello") is None
        assert normalize_query("?q=hello") is None
        assert normalize_query("?foo=1") is None


class TestPageKey:
    """Cache key construction tests."""

    def test_timezone_changes_key(self) -> None:
        """Different timezone cookies produce different keys."""
        spec, match = _resolve("/twitch/")
        versions = {"twitch": "v1"}
        utc = _page_key(spec, match, _request("/twitch/"), versions)
        oslo = _page_key(
            spec,
            match,
            _request("/twitch/", cookies={"timezone": "Europe/Oslo"}),
            versions,
        )
        assert utc != oslo
        assert ":UTC:" in utc
        assert ":Europe/Oslo:" in oslo

    def test_invalid_timezone_falls_back_to_utc(self) -> None:
        """An invalid timezone cookie is normalized to UTC."""
        spec, match = _resolve("/twitch/")
        key = _page_key(
            spec,
            match,
            _request("/twitch/", cookies={"timezone": "Not/AZone"}),
            {"twitch": "v1"},
        )
        assert ":UTC:" in key

    def test_query_changes_key(self) -> None:
        """Different allowlisted queries produce different keys."""
        spec, match = _resolve("/twitch/campaigns/")
        versions = {"twitch": "v1"}
        first = _page_key(spec, match, _request("/twitch/campaigns/"), versions)
        second = _page_key(
            spec,
            match,
            _request("/twitch/campaigns/", query="page=2"),
            versions,
        )
        assert first != second

    def test_unknown_query_returns_none(self) -> None:
        """A query that cannot be cached produces no key."""
        spec, match = _resolve("/twitch/")
        assert (
            build_page_key(
                spec,
                match,
                _request("/twitch/", query="search=x"),
                {"twitch": "v1"},
            )
            is None
        )

    @override_settings(PAGE_CACHE_RELEASE="release-1")
    def test_release_changes_key(self) -> None:
        """A new release invalidates every key."""
        spec, match = _resolve("/twitch/")
        key = _page_key(spec, match, _request("/twitch/"), {"twitch": "v1"})
        assert ":release-1:" in key


class TestShouldStore:
    """Response eligibility tests."""

    def _response(
        self,
        content: bytes = b"",
        *,
        status: int = 200,
        content_type: str | None = None,
    ) -> HttpResponse:
        return HttpResponse(content, status=status, content_type=content_type)

    def test_normal_response(self) -> None:
        """A plain 200 response is cacheable."""
        assert should_store(self._response(b"hello"))

    def test_non_200(self) -> None:
        """Only 200 responses are cached."""
        assert not should_store(self._response(b"missing", status=404))

    def test_streaming(self) -> None:
        """Streaming responses are not cached."""
        assert not should_store(StreamingHttpResponse([b"hello"]))

    def test_cookie(self) -> None:
        """Responses that set cookies are not cached."""
        response = self._response(b"hello")
        response.set_cookie("sessionid", "abc")
        assert not should_store(response)

    def test_private_cache_control(self) -> None:
        """Private responses are not cached."""
        response = self._response(b"hello")
        response.headers["Cache-Control"] = "private, max-age=60"
        assert not should_store(response)

    def test_no_store_cache_control(self) -> None:
        """no-store responses are not cached."""
        response = self._response(b"hello")
        response.headers["Cache-Control"] = "no-store"
        assert not should_store(response)

    def test_vary_star(self) -> None:
        """Vary: * responses are not cached."""
        response = self._response(b"hello")
        response.headers["Vary"] = "*"
        assert not should_store(response)

    @override_settings(PAGE_CACHE_MAX_BODY=4)
    def test_body_too_large(self) -> None:
        """Oversized responses are not cached."""
        assert not should_store(self._response(b"hello"))

    def test_already_encoded_response(self) -> None:
        """Already encoded responses are not re-encoded."""
        response = self._response(b"hello")
        response.headers["Content-Encoding"] = "br"
        assert not should_store(response)


class TestRequestBypass:
    """Request eligibility tests."""

    def test_normal_request(self) -> None:
        """A plain GET request may use the cache."""
        assert not request_bypasses_cache(_request("/twitch/"))

    def test_authorization_header(self) -> None:
        """Authenticated requests bypass the cache."""
        request = RequestFactory().get(
            "/twitch/",
            headers={"Authorization": "Bearer x"},
        )
        assert request_bypasses_cache(request)

    def test_session_cookie(self) -> None:
        """Requests with a session cookie bypass the cache."""
        request = _request("/twitch/", cookies={settings.SESSION_COOKIE_NAME: "abc"})
        assert request_bypasses_cache(request)

    def test_no_store(self) -> None:
        """Client no-store requests bypass the cache."""
        request = RequestFactory().get(
            "/twitch/",
            headers={"Cache-Control": "no-store"},
        )
        assert request_bypasses_cache(request)

    def test_no_cache(self) -> None:
        """Client no-cache requests bypass the cache."""
        request = RequestFactory().get(
            "/twitch/",
            headers={"Cache-Control": "no-cache"},
        )
        assert request_bypasses_cache(request)


class TestAcceptEncoding:
    """Gzip negotiation tests."""

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("", False),
            ("gzip", True),
            ("br, gzip;q=0.5", True),
            ("*", True),
            ("gzip;q=0", False),
            ("*;q=0", False),
            ("br", False),
        ],
    )
    def test_accepts_gzip(self, header: str, expected: bool) -> None:
        """Accept-Encoding is parsed with quality values."""
        request = RequestFactory().get("/twitch/", headers={"Accept-Encoding": header})
        assert client_accepts_gzip(request) is expected


class TestCacheStorage:
    """Low-level storage and fail-open behavior."""

    @override_settings(PAGE_CACHE_ENABLED=True)
    def test_bump_version_fails_open(self) -> None:
        """A cache write failure during invalidation does not raise."""
        with patch.object(
            caches["default"],
            "set_many",
            side_effect=RuntimeError("redis down"),
        ):
            bump_version("twitch")

    def test_read_versions_fails_open(self) -> None:
        """A cache read failure falls back to version zero."""
        with patch.object(
            caches["default"],
            "get_many",
            side_effect=RuntimeError("redis down"),
        ):
            assert read_versions(("twitch",)) == {"twitch": "0"}

    def test_load_page_discards_unknown_entry_shape(self) -> None:
        """Unknown entry shapes are deleted and treated as misses."""
        caches["default"].set("unknown-shape", "garbage")
        assert load_page("unknown-shape") is None
        assert caches["default"].get("unknown-shape") is None

    def test_store_page_uses_ttl(self) -> None:
        """Stored entries expire after the route TTL."""
        response = HttpResponse(b"hello")
        with patch.object(caches["default"], "set") as set_mock:
            assert store_page("key", response, 60)
        assert set_mock.call_args.kwargs["timeout"] == 60

    def test_store_page_strips_transport_headers(self) -> None:
        """Transport headers are not replayed from the cache."""
        response = HttpResponse(b"hello")
        response.headers["X-Cache"] = "MISS"
        response.headers["Content-Length"] = "5"
        assert store_page("key", response, 60)
        entry = caches["default"].get("key")
        assert isinstance(entry, CachedPage)
        assert "x-cache" not in {name.lower() for name in entry.headers}
        assert "content-length" not in {name.lower() for name in entry.headers}

    def test_render_cached_page_handles_corrupt_gzip(self) -> None:
        """Corrupt gzip bodies are treated as misses."""
        entry = CachedPage(body=b"not-gzip", status=200, headers={}, gzipped=True)
        route = resolve_route("/about/")
        assert route is not None
        request = RequestFactory().get("/about/")
        assert render_cached_page(entry, route[0], request) is None


@override_settings(
    PAGE_CACHE_ENABLED=True,
    ROOT_URLCONF="core.tests.cache_urls",
)
class TestMiddleware(TestCase):
    """End-to-end middleware tests using a minimal URLconf."""

    def setUp(self) -> None:
        """Reset the static view call log."""
        cache_urls.calls.clear()

    def test_hit_after_miss(self) -> None:
        """The second request is served from the cache."""
        first = self.client.get("/about/")
        assert first.status_code == 200
        assert first["X-Cache"] == "MISS"
        assert first["Cache-Control"] == "public, max-age=86400"
        assert "Accept-Encoding" in first["Vary"]
        assert "Cookie" in first["Vary"]
        assert len(cache_urls.calls) == 1

        second = self.client.get("/about/")
        assert second.status_code == 200
        assert second["X-Cache"] == "HIT"
        assert second.content == first.content
        assert len(cache_urls.calls) == 1

    def test_gzip_round_trip(self) -> None:
        """A gzip client receives the same body, compressed."""
        first = self.client.get("/about/")
        compressed = self.client.get("/about/", headers={"Accept-Encoding": "gzip"})
        assert compressed["X-Cache"] == "HIT"
        assert compressed["Content-Encoding"] == "gzip"
        assert gzip.decompress(compressed.content) == first.content

    def test_identity_client_after_gzip_store(self) -> None:
        """A non-gzip client gets the decompressed body from a gzip store."""
        self.client.get("/about/")
        plain = self.client.get("/about/")
        assert plain["X-Cache"] == "HIT"
        assert "Content-Encoding" not in plain.headers

    def test_unknown_query_bypasses(self) -> None:
        """A query string that cannot be cached bypasses the cache."""
        first = self.client.get("/about/?search=x")
        assert first.status_code == 200
        assert first["X-Cache"] == "BYPASS"
        second = self.client.get("/about/?search=x")
        assert second["X-Cache"] == "BYPASS"

    def test_non_200_not_cached(self) -> None:
        """A non-200 response is served uncached without public cache headers."""
        first = self.client.get("/twitch/missing/")
        assert first.status_code == 404
        assert first["X-Cache"] == "BYPASS"
        assert "Cache-Control" not in first.headers

        second = self.client.get("/twitch/missing/")
        assert second.status_code == 404
        assert second["X-Cache"] == "BYPASS"
        assert len(cache_urls.calls) == 2

    def test_timezones_are_cached_separately(self) -> None:
        """Each timezone cookie gets its own cache entry."""
        oslo_headers = {"Cookie": "timezone=Europe/Oslo"}
        oslo = self.client.get("/about/", headers=oslo_headers)
        utc = self.client.get("/about/")
        assert oslo["X-Cache"] == "MISS"
        assert utc["X-Cache"] == "MISS"

        assert self.client.get("/about/", headers=oslo_headers)["X-Cache"] == "HIT"
        assert self.client.get("/about/")["X-Cache"] == "HIT"
        assert len(cache_urls.calls) == 2

    def test_release_change_invalidates(self) -> None:
        """Changing the release serves a fresh response."""
        assert self.client.get("/about/")["X-Cache"] == "MISS"

        with self.settings(PAGE_CACHE_RELEASE="next-release"):
            assert self.client.get("/about/")["X-Cache"] == "MISS"

        assert len(cache_urls.calls) == 2

    def test_tracking_parameters_share_an_entry(self) -> None:
        """Tracking parameters do not fragment the cache."""
        first = self.client.get("/about/?utm_source=newsletter")
        second = self.client.get("/about/")
        assert first["X-Cache"] == "MISS"
        assert second["X-Cache"] == "HIT"
        assert len(cache_urls.calls) == 1

    def test_excluded_route_has_no_cache_header(self) -> None:
        """Excluded routes bypass the cache silently."""
        first = self.client.get("/search/")
        second = self.client.get("/search/")
        assert "X-Cache" not in first.headers
        assert "X-Cache" not in second.headers
        assert len(cache_urls.calls) == 2

    def test_post_is_not_cached(self) -> None:
        """Only GET requests use the cache."""
        first = self.client.post("/about/")
        second = self.client.post("/about/")
        assert "X-Cache" not in first.headers
        assert "X-Cache" not in second.headers
        assert len(cache_urls.calls) == 2

    def test_platform_change_invalidates_list_page(self) -> None:
        """A campaign write invalidates the platform list page."""
        game = Game.objects.create(twitch_id="game-1")
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="First",
            game=game,
        )
        assert self.client.get("/twitch/")["X-Cache"] == "MISS"
        assert self.client.get("/twitch/")["X-Cache"] == "HIT"

        with TestCase.captureOnCommitCallbacks(execute=True):
            campaign.name = "First updated"
            campaign.save()

        assert self.client.get("/twitch/")["X-Cache"] == "MISS"

    def test_campaign_details_are_invalidated_independently(self) -> None:
        """A campaign write only invalidates that campaign's detail page."""
        game = Game.objects.create(twitch_id="game-1")
        first = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="First",
            game=game,
        )
        second = DropCampaign.objects.create(
            twitch_id="campaign-2",
            name="Second",
            game=game,
        )
        first_url = f"/twitch/campaigns/{first.twitch_id}/"
        second_url = f"/twitch/campaigns/{second.twitch_id}/"
        assert self.client.get(first_url)["X-Cache"] == "MISS"
        assert self.client.get(second_url)["X-Cache"] == "MISS"

        with TestCase.captureOnCommitCallbacks(execute=True):
            second.name = "Second updated"
            second.save()

        assert self.client.get(first_url)["X-Cache"] == "HIT"
        assert self.client.get(second_url)["X-Cache"] == "MISS"

    def test_shared_change_invalidates_all_campaign_details(self) -> None:
        """A shared data change invalidates every campaign detail page."""
        game = Game.objects.create(twitch_id="game-1")
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="First",
            game=game,
        )
        url = f"/twitch/campaigns/{campaign.twitch_id}/"
        assert self.client.get(url)["X-Cache"] == "MISS"
        assert self.client.get(url)["X-Cache"] == "HIT"

        with TestCase.captureOnCommitCallbacks(execute=True):
            Channel.objects.create(twitch_id="channel-1", name="Channel")

        assert self.client.get(url)["X-Cache"] == "MISS"

    def test_cache_error_fails_open(self) -> None:
        """A cache failure serves the request uncached."""
        with patch("core.middleware.read_versions", side_effect=RuntimeError("boom")):
            response = self.client.get("/about/")
        assert response.status_code == 200
        assert response["X-Cache"] == "BYPASS"

    def test_backend_error_fails_open(self) -> None:
        """A backend error serves the request uncached."""
        with patch.object(
            caches["default"],
            "get_many",
            side_effect=RuntimeError("redis down"),
        ):
            response = self.client.get("/about/")
        assert response.status_code == 200
        assert response["X-Cache"] == "MISS"

    @override_settings(PAGE_CACHE_ENABLED=False)
    def test_disabled(self) -> None:
        """The middleware is a no-op when disabled."""
        first = self.client.get("/about/")
        assert "X-Cache" not in first.headers
        second = self.client.get("/about/")
        assert "X-Cache" not in second.headers


@override_settings(PAGE_CACHE_ENABLED=True)
class TestInvalidation(TestCase):
    """Signal-driven invalidation tests."""

    def test_campaign_save_bumps_campaign_and_list(self) -> None:
        """Creating a campaign bumps Twitch lists and that campaign."""
        game = Game.objects.create(twitch_id="game-1")
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            campaign = DropCampaign.objects.create(
                twitch_id="campaign-1",
                name="Campaign",
                game=game,
            )

        versions = read_versions(
            (
                "twitch",
                "twitch:shared",
                f"twitch:campaign:{campaign.twitch_id}",
                "kick",
                "chzzk",
            ),
        )
        assert versions["twitch"] != "0"
        assert versions[f"twitch:campaign:{campaign.twitch_id}"] != "0"
        assert versions["twitch:shared"] == "0"
        assert versions["kick"] == "0"
        assert versions["chzzk"] == "0"

    def test_time_based_drop_bumps_its_campaign(self) -> None:
        """Saving a drop bumps the owning campaign."""
        game = Game.objects.create(twitch_id="game-1")
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="Campaign",
            game=game,
        )
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            TimeBasedDrop.objects.create(
                twitch_id="drop-1",
                name="Drop",
                campaign=campaign,
            )

        versions = read_versions(
            (
                "twitch",
                f"twitch:campaign:{campaign.twitch_id}",
                "twitch:shared",
            ),
        )
        assert versions["twitch"] != "0"
        assert versions[f"twitch:campaign:{campaign.twitch_id}"] != "0"
        assert versions["twitch:shared"] == "0"

    def test_reward_bumps_its_reward_campaign(self) -> None:
        """Saving a reward bumps its reward campaign."""
        reward_campaign = RewardCampaign.objects.create(
            twitch_id="reward-campaign-1",
            name="Reward campaign",
        )
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            Reward.objects.create(
                twitch_id="reward-1",
                name="Reward",
                reward_campaign=reward_campaign,
            )

        versions = read_versions(
            ("twitch", f"twitch:reward:{reward_campaign.twitch_id}"),
        )
        assert versions["twitch"] != "0"
        assert versions[f"twitch:reward:{reward_campaign.twitch_id}"] != "0"

    def test_benefit_edge_bumps_shared(self) -> None:
        """Benefit edges bump the shared Twitch scope."""
        game = Game.objects.create(twitch_id="game-1")
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="Campaign",
            game=game,
        )
        drop = TimeBasedDrop.objects.create(
            twitch_id="drop-1",
            name="Drop",
            campaign=campaign,
        )
        benefit = DropBenefit.objects.create(twitch_id="benefit-1")
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            DropBenefitEdge.objects.create(drop=drop, benefit=benefit)

        versions = read_versions(
            (
                "twitch",
                "twitch:shared",
                f"twitch:campaign:{campaign.twitch_id}",
            ),
        )
        assert versions["twitch"] != "0"
        assert versions["twitch:shared"] != "0"
        assert versions[f"twitch:campaign:{campaign.twitch_id}"] == "0"

    def test_allow_channels_m2m_bumps_campaign(self) -> None:
        """Adding an allowed channel bumps the campaign."""
        game = Game.objects.create(twitch_id="game-1")
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="Campaign",
            game=game,
        )
        channel = Channel.objects.create(twitch_id="channel-1", name="Channel")
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            campaign.allow_channels.add(channel)

        versions = read_versions(
            (
                "twitch",
                f"twitch:campaign:{campaign.twitch_id}",
                "twitch:shared",
            ),
        )
        assert versions["twitch"] != "0"
        assert versions[f"twitch:campaign:{campaign.twitch_id}"] != "0"
        assert versions["twitch:shared"] == "0"

    def test_kick_campaign_bumps_only_kick(self) -> None:
        """A Kick write does not invalidate Twitch or Chzzk."""
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            campaign = KickDropCampaign.objects.create(
                kick_id="kick-1",
                name="Kick campaign",
            )

        versions = read_versions(
            (
                "kick",
                f"kick:campaign:{campaign.kick_id}",
                "twitch",
                "chzzk",
            ),
        )
        assert versions["kick"] != "0"
        assert versions[f"kick:campaign:{campaign.kick_id}"] != "0"
        assert versions["twitch"] == "0"
        assert versions["chzzk"] == "0"

    def test_kick_reward_bumps_its_campaign(self) -> None:
        """Saving a Kick reward bumps its campaign."""
        campaign = KickDropCampaign.objects.create(
            kick_id="kick-1",
            name="Kick campaign",
        )
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            KickReward.objects.create(
                kick_id="kick-reward-1",
                name="Kick reward",
                campaign=campaign,
            )

        versions = read_versions(
            ("kick", f"kick:campaign:{campaign.kick_id}"),
        )
        assert versions["kick"] != "0"
        assert versions[f"kick:campaign:{campaign.kick_id}"] != "0"

    def test_chzzk_campaign_and_reward_bump_campaign(self) -> None:
        """Chzzk writes bump the campaign and platform scopes."""
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            campaign = ChzzkCampaign.objects.create(
                campaign_no=42,
                title="Chzzk campaign",
                description="",
                category_type="",
                category_id="",
                category_value="",
                service_id="",
                state="active",
                start_date=datetime.now(UTC),
                end_date=datetime.now(UTC),
                has_ios_based_reward=False,
                drops_campaign_not_started=False,
                source_api="test",
            )

        versions = read_versions(
            ("chzzk", f"chzzk:campaign:{campaign.campaign_no}", "twitch"),
        )
        assert versions["chzzk"] != "0"
        assert versions[f"chzzk:campaign:{campaign.campaign_no}"] != "0"
        assert versions["twitch"] == "0"

        caches["default"].clear()
        with TestCase.captureOnCommitCallbacks(execute=True):
            ChzzkReward.objects.create(
                campaign=campaign,
                reward_no=1,
                title="Reward",
                reward_type="",
                condition_type="",
                condition_for_minutes=0,
                ios_based_reward=False,
                code_remaining_count=0,
            )

        versions = read_versions(
            ("chzzk", f"chzzk:campaign:{campaign.campaign_no}"),
        )
        assert versions["chzzk"] != "0"
        assert versions[f"chzzk:campaign:{campaign.campaign_no}"] != "0"

    def test_shared_model_bumps_shared_scope(self) -> None:
        """A shared Twitch model bumps the shared and platform scopes."""
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            Channel.objects.create(twitch_id="channel-1", name="Channel")

        versions = read_versions(("twitch", "twitch:shared"))
        assert versions["twitch"] != "0"
        assert versions["twitch:shared"] != "0"

    def test_kick_shared_model_bumps_only_kick(self) -> None:
        """A shared Kick model does not invalidate Twitch or Chzzk."""
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            KickOrganization.objects.create(kick_id="org-1", name="Org")

        versions = read_versions(("kick", "kick:shared", "twitch", "chzzk"))
        assert versions["kick"] != "0"
        assert versions["kick:shared"] != "0"
        assert versions["twitch"] == "0"
        assert versions["chzzk"] == "0"

    def test_campaign_delete_bumps_campaign_and_list(self) -> None:
        """Deleting a campaign invalidates its detail page and the list."""
        game = Game.objects.create(twitch_id="game-1")
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-1",
            name="Campaign",
            game=game,
        )
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            campaign.delete()

        versions = read_versions(("twitch", "twitch:campaign:campaign-1"))
        assert versions["twitch"] != "0"
        assert versions["twitch:campaign:campaign-1"] != "0"

    @override_settings(PAGE_CACHE_ENABLED=False)
    def test_disabled_does_not_bump(self) -> None:
        """No versions are written while the cache is disabled."""
        game = Game.objects.create(twitch_id="game-1")
        caches["default"].clear()

        with TestCase.captureOnCommitCallbacks(execute=True):
            DropCampaign.objects.create(
                twitch_id="campaign-1",
                name="Campaign",
                game=game,
            )

        versions = read_versions(("twitch", "twitch:campaign:campaign-1"))
        assert versions == {
            "twitch": "0",
            "twitch:campaign:campaign-1": "0",
        }
