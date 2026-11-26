from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable

MIN_STARTS_FOR_AVERAGE = 2


def campaign_timing_stats(
    campaigns: Iterable[tuple[datetime.datetime | None, datetime.datetime | None]],
) -> tuple[datetime.datetime | None, float | None]:
    """Return the last campaign end and the average gap between campaigns.

    The average gap is measured between consecutive campaign start dates,
    ignoring campaigns without a start date.

    Returns:
        A tuple of (last campaign end, average days between campaign starts).
    """
    starts: list[datetime.datetime] = []
    ends: list[datetime.datetime] = []

    for start, end in campaigns:
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)

    last_end: datetime.datetime | None = max(ends) if ends else None
    if len(starts) < MIN_STARTS_FOR_AVERAGE:
        return last_end, None

    starts.sort()
    total_gap_seconds: float = sum(
        (later - earlier).total_seconds()
        for earlier, later in itertools.pairwise(starts)
    )
    average_gap_days: float = total_gap_seconds / (len(starts) - 1) / 86400

    return last_end, average_gap_days
