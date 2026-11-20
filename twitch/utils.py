import contextvars
import re
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from urllib.parse import urlunparse

import dateparser
from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from urllib.parse import ParseResult

TWITCH_BOX_ART_HOST = "static-cdn.jtvnw.net"
TWITCH_BOX_ART_PATH_PREFIX = "/ttv-boxart/"
TWITCH_BOX_ART_SIZE_PATTERN: re.Pattern[str] = re.compile(
    r"-(\{width\}|\d+)x(\{height\}|\d+)(?=\.[A-Za-z0-9]+$)",
)

_IMAGE_TASKS_SUPPRESSED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "twitch_image_tasks_suppressed",
    default=False,
)


@contextmanager
def suppress_image_tasks() -> Generator[None]:
    """Temporarily stop dispatching and performing Twitch image work.

    Yields:
        None: Control back to the caller.
    """
    token: contextvars.Token[bool] = _IMAGE_TASKS_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _IMAGE_TASKS_SUPPRESSED.reset(token)


def image_tasks_suppressed() -> bool:
    """Return True when image downloads and task dispatch should be skipped.

    Returns:
        bool: Whether image work is currently suppressed.
    """
    return _IMAGE_TASKS_SUPPRESSED.get()


def is_twitch_box_art_url(url: str) -> bool:
    """Return True when the URL points at Twitch's box art CDN."""
    if not url:
        return False

    parsed: ParseResult = urlparse(url)
    return parsed.netloc == TWITCH_BOX_ART_HOST and parsed.path.startswith(
        TWITCH_BOX_ART_PATH_PREFIX,
    )


def normalize_twitch_box_art_url(url: str) -> str:
    """Normalize Twitch box art URLs to remove size suffixes for higher quality.

    Example:
        https://static-cdn.jtvnw.net/ttv-boxart/65654_IGDB-120x160.jpg
        -> https://static-cdn.jtvnw.net/ttv-boxart/65654_IGDB.jpg

    Args:
        url: The Twitch box art URL to normalize.

    Returns:
        The normalized Twitch box art URL without size suffixes.
    """
    if not url:
        return url

    parsed: ParseResult = urlparse(url)
    if parsed.netloc != TWITCH_BOX_ART_HOST:
        return url

    if not parsed.path.startswith(TWITCH_BOX_ART_PATH_PREFIX):
        return url

    normalized_path: str = TWITCH_BOX_ART_SIZE_PATTERN.sub("", parsed.path)
    return str(urlunparse(parsed._replace(path=normalized_path)))


def normalize_twitch_image_url(url: str) -> str:
    """Normalize Twitch CDN image URLs to the largest available variant.

    Handles box art, profile images, emotes, and VOD thumbnails.

    Args:
        url: Twitch CDN image URL.

    Returns:
        URL pointing at the highest-quality variant when possible.
    """
    if not url:
        return url

    parsed: ParseResult = urlparse(url)
    if parsed.netloc != TWITCH_BOX_ART_HOST:
        return url

    path: str = parsed.path
    if path.startswith(TWITCH_BOX_ART_PATH_PREFIX):
        path = TWITCH_BOX_ART_SIZE_PATTERN.sub("", path)
    elif "/emoticons/v1/" in path:
        path = re.sub(
            r"(/emoticons/v1/\d+/)[012][0-9.]*$",
            r"\g<1>3.0",
            path,
        )
    elif "/emoticons/v2/" in path:
        path = re.sub(
            r"(/emoticons/v2/(?:[0-9]+|emotesv2_[0-9a-f]{10,})/[^/]+/[a-z]+/)[0-4][0-9.]*$",
            r"\g<1>5.0",
            path,
        )
    elif "/thumb/thumb" in path:
        path = re.sub(
            r"(/thumb/thumb[0-9]*-)[0-9]+x[0-9]+(\.[a-z]+)$",
            r"\g<1>0x0\2",
            path,
        )
    elif "/jtv_user_pictures/" in path:
        upgraded: str = re.sub(
            r"-(?:50x50|70x70)(\.[^/.]+)$",
            r"-300x300\1",
            path,
        )
        path = (
            upgraded
            if upgraded != path
            else re.sub(r"-[0-9]+x[0-9]+(\.[^/.]+)$", r"\1", path)
        )
    else:
        path = re.sub(r"-[0-9]+x[0-9]+(\.[^/.]+)$", r"\1", path)

    return str(urlunparse(parsed._replace(path=path)))


def prefer_display_name(existing: str, incoming: str) -> str:
    """Keep a capitalized display name over a lowercase login-style fallback.

    Args:
        existing: Current display name.
        incoming: New display name.

    Returns:
        The preferred display name.
    """
    if not incoming:
        return existing
    if existing and existing != existing.lower() and incoming == incoming.lower():
        return existing
    return incoming


@lru_cache(maxsize=40 * 40 * 1024)
def parse_date(value: str) -> datetime | None:
    """Parse a datetime string into a timezone-aware datetime using dateparser.

    Args:
        value: The datetime string to parse.

    Returns:
        A timezone-aware datetime object or None if parsing fails.
    """
    dateparser_settings: dict[str, bool | int] = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "CACHE_SIZE_LIMIT": 0,
    }
    dt: datetime | None = dateparser.parse(
        date_string=value,
        settings=dateparser_settings,  # pyright: ignore[reportArgumentType]
    )
    if not dt:
        return None

    # Ensure aware in Django's current timezone
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt
