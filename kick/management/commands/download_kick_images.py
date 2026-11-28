"""Management command to download and cache Kick reward images locally."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from kick.models import KickReward

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


class Command(BaseCommand):
    """Download and cache Kick reward images locally."""

    help = "Download and cache Kick reward images locally."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command arguments."""
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit the number of reward images to process.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-download even if a local image file already exists.",
        )

    def handle(
        self,
        *_args: str,
        **options: str | bool | int | None,
    ) -> None:
        """Download missing Kick reward images."""
        limit_value: str | bool | int | None = options.get("limit")
        limit: int | None = limit_value if isinstance(limit_value, int) else None
        force: bool = bool(options.get("force"))

        queryset = KickReward.objects.exclude(image_url="").order_by("kick_id")
        if limit:
            queryset = queryset[:limit]

        downloaded = 0
        skipped = 0
        failed = 0
        total = queryset.count()

        with httpx.Client(timeout=20, follow_redirects=True) as client:
            for reward in queryset:
                if (
                    reward.image_file
                    and getattr(reward.image_file, "name", "")
                    and not force
                ):
                    skipped += 1
                    continue

                image_url: str = reward.full_image_url
                if not image_url:
                    skipped += 1
                    continue

                try:
                    response: httpx.Response = client.get(image_url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Failed to download image for {reward.kick_id}: {exc}",
                        ),
                    )
                    failed += 1
                    continue

                suffix: str = Path(urlparse(image_url).path).suffix.lower() or ".png"
                file_name: str = f"reward-{reward.kick_id}{suffix}"
                reward.image_file.save(
                    file_name,
                    ContentFile(response.content),
                    save=True,
                )
                downloaded += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} Kick rewards. "
                f"Downloaded: {downloaded}, Skipped: {skipped}, Failed: {failed}.",
            ),
        )
