from typing import TYPE_CHECKING

from django.core.management import BaseCommand
from django.core.management import CommandError
from django.core.management import call_command

if TYPE_CHECKING:
    import argparse


class Command(BaseCommand):
    """Django management command to import a range of Chzzk campaigns by calling import_chzzk_campaign repeatedly."""

    help = (
        "Import a range of Chzzk campaigns by calling import_chzzk_campaign repeatedly."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add command-line arguments for the management command."""
        parser.add_argument("start", type=int, help="Starting campaign number")
        parser.add_argument("end", type=int, help="Ending campaign number (inclusive)")
        parser.add_argument(
            "--step",
            type=int,
            default=None,
            help="Step to move between numbers (default -1 for descending, 1 for ascending)",
        )

    def handle(self, **options) -> None:
        """Main handler for the management command. Calls import_chzzk_campaign for each campaign number in the specified range.

        Raises:
            ValueError: If step is 0.
        """
        start: int = options["start"]
        end: int = options["end"]
        step: int | None = options["step"]

        if step is None:
            step = -1 if start > end else 1

        if step == 0:
            msg = "Step cannot be 0"
            raise ValueError(msg)

        range_end: int = end + 1 if step > 0 else end - 1

        msg: str = f"Importing campaigns from {start} to {end} with step {step}"
        self.stdout.write(self.style.SUCCESS(msg))

        for campaign_no in range(start, range_end, step):
            self.stdout.write(f"Importing campaign {campaign_no}...")
            try:
                call_command("import_chzzk_campaign", str(campaign_no))
            except CommandError as exc:
                msg = f"Failed campaign {campaign_no}: {exc}"
                self.stdout.write(self.style.ERROR(msg))

        self.stdout.write(self.style.SUCCESS("Batch import complete."))
