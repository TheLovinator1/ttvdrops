from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from twitch.models import DropBenefit, DropBenefitEdge, DropCampaign, Game, Organization, TimeBasedDrop


class Command(BaseCommand):
    """Import Twitch drop campaign data from a JSON file or directory of JSON files."""

    help = "Import Twitch drop campaign data from a JSON file or directory"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments.

        Args:
            parser: The command argument parser.
        """
        parser.add_argument(
            "path",
            type=str,
            help="Path to the JSON file or directory containing JSON files with drop campaign data",
        )
        parser.add_argument(
            "--processed-dir",
            type=str,
            default="processed",
            help="Name of subdirectory to move processed files to (default: 'processed')",
        )

    def handle(self, **options: str) -> None:
        """Execute the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the file/directory doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
        """
        path: str = options["path"]
        processed_dir: str = options["processed_dir"]
        path_obj = Path(path)

        # Check if path exists
        if not path_obj.exists():
            msg = f"Path {path} does not exist"
            raise CommandError(msg)

        # Process single file or directory
        if path_obj.is_file():
            self._process_file(path_obj, processed_dir)
        elif path_obj.is_dir():
            self._process_directory(path_obj, processed_dir)
        else:
            msg = f"Path {path} is neither a file nor a directory"
            raise CommandError(msg)

    def _process_directory(self, directory: Path, processed_dir: str) -> None:
        """Process all JSON files in a directory.

        Args:
            directory: Path to the directory.
            processed_dir: Name of subdirectory to move processed files to.
        """
        # Create processed directory if it doesn't exist
        processed_path = directory / processed_dir
        if not processed_path.exists():
            processed_path.mkdir()
            self.stdout.write(f"Created directory for processed files: {processed_path}")

        # Process all JSON files in the directory
        json_files = list(directory.glob("*.json"))
        if not json_files:
            self.stdout.write(self.style.WARNING(f"No JSON files found in {directory}"))
            return

        for json_file in json_files:
            try:
                self._process_file(json_file, processed_dir)
            except CommandError as e:
                self.stdout.write(self.style.ERROR(f"Error processing {json_file}: {e}"))

    def _process_file(self, file_path: Path, processed_dir: str) -> None:
        """Process a single JSON file.

        Args:
            file_path: Path to the JSON file.
            processed_dir: Name of subdirectory to move processed files to.

        Raises:
            CommandError: If the file isn't a JSON file or has invalid JSON structure.
        """
        # Validate file is a JSON file
        if not file_path.name.endswith(".json"):
            msg = f"File {file_path} is not a JSON file"
            raise CommandError(msg)

        # Load JSON data
        try:
            with file_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            msg = f"Error decoding JSON: {e}"
            raise CommandError(msg) from e

        # Check if the JSON has the expected structure
        if "data" not in data or "user" not in data["data"] or "dropCampaign" not in data["data"]["user"]:
            msg = "Invalid JSON structure: Missing data.user.dropCampaign"
            raise CommandError(msg)

        # Extract drop campaign data
        drop_campaign_data = data["data"]["user"]["dropCampaign"]

        # Process the data
        self._import_drop_campaign(drop_campaign_data)

        # Move the processed file to the processed directory
        if processed_dir:
            processed_path = file_path.parent / processed_dir
            if not processed_path.exists():
                processed_path.mkdir()

            # Move the file to the processed directory
            new_path = processed_path / file_path.name
            shutil.move(str(file_path), str(new_path))
            self.stdout.write(f"Moved processed file to: {new_path}")

        self.stdout.write(self.style.SUCCESS(f"Successfully imported drop campaign: {drop_campaign_data['name']}"))

    def _import_drop_campaign(self, campaign_data: dict[str, Any]) -> None:
        """Import drop campaign data into the database.

        Args:
            campaign_data: The drop campaign data to import.
        """
        # First, create or update the game
        game_data = campaign_data["game"]
        game, _ = Game.objects.update_or_create(
            id=game_data["id"],
            defaults={
                "slug": game_data.get("slug", ""),
                "display_name": game_data["displayName"],
            },
        )

        # Create or update the organization
        org_data = campaign_data["owner"]
        organization, _ = Organization.objects.update_or_create(
            id=org_data["id"],
            defaults={"name": org_data["name"]},
        )

        # Create or update the drop campaign
        drop_campaign, _ = DropCampaign.objects.update_or_create(
            id=campaign_data["id"],
            defaults={
                "name": campaign_data["name"],
                "description": campaign_data["description"],
                "details_url": campaign_data.get("detailsURL", ""),
                "account_link_url": campaign_data.get("accountLinkURL", ""),
                "image_url": campaign_data.get("imageURL", ""),
                "start_at": campaign_data["startAt"],
                "end_at": campaign_data["endAt"],
                "status": campaign_data["status"],
                "is_account_connected": campaign_data["self"]["isAccountConnected"],
                "game": game,
                "owner": organization,
            },
        )

        # Process time-based drops
        for drop_data in campaign_data.get("timeBasedDrops", []):
            time_based_drop, _ = TimeBasedDrop.objects.update_or_create(
                id=drop_data["id"],
                defaults={
                    "name": drop_data["name"],
                    "required_minutes_watched": drop_data["requiredMinutesWatched"],
                    "required_subs": drop_data.get("requiredSubs", 0),
                    "start_at": drop_data["startAt"],
                    "end_at": drop_data["endAt"],
                    "campaign": drop_campaign,
                },
            )

            # Process benefits
            for benefit_edge in drop_data.get("benefitEdges", []):
                benefit_data = benefit_edge["benefit"]
                benefit, _ = DropBenefit.objects.update_or_create(
                    id=benefit_data["id"],
                    defaults={
                        "name": benefit_data["name"],
                        "image_asset_url": benefit_data.get("imageAssetURL", ""),
                        "created_at": benefit_data["createdAt"],
                        "entitlement_limit": benefit_data.get("entitlementLimit", 1),
                        "is_ios_available": benefit_data.get("isIosAvailable", False),
                        "distribution_type": benefit_data["distributionType"],
                        "game": game,
                        "owner_organization": organization,
                    },
                )

                # Create the relationship between drop and benefit
                DropBenefitEdge.objects.update_or_create(
                    drop=time_based_drop,
                    benefit=benefit,
                    defaults={
                        "entitlement_limit": benefit_edge.get("entitlementLimit", 1),
                    },
                )
