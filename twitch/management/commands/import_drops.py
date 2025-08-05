from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path
from typing import Any

import orjson
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from twitch.models import DropBenefit, DropBenefitEdge, DropCampaign, Game, Organization, TimeBasedDrop


class Command(BaseCommand):
    """Import Twitch drop campaign data from a JSON file or directory of JSON files."""

    help = "Import Twitch drop campaign data from a JSON file or directory"
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments.

        Args:
            parser: The command argument parser.
        """
        parser.add_argument("path", type=str, help="Path to the JSON file or directory containing JSON files.")
        parser.add_argument("--processed-dir", type=str, default="processed", help="Subdirectory to move processed files to")

    def handle(self, **options) -> None:
        """Execute the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the file/directory doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
        """
        path: Path = Path(options["path"])
        processed_dir: str = options["processed_dir"]

        processed_path: Path = path / processed_dir
        processed_path.mkdir(exist_ok=True)

        if not path.exists():
            msg: str = f"Path {path} does not exist"
            raise CommandError(msg)

        if path.is_file():
            self._process_file(file_path=path, processed_path=processed_path)
        elif path.is_dir():
            self._process_directory(directory=path, processed_path=processed_path)
        else:
            msg = f"Path {path} is neither a file nor a directory"
            raise CommandError(msg)

    def _process_directory(self, directory: Path, processed_path: Path) -> None:
        """Process all JSON files in a directory using parallel processing.

        Args:
            directory: Path to the directory.
            processed_path: Name of subdirectory to move processed files to.
        """
        json_files: list[Path] = list(directory.glob("*.json"))
        if not json_files:
            self.stdout.write(self.style.WARNING(f"No JSON files found in {directory}"))
            return

        total_files: int = len(json_files)
        self.stdout.write(f"Found {total_files} JSON files to process")

        for json_file in json_files:
            self.stdout.write(f"Processing file {json_file.name}...")
            try:
                self._process_file(json_file, processed_path)
            except CommandError as e:
                self.stdout.write(self.style.ERROR(f"Error processing {json_file}: {e}"))
            except (orjson.JSONDecodeError, json.JSONDecodeError):
                broken_json_dir: Path = processed_path / "broken_json"
                broken_json_dir.mkdir(parents=True, exist_ok=True)
                self.stdout.write(self.style.WARNING(f"Invalid JSON in '{json_file}'. Moving to '{broken_json_dir}'."))
                self.move_file(json_file, broken_json_dir / json_file.name)
            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                self.stdout.write(self.style.ERROR(f"Data error processing {json_file}"))
                self.stdout.write(self.style.ERROR(traceback.format_exc()))

        msg: str = f"Processed {total_files} JSON files in {directory}. Moved processed files to {processed_path}."
        self.stdout.write(self.style.SUCCESS(msg))

    def _process_file(self, file_path: Path, processed_path: Path) -> None:
        """Process a single JSON file.

        Args:
            file_path: Path to the JSON file.
            processed_path: Subdirectory to move processed files to.
        """
        data = orjson.loads(file_path.read_text(encoding="utf-8"))
        broken_dir: Path = processed_path / "broken"
        broken_dir.mkdir(parents=True, exist_ok=True)

        probably_shit: list[str] = [
            "ChannelPointsContext",
            "DropCurrentSessionContext",
            "DropsHighlightService_AvailableDrops",
            "DropsPage_ClaimDropRewards",
            "Inventory",
            "PlaybackAccessToken",
            "streamPlaybackAccessToken",
            "VideoPlayerStreamInfoOverlayChannel",
            "OnsiteNotifications_DeleteNotification",
            "DirectoryPage_Game",
        ]

        for keyword in probably_shit:
            if f'"operationName": "{keyword}"' in file_path.read_text():
                target_dir: Path = broken_dir / keyword
                target_dir.mkdir(parents=True, exist_ok=True)

                self.stdout.write(msg=f"Trying to move {file_path!s} to {target_dir / file_path.name!s}")

                self.move_file(file_path, target_dir / file_path.name)
                self.stdout.write(f"Moved {file_path} to {target_dir} (matched '{keyword}')")
                return

        if isinstance(data, list):
            for _item in data:
                self.import_drop_campaign(_item)
        else:
            self.import_drop_campaign(data)

        self.move_file(file_path, processed_path)

    def move_file(self, file_path: Path, processed_path: Path) -> None:
        """Move file and check if already exists."""
        try:
            shutil.move(str(file_path), str(processed_path))
        except FileExistsError:
            # Rename the file if contents is different than the existing one
            with file_path.open("rb") as f1, (processed_path / file_path.name).open("rb") as f2:
                if f1.read() != f2.read():
                    new_name: Path = processed_path / f"{file_path.stem}_duplicate{file_path.suffix}"
                    shutil.move(str(file_path), str(new_name))
                    self.stdout.write(f"Moved {file_path!s} to {new_name!s} (content differs)")
                else:
                    self.stdout.write(f"{file_path!s} already exists in {processed_path!s}, removing original file.")
                    file_path.unlink()
        except (PermissionError, OSError, shutil.Error) as e:
            self.stdout.write(self.style.ERROR(f"Error moving {file_path!s} to {processed_path!s}: {e}"))
            traceback.print_exc()

    def import_drop_campaign(self, data: dict[str, Any]) -> None:
        """Find the key with all the data.

        Args:
            data: The JSON data.

        Raises:
            CommandError: If the JSON structure is invalid.
        """
        if "data" not in data:
            msg = "Invalid JSON structure: Missing top-level 'data'"
            raise CommandError(msg)

        if "user" in data["data"] and "dropCampaign" in data["data"]["user"]:
            drop_campaign_data = data["data"]["user"]["dropCampaign"]
            self.import_to_db(drop_campaign_data)

        elif "currentUser" in data["data"] and "dropCampaigns" in data["data"]["currentUser"]:
            campaigns = data["data"]["currentUser"]["dropCampaigns"]
            for drop_campaign_data in campaigns:
                self.import_to_db(drop_campaign_data)

        else:
            msg = "Invalid JSON structure: Missing either data.user.dropCampaign or data.currentUser.dropCampaigns"
            raise CommandError(msg)

    def import_to_db(self, campaign_data: dict[str, Any]) -> None:
        """Import drop campaign data into the database with retry logic for SQLite locks.

        Args:
            campaign_data: The drop campaign data to import.
        """
        with transaction.atomic():
            game: Game = self.game_update_or_create(campaign_data=campaign_data)

            organization: Organization = self.owner_update_or_create(campaign_data=campaign_data)

            drop_campaign: DropCampaign = self.drop_campaign_update_or_get(
                campaign_data=campaign_data,
                game=game,
                organization=organization,
            )

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

                    DropBenefitEdge.objects.update_or_create(
                        drop=time_based_drop,
                        benefit=benefit,
                        defaults={
                            "entitlement_limit": benefit_edge.get("entitlementLimit", 1),
                        },
                    )
            self.stdout.write(self.style.SUCCESS(f"Successfully imported drop campaign {drop_campaign.name} (ID: {drop_campaign.id})"))

    def drop_campaign_update_or_get(self, campaign_data: dict[str, Any], game: Game, organization: Organization) -> DropCampaign:
        """Update or create a drop campaign.

        Args:
            campaign_data: The drop campaign data to import.
            game: The game this drop campaign is for.
            organization: The company that owns the game.

        Returns:
            Returns the DropCampaign object.
        """
        drop_campaign, created = DropCampaign.objects.update_or_create(
            id=campaign_data["id"],
            defaults={
                "name": campaign_data.get("name", ""),
                "description": campaign_data.get("description", "").replace("\\n", "\n"),
                "details_url": campaign_data.get("detailsURL", ""),
                "account_link_url": campaign_data.get("accountLinkURL", ""),
                "image_url": campaign_data.get("imageURL", ""),
                "start_at": campaign_data.get("startAt"),
                "end_at": campaign_data.get("endAt"),
                "is_account_connected": campaign_data.get("self", {}).get("isAccountConnected", False),
                "game": game,
                "owner": organization,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new drop campaign: {drop_campaign.name} (ID: {drop_campaign.id})"))
        return drop_campaign

    def owner_update_or_create(self, campaign_data: dict[str, Any]) -> Organization:
        """Update or create an organization.

        Args:
            campaign_data: The drop campaign data to import.

        Returns:
            Returns the Organization object.
        """
        org_data: dict[str, Any] = campaign_data["owner"]
        organization, created = Organization.objects.update_or_create(
            id=org_data["id"],
            defaults={"name": org_data["name"]},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new organization: {organization.name} (ID: {organization.id})"))
        return organization

    def game_update_or_create(self, campaign_data: dict[str, Any]) -> Game:
        """Update or create a game.

        Args:
            campaign_data: The drop campaign data to import.

        Returns:
            Returns the Game object.
        """
        game_data: dict[str, Any] = campaign_data["game"]
        game, created = Game.objects.update_or_create(
            id=game_data["id"],
            defaults={
                "slug": game_data.get("slug", ""),
                "display_name": game_data["displayName"],
                "box_art": game_data.get("boxArtURL", ""),
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new game: {game.display_name} (ID: {game.id})"))
        return game
