"""Replay historical Twitch JSON files into django-pghistory.

The command is intentionally read-only with respect to the response archive.
Files are read in place and are never moved, renamed, or deleted.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from uuid import uuid4

import pghistory
from colorama import Fore
from colorama import Style
from colorama import init as colorama_init
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import connection
from django.db import transaction
from pghistory.models import Context
from tqdm import tqdm

from core.bluesky import suppress_bluesky_posts
from twitch.history_replay import HistoryReplay
from twitch.management.commands.better_import_drops import Command as Importer
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import DropCampaignAllowedChannel
from twitch.models import Game
from twitch.models import GameOwner
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.utils import suppress_image_tasks

if TYPE_CHECKING:
    from django.core.management.base import CommandParser
    from django.db.models import Model

logger: logging.Logger = logging.getLogger("ttvdrops.build_history")

_TIMESTAMP_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<date>\d{8})T(?P<time>\d{6})(?:_(?P<fraction>\d+))?",
)

_TRACKED_MODELS: tuple[type[Model], ...] = (
    Organization,
    Game,
    Channel,
    DropCampaign,
    TimeBasedDrop,
    DropBenefit,
    DropBenefitEdge,
    RewardCampaign,
    Reward,
    DropCampaignAllowedChannel,
    GameOwner,
)

_QUARANTINE_DIR: Path = settings.BASE_DIR / "history_missing_timestamp"
_DRY_RUN_PREVIEW_LIMIT: int = 20


def parse_filename_timestamp(file_path: Path) -> datetime | None:
    """Parse the timestamp embedded in a response filename.

    Args:
        file_path: Path to the JSON response file.

    Returns:
        A UTC datetime, or None when the filename has no timestamp.
    """
    match = _TIMESTAMP_PATTERN.search(file_path.stem)
    if match is None:
        return None

    fraction: str = (match.group("fraction") or "0")[:6].ljust(6, "0")
    raw_timestamp: str = f"{match.group('date')}{match.group('time')}.{fraction}"
    try:
        parsed: datetime = datetime.strptime(  # ruff:ignore[call-datetime-strptime-without-zone]
            raw_timestamp,
            "%Y%m%d%H%M%S.%f",
        )
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def _get_input_path(path: str | None) -> Path:
    """Resolve the command input path.

    Args:
        path: User-supplied path, or None to use TTVDROPS_IMPORTED_DIR.

    Returns:
        The resolved input path.

    Raises:
        CommandError: If no path is supplied and no default is configured.
    """
    raw_path: str = path or os.environ.get("TTVDROPS_IMPORTED_DIR", "")
    if not raw_path:
        msg = (
            "No path supplied and TTVDROPS_IMPORTED_DIR is not configured. "
            "Pass a directory or set TTVDROPS_IMPORTED_DIR."
        )
        raise CommandError(msg)

    resolved: Path = Path(raw_path).expanduser().resolve()
    if not resolved.exists():
        msg = f"Path does not exist: {resolved}"
        raise CommandError(msg)
    return resolved


def _collect_json_files(input_path: Path, *, recursive: bool) -> list[Path]:
    """Collect JSON files from a file or directory.

    Args:
        input_path: File or directory to search.
        recursive: Whether to descend into subdirectories.

    Returns:
        List of JSON file paths.
    """
    if input_path.is_file():
        return [input_path] if input_path.suffix == ".json" else []
    if recursive:
        return list(input_path.rglob("*.json"))
    return [
        path
        for path in input_path.iterdir()
        if path.is_file() and path.suffix == ".json"
    ]


def _source_base(file_path: Path, input_path: Path) -> Path:
    """Return the base used to preserve the source path in quarantine.

    The ``imported`` segment is kept when the file lives under the configured
    imported directory (or under any directory named ``imported``).

    Args:
        file_path: The source file.
        input_path: The resolved command input path.

    Returns:
        Base directory used for relative paths.
    """
    imported_dir_env: str = os.environ.get("TTVDROPS_IMPORTED_DIR", "")
    if imported_dir_env:
        imported_dir: Path = Path(imported_dir_env).expanduser().resolve()
        if file_path == imported_dir or imported_dir in file_path.parents:
            return imported_dir.parent

    for parent in file_path.parents:
        if parent.name == "imported":
            return parent.parent

    return input_path if input_path.is_dir() else input_path.parent


def _relative_source_dir(file_path: Path, input_path: Path) -> Path:
    """Return the relative source directory for a file.

    Args:
        file_path: The source file.
        input_path: The resolved command input path.

    Returns:
        Relative directory, or the immediate parent name as a fallback.
    """
    base: Path = _source_base(file_path, input_path)
    try:
        return file_path.parent.relative_to(base)
    except ValueError:
        return Path(file_path.parent.name)


def _quarantine_file(
    file_path: Path,
    input_path: Path,
    *,
    dry_run: bool,
) -> Path:
    """Copy a file with no parsable timestamp into the project quarantine.

    The original file is never modified or removed.

    Args:
        file_path: Source file.
        input_path: Resolved command input path.
        dry_run: When True, calculate the destination without copying.

    Returns:
        Destination path.
    """
    destination: Path = (
        _QUARANTINE_DIR / _relative_source_dir(file_path, input_path) / file_path.name
    )
    if destination.exists():
        return destination

    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, destination)
    return destination


def _reset_history(*, dry_run: bool) -> None:
    """Delete every pghistory event row and context.

    Args:
        dry_run: When True, do not execute the truncate.
    """
    if dry_run:
        return

    tables: list[str] = [
        model.pgh_event_model._meta.db_table  # ruff:ignore[private-member-access]
        for model in _TRACKED_MODELS
    ]
    tables.append(Context._meta.db_table)  # ruff:ignore[private-member-access]
    quoted: str = ", ".join(connection.ops.quote_name(table) for table in tables)
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY")


def _processed_source_files(*, mode: str) -> set[str]:
    """Return source files that were already imported by a history run.

    Args:
        mode: History mode metadata value to match.

    Returns:
        Set of absolute source path strings.
    """
    return {
        str(source_file)
        for source_file in (
            Context.objects.filter(
                metadata__mode=mode,
                metadata__status__in=["ok", "skipped"],
            ).values_list("metadata__source_file", flat=True)
        )
        if source_file
    }


def _backdate_context_events(context: Context, timestamp: datetime) -> int:
    """Set pgh_created_at on every event attached to a history context.

    Args:
        context: The history context for one source file.
        timestamp: Timestamp parsed from the source filename.

    Returns:
        Number of event rows updated.
    """
    updated: int = 0
    for model in _TRACKED_MODELS:
        event_model: Any | None = getattr(model, "pgh_event_model", None)
        if event_model is None:
            continue
        updated += event_model.objects.filter(pgh_context_id=context.pk).update(
            pgh_created_at=timestamp,
        )
    return updated


class Command(BaseCommand):
    """Replay historical Twitch JSON files and backdate their history events."""

    help = "Replay old Twitch JSON files and backdate django-pghistory events"

    def add_arguments(self, parser: CommandParser) -> None:
        """Populate the command with arguments."""
        parser.add_argument(
            "path",
            nargs="?",
            type=str,
            default=None,
            help="JSON file or directory (defaults to TTVDROPS_IMPORTED_DIR)",
        )
        parser.add_argument(
            "--recursive",
            action="store_true",
            help="Recursively search directories for JSON files",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be processed without writing anything",
        )
        parser.add_argument(
            "--history-only",
            action="store_true",
            help=(
                "Write pghistory events only; never save live campaign, drop, "
                "game, organization, or channel rows"
            ),
        )
        parser.add_argument(
            "--reset-history",
            action="store_true",
            help=(
                "Delete all pghistory events and contexts before replaying, "
                "then rebuild the history from the response files"
            ),
        )

    def handle(  # ruff:ignore[too-many-statements, too-many-locals]
        self,
        *_args: str,
        **options: Any,  # ruff:ignore[any-type]
    ) -> None:
        """Run the historical replay."""
        colorama_init(autoreset=True)

        input_path: Path = _get_input_path(options.get("path"))
        dry_run: bool = bool(options.get("dry_run"))
        recursive: bool = bool(options.get("recursive"))
        history_only: bool = bool(options.get("history_only"))
        reset_history: bool = bool(options.get("reset_history"))
        mode: str = "history-only" if history_only else "history"

        all_files: list[Path] = _collect_json_files(input_path, recursive=recursive)
        if not all_files:
            self.stdout.write(self.style.WARNING("No JSON files found."))
            return

        dated_files: list[tuple[datetime, Path]] = []
        missing_timestamp: list[Path] = []
        for file_path in all_files:
            timestamp: datetime | None = parse_filename_timestamp(file_path)
            if timestamp is None:
                missing_timestamp.append(file_path)
            else:
                dated_files.append((timestamp, file_path))

        dated_files.sort(key=lambda item: (item[0], str(item[1])))

        for file_path in missing_timestamp:
            destination: Path = _quarantine_file(
                file_path,
                input_path,
                dry_run=dry_run,
            )
            tqdm.write(
                f"{Fore.YELLOW}!{Style.RESET_ALL} No timestamp in filename: "
                f"{file_path} -> {destination}",
            )

        if not dated_files:
            self.stdout.write(
                self.style.WARNING("No files with a parsable timestamp found."),
            )
            return

        if reset_history:
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        "Would delete all pghistory events and contexts.",
                    ),
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Deleting all pghistory events and contexts...",
                    ),
                )
                _reset_history(dry_run=False)

        processed: set[str] = (
            set() if reset_history else _processed_source_files(mode=mode)
        )
        pending: list[tuple[datetime, Path]] = [
            (timestamp, file_path)
            for timestamp, file_path in dated_files
            if str(file_path) not in processed
        ]

        tqdm.write(
            f"Found {len(all_files)} JSON file(s), "
            f"{len(dated_files)} dated, {len(missing_timestamp)} without timestamp, "
            f"{len(pending)} to process.",
        )

        if dry_run:
            for timestamp, file_path in pending[:_DRY_RUN_PREVIEW_LIMIT]:
                tqdm.write(
                    f"  {timestamp.isoformat()}  {file_path}",
                )
            if len(pending) > _DRY_RUN_PREVIEW_LIMIT:
                tqdm.write(f"  ... and {len(pending) - _DRY_RUN_PREVIEW_LIMIT} more")
            return

        importer: Importer = Importer()
        replay: HistoryReplay | None = HistoryReplay() if history_only else None
        importer_options: dict[str, Any] = {
            "recursive": False,
            "crash_on_error": True,
            "verbose": False,
            "skip_broken_moves": True,
            "skip_completed_moves": True,
        }

        success_count: int = 0
        skipped_count: int = 0
        failed_count: int = 0
        event_count: int = 0

        with tqdm(  # ruff: ignore[too-many-nested-blocks]
            total=len(pending),
            desc="Building history",
            unit="file",
            bar_format=(
                "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
            ),
            colour="green",
            dynamic_ncols=True,
        ) as progress_bar:
            for timestamp, file_path in pending:
                group: str = uuid4().hex
                result: dict[str, bool | str] | None = None
                failure: str = ""

                with (
                    pghistory.context(
                        mode=mode,
                        data_source="TwitchDropsMiner",
                        event_group=group,
                        source_file=str(file_path),
                        source_timestamp=timestamp.isoformat(),
                    ),
                    suppress_image_tasks(),
                    suppress_bluesky_posts(),
                ):
                    try:  # ruff: ignore[too-many-statements-in-try-clause]
                        with transaction.atomic():
                            if replay is not None:
                                result = replay.process_file(
                                    file_path=file_path,
                                    options=importer_options,
                                )
                            else:
                                importer_options["observation_timestamp"] = timestamp
                                result = importer.process_file_worker(
                                    file_path=file_path,
                                    options=importer_options,
                                )
                    except Exception as exc:  # ruff:ignore[blind-except]
                        failure = str(exc) or exc.__class__.__name__

                context: Context | None = Context.objects.filter(
                    metadata__event_group=group,
                ).first()

                is_success: bool = bool(result and result.get("success"))
                if is_success:
                    success_count += 1
                    if context is not None:
                        file_events: int = _backdate_context_events(context, timestamp)
                        event_count += file_events
                        context.metadata.update({
                            "status": "ok",
                            "event_count": file_events,
                        })
                        context.save(update_fields=["metadata"])
                    progress_bar.write(
                        f"{Fore.GREEN}✓{Style.RESET_ALL} {file_path.name} "
                        f"({timestamp.isoformat()})",
                    )
                else:
                    reason: str = failure or str(
                        (result or {}).get("reason", "not a campaign response"),
                    )
                    if "dropCampaign" in reason or "matched" in reason:
                        skipped_count += 1
                        status: str = "skipped"
                    else:
                        failed_count += 1
                        status = "failed"
                    if context is not None:
                        context.metadata.update({
                            "status": status,
                            "reason": reason[:500],
                        })
                        context.save(update_fields=["metadata"])
                    progress_bar.write(
                        f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} ({reason})",
                    )

                progress_bar.set_postfix_str(
                    f"✓ {success_count} | ✗ {failed_count + skipped_count}",
                    refresh=True,
                )
                progress_bar.update(1)

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(
            self.style.SUCCESS(f"✓ Files imported: {success_count}"),
        )
        self.stdout.write(f"Events backdated: {event_count}")
        if skipped_count:
            self.stdout.write(self.style.WARNING(f"✗ Skipped: {skipped_count}"))
        if failed_count:
            self.stdout.write(self.style.ERROR(f"✗ Failed: {failed_count}"))
        self.stdout.write(f"Total considered: {len(pending)}")
        self.stdout.write(f"Quarantined without timestamp: {len(missing_timestamp)}")
        self.stdout.write("=" * 50)
