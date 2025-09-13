from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.management.base import BaseCommand

from twitch.models import DropBenefit, DropCampaign, Game
from twitch.utils.images import cache_remote_image

if TYPE_CHECKING:  # pragma: no cover - typing only
    from argparse import ArgumentParser

    from django.db.models import QuerySet

logger: logging.Logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill local image files for existing rows."""

    help = "Download and cache remote images to MEDIA for Games, Campaigns, and Benefits"

    def add_arguments(self, parser: ArgumentParser) -> None:  # type: ignore[override]
        """Add CLI arguments for the management command."""
        parser.add_argument("--limit", type=int, default=0, help="Limit number of objects per model to process (0 = no limit)")

    def handle(self, **options: object) -> None:
        """Execute the backfill process using provided options."""
        limit: int = int(options.get("limit", 0))  # type: ignore[arg-type]

        def maybe_limit(qs: QuerySet) -> QuerySet:
            """Apply slicing if --limit is provided.

            Returns:
                Queryset possibly sliced by the limit.
            """
            return qs[:limit] if limit > 0 else qs

        processed = 0

        for game in maybe_limit(Game.objects.filter(box_art_file__isnull=True).exclude(box_art="")):
            rel = cache_remote_image(game.box_art, "games/box_art")
            if rel:
                game.box_art_file.name = rel
                game.save(update_fields=["box_art_file"])  # type: ignore[list-item]
                processed += 1

                self.stdout.write(self.style.SUCCESS(f"Processed game box art: {game.id}"))

        for campaign in maybe_limit(DropCampaign.objects.filter(image_file__isnull=True).exclude(image_url="")):
            rel = cache_remote_image(campaign.image_url, "campaigns/images")
            if rel:
                campaign.image_file.name = rel
                campaign.save(update_fields=["image_file"])  # type: ignore[list-item]
                processed += 1

                self.stdout.write(self.style.SUCCESS(f"Processed campaign image: {campaign.id}"))

        for benefit in maybe_limit(DropBenefit.objects.filter(image_file__isnull=True).exclude(image_asset_url="")):
            rel = cache_remote_image(benefit.image_asset_url, "benefits/images")
            if rel:
                benefit.image_file.name = rel
                benefit.save(update_fields=["image_file"])  # type: ignore[list-item]
                processed += 1

                self.stdout.write(self.style.SUCCESS(f"Processed benefit image: {benefit.id}"))

        self.stdout.write(self.style.SUCCESS(f"Backfill complete. Updated {processed} images."))
