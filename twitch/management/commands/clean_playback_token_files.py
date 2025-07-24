from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Django management command to clean response files that only contain PlaybackAccessToken data.

    This command scans JSON files in the specified directory and removes those that only contain
    PlaybackAccessToken data without any other meaningful content.
    """

    help = "Cleans response files that only contain PlaybackAccessToken data"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command line arguments to the parser.

        Parameters:
        ----------
        parser : CommandParser
            The command argument parser
        """
        parser.add_argument("--dir", type=str, default="responses", help="Directory containing the response files to clean")
        parser.add_argument(
            "--deleted-dir",
            type=str,
            default=None,
            help="Directory to move files to instead of deleting them (defaults to '<dir>/deleted')",
        )
        parser.add_argument("--dry-run", action="store_true", help="Only print files that would be moved without actually moving them")

    def is_playback_token_only(self, data: dict[str, Any]) -> bool:
        """Determine if a JSON data structure only contains PlaybackAccessToken data.

        Parameters:
        ----------
        data : dict[str, Any]
            The JSON data to check

        Returns:
        -------
        bool
            True if the data only contains PlaybackAccessToken data, False otherwise
        """
        # Check if data has streamPlaybackAccessToken and it's the only key
        has_playback_token = (
            "data" in data
            and "streamPlaybackAccessToken" in data["data"]
            and "__typename" in data["data"]["streamPlaybackAccessToken"]
            and data["data"]["streamPlaybackAccessToken"]["__typename"] == "PlaybackAccessToken"
            and len(data["data"]) == 1
        )

        if has_playback_token:
            return True

        # Also check if the operation name in extensions is PlaybackAccessToken and no other data
        return (
            "extensions" in data
            and "operationName" in data["extensions"]
            and data["extensions"]["operationName"] == "PlaybackAccessToken"
            and ("data" not in data or ("data" in data and len(data["data"]) <= 1))
        )

    def process_file(self, file_path: Path, *, dry_run: bool = False, deleted_dir: Path) -> bool:
        """Process a single JSON file to check if it only contains PlaybackAccessToken data.

        Parameters:
        ----------
        file_path : Path
            The path to the JSON file
        dry_run : bool, keyword-only
            If True, only log the action without actually moving the file
        deleted_dir : Path, keyword-only
            Directory to move files to instead of deleting them

        Returns:
        -------
        bool
            True if the file was (or would be) moved, False otherwise
        """
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))

            if self.is_playback_token_only(data):
                if dry_run:
                    self.stdout.write(f"Would move: {file_path} to {deleted_dir}")
                else:
                    # Create the deleted directory if it doesn't exist
                    if not deleted_dir.exists():
                        deleted_dir.mkdir(parents=True, exist_ok=True)

                    # Get the relative path from the source directory to maintain structure
                    target_file = deleted_dir / file_path.name

                    # If a file with the same name already exists in the target dir,
                    # append a number to the filename
                    counter = 1
                    while target_file.exists():
                        stem = target_file.stem
                        # If the stem already ends with a counter pattern like "_1", increment it
                        if stem.rfind("_") > 0 and stem[stem.rfind("_") + 1 :].isdigit():
                            base_stem = stem[: stem.rfind("_")]
                            counter = int(stem[stem.rfind("_") + 1 :]) + 1
                            target_file = deleted_dir / f"{base_stem}_{counter}{target_file.suffix}"
                        else:
                            target_file = deleted_dir / f"{stem}_{counter}{target_file.suffix}"
                        counter += 1

                    # Move the file
                    file_path.rename(target_file)
                    self.stdout.write(f"Moved: {file_path} to {target_file}")
                return True

        except json.JSONDecodeError:
            self.stderr.write(self.style.WARNING(f"Error parsing JSON in {file_path}"))
        except OSError as e:
            self.stderr.write(self.style.ERROR(f"IO error processing {file_path}: {e!s}"))

        return False

    def handle(self, **options: dict[str, object]) -> None:
        """Execute the command to clean response files.

        Parameters:
        ----------
        **options : dict[str, object]
            Command options
        """
        directory = str(options["dir"])
        dry_run = bool(options.get("dry_run"))
        deleted_dir_path = options.get("deleted_dir")

        # Set up the base directory for processing
        base_dir = Path(directory)
        if not base_dir.exists():
            self.stderr.write(self.style.ERROR(f"Directory {directory} does not exist"))
            return

        # Set up the deleted directory
        deleted_dir: Path = Path(str(deleted_dir_path)) if deleted_dir_path else base_dir / "deleted"

        if not dry_run and not deleted_dir.exists():
            deleted_dir.mkdir(parents=True, exist_ok=True)
            self.stdout.write(f"Created directory for moved files: {deleted_dir}")

        file_count = 0
        moved_count = 0

        # Process all JSON files in the directory
        for file_path in base_dir.glob("**/*.json"):
            # Skip files in the deleted directory
            if deleted_dir in file_path.parents or deleted_dir == file_path.parent:
                continue

            if not file_path.is_file():
                continue

            file_count += 1
            if self.process_file(file_path, dry_run=dry_run, deleted_dir=deleted_dir):
                moved_count += 1

        # Report the results
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Dry run completed' if dry_run else 'Cleanup completed'}: "
                f"Processed {file_count} files, {'would move' if dry_run else 'moved'} {moved_count} files to {deleted_dir}"
            )
        )
