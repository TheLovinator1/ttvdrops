from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from twitch.models import DropBenefit, DropBenefitEdge, DropCampaign, Game, Organization, TimeBasedDrop


class Command(BaseCommand):
    """Import Twitch drop campaign data from a JSON file."""

    help = "Import Twitch drop campaign data from a JSON file"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments.

        Args:
            parser: The command argument parser.
        """
        parser.add_argument(
            "json_file",
            type=str,
            help="Path to the JSON file containing the drop campaign data",
        )

    def handle(self, **options: str) -> None:
        """Execute the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the file doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
        """
        json_file_path: str = options["json_file"]
        file_path = Path(json_file_path)

        # Validate file exists and is a JSON file
        if not file_path.exists():
            msg = f"File {json_file_path} does not exist"
            raise CommandError(msg)

        if not json_file_path.endswith(".json"):
            msg = f"File {json_file_path} is not a JSON file"
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
