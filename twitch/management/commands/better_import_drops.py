from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser
from pydantic import ValidationError

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

    def pre_fill_cache(self) -> None:
        """Load all existing IDs from DB into memory to avoid N+1 queries."""
        self.stdout.write("Pre-filling caches...")
        self.game_cache = {str(g.twitch_id): g for g in Game.objects.all()}
        self.stdout.write(f"\tGames: {len(self.game_cache)}")

        self.organization_cache = {str(o.twitch_id): o for o in Organization.objects.all()}
        self.stdout.write(f"\tOrganizations: {len(self.organization_cache)}")

        self.drop_campaign_cache = {str(c.twitch_id): c for c in DropCampaign.objects.all()}
        self.stdout.write(f"\tDrop Campaigns: {len(self.drop_campaign_cache)}")

        self.channel_cache = {str(ch.twitch_id): ch for ch in Channel.objects.all()}
        self.stdout.write(f"\tChannels: {len(self.channel_cache)}")

        self.benefit_cache = {str(b.twitch_id): b for b in DropBenefit.objects.all()}
        self.stdout.write(f"\tBenefits: {len(self.benefit_cache)}")

    def handle(self, *args, **options) -> None:  # noqa: ARG002
        """Main entry point for the command.

        Raises:
            CommandError: If the provided path does not exist.
        """
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
            self.stdout.write(self.style.WARNING("\n\nInterrupted by user!"))
            self.stdout.write(self.style.WARNING("Shutting down gracefully..."))
            sys.exit(130)

    def process_json_files(self, input_path: Path, options: dict) -> None:
        """Process multiple JSON files in a directory.

        Args:
            input_path: Path to the directory containing JSON files
            options: Command options
        """
        json_files: list[Path] = self.collect_json_files(options, input_path)
        self.stdout.write(f"Found {len(json_files)} JSON files to process")

        completed_count = 0
        with ProcessPoolExecutor() as executor:
            futures = {executor.submit(self.process_file_worker, file_path, options): file_path for file_path in json_files}

            for future in as_completed(futures):
                file_path: Path = futures[future]
                try:
                    result: dict[str, bool | str] = future.result()
                    if result["success"]:
                        self.stdout.write(f"✓ {file_path}")
                    else:
                        self.stdout.write(f"✗ {file_path} -> {result['broken_dir']}/{file_path.name}")

                    completed_count += 1
                except (OSError, ValueError, KeyError) as e:
                    self.stdout.write(f"✗ {file_path} (error: {e})")
                    completed_count += 1

                self.stdout.write(f"Progress: {completed_count}/{len(json_files)} files processed")
                self.stdout.write("")

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
            ViewerDropsDashboardPayload.model_validate_json(file_path.read_text(encoding="utf-8"))
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
        self.stdout.write(f"Processing file: {file_path}")

        try:
            _: ViewerDropsDashboardPayload = ViewerDropsDashboardPayload.model_validate_json(file_path.read_text(encoding="utf-8"))
            self.stdout.write("\tProcessed drop campaigns")
        except ValidationError:
            if options["crash_on_error"]:
                raise

            broken_dir: Path = move_failed_validation_file(file_path)
            self.stdout.write(f"\tMoved to {broken_dir} (validation failed)")
