"""Management command to update PostgreSQL search vectors."""

from __future__ import annotations

from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand

from twitch.models import DropCampaign


class Command(BaseCommand):
    """Update search vectors for existing campaign records."""

    help = "Update PostgreSQL search vectors for existing campaigns"

    def handle(self, *_args, **_options) -> None:
        """Update search vectors for all campaigns."""
        self.stdout.write("Updating search vectors for campaigns...")

        DropCampaign.objects.update(search_vector=SearchVector("name", "description", weight="A"))

        campaign_count: int = DropCampaign.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Successfully updated search vectors for {campaign_count} campaigns"))
