from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import Any

from colorama import Fore
from colorama import Style
from colorama import init as colorama_init
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser
from pydantic import ValidationError
from tqdm import tqdm

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.schemas import ViewerDropsDashboardPayload


def move_failed_validation_file(file_path: Path) -> Path:
    """Moves a file that failed validation to a 'broken' subdirectory.

    Args:
        file_path: Path to the file that failed validation

    Returns:
        Path to the 'broken' directory where the file was moved
    """
    broken_dir: Path = file_path.parent / "broken"
    broken_dir.mkdir(parents=True, exist_ok=True)

    target_file: Path = broken_dir / file_path.name
    file_path.rename(target_file)

    return broken_dir


def move_file_to_broken_subdir(file_path: Path, subdir: str) -> Path:
    """Move file to a nested broken/<subdir> directory and return that directory.

    Args:
        file_path: The file to move.
        subdir: Subdirectory name under "broken" (e.g., the matched keyword).

    Returns:
        Path to the directory where the file was moved.
    """
    broken_dir: Path = Path.home() / "broken" / subdir
    broken_dir.mkdir(parents=True, exist_ok=True)

    target_file: Path = broken_dir / file_path.name
    file_path.rename(target_file)

    return broken_dir


def detect_non_campaign_keyword(raw_text: str) -> str | None:
    """Detect if payload is a known non-drop-campaign response.

    Looks for operationName values that are commonly present in unrelated
    Twitch API responses. Returns the matched keyword if found.

    Args:
        raw_text: The raw JSON text to scan.

    Returns:
        The matched keyword, or None if no match found.
    """
    probably_shit: list[str] = [
        "ChannelPointsContext",
        "ClaimCommunityPoints",
        "DirectoryPage_Game",
        "DropCurrentSessionContext",
        "DropsPage_ClaimDropRewards",
        "OnsiteNotifications_DeleteNotification",
        "PlaybackAccessToken",
        "streamPlaybackAccessToken",
        "VideoPlayerStreamInfoOverlayChannel",
    ]

    for keyword in probably_shit:
        if f'"operationName": "{keyword}"' in raw_text:
            return keyword
    return None


class Command(BaseCommand):
    """Import Twitch drop campaign data from a JSON file or directory of JSON files."""

    help = "Import Twitch drop campaign data from a JSON file or directory"
    requires_migrations_checks = True

    game_cache: dict[str, Game] = {}
    organization_cache: dict[str, Organization] = {}
    drop_campaign_cache: dict[str, DropCampaign] = {}
    channel_cache: dict[str, Channel] = {}
    benefit_cache: dict[str, DropBenefit] = {}

    def add_arguments(self, parser: CommandParser) -> None:
        """Populate the command with arguments."""
        parser.add_argument("path", type=str, help="Path to JSON file or directory")
        parser.add_argument("--recursive", action="store_true", help="Recursively search directories for JSON files")
        parser.add_argument("--crash-on-error", action="store_true", help="Crash the command on first error instead of continuing")
        parser.add_argument("--verbose", action="store_true", help="Print per-file success messages")

    def pre_fill_cache(self) -> None:
        """Load all existing IDs from DB into memory to avoid N+1 queries."""
        cache_operations: list[tuple[str, type, str]] = [
            ("Games", Game, "game_cache"),
            ("Organizations", Organization, "organization_cache"),
            ("Drop Campaigns", DropCampaign, "drop_campaign_cache"),
            ("Channels", Channel, "channel_cache"),
            ("Benefits", DropBenefit, "benefit_cache"),
        ]

        with tqdm(cache_operations, desc="Loading caches", unit="cache", colour="cyan") as progress_bar:
            for name, model, cache_attr in progress_bar:
                progress_bar.set_description(f"Loading {name}")
                cache: dict[str, Any] = {str(obj.twitch_id): obj for obj in model.objects.all()}
                setattr(self, cache_attr, cache)
                progress_bar.write(f"  {Fore.GREEN}✓{Style.RESET_ALL} {name}: {len(cache):,}")

        tqdm.write("")

    def handle(self, *args, **options) -> None:  # noqa: ARG002
        """Main entry point for the command.

        Raises:
            CommandError: If the provided path does not exist.
        """
        colorama_init(autoreset=True)

        input_path: Path = Path(options["path"]).resolve()

        self.pre_fill_cache()

        try:
            if input_path.is_file():
                self.process_file(file_path=input_path, options=options)
            elif input_path.is_dir():
                self.process_json_files(input_path=input_path, options=options)
            else:
                msg: str = f"Path does not exist: {input_path}"
                raise CommandError(msg)
        except KeyboardInterrupt:
            tqdm.write(self.style.WARNING("\n\nInterrupted by user!"))
            tqdm.write(self.style.WARNING("Shutting down gracefully..."))
            sys.exit(130)

    def process_json_files(self, input_path: Path, options: dict) -> None:
        """Process multiple JSON files in a directory.

        Args:
            input_path: Path to the directory containing JSON files
            options: Command options
        """
        json_files: list[Path] = self.collect_json_files(options, input_path)
        tqdm.write(f"Found {len(json_files)} JSON files to process\n")

        success_count = 0
        failed_count = 0
        error_count = 0

        with (
            ProcessPoolExecutor() as executor,
            tqdm(
                total=len(json_files),
                desc="Processing",
                unit="file",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                colour="green",
                dynamic_ncols=True,
            ) as progress_bar,
        ):
            # Choose a reasonable chunk_size to reduce overhead for huge file counts
            cpu_count = os.cpu_count() or 1
            chunk_size = max(1, min(1000, len(json_files) // (cpu_count * 8 or 1)))

            results_iter = executor.map(self.process_file_worker, json_files, repeat(options), chunksize=chunk_size)

            for file_path in json_files:
                try:
                    result: dict[str, bool | str] = next(results_iter)
                    if result["success"]:
                        success_count += 1
                        if options.get("verbose"):
                            progress_bar.write(f"{Fore.GREEN}✓{Style.RESET_ALL} {file_path.name}")
                    else:
                        failed_count += 1
                        reason = result.get("reason") if isinstance(result, dict) else None
                        if reason:
                            progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → {result['broken_dir']}/{file_path.name} ({reason})")
                        else:
                            progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → {result['broken_dir']}/{file_path.name}")
                except (OSError, ValueError, KeyError) as e:
                    error_count += 1
                    progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} (error: {e})")

                # Update postfix with statistics
                progress_bar.set_postfix_str(f"✓ {success_count} | ✗ {failed_count + error_count}", refresh=True)
                progress_bar.update(1)

        self.print_processing_summary(json_files, success_count, failed_count, error_count)

    def print_processing_summary(self, json_files: list[Path], success_count: int, failed_count: int, error_count: int) -> None:
        """Print a summary of the batch processing results.

        Args:
            json_files: List of JSON file paths that were processed.
            success_count: Number of files processed successfully.
            failed_count: Number of files that failed validation and were moved.
            error_count: Number of files that encountered unexpected errors.
        """
        tqdm.write("\n" + "=" * 50)
        tqdm.write(self.style.SUCCESS(f"✓ Successfully processed: {success_count}"))
        if failed_count > 0:
            tqdm.write(self.style.WARNING(f"✗ Validation failed: {failed_count}"))
        if error_count > 0:
            tqdm.write(self.style.ERROR(f"✗ Errors: {error_count}"))
        tqdm.write(f"Total: {len(json_files)}")
        tqdm.write("=" * 50)

    def collect_json_files(self, options: dict, input_path: Path) -> list[Path]:
        """Collect JSON files from the specified directory.

        Args:
            options: Command options
            input_path: Path to the directory

        Returns:
            List of JSON file paths
        """
        json_files: list[Path] = []
        if options["recursive"]:
            for root, _dirs, files in os.walk(input_path):
                root_path = Path(root)
                json_files.extend(root_path / file for file in files if file.endswith(".json"))
        else:
            json_files = [f for f in input_path.iterdir() if f.is_file() and f.suffix == ".json"]
        return json_files

    @staticmethod
    def process_file_worker(file_path: Path, options: dict) -> dict[str, bool | str]:
        """Worker function for parallel processing of files.

        Args:
            file_path: Path to the JSON file to process
            options: Command options

        Raises:
            ValidationError: If the JSON file fails validation

        Returns:
            Dict with success status and optional broken_dir path
        """
        try:
            raw_text: str = file_path.read_text(encoding="utf-8", errors="ignore")

            # Fast pre-filter: check for known non-campaign keywords and move early
            matched: str | None = detect_non_campaign_keyword(raw_text)
            if matched:
                broken_dir: Path = move_file_to_broken_subdir(file_path, matched)
                return {"success": False, "broken_dir": str(broken_dir), "reason": f"matched '{matched}'"}

            ViewerDropsDashboardPayload.model_validate_json(raw_text)
        except ValidationError:
            if options["crash_on_error"]:
                raise

            broken_dir: Path = move_failed_validation_file(file_path)
            return {"success": False, "broken_dir": str(broken_dir)}
        else:
            return {"success": True}

    def process_file(self, file_path: Path, options: dict) -> None:
        """Reads a JSON file and processes the campaign data.

        Args:
            file_path: Path to the JSON file
            options: Command options

        Raises:
            ValidationError: If the JSON file fails validation
        """
        with tqdm(
            total=1,
            desc=f"Processing {file_path.name}",
            unit="file",
            colour="green",
            dynamic_ncols=True,
        ) as progress_bar:
            try:
                raw_text: str = file_path.read_text(encoding="utf-8", errors="ignore")

                # Fast pre-filter for non-campaign responses
                matched: str | None = detect_non_campaign_keyword(raw_text)
                if matched:
                    broken_dir: Path = move_file_to_broken_subdir(file_path, matched)
                    progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → {broken_dir}/{file_path.name} (matched '{matched}')")
                    return

                _: ViewerDropsDashboardPayload = ViewerDropsDashboardPayload.model_validate_json(raw_text)
                progress_bar.update(1)
                progress_bar.write(f"{Fore.GREEN}✓{Style.RESET_ALL} {file_path.name}")
            except ValidationError:
                if options["crash_on_error"]:
                    raise

                broken_dir: Path = move_failed_validation_file(file_path)
                progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → {broken_dir}/{file_path.name}")
