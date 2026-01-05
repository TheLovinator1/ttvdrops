from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import dateparser
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime


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
