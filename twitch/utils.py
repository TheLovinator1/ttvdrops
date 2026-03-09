import re
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from urllib.parse import urlunparse

import dateparser
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime
    from urllib.parse import ParseResult

TWITCH_BOX_ART_HOST = "static-cdn.jtvnw.net"
TWITCH_BOX_ART_PATH_PREFIX = "/ttv-boxart/"
TWITCH_BOX_ART_SIZE_PATTERN: re.Pattern[str] = re.compile(
    r"-(\{width\}|\d+)x(\{height\}|\d+)(?=\.[A-Za-z0-9]+$)",
)


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
    return urlunparse(parsed._replace(path=normalized_path))


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
