"""Publish tracked drops to Bluesky over the AT Protocol XRPC API.

The Bluesky ``createdAt`` field is supplied by the client, which makes it
possible to backfill old drops with their original discovery timestamps. See
https://atproto.com/blog/create-post and https://docs.bsky.app/docs/advanced-guides/timestamps.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import json
import logging
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import UTC
from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.mcp import DropDetail
from core.mcp import load_drop_detail
from core.models import BlueskyPost
from kick.models import KickDropCampaign
from twitch.models import DropCampaign

if TYPE_CHECKING:
    from collections.abc import Generator

logger: logging.Logger = logging.getLogger("ttvdrops.bluesky")

MAX_POST_GRAPHEMES = 300
BLUESKY_PLATFORMS: tuple[str, ...] = ("twitch", "kick")
SESSION_CACHE_KEY = "bluesky:session"
SESSION_CACHE_TIMEOUT = 24 * 60 * 60
TOKEN_SKEW_SECONDS = 60
_REGIONAL_INDICATOR_START = 0x1F1E6
_REGIONAL_INDICATOR_END = 0x1F1FF
_HTTP_SERVER_ERROR_LIMIT = 600

_PLATFORM_LABELS: dict[str, str] = {
    "twitch": "Twitch",
    "kick": "Kick",
}
_URL_RE: re.Pattern[str] = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCTUATION = ".,;:!?)]}'\""

_BLUESKY_SUPPRESSED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "bluesky_posts_suppressed",
    default=False,
)


class BlueskyError(RuntimeError):
    """An error returned by a Bluesky/AT Protocol endpoint."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BlueskyNotConfiguredError(BlueskyError):
    """Bluesky posting is disabled or credentials are missing."""


class BlueskyRateLimitedError(BlueskyError):
    """Bluesky returned HTTP 429 after retries were exhausted."""


@contextmanager
def suppress_bluesky_posts() -> Generator[None]:
    """Temporarily stop automatic posting when new campaigns are imported.

    Historical replay commands use this so they do not spam Bluesky with posts
    that the dedicated backfill command will publish deliberately.
    """
    token: contextvars.Token[bool] = _BLUESKY_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _BLUESKY_SUPPRESSED.reset(token)


def bluesky_posts_suppressed() -> bool:
    """Return whether automatic Bluesky posting is currently suppressed."""
    return _BLUESKY_SUPPRESSED.get()


def is_configured() -> bool:
    """Return whether credentials for posting are available."""
    return bool(settings.BLUESKY_HANDLE and settings.BLUESKY_APP_PASSWORD)


def _grapheme_count(text: str) -> int:
    """Count grapheme-like units conservatively for Bluesky's 300 limit.

    Returns:
        The approximate number of grapheme clusters in ``text``.
    """
    count: int = 0
    previous_regional: bool = False
    for char in text:
        if unicodedata.combining(char) or char in {"\u200d", "\ufe0f"}:
            continue
        codepoint: int = ord(char)
        if _REGIONAL_INDICATOR_START <= codepoint <= _REGIONAL_INDICATOR_END:
            if previous_regional:
                previous_regional = False
                continue
            previous_regional = True
        else:
            previous_regional = False
        count += 1
    return count


def _truncate(text: str, limit: int) -> str:
    """Trim text so its approximate grapheme count fits ``limit``.

    Returns:
        The original text, or a shortened version ending in an ellipsis.
    """
    if _grapheme_count(text) <= limit:
        return text
    trimmed: str = text
    while trimmed and _grapheme_count(trimmed + "…") > limit:
        trimmed = trimmed[:-1]
    return f"{trimmed.rstrip()}…"


def url_facets(text: str) -> list[dict[str, Any]]:
    """Build Bluesky link facets for HTTP(S) URLs in ``text``.

    Returns:
        One facet per URL, with UTF-8 byte offsets into ``text``.
    """
    facets: list[dict[str, Any]] = []
    for match in _URL_RE.finditer(text):
        url: str = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if not url:
            continue
        end: int = match.start() + len(url)
        facets.append(
            {
                "index": {
                    "byteStart": len(text[: match.start()].encode("utf-8")),
                    "byteEnd": len(text[:end].encode("utf-8")),
                },
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": url,
                    },
                ],
            },
        )
    return facets


def compose_drop_post(detail: DropDetail) -> tuple[str, list[dict[str, Any]]]:
    """Build the post text and facets for one drop campaign.

    Returns:
        The post text and its link facets.
    """
    label: str = _PLATFORM_LABELS.get(detail.platform, detail.platform.title())
    header: str = f"New {label} drop: "
    meta_parts: list[str] = [
        part for part in (detail.game, detail.organization) if part
    ]
    if detail.rewards:
        count: int = len(detail.rewards)
        meta_parts.append(f"{count} reward{'s' if count != 1 else ''}")
    meta: str = " • ".join(meta_parts)
    tail: str = f"\n{meta}" if meta else ""
    link: str = f"\n{detail.url}" if detail.url else ""
    room: int = max(20, MAX_POST_GRAPHEMES - _grapheme_count(header + tail + link))
    name: str = _truncate(detail.name or "Drop", room)
    text: str = f"{header}{name}{tail}{link}"
    return text, url_facets(text)


def as_utc_datetime(value: datetime | str | None) -> datetime:
    """Normalize a datetime or ISO string to an aware UTC datetime.

    Returns:
        The normalized datetime, or the current time when ``value`` is None.

    Raises:
        ValueError: If ``value`` is not a valid ISO 8601 datetime string.
    """
    if value is None:
        return timezone.now()
    parsed: datetime | None
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            msg = f"Invalid ISO 8601 datetime: {value!r}"
            raise ValueError(msg)
    else:
        parsed = value
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_created_at(value: datetime | str | None) -> str:
    """Format a datetime as an AT Protocol timestamp with a trailing ``Z``.

    Returns:
        The timestamp in UTC with millisecond precision.
    """
    return (
        as_utc_datetime(value)
        .isoformat(timespec="milliseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _token_expires_at(token: str) -> float | None:
    """Read the ``exp`` claim from a JWT without verifying its signature.

    Returns:
        The expiry as a Unix timestamp, or None if it cannot be read.
    """
    try:
        encoded: str = token.split(".")[1]
        payload: dict[str, Any] = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)),
        )
        return float(payload["exp"])
    except (
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        return None


def _token_valid(token: str | None) -> bool:
    """Return whether a session token is still usable."""
    if not token:
        return False
    expires_at: float | None = _token_expires_at(token)
    if expires_at is None:
        return True
    return expires_at > time.time() + TOKEN_SKEW_SECONDS


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Choose a backoff delay, preferring the Retry-After response header.

    Returns:
        The number of seconds to wait before the next attempt.
    """
    header: str | None = response.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return float(2**attempt)


class BlueskyClient:
    """Small XRPC client for creating Bluesky posts."""

    def __init__(
        self,
        *,
        service_url: str | None = None,
        handle: str | None = None,
        app_password: str | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.service_url: str = (service_url or settings.BLUESKY_SERVICE_URL).rstrip(
            "/",
        )
        self.handle: str = handle or settings.BLUESKY_HANDLE
        self.app_password: str = app_password or settings.BLUESKY_APP_PASSWORD
        self.timeout: float = timeout
        self.max_retries: int = max_retries
        self._owned_client: httpx.Client | None = None
        self._http_client: httpx.Client = http_client or self._new_client()

    @property
    def configured(self) -> bool:
        """Whether this client has credentials to log in with."""
        return bool(self.handle and self.app_password)

    def _new_client(self) -> httpx.Client:
        self._owned_client = httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": "ttvdrops/1.0 (+https://ttvdrops.lovinator.space)"},
        )
        return self._owned_client

    def close(self) -> None:
        """Close an internally created HTTP client."""
        if self._owned_client is not None:
            self._owned_client.close()

    def _request(
        self,
        nsid: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url: str = f"{self.service_url}/xrpc/{nsid}"
        for attempt in range(self.max_retries + 1):
            try:
                response: httpx.Response = self._http_client.post(
                    url,
                    json=payload,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    msg = f"Bluesky request to {nsid} failed: {exc}"
                    raise BlueskyError(msg) from exc
                time.sleep(float(2**attempt))
                continue

            if (
                response.status_code == HTTPStatus.TOO_MANY_REQUESTS
                or HTTPStatus.INTERNAL_SERVER_ERROR
                <= response.status_code
                < _HTTP_SERVER_ERROR_LIMIT
            ):
                if attempt >= self.max_retries:
                    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                        msg = f"Bluesky rate limited {nsid}"
                        raise BlueskyRateLimitedError(
                            msg,
                            status_code=HTTPStatus.TOO_MANY_REQUESTS,
                        ) from None
                    msg = f"Bluesky {nsid} returned {response.status_code}"
                    raise BlueskyError(msg, status_code=response.status_code) from None
                time.sleep(_retry_after(response, attempt))
                continue

            if response.status_code >= HTTPStatus.BAD_REQUEST:
                detail: str = response.text[:500]
                msg = f"Bluesky {nsid} returned {response.status_code}: {detail}"
                raise BlueskyError(msg, status_code=response.status_code) from None

            try:
                return response.json()
            except ValueError as exc:
                msg = f"Bluesky {nsid} returned invalid JSON"
                raise BlueskyError(msg) from exc

        msg = f"Bluesky request to {nsid} failed after retries"
        raise BlueskyError(msg)

    def create_session(self) -> dict[str, Any]:
        """Create a new app-password session and return its tokens.

        Returns:
            The session data, including access and refresh tokens.

        Raises:
            BlueskyNotConfiguredError: If credentials are missing.
        """
        if not self.configured:
            msg = "Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD before posting."
            raise BlueskyNotConfiguredError(msg)
        return self._request(
            "com.atproto.server.createSession",
            {"identifier": self.handle, "password": self.app_password},
        )

    def refresh_session(self, refresh_jwt: str) -> dict[str, Any]:
        """Rotate an expired access token using a refresh token.

        Returns:
            The refreshed session data.
        """
        return self._request(
            "com.atproto.server.refreshSession",
            token=refresh_jwt,
        )

    def _fresh_session(self) -> dict[str, Any]:
        session: dict[str, Any] = dict(self.create_session())
        session["handle"] = self.handle
        cache.set(SESSION_CACHE_KEY, session, SESSION_CACHE_TIMEOUT)
        return session

    def _get_session(self) -> dict[str, Any]:
        cached: dict[str, Any] | None = cache.get(SESSION_CACHE_KEY)
        if isinstance(cached, dict) and cached.get("handle") == self.handle:
            if _token_valid(cached.get("accessJwt")):
                return cached
            refresh_jwt: str | None = cached.get("refreshJwt")
            if _token_valid(refresh_jwt) and refresh_jwt:
                try:
                    refreshed: dict[str, Any] = dict(
                        self.refresh_session(refresh_jwt),
                    )
                except BlueskyError:
                    logger.debug("Could not refresh Bluesky session; logging in again")
                else:
                    refreshed["handle"] = self.handle
                    cache.set(SESSION_CACHE_KEY, refreshed, SESSION_CACHE_TIMEOUT)
                    return refreshed
        return self._fresh_session()

    def post(
        self,
        text: str,
        *,
        created_at: datetime | str | None = None,
        facets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create an ``app.bsky.feed.post`` record and return its URI and CID.

        Returns:
            The created record's ``uri`` and ``cid``.

        Raises:
            BlueskyError: If the request fails.
            BlueskyNotConfiguredError: If credentials are missing.
        """
        if not self.configured:
            msg = "Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD before posting."
            raise BlueskyNotConfiguredError(msg)

        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": format_created_at(created_at),
            "langs": ["en"],
        }
        if facets:
            record["facets"] = facets

        for attempt in range(2):
            session: dict[str, Any] = self._get_session()
            payload: dict[str, Any] = {
                "repo": session["did"],
                "collection": "app.bsky.feed.post",
                "record": record,
            }
            try:
                return self._request(
                    "com.atproto.repo.createRecord",
                    payload,
                    token=session["accessJwt"],
                )
            except BlueskyError as exc:
                if attempt == 0 and exc.status_code == HTTPStatus.UNAUTHORIZED:
                    cache.delete(SESSION_CACHE_KEY)
                    continue
                raise
        msg = "Could not create Bluesky post"
        raise BlueskyError(msg)


def _discovered_at(platform: str, drop_id: str) -> datetime:
    """Return when a campaign first appeared in the local database.

    Returns:
        The campaign's ``added_at`` value, or the current time if unknown.
    """
    if platform == "twitch":
        value = (
            DropCampaign.objects
            .filter(twitch_id=drop_id)
            .values_list("added_at", flat=True)
            .first()
        )
    elif platform == "kick":
        value = (
            KickDropCampaign.objects
            .filter(kick_id=drop_id)
            .values_list("added_at", flat=True)
            .first()
        )
    else:
        value = None
    return value or timezone.now()


def _reserve_post(
    platform: str,
    drop_id: str,
    created_at: datetime | str | None,
) -> BlueskyPost | None:
    """Create the ledger row that claims the drop for this process.

    Returns:
        The new row, or None if another process already claimed the drop.
    """
    try:
        with transaction.atomic():
            return BlueskyPost.objects.create(
                platform=platform,
                drop_id=drop_id,
                created_at=as_utc_datetime(created_at),
            )
    except IntegrityError:
        return None


def _publish_reserved_post(
    entry: BlueskyPost,
    platform: str,
    drop_id: str,
    created_at: datetime | str | None,
    client: BlueskyClient | None,
) -> BlueskyPost:
    """Post a reserved drop and update its ledger row with the result.

    Returns:
        The updated ledger row.
    """
    detail: DropDetail = load_drop_detail(platform, drop_id)
    text, facets = compose_drop_post(detail)
    effective_created_at: datetime = as_utc_datetime(
        created_at or _discovered_at(platform, drop_id),
    )
    post_client: BlueskyClient = client or BlueskyClient()
    result: dict[str, Any] = post_client.post(
        text,
        created_at=effective_created_at,
        facets=facets,
    )
    entry.uri = str(result.get("uri", ""))
    entry.cid = str(result.get("cid", ""))
    entry.text = text
    entry.created_at = effective_created_at
    entry.save(update_fields=["uri", "cid", "text", "created_at"])
    return entry


def publish_drop_to_bluesky(
    platform: str,
    drop_id: str,
    *,
    created_at: datetime | str | None = None,
    client: BlueskyClient | None = None,
) -> BlueskyPost | None:
    """Publish one drop, returning the ledger row or None if already posted.

    Returns:
        The ledger row for the new post, or None when it was skipped.

    Raises:
        BlueskyNotConfiguredError: If posting is disabled.
    """
    if not settings.BLUESKY_ENABLED:
        msg = "Bluesky posting is disabled (set BLUESKY_ENABLED=true)."
        raise BlueskyNotConfiguredError(msg)
    if bluesky_posts_suppressed():
        return None
    if platform not in BLUESKY_PLATFORMS:
        logger.debug("Skipping Bluesky post for unsupported platform %s", platform)
        return None

    key: str = str(drop_id)
    entry: BlueskyPost | None = _reserve_post(platform, key, created_at)
    if entry is None:
        return None

    try:
        return _publish_reserved_post(entry, platform, key, created_at, client)
    except Exception:
        entry.delete()
        raise
