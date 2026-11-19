"""Full-page response cache helpers.

Responses are stored gzipped under keys that embed a version for every data
scope they depend on. Writers bump those version keys inside
``transaction.on_commit`` so a cached page is invalidated as soon as the data
behind it changes. Short TTLs additionally bound staleness for pages that
render relative timestamps (``timesince``/``timeuntil``).

Everything here fails open: a cache or Redis error is logged at most once per
minute and the request is served uncached.
"""

import gzip
import logging
import re
import time
import uuid
import zlib
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.http import HttpResponseBase
from django.utils.cache import patch_vary_headers

if TYPE_CHECKING:
    from re import Match
    from re import Pattern

    from django.core.cache.backends.base import BaseCache
    from django.http import HttpRequest

logger: logging.Logger = logging.getLogger("ttvdrops.cache")

_VERSION_PREFIX: str = "pcv:"
_PAGE_KEY_PREFIX: str = "pc1"
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"},
)
_TRACKING_PREFIXES: tuple[str, ...] = ("utm_",)
_ALLOWED_QUERY_PARAMS: frozenset[str] = frozenset(
    {"page", "page_size", "status", "game", "organization", "category", "limit"},
)
_SKIP_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "age",
        "cache-control",
        "content-encoding",
        "content-length",
        "expires",
        "set-cookie",
        "vary",
        "x-cache",
    },
)
_ERROR_LOG_INTERVAL: float = 60.0
_ERROR_STATE: dict[str, float] = {"last": 0.0}


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """Cache policy for one route."""

    name: str
    ttl: int
    versions: tuple[str, ...] = ()
    detail_versions: tuple[str, ...] = ()


# First match wins. Detail routes must come before their platform prefix, and
# feeds must come before the platform prefix so they get the longer TTL.
_ROUTE_TABLE: tuple[tuple[str, RouteSpec], ...] = (
    (r"^/$", RouteSpec("core-dashboard", 60, ("twitch", "kick", "chzzk"))),
    (r"^/about/$", RouteSpec("about", 86400)),
    (r"^/docs/rss/$", RouteSpec("docs-rss", 86400)),
    (r"^/datasets/$", RouteSpec("datasets", 300)),
    (r"^/youtube/", RouteSpec("youtube", 86400)),
    (r"^/sitemap\.xml$", RouteSpec("sitemap-index", 3600, ("twitch", "kick", "chzzk"))),
    (r"^/sitemap-twitch", RouteSpec("sitemap-twitch", 3600, ("twitch",))),
    (r"^/sitemap-kick\.xml$", RouteSpec("sitemap-kick", 3600, ("kick",))),
    (r"^/sitemap-(?:static|youtube)\.xml$", RouteSpec("sitemap-static", 3600)),
    (r"^/(?:rss|atom|discord)/", RouteSpec("twitch-feed", 300, ("twitch",))),
    (r"^/kick/(?:rss|atom|discord)/", RouteSpec("kick-feed", 300, ("kick",))),
    (r"^/chzzk/(?:rss|atom|discord)/", RouteSpec("chzzk-feed", 300, ("chzzk",))),
    (
        r"^/twitch/campaigns/(?P<twitch_id>[^/]+)/$",
        RouteSpec(
            "twitch-campaign-detail",
            60,
            ("twitch:shared",),
            ("twitch:campaign:{twitch_id}",),
        ),
    ),
    (
        r"^/twitch/reward-campaigns/(?P<twitch_id>[^/]+)/$",
        RouteSpec(
            "twitch-reward-detail",
            60,
            ("twitch:shared",),
            ("twitch:reward:{twitch_id}",),
        ),
    ),
    (
        r"^/kick/campaigns/(?P<kick_id>[^/]+)/$",
        RouteSpec(
            "kick-campaign-detail",
            60,
            ("kick:shared",),
            ("kick:campaign:{kick_id}",),
        ),
    ),
    (
        r"^/chzzk/campaigns/(?P<campaign_no>\d+)/$",
        RouteSpec(
            "chzzk-campaign-detail",
            60,
            ("chzzk:shared",),
            ("chzzk:campaign:{campaign_no}",),
        ),
    ),
    (r"^/twitch/", RouteSpec("twitch", 60, ("twitch",))),
    (r"^/kick/", RouteSpec("kick", 60, ("kick",))),
    (r"^/chzzk/", RouteSpec("chzzk", 60, ("chzzk",))),
    (r"^/api/v1/twitch/", RouteSpec("api-twitch", 60, ("twitch",))),
    (r"^/api/v1/kick/", RouteSpec("api-kick", 60, ("kick",))),
)

_COMPILED_ROUTES: tuple[tuple[Pattern[str], RouteSpec], ...] = tuple(
    (re.compile(pattern), spec) for pattern, spec in _ROUTE_TABLE
)

_EXCLUDED_PATHS: frozenset[str] = frozenset({"/api/v1/", "/favicon.ico", "/mcp"})
_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/__debug__/",
    "/api/v1/docs",
    "/api/v1/openapi.json",
    "/datasets/download/",
    "/debug/",
    "/media/",
    "/search/",
    "/static/",
)


@dataclass(frozen=True, slots=True)
class CachedPage:
    """A response body and the headers needed to reconstruct it."""

    body: bytes
    status: int
    headers: dict[str, str]
    gzipped: bool


def log_cache_error(operation: str, exc: BaseException) -> None:
    """Log a cache failure, rate-limited to once per minute.

    Args:
        operation: Name of the cache operation that failed.
        exc: The exception that was raised.
    """
    now: float = time.monotonic()
    if now - _ERROR_STATE["last"] < _ERROR_LOG_INTERVAL:
        return
    _ERROR_STATE["last"] = now
    logger.warning("Page cache %s failed: %s", operation, exc, exc_info=exc)


def _cache() -> BaseCache:
    """Return the configured cache backend."""
    return caches[settings.PAGE_CACHE_ALIAS]


def version_key(name: str) -> str:
    """Return the cache key that stores the version for ``name``."""
    return f"{_VERSION_PREFIX}{name}"


def read_versions(names: tuple[str, ...]) -> dict[str, str]:
    """Read current versions for ``names``; missing versions read as ``"0"``.

    Args:
        names: Version scope names to read.

    Returns:
        Mapping of version scope name to its current version value.
    """
    unique: tuple[str, ...] = tuple(dict.fromkeys(names))
    if not unique:
        return {}

    key_map: dict[str, str] = {version_key(name): name for name in unique}
    try:
        values: dict[str, str] = _cache().get_many(key_map)
    except Exception as exc:  # ruff: ignore[blind-except]
        log_cache_error("get_many", exc)
        return dict.fromkeys(unique, "0")

    return {name: values.get(version_key(name), "0") for name in unique}


def bump_version(*names: str) -> None:
    """Invalidate every page that depends on any of ``names``.

    A new random version is written for each name. Concurrent bumps can only
    over-invalidate, never serve stale data.
    """
    if not names or not settings.PAGE_CACHE_ENABLED:
        return

    unique: tuple[str, ...] = tuple(dict.fromkeys(names))
    try:
        _cache().set_many(
            {version_key(name): uuid.uuid4().hex for name in unique},
            timeout=None,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        log_cache_error("set_many", exc)


def is_excluded(path: str) -> bool:
    """Return whether ``path`` must never be cached."""
    if path in _EXCLUDED_PATHS:
        return True
    return path.startswith(_EXCLUDED_PREFIXES)


def resolve_route(path: str) -> tuple[RouteSpec, Match[str]] | None:
    """Return the cache policy and pattern match for ``path``, if any."""
    if is_excluded(path):
        return None
    for pattern, spec in _COMPILED_ROUTES:
        match: Match[str] | None = pattern.match(path)
        if match is not None:
            return spec, match
    return None


def route_version_names(spec: RouteSpec, match: Match[str]) -> tuple[str, ...]:
    """Return every version key the route depends on for this request."""
    names: list[str] = list(spec.versions)
    if spec.detail_versions:
        groups: dict[str, str] = match.groupdict()
        names.extend(template.format(**groups) for template in spec.detail_versions)
    return tuple(names)


def request_timezone_name(request: HttpRequest) -> str:
    """Return the validated request timezone, defaulting to UTC."""
    name: str | None = request.COOKIES.get("timezone")
    if not name:
        return "UTC"
    try:
        ZoneInfo(name)
    except KeyError, ValueError, TypeError:
        return "UTC"
    return name


def normalize_query(query_string: str) -> str | None:
    """Return a stable query string, or ``None`` if the query cannot be cached.

    Tracking parameters are dropped. Any other parameter that is not on the
    allowlist (notably free-text ``search``/``q``) disables caching for the
    request so bots cannot create unbounded cache keys.
    """
    if not query_string:
        return ""

    try:
        pairs: list[tuple[str, str]] = parse_qsl(
            query_string.removeprefix("?"),
            keep_blank_values=True,
            strict_parsing=False,
        )
    except ValueError:
        return None

    kept: list[tuple[str, str]] = []
    for key, value in pairs:
        if key in _TRACKING_PARAMS or key.startswith(_TRACKING_PREFIXES):
            continue
        if key not in _ALLOWED_QUERY_PARAMS:
            return None
        kept.append((key, value))

    kept.sort()
    return urlencode(kept)


def build_page_key(
    spec: RouteSpec,
    match: Match[str],
    request: HttpRequest,
    versions: dict[str, str],
) -> str | None:
    """Build the cache key for a request.

    Args:
        spec: Route policy for the matched page.
        match: Regex match for the route.
        request: The incoming request.
        versions: Current version values for the route's data scopes.

    Returns:
        The cache key, or ``None`` if the request cannot be cached.
    """
    query: str | None = normalize_query(request.META.get("QUERY_STRING", ""))
    if query is None:
        return None

    try:
        host: str = request.get_host().lower()
    except DisallowedHost:
        return None

    scope: str = ";".join(
        f"{name}={versions.get(name, '0')}" for name in route_version_names(spec, match)
    )
    release: str = re.sub(
        r"[^A-Za-z0-9._-]",
        "_",
        settings.PAGE_CACHE_RELEASE,
    )
    timezone_name: str = request_timezone_name(request)
    return f"{_PAGE_KEY_PREFIX}:{release}:{scope}:{timezone_name}:{host}{request.path}?{query}"


def load_page(key: str) -> CachedPage | None:
    """Load a cached page.

    Args:
        key: Cache key to look up.

    Returns:
        The cached page, or ``None`` on a miss or a cache error.
    """
    try:
        page: object = _cache().get(key)
    except Exception as exc:  # ruff: ignore[blind-except]
        log_cache_error("get", exc)
        return None

    if isinstance(page, CachedPage):
        return page
    if page is not None:
        # Unknown entry shape (for example after a deploy): treat as a miss.
        try:
            _cache().delete(key)
        except Exception as exc:  # ruff: ignore[blind-except]
            log_cache_error("delete", exc)
    return None


def should_store(response: HttpResponseBase) -> bool:
    """Return whether ``response`` is safe to cache."""
    if response.status_code != HTTPStatus.OK or response.streaming:
        return False
    if response.cookies or "Set-Cookie" in response.headers:
        return False
    if response.headers.get("Content-Encoding", "identity").lower() not in {
        "",
        "identity",
    }:
        return False

    cache_control: str = response.headers.get("Cache-Control", "").lower()
    if "private" in cache_control or "no-store" in cache_control:
        return False
    if response.headers.get("Vary", "").strip() == "*":
        return False

    # Streaming responses have no ``content`` attribute, but they were already
    # rejected above, so only a non-streaming response can reach this point.
    content: object = getattr(response, "content", None)
    if not isinstance(content, bytes):
        return False
    return len(content) <= settings.PAGE_CACHE_MAX_BODY


def store_page(key: str, response: HttpResponse, ttl: int) -> bool:
    """Store ``response`` under ``key``.

    Args:
        key: Cache key to store under.
        response: The rendered response.
        ttl: Lifetime of the entry in seconds.

    Returns:
        Whether the response was stored.
    """
    if not should_store(response):
        return False

    body: bytes = response.content
    gzipped: bool = settings.PAGE_CACHE_GZIP
    if gzipped:
        body = gzip.compress(body, compresslevel=6)

    headers: dict[str, str] = {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in _SKIP_RESPONSE_HEADERS
    }
    entry = CachedPage(
        body=body,
        status=response.status_code,
        headers=headers,
        gzipped=gzipped,
    )

    try:
        _cache().set(key, entry, timeout=ttl)
    except Exception as exc:  # ruff: ignore[blind-except]
        log_cache_error("set", exc)
        return False
    return True


def client_accepts_gzip(request: HttpRequest) -> bool:
    """Return whether the client accepts a gzip-encoded response."""
    header: str = request.headers.get("Accept-Encoding", "")
    for token in header.split(","):
        parts: list[str] = token.strip().split(";")
        coding: str = parts[0].strip().lower()
        if coding not in {"gzip", "*"}:
            continue
        quality: float = 1.0
        for parameter in parts[1:]:
            name, _, value = parameter.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def render_cached_page(
    entry: CachedPage,
    spec: RouteSpec,
    request: HttpRequest,
) -> HttpResponse | None:
    """Rebuild an :class:`HttpResponse` from a cache entry.

    Args:
        entry: The stored cache entry.
        spec: Route policy for the matched page.
        request: The incoming request, used to negotiate gzip.

    Returns:
        The rebuilt response, or ``None`` if the body cannot be decompressed.
    """
    body: bytes = entry.body
    headers: dict[str, str] = dict(entry.headers)
    content_type: str = headers.pop(
        "Content-Type",
        "text/html; charset=utf-8",
    )
    accepts_gzip: bool = entry.gzipped and client_accepts_gzip(request)

    if entry.gzipped and not accepts_gzip:
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as exc:
            log_cache_error("decompress", exc)
            return None

    response = HttpResponse(body, status=entry.status, content_type=content_type)
    for name, value in headers.items():
        response.headers[name] = value
    if accepts_gzip:
        response.headers["Content-Encoding"] = "gzip"
    response.headers["Cache-Control"] = f"public, max-age={spec.ttl}"
    patch_vary_headers(response, ("Accept-Encoding", "Cookie"))
    response.headers["X-Cache"] = "HIT"
    return response


def request_bypasses_cache(request: HttpRequest) -> bool:
    """Return whether the request must not read from or write to the cache."""
    if request.headers.get("Authorization"):
        return True
    session_cookie: str = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
    if session_cookie and session_cookie in request.COOKIES:
        return True
    cache_control: str = request.headers.get("Cache-Control", "").lower()
    return "no-store" in cache_control or "no-cache" in cache_control
