"""Tests for display-name helpers."""

from __future__ import annotations

from twitch.utils import merge_display_names
from twitch.utils import prefer_display_name


def test_merge_display_names_keeps_localized_and_latin_names() -> None:
    """Localized display names and their login should both be retained."""
    assert merge_display_names(None, "象先輩", "zousenpai") == [
        "象先輩",
        "zousenpai",
    ]


def test_merge_display_names_upgrades_lowercase_spelling() -> None:
    """A later capitalized spelling should replace an all-lowercase entry."""
    assert merge_display_names(["ztockerstein"], "Ztockerstein") == [
        "Ztockerstein",
    ]


def test_merge_display_names_does_not_duplicate_case_variants() -> None:
    """Case-only variants should collapse to one entry."""
    assert merge_display_names(
        ["Ztockerstein"],
        "ztockerstein",
    ) == ["Ztockerstein"]


def test_prefer_display_name_keeps_non_latin_name() -> None:
    """A login fallback must not replace an uncased display name."""
    assert prefer_display_name("象先輩", "zousenpai", "zousenpai") == "象先輩"


def test_prefer_display_name_accepts_real_change() -> None:
    """A different, non-login display name should still update."""
    assert prefer_display_name("象先輩", "先輩", "zousenpai") == "先輩"
