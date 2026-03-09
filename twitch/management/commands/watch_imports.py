import logging
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from twitch.management.commands.better_import_drops import (
    Command as BetterImportDropsCommand,
)

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

logger: logging.Logger = logging.getLogger("ttvdrops.watch_imports")


class Command(BaseCommand):
    """Watch for JSON files in a directory and import them automatically."""

    help = "Watch a directory for JSON files and import them automatically"
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Populate the command with arguments."""
        parser.add_argument(
            "path",
            type=str,
            help="Path to directory to watch for JSON files",
        )

    def handle(self, *args, **options) -> None:  # noqa: ARG002
        """Main entry point for the watch command.

        Args:
            *args: Variable length argument list (unused).
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the provided path does not exist or is not a directory.
        """
        watch_path: Path = self.get_watch_path(options)
        self.stdout.write(
            self.style.SUCCESS(f"Watching {watch_path} for JSON files..."),
        )
        self.stdout.write("Press Ctrl+C to stop\n")

        importer_command: BetterImportDropsCommand = BetterImportDropsCommand()

        while True:
            try:
                sleep(1)
                self.import_json_files(
                    importer_command=importer_command,
                    watch_path=watch_path,
                )

            except KeyboardInterrupt:
                msg = "Received keyboard interrupt. Stopping watch..."
                self.stdout.write(self.style.WARNING(msg))
                break
            except CommandError as e:
                msg = f"Import command error: {e}"
                self.stdout.write(self.style.ERROR(msg))
            except Exception as e:
                msg: str = f"Error while watching directory: {e}"
                raise CommandError(msg) from e

        logger.info("Stopped watching directory: %s", watch_path)

    def import_json_files(
        self,
        importer_command: BetterImportDropsCommand,
        watch_path: Path,
    ) -> None:
        """Import all JSON files in the watch directory using the provided importer command.

        Args:
            importer_command: An instance of the BetterImportDropsCommand to handle the import logic.
            watch_path: The directory path to watch for JSON files.
        """
        # TODO(TheLovinator): Implement actual file watching using watchdog or similar library.  # noqa: TD003
        json_files: list[Path] = [
            f for f in watch_path.iterdir() if f.suffix == ".json" and f.is_file()
        ]
        if not json_files:
            return

        for json_file in json_files:
            self.stdout.write(f"Importing {json_file}...")
            importer_command.handle(path=json_file)

    def get_watch_path(self, options: dict[str, str]) -> Path:
        """Validate and return the watch path from the command options.

        Args:
            options: The command options containing the 'path' key.

        Returns:
            The validated watch path as a Path object.

        Raises:
            CommandError: If the provided path does not exist or is not a directory.
        """
        watch_path: Path = Path(options["path"]).resolve()

        if not watch_path.exists():
            msg: str = f"Path does not exist: {watch_path}"
            raise CommandError(msg)

        if not watch_path.is_dir():
            msg: str = f"Path is not a directory: {watch_path}, it is a {watch_path.stat().st_mode}"
            raise CommandError(msg)

        return watch_path
