"""Management command to download and cache Chzzk campaign and reward images locally."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward

if TYPE_CHECKING:
    from django.core.management.base import CommandParser
    from django.db.models import QuerySet
    from django.db.models.fields.files import FieldFile

STAT_KEYS: tuple[str, ...] = (
    "total",
    "downloaded",
    "skipped",
    "failed",
    "placeholders_404",
)

DEFAULT_WORKERS: int = 8

# url, local file name, log label, target file field
ImageJob = tuple[str, str, str, "FieldFile | None"]


class Command(BaseCommand):
    """Download and cache Chzzk campaign and reward images locally."""

    help = "Download and cache Chzzk campaign and reward images locally."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command arguments."""
        parser.add_argument(
            "--model",
            type=str,
            choices=["campaigns", "rewards", "all"],
            default="all",
            help="Which model to download images for (campaigns, rewards, or all).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit the number of items to process per model.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-download even if a local image file already exists.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=DEFAULT_WORKERS,
            help=(
                "Number of images to download concurrently "
                f"(default: {DEFAULT_WORKERS})."
            ),
        )

    def handle(
        self,
        *_args: str,
        **options: str | bool | int | None,
    ) -> None:
        """Download images for Chzzk campaigns and/or rewards."""
        model_choice: str = str(options.get("model", "all"))
        limit_value: str | bool | int | None = options.get("limit")
        limit: int | None = limit_value if isinstance(limit_value, int) else None
        force: bool = bool(options.get("force"))
        workers_value: str | bool | int | None = options.get("workers")
        workers: int = (
            workers_value if isinstance(workers_value, int) else DEFAULT_WORKERS
        )
        workers = max(1, workers)

        total_stats: dict[str, int] = dict.fromkeys(STAT_KEYS, 0)

        with httpx.Client(timeout=20, follow_redirects=True) as client:
            if model_choice in {"campaigns", "all"}:
                self.stdout.write(
                    self.style.MIGRATE_HEADING("\nProcessing Chzzk Campaigns..."),
                )
                stats: dict[str, int] = self._download_campaign_images(
                    client=client,
                    limit=limit,
                    force=force,
                    workers=workers,
                )
                self._merge_stats(total_stats, stats)
                self._print_stats("Chzzk Campaigns", stats)

            if model_choice in {"rewards", "all"}:
                self.stdout.write(
                    self.style.MIGRATE_HEADING("\nProcessing Chzzk Rewards..."),
                )
                stats = self._download_reward_images(
                    client=client,
                    limit=limit,
                    force=force,
                    workers=workers,
                )
                self._merge_stats(total_stats, stats)
                self._print_stats("Chzzk Rewards", stats)

        if model_choice == "all":
            self.stdout.write(self.style.MIGRATE_HEADING("\nTotal Summary:"))
            self.stdout.write(
                self.style.SUCCESS(
                    f"Processed {total_stats['total']} items. "
                    f"Downloaded: {total_stats['downloaded']}, "
                    f"Skipped: {total_stats['skipped']}, "
                    f"404 placeholders: {total_stats['placeholders_404']}, "
                    f"Failed: {total_stats['failed']}.",
                ),
            )

    def _download_campaign_images(
        self,
        client: httpx.Client,
        limit: int | None,
        *,
        force: bool,
        workers: int = 1,
    ) -> dict[str, int]:
        """Download ChzzkCampaign images.

        Returns:
            Dictionary with download statistics.
        """
        queryset: QuerySet[ChzzkCampaign] = ChzzkCampaign.objects.exclude(
            image_url="",
        ).order_by("campaign_no")
        if limit:
            queryset = queryset[:limit]

        stats: dict[str, int] = dict.fromkeys(STAT_KEYS, 0)
        stats["total"] = queryset.count()
        jobs: list[ImageJob] = []

        for campaign in queryset:
            if (
                campaign.image_file
                and getattr(campaign.image_file, "name", "")
                and not force
            ):
                stats["skipped"] += 1
                continue

            file_stem: str = f"campaign-{campaign.campaign_no}"
            file_name: str = self._image_file_name(campaign.image_url, file_stem)
            jobs.append(
                (campaign.image_url, file_name, file_name, campaign.image_file),
            )

        self._download_images(client, jobs, workers, stats)
        return stats

    def _download_reward_images(
        self,
        client: httpx.Client,
        limit: int | None,
        *,
        force: bool,
        workers: int = 1,
    ) -> dict[str, int]:
        """Download ChzzkReward images.

        Returns:
            Dictionary with download statistics.
        """
        queryset: QuerySet[ChzzkReward] = (
            ChzzkReward.objects
            .exclude(image_url="")
            .select_related("campaign")
            .order_by("campaign__campaign_no", "reward_no")
        )
        if limit:
            queryset = queryset[:limit]

        stats: dict[str, int] = dict.fromkeys(STAT_KEYS, 0)
        stats["total"] = queryset.count()
        jobs: list[ImageJob] = []

        for reward in queryset:
            if (
                reward.image_file
                and getattr(reward.image_file, "name", "")
                and not force
            ):
                stats["skipped"] += 1
                continue

            file_stem: str = (
                f"campaign-{reward.campaign.campaign_no}-reward-{reward.reward_no}"
            )
            file_name: str = self._image_file_name(reward.image_url, file_stem)
            jobs.append(
                (reward.image_url, file_name, file_name, reward.image_file),
            )

        self._download_images(client, jobs, workers, stats)
        return stats

    def _download_images(
        self,
        client: httpx.Client,
        jobs: list[ImageJob],
        workers: int,
        stats: dict[str, int],
    ) -> None:
        """Fetch images concurrently and save each result as it arrives."""
        if not jobs:
            return

        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    self._fetch_image,
                    client,
                    image_url,
                    label,
                ): (file_name, file_field)
                for image_url, file_name, label, file_field in jobs
            }

            for future in as_completed(futures):
                file_name, file_field = futures[future]
                try:
                    status, content = future.result()
                except Exception as exc:  # ruff:ignore[blind-except]
                    self.stdout.write(
                        self.style.WARNING(
                            f"Failed to download image {file_name}: {exc}",
                        ),
                    )
                    stats["failed"] += 1
                    continue

                if (
                    status == "downloaded"
                    and content is not None
                    and file_field is not None
                    and not self._save_image(file_field, file_name, content)
                ):
                    status = "failed"

                stats[status] += 1

    def _fetch_image(
        self,
        client: httpx.Client,
        image_url: str,
        label: str,
    ) -> tuple[str, bytes | None]:
        """Fetch an image without touching the database.

        Returns:
            Status string and response bytes when the download succeeded.
        """
        try:
            response: httpx.Response = client.get(image_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.stdout.write(
                self.style.WARNING(f"Failed to download image for {label}: {exc}"),
            )
            return "failed", None

        if "/ttv-static/404_" in str(response.url.path):
            return "placeholders_404", None

        return "downloaded", response.content

    def _save_image(
        self,
        file_field: FieldFile,
        file_name: str,
        content: bytes,
    ) -> bool:
        """Persist downloaded image bytes to the model's image field.

        Returns:
            True when the image was saved, False otherwise.
        """
        try:
            file_field.save(file_name, ContentFile(content), save=True)
        except Exception as exc:  # ruff:ignore[blind-except]
            self.stdout.write(
                self.style.WARNING(f"Failed to save image {file_name}: {exc}"),
            )
            return False

        return True

    def _download_image(
        self,
        client: httpx.Client,
        image_url: str,
        file_stem: str,
        file_field: FieldFile | None,
    ) -> str:
        """Download a single image and save it to the file field.

        Returns:
            Status string: 'downloaded', 'skipped', 'failed', or 'placeholders_404'.
        """
        if file_field is None:
            return "failed"

        file_name: str = self._image_file_name(image_url, file_stem)
        status, content = self._fetch_image(client, image_url, file_name)
        if (
            status == "downloaded"
            and content is not None
            and not self._save_image(file_field, file_name, content)
        ):
            return "failed"

        return status

    @staticmethod
    def _image_file_name(image_url: str, file_stem: str) -> str:
        """Build a local filename from the image URL's suffix.

        Returns:
            The generated local filename.
        """
        suffix: str = Path(urlparse(image_url).path).suffix.lower() or ".jpg"
        return f"{file_stem}{suffix}"

    def _merge_stats(self, total: dict[str, int], new: dict[str, int]) -> None:
        """Merge statistics from a single model into the total stats."""
        for key in STAT_KEYS:
            total[key] += new[key]

    def _print_stats(self, model_name: str, stats: dict[str, int]) -> None:
        """Print statistics for a specific model."""
        self.stdout.write(
            self.style.SUCCESS(
                f"{model_name}: Processed {stats['total']} items. "
                f"Downloaded: {stats['downloaded']}, "
                f"Skipped: {stats['skipped']}, "
                f"404 placeholders: {stats['placeholders_404']}, "
                f"Failed: {stats['failed']}.",
            ),
        )
