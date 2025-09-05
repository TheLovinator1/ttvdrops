"""Management command to update PostgreSQL search vectors."""

from __future__ import annotations

from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand

from twitch.models import DropBenefit, DropCampaign, Game, Organization, TimeBasedDrop


class Command(BaseCommand):
    """Update search vectors for existing records."""

    help = "Update PostgreSQL search vectors for existing records"

    def handle(self, *_args, **_options) -> None:
        """Update search vectors for all models."""
        self.stdout.write("Updating search vectors...")

        # Update Organizations
        org_count = Organization.objects.update(search_vector=SearchVector("name"))
        self.stdout.write(self.style.SUCCESS(f"Successfully updated search vectors for {org_count} organizations"))

        # Update Games
        game_count = Game.objects.update(search_vector=SearchVector("name", "display_name"))
        self.stdout.write(self.style.SUCCESS(f"Successfully updated search vectors for {game_count} games"))

        # Update DropCampaigns
        campaign_count = DropCampaign.objects.update(search_vector=SearchVector("name", "description"))
        self.stdout.write(self.style.SUCCESS(f"Successfully updated search vectors for {campaign_count} campaigns"))

        # Update TimeBasedDrops
        drop_count = TimeBasedDrop.objects.update(search_vector=SearchVector("name"))
        self.stdout.write(self.style.SUCCESS(f"Successfully updated search vectors for {drop_count} time-based drops"))

        # Update DropBenefits
        benefit_count = DropBenefit.objects.update(search_vector=SearchVector("name"))
        self.stdout.write(self.style.SUCCESS(f"Successfully updated search vectors for {benefit_count} drop benefits"))

        self.stdout.write(self.style.SUCCESS("All search vectors updated."))
