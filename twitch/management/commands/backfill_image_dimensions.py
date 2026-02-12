"""Management command to backfill image dimensions for existing cached images."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import RewardCampaign


class Command(BaseCommand):
    """Backfill image width and height fields for existing cached images."""

    help = "Backfill image dimensions for existing cached images"

    def handle(self, *args, **options) -> None:  # noqa: ARG002
        """Execute the command."""
        total_updated = 0

        # Update Game box art
        self.stdout.write("Processing Game box_art_file...")
        for game in Game.objects.exclude(box_art_file=""):
            if game.box_art_file and not game.box_art_width:
                try:
                    # Opening the file and saving triggers dimension calculation
                    game.box_art_file.open()
                    game.save()
                    total_updated += 1
                    self.stdout.write(self.style.SUCCESS(f"  Updated {game}"))
                except (OSError, ValueError, AttributeError) as exc:
                    self.stdout.write(self.style.ERROR(f"  Failed {game}: {exc}"))

        # Update DropCampaign images
        self.stdout.write("Processing DropCampaign image_file...")
        for campaign in DropCampaign.objects.exclude(image_file=""):
            if campaign.image_file and not campaign.image_width:
                try:
                    campaign.image_file.open()
                    campaign.save()
                    total_updated += 1
                    self.stdout.write(self.style.SUCCESS(f"  Updated {campaign}"))
                except (OSError, ValueError, AttributeError) as exc:
                    self.stdout.write(self.style.ERROR(f"  Failed {campaign}: {exc}"))

        # Update DropBenefit images
        self.stdout.write("Processing DropBenefit image_file...")
        for benefit in DropBenefit.objects.exclude(image_file=""):
            if benefit.image_file and not benefit.image_width:
                try:
                    benefit.image_file.open()
                    benefit.save()
                    total_updated += 1
                    self.stdout.write(self.style.SUCCESS(f"  Updated {benefit}"))
                except (OSError, ValueError, AttributeError) as exc:
                    self.stdout.write(self.style.ERROR(f"  Failed {benefit}: {exc}"))

        # Update RewardCampaign images
        self.stdout.write("Processing RewardCampaign image_file...")
        for reward in RewardCampaign.objects.exclude(image_file=""):
            if reward.image_file and not reward.image_width:
                try:
                    reward.image_file.open()
                    reward.save()
                    total_updated += 1
                    self.stdout.write(self.style.SUCCESS(f"  Updated {reward}"))
                except (OSError, ValueError, AttributeError) as exc:
                    self.stdout.write(self.style.ERROR(f"  Failed {reward}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(f"\nBackfill complete! Updated {total_updated} images."),
        )
