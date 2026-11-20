"""Tests for replacing already-downloaded images with larger variants."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest
from PIL import Image

from twitch.management.commands.replace_downloaded_images import Command
from twitch.models import Game

if TYPE_CHECKING:
    from pathlib import Path


def _png_bytes(color: str) -> bytes:
    """Return a tiny valid PNG image.

    Args:
        color: Fill color.

    Returns:
        PNG file bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.django_db
@pytest.mark.no_zeal
def test_replace_group_replaces_file_and_normalizes_url(
    tmp_path: Path,
    settings: object,
) -> None:
    """Replace a cached file with the normalized image and remove the old file."""
    settings.MEDIA_ROOT = tmp_path  # type: ignore[attr-defined]
    old_name: str = "games/box_art/old.jpg"
    old_path: Path = tmp_path / old_name
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(_png_bytes("red"))

    game: Game = Game.objects.create(
        twitch_id="replace-image-game",
        display_name="Replace Image Game",
        box_art="https://static-cdn.jtvnw.net/ttv-boxart/123_IGDB-120x160.jpg",
    )
    game.box_art_file.name = old_name
    game.save(update_fields=["box_art_file"])

    new_bytes: bytes = _png_bytes("blue")
    response = MagicMock()
    response.content = new_bytes
    response.url = urlparse("https://static-cdn.jtvnw.net/ttv-boxart/123_IGDB.jpg")
    response.headers = {"content-type": "image/png"}
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get.return_value = response
    stats: dict[str, int] = {"replaced": 0, "skipped": 0, "failed": 0}

    Command()._replace_group(
        client=client,
        items=[game],
        field_name="box_art_file",
        url_field="box_art",
        prefix="box_art",
        dry_run=False,
        include_missing=False,
        workers=1,
        stats=stats,
    )

    client.get.assert_called_once_with(
        "https://static-cdn.jtvnw.net/ttv-boxart/123_IGDB.jpg",
    )
    game.refresh_from_db()
    assert game.box_art_file.name == old_name
    assert old_path.exists()
    assert old_path.read_bytes() == new_bytes
    assert game.box_art_size_bytes == len(new_bytes)
    assert game.box_art_mime_type == "image/png"
    assert stats == {"replaced": 1, "skipped": 0, "failed": 0}


@pytest.mark.django_db
@pytest.mark.no_zeal
def test_dry_run_does_not_download(settings: object) -> None:
    """Dry runs should report without downloading or touching files."""
    game: Game = Game.objects.create(
        twitch_id="dry-run-game",
        display_name="Dry Run Game",
        box_art="https://static-cdn.jtvnw.net/ttv-boxart/456_IGDB-70x70.jpg",
    )
    client = MagicMock()
    stats: dict[str, int] = {"replaced": 0, "skipped": 0, "failed": 0}

    Command()._replace_group(
        client=client,
        items=[game],
        field_name="box_art_file",
        url_field="box_art",
        prefix="box_art",
        dry_run=True,
        include_missing=True,
        workers=1,
        stats=stats,
    )

    client.get.assert_not_called()
    assert stats["replaced"] == 1
