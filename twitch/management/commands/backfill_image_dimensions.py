import contextlib
import mimetypes

from django.core.management.base import BaseCommand

from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import RewardCampaign


class Command(BaseCommand):
    """Backfill image width and height fields for existing cached images."""

    help = "Backfill image dimensions for existing cached images"

    def handle(self, *args, **options) -> None:  # noqa: ARG002, PLR0915
        """Execute the command."""
        total_updated = 0

        # Update Game box art
        self.stdout.write("Processing Game box_art_file...")
        for game in Game.objects.exclude(box_art_file=""):
            needs_update = (
                not game.box_art_size_bytes
                or not game.box_art_mime_type
                or not game.box_art_width
            )
            if not (game.box_art_file and needs_update):
                continue

            try:
                if not game.box_art_width:
                    game.box_art_file.open()
                with contextlib.suppress(Exception):
                    game.box_art_size_bytes = game.box_art_file.size
            except (OSError, ValueError, AttributeError) as exc:
                self.stdout.write(self.style.ERROR(f"  Failed {game}: {exc}"))
                continue

            mime, _ = mimetypes.guess_type(game.box_art_file.name or "")
            if mime:
                game.box_art_mime_type = mime

            game.save()
            total_updated += 1
            self.stdout.write(self.style.SUCCESS(f"  Updated {game}"))

        # Update DropCampaign images
        self.stdout.write("Processing DropCampaign image_file...")
        for campaign in DropCampaign.objects.exclude(image_file=""):
            needs_update: bool = (
                not campaign.image_size_bytes
                or not campaign.image_mime_type
                or not campaign.image_width
            )
            if not (campaign.image_file and needs_update):
                continue

            try:
                if not campaign.image_width:
                    campaign.image_file.open()
                with contextlib.suppress(Exception):
                    campaign.image_size_bytes = campaign.image_file.size
            except (OSError, ValueError, AttributeError) as exc:
                self.stdout.write(self.style.ERROR(f"  Failed {campaign}: {exc}"))
                continue

            mime, _ = mimetypes.guess_type(campaign.image_file.name or "")
            if mime:
                campaign.image_mime_type = mime

            campaign.save()
            total_updated += 1
            self.stdout.write(self.style.SUCCESS(f"  Updated {campaign}"))

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
