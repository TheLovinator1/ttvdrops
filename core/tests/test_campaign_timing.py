from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from core.campaign_timing import campaign_timing_stats


def test_campaign_timing_stats_handles_empty_history() -> None:
    """Empty campaign history returns no dates or average."""
    assert campaign_timing_stats([]) == (None, None)


def test_campaign_timing_stats_returns_last_end_and_average_gap() -> None:
    """Average gaps are measured between consecutive campaign starts."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    campaigns = [
        (start, start + timedelta(days=2)),
        (start + timedelta(days=10), start + timedelta(days=12)),
        (start + timedelta(days=30), start + timedelta(days=32)),
    ]

    last_end, average_gap = campaign_timing_stats(campaigns)

    assert last_end == start + timedelta(days=32)
    assert average_gap == pytest.approx(15.0)


def test_campaign_timing_stats_needs_two_starts_for_average() -> None:
    """A single campaign has no interval to average."""
    start = datetime(2026, 1, 1, tzinfo=UTC)

    last_end, average_gap = campaign_timing_stats([
        (start, start + timedelta(days=2)),
    ])

    assert last_end == start + timedelta(days=2)
    assert average_gap is None
