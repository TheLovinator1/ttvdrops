from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import BaseCommand
from django.db.models import Count

from twitch.models import Channel

if TYPE_CHECKING:
    from argparse import ArgumentParser

    from debug_toolbar.panels.templates.panel import QuerySet

SAMPLE_PREVIEW_COUNT = 10


class Command(BaseCommand):
    """Management command to clean up orphaned channels with no campaigns.

    Orphaned channels are channels that exist in the database but are not
    associated with any drop campaigns. This can happen when campaign ACLs
    are updated with empty channel lists, clearing previous associations.
    """

    help = "Remove channels that have no associated drop campaigns"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete channels without confirmation prompt",
        )

    def handle(self, **options: str | bool) -> None:
        """Execute the command."""
        dry_run: str | bool = options["dry_run"]
        force: str | bool = options["force"]

        # Find channels with no campaigns
        orphaned_channels: QuerySet[Channel, Channel] = Channel.objects.annotate(
            campaign_count=Count("allowed_campaigns"),
        ).filter(campaign_count=0)

        count: int = orphaned_channels.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No orphaned channels found."))
            return

        self.stdout.write(f"Found {count} orphaned channels with no associated campaigns:")

        # Show sample of channels to be deleted
        for channel in orphaned_channels[:SAMPLE_PREVIEW_COUNT]:
            self.stdout.write(f"  - {channel.display_name} (Twitch ID: {channel.twitch_id})")

        if count > SAMPLE_PREVIEW_COUNT:
            self.stdout.write(f"  ... and {count - SAMPLE_PREVIEW_COUNT} more")

        if dry_run:
            self.stdout.write(self.style.WARNING(f"\n[DRY RUN] Would delete {count} orphaned channels."))
            return

        if not force:
            response: str = input(f"\nAre you sure you want to delete {count} orphaned channels? (yes/no): ")
            if response.lower() != "yes":
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        # Delete the orphaned channels
        deleted_count, _ = orphaned_channels.delete()

        self.stdout.write(self.style.SUCCESS(f"\nSuccessfully deleted {deleted_count} orphaned channels."))
