from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import ParseResult
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.core.management.base import CommandParser

from twitch.models import Game
from twitch.utils import is_twitch_box_art_url
from twitch.utils import normalize_twitch_box_art_url

if TYPE_CHECKING:
    from django.db.models import QuerySet


class Command(BaseCommand):
    """Download and cache Twitch game box art locally."""

    help = "Download and cache Twitch game box art locally."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command arguments."""
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit the number of games to process.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-download even if a local box art file already exists.",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Download Twitch box art images for all games."""
        limit_value: object | None = options.get("limit")
        limit: int | None = limit_value if isinstance(limit_value, int) else None
        force: bool = bool(options.get("force"))

        queryset: QuerySet[Game] = Game.objects.all().order_by("twitch_id")
        if limit:
            queryset = queryset[:limit]

        total: int = queryset.count()
        downloaded: int = 0
        skipped: int = 0
        failed: int = 0
        placeholders_404: int = 0

        with httpx.Client(timeout=20, follow_redirects=True) as client:
            for game in queryset:
                if not game.box_art:
                    skipped += 1
                    continue
                if not is_twitch_box_art_url(game.box_art):
                    skipped += 1
                    continue
                if game.box_art_file and getattr(game.box_art_file, "name", "") and not force:
                    skipped += 1
                    continue

                normalized_url: str = normalize_twitch_box_art_url(game.box_art)
                parsed_url: ParseResult = urlparse(normalized_url)
                suffix: str = Path(parsed_url.path).suffix or ".jpg"
                file_name: str = f"{game.twitch_id}{suffix}"

                try:
                    response: httpx.Response = client.get(normalized_url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    failed += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"Failed to download box art for {game.twitch_id}: {exc}",
                        ),
                    )
                    continue

                if response.url.path.endswith("/ttv-static/404_boxart.jpg"):
                    placeholders_404 += 1
                    skipped += 1
                    continue

                game.box_art_file.save(file_name, ContentFile(response.content), save=True)
                downloaded += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} games. Downloaded: {downloaded}, skipped: {skipped}, "
                f"404 placeholders: {placeholders_404}, failed: {failed}.",
            ),
        )
        box_art_dir: Path = Path(settings.MEDIA_ROOT) / "games" / "box_art"
        self.stdout.write(self.style.SUCCESS(f"Saved box art to: {box_art_dir}"))
