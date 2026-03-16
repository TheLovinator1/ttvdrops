from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image

from twitch.models import Game
from twitch.utils import is_twitch_box_art_url
from twitch.utils import normalize_twitch_box_art_url

if TYPE_CHECKING:
    from urllib.parse import ParseResult

    from django.core.management.base import CommandParser
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

    def handle(self, *_args: object, **options: object) -> None:  # noqa: PLR0914
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
                if (
                    game.box_art_file
                    and getattr(game.box_art_file, "name", "")
                    and not force
                ):
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

                if game.box_art_file is None:
                    failed += 1
                    continue

                game.box_art_file.save(
                    file_name,
                    ContentFile(response.content),
                    save=True,
                )

                # Auto-convert to WebP and AVIF
                box_art_path: str | None = getattr(game.box_art_file, "path", None)
                if box_art_path:
                    self._convert_to_modern_formats(box_art_path)

                downloaded += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} games. Downloaded: {downloaded}, skipped: {skipped}, "
                f"404 placeholders: {placeholders_404}, failed: {failed}.",
            ),
        )
        box_art_dir: Path = Path(settings.MEDIA_ROOT) / "games" / "box_art"
        self.stdout.write(self.style.SUCCESS(f"Saved box art to: {box_art_dir}"))

    def _convert_to_modern_formats(self, image_path: str) -> None:
        """Convert downloaded image to WebP and AVIF formats.

        Args:
            image_path: Absolute path to the downloaded image file
        """
        try:
            source_path = Path(image_path)
            if not source_path.exists() or source_path.suffix.lower() not in {
                ".jpg",
                ".jpeg",
                ".png",
            }:
                return

            base_path = source_path.with_suffix("")
            webp_path = base_path.with_suffix(".webp")
            avif_path = base_path.with_suffix(".avif")

            with Image.open(source_path) as img:
                # Convert to RGB if needed
                if img.mode in {"RGBA", "LA"} or (
                    img.mode == "P" and "transparency" in img.info
                ):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    rgba_img = img.convert("RGBA") if img.mode == "P" else img
                    background.paste(
                        rgba_img,
                        mask=rgba_img.split()[-1]
                        if rgba_img.mode in {"RGBA", "LA"}
                        else None,
                    )
                    rgb_img = background
                elif img.mode != "RGB":
                    rgb_img = img.convert("RGB")
                else:
                    rgb_img = img

                # Save WebP
                rgb_img.save(webp_path, "WEBP", quality=85, method=6)

                # Save AVIF
                rgb_img.save(avif_path, "AVIF", quality=85, speed=4)

        except (OSError, ValueError) as e:
            # Don't fail the download if conversion fails
            self.stdout.write(
                self.style.WARNING(f"Failed to convert {image_path}: {e}"),
            )
