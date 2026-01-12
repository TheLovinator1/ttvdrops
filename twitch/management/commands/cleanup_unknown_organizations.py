from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from colorama import Fore
from colorama import Style
from colorama import init as colorama_init
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser

from twitch.models import Game
from twitch.models import Organization

if TYPE_CHECKING:
    from debug_toolbar.panels.templates.panel import QuerySet


class Command(BaseCommand):
    """Detach the placeholder 'Unknown Organization' from games and optionally re-import data.

    This command removes ManyToMany relationships between the special
    organization (default twitch_id='unknown') and any games it was attached to
    as a fallback. Organizations in Twitch data are optional; this cleanup
    ensures we don't over-assign a fake org.
    """

    help = "Detach 'Unknown Organization' from games and optionally re-import"
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Define CLI arguments for the command."""
        parser.add_argument(
            "--org-id",
            dest="org_id",
            default="unknown",
            help="Organization twitch_id to detach (default: 'unknown')",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not modify the database; print what would change.",
        )
        parser.add_argument(
            "--delete-empty-org",
            action="store_true",
            help=(
                "Delete the organization after detaching if it has no games remaining. "
                "Only applies when not in --dry-run."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401, ARG002
        """Execute the command to detach the organization and optionally re-import data.

        Args:
            *args: Positional arguments (unused).
            **options: Command-line options parsed by argparse.

        Raises:
            CommandError: If the specified organization does not exist.
        """
        colorama_init(autoreset=True)

        org_id: str = options["org_id"]
        dry_run: bool = bool(options.get("dry_run"))
        delete_empty_org: bool = bool(options.get("delete_empty_org"))

        try:
            org: Organization = Organization.objects.get(twitch_id=org_id)
        except Organization.DoesNotExist as exc:  # pragma: no cover - simple guard
            msg: str = f"Organization with twitch_id='{org_id}' does not exist. Nothing to do."
            raise CommandError(msg) from exc

        # Compute the set of affected games via the through relation for accuracy and performance
        affected_games_qs: QuerySet[Game, Game] = Game.objects.filter(owners=org).order_by("display_name")
        affected_count: int = affected_games_qs.count()

        if affected_count == 0:
            self.stdout.write(
                f"{Fore.YELLOW}→{Style.RESET_ALL} No games linked to organization '{org.name}' ({org.twitch_id}).",
            )
        else:
            self.stdout.write(
                f"{Fore.CYAN}•{Style.RESET_ALL} Found {affected_count:,} game(s) linked to '{org.name}' ({org.twitch_id}).",  # noqa: E501
            )

        # Show a short preview list in dry-run mode
        preview_limit = 10
        if affected_count > 0 and dry_run:
            sample: list[Game] = list(affected_games_qs[:preview_limit])
            for g in sample:
                self.stdout.write(f"  - {g.display_name} [{g.twitch_id}]")
            if affected_count > preview_limit:
                self.stdout.write(f"  ... and {affected_count - preview_limit:,} more")

        # Perform the detachment
        if affected_count > 0 and not dry_run:
            # Use the reverse relation clear() to efficiently remove all M2M rows for this org
            # This issues a single DELETE on the through table for organization=org
            org.games.clear()  # pyright: ignore[reportAttributeAccessIssue]
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} Detached organization '{org.name}' from {affected_count:,} game(s).",
            )

        # Optionally delete the organization if it no longer has games
        if not dry_run and delete_empty_org:
            remaining_games: int = org.games.count()  # pyright: ignore[reportAttributeAccessIssue]
            if remaining_games == 0:
                org_name: str = org.name
                org_twid: str = org.twitch_id
                org.delete()
                self.stdout.write(
                    f"{Fore.GREEN}✓{Style.RESET_ALL} Deleted organization '{org_name}' ({org_twid}) as it has no games.",  # noqa: E501
                )
            else:
                self.stdout.write(
                    f"{Fore.YELLOW}→{Style.RESET_ALL} Organization '{org.name}' still has {remaining_games:,} game(s); not deleted.",  # noqa: E501
                )
