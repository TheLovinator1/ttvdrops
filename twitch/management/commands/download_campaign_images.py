"""Management command to download and cache campaign, benefit, and reward images locally."""

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

from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import RewardCampaign

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.db.models.fields.files import FieldFile


class Command(BaseCommand):
    """Download and cache campaign, benefit, and reward images locally."""

    help = "Download and cache campaign, benefit, and reward images locally."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command arguments."""
        parser.add_argument(
            "--model",
            type=str,
            choices=["campaigns", "benefits", "rewards", "all"],
            default="all",
            help="Which model to download images for (campaigns, benefits, rewards, or all).",
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

    def handle(self, *_args: object, **options: object) -> None:
        """Download images for campaigns, benefits, and/or rewards."""
        model_choice: str = str(options.get("model", "all"))
        limit_value: object | None = options.get("limit")
        limit: int | None = limit_value if isinstance(limit_value, int) else None
        force: bool = bool(options.get("force"))

        total_stats: dict[str, int] = {
            "total": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "placeholders_404": 0,
        }

        with httpx.Client(timeout=20, follow_redirects=True) as client:
            if model_choice in {"campaigns", "all"}:
                self.stdout.write(self.style.MIGRATE_HEADING("\nProcessing Drop Campaigns..."))
                stats = self._download_campaign_images(client=client, limit=limit, force=force)
                self._merge_stats(total_stats, stats)
                self._print_stats("Drop Campaigns", stats)

            if model_choice in {"benefits", "all"}:
                self.stdout.write(self.style.MIGRATE_HEADING("\nProcessing Drop Benefits..."))
                stats = self._download_benefit_images(client=client, limit=limit, force=force)
                self._merge_stats(total_stats, stats)
                self._print_stats("Drop Benefits", stats)

            if model_choice in {"rewards", "all"}:
                self.stdout.write(self.style.MIGRATE_HEADING("\nProcessing Reward Campaigns..."))
                stats = self._download_reward_campaign_images(client=client, limit=limit, force=force)
                self._merge_stats(total_stats, stats)
                self._print_stats("Reward Campaigns", stats)

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
    ) -> dict[str, int]:
        """Download DropCampaign images.

        Returns:
            Dictionary with download statistics (total, downloaded, skipped, failed, placeholders_404).
        """
        queryset: QuerySet[DropCampaign] = DropCampaign.objects.all().order_by("twitch_id")
        if limit:
            queryset = queryset[:limit]

        stats: dict[str, int] = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "placeholders_404": 0}
        stats["total"] = queryset.count()

        for campaign in queryset:
            if not campaign.image_url:
                stats["skipped"] += 1
                continue
            if campaign.image_file and getattr(campaign.image_file, "name", "") and not force:
                stats["skipped"] += 1
                continue

            result = self._download_image(
                client,
                campaign.image_url,
                campaign.twitch_id,
                campaign.image_file,
            )
            stats[result] += 1

        return stats

    def _download_benefit_images(
        self,
        client: httpx.Client,
        limit: int | None,
        *,
        force: bool,
    ) -> dict[str, int]:
        """Download DropBenefit images.

        Returns:
            Dictionary with download statistics (total, downloaded, skipped, failed, placeholders_404).
        """
        queryset: QuerySet[DropBenefit] = DropBenefit.objects.all().order_by("twitch_id")
        if limit:
            queryset = queryset[:limit]

        stats: dict[str, int] = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "placeholders_404": 0}
        stats["total"] = queryset.count()

        for benefit in queryset:
            if not benefit.image_asset_url:
                stats["skipped"] += 1
                continue
            if benefit.image_file and getattr(benefit.image_file, "name", "") and not force:
                stats["skipped"] += 1
                continue

            result = self._download_image(
                client,
                benefit.image_asset_url,
                benefit.twitch_id,
                benefit.image_file,
            )
            stats[result] += 1

        return stats

    def _download_reward_campaign_images(
        self,
        client: httpx.Client,
        limit: int | None,
        *,
        force: bool,
    ) -> dict[str, int]:
        """Download RewardCampaign images.

        Returns:
            Dictionary with download statistics (total, downloaded, skipped, failed, placeholders_404).
        """
        queryset: QuerySet[RewardCampaign] = RewardCampaign.objects.all().order_by("twitch_id")
        if limit:
            queryset = queryset[:limit]

        stats: dict[str, int] = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "placeholders_404": 0}
        stats["total"] = queryset.count()

        for reward_campaign in queryset:
            if not reward_campaign.image_url:
                stats["skipped"] += 1
                continue
            if reward_campaign.image_file and getattr(reward_campaign.image_file, "name", "") and not force:
                stats["skipped"] += 1
                continue

            result = self._download_image(
                client,
                reward_campaign.image_url,
                reward_campaign.twitch_id,
                reward_campaign.image_file,
            )
            stats[result] += 1

        return stats

    def _download_image(
        self,
        client: httpx.Client,
        image_url: str,
        twitch_id: str,
        file_field: FieldFile,
    ) -> str:
        """Download a single image and save it to the file field.

        Args:
            client: httpx.Client instance for making requests.
            image_url: URL of the image to download.
            twitch_id: Twitch ID to use in filename.
            file_field: Django FileField to save the image to.

        Returns:
            Status string: 'downloaded', 'skipped', 'failed', or 'placeholders_404'.
        """
        parsed_url: ParseResult = urlparse(image_url)
        suffix: str = Path(parsed_url.path).suffix or ".jpg"
        file_name: str = f"{twitch_id}{suffix}"

        try:
            response: httpx.Response = client.get(image_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"Failed to download image for {twitch_id}: {exc}",
                ),
            )
            return "failed"

        # Check for 404 placeholder images (common pattern on Twitch)
        if "/ttv-static/404_" in str(response.url.path):
            return "placeholders_404"

        # Save the image to the FileField
        if hasattr(file_field, "save"):
            file_field.save(file_name, ContentFile(response.content), save=True)
            return "downloaded"

        return "failed"

    def _merge_stats(self, total: dict[str, int], new: dict[str, int]) -> None:
        """Merge statistics from a single model into the total stats."""
        for key in ["total", "downloaded", "skipped", "failed", "placeholders_404"]:
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
        if stats["downloaded"] > 0:
            media_path = Path(settings.MEDIA_ROOT)
            if "Campaigns" in model_name and "Reward" not in model_name:
                image_dir = media_path / "campaigns" / "images"
            elif "Benefits" in model_name:
                image_dir = media_path / "benefits" / "images"
            else:
                image_dir = media_path / "reward_campaigns" / "images"
            self.stdout.write(self.style.SUCCESS(f"Saved images to: {image_dir}"))
