"""Publish historical drops to Bluesky with their original timestamps."""

from __future__ import annotations

import logging
import operator
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import TypedDict
from typing import Unpack

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.bluesky import BlueskyClient
from core.bluesky import as_utc_datetime
from core.bluesky import is_configured
from core.bluesky import publish_drop_to_bluesky
from core.models import BlueskyPost
from kick.models import KickDropCampaign
from twitch.models import DropCampaign

if TYPE_CHECKING:
    from datetime import datetime

    from django.core.management.base import CommandParser

logger: logging.Logger = logging.getLogger("ttvdrops.bluesky.backfill")


class BackfillBlueskyOptions(TypedDict):
    """Options accepted by the backfill_bluesky command."""

    platform: str
    timestamp: str
    order: str
    limit: int
    days: int
    since: str | None
    until: str | None
    dry_run: bool


class Command(BaseCommand):
    """Backfill existing drop campaigns to Bluesky with historical timestamps."""

    help = "Post existing drop campaigns to Bluesky, backdated to when they were found."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command-line arguments for filtering the backfill."""
        parser.add_argument(
            "--platform",
            choices=("all", "twitch", "kick"),
            default="all",
            help="Only backfill one platform (default: all supported platforms).",
        )
        parser.add_argument(
            "--timestamp",
            choices=("discovered", "start"),
            default="discovered",
            help="Use when the drop was first seen (default) or its start date.",
        )
        parser.add_argument(
            "--order",
            choices=("oldest", "newest"),
            default="oldest",
            help="Post oldest or newest drops first (default: oldest).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of posts to create (0 means no limit).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=0,
            help="Only backfill drops found within the last N days (0 means all).",
        )
        parser.add_argument(
            "--since",
            help="Only backfill drops at or after this ISO 8601 date/datetime.",
        )
        parser.add_argument(
            "--until",
            help="Only backfill drops at or before this ISO 8601 date/datetime.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be posted without contacting Bluesky.",
        )

    def _parse_bound(self, value: str | None, option: str) -> datetime | None:
        if not value:
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            msg = f"Invalid ISO 8601 datetime for --{option}: {value!r}"
            raise CommandError(msg)
        return as_utc_datetime(parsed)

    def _candidates(
        self,
        platform: str,
        timestamp_source: str,
    ) -> list[tuple[datetime, str, str, str]]:
        posted: set[tuple[str, str]] = set(
            BlueskyPost.objects.values_list("platform", "drop_id"),
        )
        rows: list[tuple[datetime, str, str, str]] = []

        if platform in {"all", "twitch"}:
            field: str = "added_at" if timestamp_source == "discovered" else "start_at"
            for drop_id, when, name in DropCampaign.objects.values_list(
                "twitch_id",
                field,
                "name",
            ):
                key = str(drop_id)
                if when is None and timestamp_source == "start":
                    continue
                if ("twitch", key) in posted:
                    continue
                rows.append((as_utc_datetime(when), "twitch", key, name))

        if platform in {"all", "kick"}:
            field = "added_at" if timestamp_source == "discovered" else "starts_at"
            for drop_id, when, name in KickDropCampaign.objects.values_list(
                "kick_id",
                field,
                "name",
            ):
                key = str(drop_id)
                if when is None and timestamp_source == "start":
                    continue
                if ("kick", key) in posted:
                    continue
                rows.append((as_utc_datetime(when), "kick", key, name))

        return rows

    def handle(  # ruff:ignore[too-many-statements]
        self,
        *_args: str,
        **options: Unpack[BackfillBlueskyOptions],
    ) -> None:
        """Publish or preview selected drops on Bluesky.

        Raises:
            CommandError: If Bluesky is not configured or options are invalid.
        """
        dry_run: bool = bool(options["dry_run"])
        if not dry_run and not settings.BLUESKY_ENABLED:
            msg = "Bluesky posting is disabled (set BLUESKY_ENABLED=true)."
            raise CommandError(msg)
        if not dry_run and not is_configured():
            msg = "Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD before backfilling."
            raise CommandError(msg)

        limit: int = int(options["limit"])
        if limit < 0:
            msg = "--limit cannot be negative."
            raise CommandError(msg)

        since: datetime | None = self._parse_bound(options["since"], "since")
        until: datetime | None = self._parse_bound(options["until"], "until")
        cutoff: datetime | None = None
        days: int = int(options["days"])
        if days > 0:
            cutoff = timezone.now() - timedelta(days=days)

        rows: list[tuple[datetime, str, str, str]] = self._candidates(
            str(options["platform"]),
            str(options["timestamp"]),
        )
        selected: list[tuple[datetime, str, str, str]] = []
        for when, platform, drop_id, name in rows:
            if since is not None and when < since:
                continue
            if until is not None and when > until:
                continue
            if cutoff is not None and when < cutoff:
                continue
            selected.append((when, platform, drop_id, name))

        reverse: bool = options["order"] == "newest"
        selected.sort(key=operator.itemgetter(0, 1, 2), reverse=reverse)
        if limit:
            selected = selected[:limit]

        if not selected:
            self.stdout.write("No drops to backfill.")
            return

        self.stdout.write(
            f"Found {len(selected)} drop(s) to "
            f"{'preview' if dry_run else 'publish'} on Bluesky.",
        )

        posted: int = 0
        failed: int = 0
        client: BlueskyClient | None = None
        try:
            if not dry_run:
                client = BlueskyClient()

            for index, (when, platform, drop_id, name) in enumerate(selected, start=1):
                label: str = f"{platform}:{drop_id}"
                if dry_run:
                    self.stdout.write(
                        f"[{index}/{len(selected)}] Would post {label} "
                        f"({name}) at {when.isoformat()}",
                    )
                    continue

                try:
                    entry = publish_drop_to_bluesky(
                        platform,
                        drop_id,
                        created_at=when,
                        client=client,
                    )
                except Exception as exc:
                    failed += 1
                    self.stderr.write(
                        f"[{index}/{len(selected)}] Failed {label}: {exc}",
                    )
                    logger.exception("Bluesky backfill failed for %s", label)
                    continue

                if entry is None:
                    self.stdout.write(f"[{index}/{len(selected)}] Skipped {label}")
                    continue

                posted += 1
                self.stdout.write(
                    f"[{index}/{len(selected)}] Posted {label} at {when.isoformat()} "
                    f"-> {entry.uri}",
                )
        finally:
            if client is not None:
                client.close()

        self.stdout.write(
            self.style.SUCCESS(f"Done. Posted {posted}, failed {failed}."),
        )
