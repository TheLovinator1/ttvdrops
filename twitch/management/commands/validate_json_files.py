from __future__ import annotations

import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    """Validate JSON files and move invalid ones to an error directory."""

    help = "Validate JSON files and move invalid ones to an error directory."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments.

        Args:
            parser: The command argument parser.
        """
        parser.add_argument(
            "path",
            type=str,
            help="Path to the directory containing JSON files to validate.",
        )
        parser.add_argument(
            "--error-dir",
            type=str,
            default="error",
            help="Name of subdirectory to move files with JSON errors to (default: 'error')",
        )

    def handle(self, **options: str) -> None:
        """Handle the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the provided path is not a valid directory.
        """
        path = Path(options["path"])
        error_dir_name = options["error_dir"]

        if not path.is_dir():
            msg = f"Path '{path}' is not a valid directory."
            raise CommandError(msg)

        error_dir = path / error_dir_name
        error_dir.mkdir(exist_ok=True)

        self.stdout.write(f"Validating JSON files in '{path}'...")

        for file_path in path.glob("*.json"):
            if file_path.is_file():
                try:
                    with file_path.open("r", encoding="utf-8") as f:
                        json.load(f)
                except json.JSONDecodeError:
                    self.stdout.write(self.style.WARNING(f"Invalid JSON in '{file_path.name}'. Moving to '{error_dir_name}'."))
                    try:
                        shutil.move(str(file_path), str(error_dir / file_path.name))
                    except Exception as e:  # noqa: BLE001
                        self.stderr.write(self.style.ERROR(f"Could not move file '{file_path.name}': {e}"))
                except Exception as e:  # noqa: BLE001
                    self.stderr.write(self.style.ERROR(f"An unexpected error occurred with file '{file_path.name}': {e}"))

        self.stdout.write(self.style.SUCCESS("Finished validating JSON files."))
