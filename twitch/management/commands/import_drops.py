from __future__ import annotations

import logging
import shutil
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dateparser
import json_repair
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from twitch.models import DropBenefit, DropBenefitEdge, DropCampaign, Game, Organization, TimeBasedDrop

if TYPE_CHECKING:
    from datetime import datetime


logger: logging.Logger = logging.getLogger(__name__)


def parse_date(value: str | None) -> datetime | None:
    """Parse a datetime string into a timezone-aware datetime using dateparser.

    Args:
        value: The datetime string to parse.

    Returns:
        A timezone-aware datetime object or None if parsing fails.
    """
    value = (value or "").strip()

    if not value or value == "None":
        return None

    dt: datetime | None = dateparser.parse(value, settings={"RETURN_AS_TIMEZONE_AWARE": True})
    if not dt:
        return None

    # Ensure aware in Django's current timezone
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class Command(BaseCommand):
    """Import Twitch drop campaign data from a JSON file or directory of JSON files."""

    help = "Import Twitch drop campaign data from a JSON file or directory"
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments.

        Args:
            parser: The command argument parser.
        """
        parser.add_argument(
            "path",
            type=str,
            help="Path to the JSON file or directory containing JSON files.",
        )
        parser.add_argument(
            "--processed-dir",
            type=str,
            default="processed",
            help="Subdirectory to move processed files to",
        )

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
            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                self.stdout.write(self.style.ERROR(f"Data error processing {json_file}"))
                self.stdout.write(self.style.ERROR(traceback.format_exc()))

        msg: str = f"Processed {total_files} JSON files in {directory}. Moved processed files to {processed_path}."
        self.stdout.write(self.style.SUCCESS(msg))

    def _process_file(self, file_path: Path, processed_path: Path) -> None:
        """Process a single JSON file.

        Raises:
            CommandError: If the file isn't a JSON file or has an invalid JSON structure.

        Args:
            file_path: Path to the JSON file.
            processed_path: Subdirectory to move processed files to.
        """
        raw_bytes: bytes = file_path.read_bytes()
        raw_text: str = raw_bytes.decode("utf-8")

        data = json_repair.loads(raw_text)

        broken_dir: Path = processed_path / "broken"
        broken_dir.mkdir(parents=True, exist_ok=True)

        # Check for specific keywords that indicate the file is not a valid drop campaign response
        # and move it to the "broken" directory.
        # These keywords are based on common patterns in Twitch API responses that are not related to drop campaigns.
        # If any of these keywords are found in the file, it is likely not a drop campaign response,
        # and we move it to the broken directory.
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
                target_dir: Path = broken_dir / keyword
                target_dir.mkdir(parents=True, exist_ok=True)

                self.move_file(file_path, target_dir / file_path.name)
                self.stdout.write(f"Moved {file_path} to {target_dir} (matched '{keyword}')")
                return

        # Some responses have errors:
        # {"errors": [{"message": "service timeout", "path": ["currentUser", "dropCampaigns"]}]}
        # Move them to the "actual_error" directory
        if isinstance(data, dict) and data.get("errors"):
            actual_error_dir: Path = processed_path / "actual_error"
            actual_error_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(file_path, actual_error_dir / file_path.name)
            self.stdout.write(f"Moved {file_path} to {actual_error_dir} (contains Twitch errors)")
            return

        # If file has "__typename": "BroadcastSettings" move it to the "broadcast_settings" directory
        if '"__typename": "BroadcastSettings"' in raw_text:
            broadcast_settings_dir: Path = processed_path / "broadcast_settings"
            broadcast_settings_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(file_path, broadcast_settings_dir / file_path.name)
            return

        # Remove files that only have a channel.viewerDropCampaigns and nothing more.
        # This file is useless.
        if (
            isinstance(data, dict)
            and data.get("data", {}).keys() == {"channel"}
            and data["data"]["channel"].keys() == {"id", "viewerDropCampaigns", "__typename"}
            and data["data"]["channel"]["viewerDropCampaigns"] is None
        ):
            file_path.unlink()
            self.stdout.write(f"Removed {file_path} (only contains empty viewerDropCampaigns)")
            return

        # If file only contains {"data": {"user": null}} remove the file
        if isinstance(data, dict) and data.get("data", {}).keys() == {"user"} and data["data"]["user"] is None:
            file_path.unlink()
            self.stdout.write(f"Removed {file_path} (only contains empty user)")
            return

        # If file only contains {"data": {"game": {}}} remove the file
        if isinstance(data, dict) and data.get("data", {}).keys() == {"game"} and len(data["data"]) == 1:
            game_data = data["data"]["game"]
            if isinstance(game_data, dict) and game_data.get("__typename") == "Game":
                file_path.unlink()
                self.stdout.write(f"Removed {file_path} (only contains game data)")
                return

        # If file has "__typename": "DropCurrentSession" move it to the "drop_current_session" directory so we can process it separately.
        if isinstance(data, dict) and data.get("data", {}).get("currentUser", {}).get("dropCurrentSession", {}).get("__typename") == "DropCurrentSession":
            drop_current_session_dir: Path = processed_path / "drop_current_session"
            drop_current_session_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(file_path, drop_current_session_dir / file_path.name)
            return

        # If file is a list with one item: {"data": {"user": null}}, remove it
        if (
            isinstance(data, list)
            and len(data) == 1
            and isinstance(data[0], dict)
            and data[0].get("data", {}).keys() == {"user"}
            and data[0]["data"]["user"] is None
        ):
            file_path.unlink()
            self.stdout.write(f"Removed {file_path} (list with one item: empty user)")
            return

        if isinstance(data, list):
            for _item in data:
                self.import_drop_campaign(_item, file_path=file_path)
        elif isinstance(data, dict):
            self.import_drop_campaign(data, file_path=file_path)
        else:
            msg: str = f"Invalid JSON structure in {file_path}: Expected dict or list at top level"
            raise CommandError(msg)

        self.move_file(file_path, processed_path)

    def move_file(self, file_path: Path, processed_path: Path) -> None:
        """Move file and check if already exists."""
        try:
            shutil.move(str(file_path), str(processed_path))
        except FileExistsError:
            # Rename the file if contents is different than the existing one
            with (
                file_path.open("rb") as f1,
                (processed_path / file_path.name).open("rb") as f2,
            ):
                if f1.read() != f2.read():
                    new_name: Path = processed_path / f"{file_path.stem}_duplicate{file_path.suffix}"
                    shutil.move(str(file_path), str(new_name))
                    self.stdout.write(f"Moved {file_path!s} to {new_name!s} (content differs)")
                else:
                    self.stdout.write(f"{file_path!s} already exists in {processed_path!s}, removing original file.")
                    file_path.unlink()
        except FileNotFoundError:
            self.stdout.write(f"{file_path!s} not found, skipping.")
        except (PermissionError, OSError, shutil.Error) as e:
            self.stdout.write(self.style.ERROR(f"Error moving {file_path!s} to {processed_path!s}: {e}"))
            traceback.print_exc()

    def import_drop_campaign(self, data: dict[str, Any], file_path: Path) -> None:
        """Find and import drop campaign data from various JSON structures.

        Args:
            data: The JSON data.
            file_path: The path to the file being processed.

        Raises:
            CommandError: If the JSON structure is invalid.
        """

        def try_import_from_data(d: dict[str, Any]) -> bool:
            """Try importing drop campaign data from the 'data' dict.

            Args:
                d: The dictionary to check for drop campaign data.

            Returns:
                True if any drop campaign data was imported, False otherwise.
            """
            if not isinstance(d, dict):
                return False

            if "user" in d and "dropCampaign" in d["user"]:
                self.import_to_db(d["user"]["dropCampaign"], file_path=file_path)
                return True

            if "currentUser" in d:
                current_user = d["currentUser"]

                if "dropCampaigns" in current_user:
                    campaigns = current_user["dropCampaigns"]
                    if isinstance(campaigns, list):
                        for campaign in campaigns:
                            self.import_to_db(campaign, file_path=file_path)
                        return True

                if "inventory" in current_user:
                    inventory = current_user["inventory"]
                    if "dropCampaignsInProgress" in inventory:
                        campaigns = inventory["dropCampaignsInProgress"]
                        if isinstance(campaigns, list):
                            for campaign in campaigns:
                                self.import_to_db(campaign, file_path=file_path)
                            return True

            if "channel" in d and "viewerDropCampaigns" in d["channel"]:
                campaigns = d["channel"]["viewerDropCampaigns"]
                if isinstance(campaigns, list):
                    for campaign in campaigns:
                        self.import_to_db(campaign, file_path=file_path)
                    return True
                if isinstance(campaigns, dict):
                    self.import_to_db(campaigns, file_path=file_path)
                    return True

            return False

        if "data" in data and isinstance(data["data"], dict):
            if try_import_from_data(data["data"]):
                return
            msg = "Invalid JSON structure: Missing expected drop campaign data under 'data'"
            raise CommandError(msg)

        if "responses" in data and isinstance(data["responses"], list):
            any_valid = False
            for response in data["responses"]:
                if not isinstance(response, dict):
                    continue
                try:
                    self.import_drop_campaign(response, file_path)
                    any_valid = True
                except CommandError:
                    continue
            if not any_valid:
                msg = "Invalid JSON structure: No valid dropCampaign found in 'responses' array"
                raise CommandError(msg)
            return

        msg = "Invalid JSON structure: Missing top-level 'data' or 'responses'"
        raise CommandError(msg)

    def import_to_db(self, campaign_data: dict[str, Any], file_path: Path) -> None:
        """Import drop campaign data into the database with retry logic for SQLite locks.

        Args:
            campaign_data: The drop campaign data to import.
            file_path: The path to the file being processed.
        """
        with transaction.atomic():
            game: Game = self.game_update_or_create(campaign_data=campaign_data)
            organization: Organization | None = self.owner_update_or_create(campaign_data=campaign_data)

            if organization:
                game.owner = organization
                game.save(update_fields=["owner"])

            drop_campaign: DropCampaign = self.drop_campaign_update_or_get(campaign_data=campaign_data, game=game)

            for drop_data in campaign_data.get("timeBasedDrops", []):
                self._process_time_based_drop(drop_data, drop_campaign, file_path)

            self.stdout.write(self.style.SUCCESS(f"Successfully imported drop campaign {drop_campaign.name} (ID: {drop_campaign.id})"))

    def _process_time_based_drop(self, drop_data: dict[str, Any], drop_campaign: DropCampaign, file_path: Path) -> None:
        time_based_drop: TimeBasedDrop = self.create_time_based_drop(drop_campaign=drop_campaign, drop_data=drop_data)

        benefit_edges: list[dict[str, Any]] = drop_data.get("benefitEdges", [])
        if not benefit_edges:
            self.stdout.write(self.style.WARNING(f"No benefit edges found for drop {time_based_drop.name} (ID: {time_based_drop.id})"))
            self.move_file(file_path, Path("no_benefit_edges") / file_path.name)
            return

        for benefit_edge in benefit_edges:
            benefit_data: dict[str, Any] = benefit_edge["benefit"]
            benefit_defaults = {
                "name": benefit_data.get("name"),
                "image_asset_url": benefit_data.get("imageAssetURL"),
                "created_at": parse_date(benefit_data.get("createdAt")),
                "entitlement_limit": benefit_data.get("entitlementLimit"),
                "is_ios_available": benefit_data.get("isIosAvailable"),
                "distribution_type": benefit_data.get("distributionType"),
            }
            # Filter out None values to avoid overwriting with them
            benefit_defaults = {k: v for k, v in benefit_defaults.items() if v is not None}

            benefit, _ = DropBenefit.objects.update_or_create(
                id=benefit_data["id"],
                defaults=benefit_defaults,
            )

            DropBenefitEdge.objects.update_or_create(
                drop=time_based_drop,
                benefit=benefit,
                defaults={"entitlement_limit": benefit_edge.get("entitlementLimit", 1)},
            )

    def create_time_based_drop(self, drop_campaign: DropCampaign, drop_data: dict[str, Any]) -> TimeBasedDrop:
        """Creates or updates a TimeBasedDrop instance based on the provided drop data.

        Args:
            drop_campaign (DropCampaign): The campaign to which the drop belongs.
            drop_data (dict[str, Any]): A dictionary containing drop information. Expected keys include:
                - "id" (str): The unique identifier for the drop (required).
                - "name" (str, optional): The name of the drop.
                - "requiredMinutesWatched" (int, optional): Minutes required to earn the drop.
                - "requiredSubs" (int, optional): Number of subscriptions required to earn the drop.
                - "startAt" (str, optional): ISO 8601 datetime string for when the drop starts.
                - "endAt" (str, optional): ISO 8601 datetime string for when the drop ends.

        Returns:
            TimeBasedDrop: The created or updated TimeBasedDrop instance.

        """
        time_based_drop_defaults: dict[str, Any] = {
            "campaign": drop_campaign,
            "name": drop_data.get("name"),
            "required_minutes_watched": drop_data.get("requiredMinutesWatched"),
            "required_subs": drop_data.get("requiredSubs"),
            "start_at": parse_date(drop_data.get("startAt")),
            "end_at": parse_date(drop_data.get("endAt")),
        }
        # Filter out None values to avoid overwriting with them
        time_based_drop_defaults = {k: v for k, v in time_based_drop_defaults.items() if v is not None}

        time_based_drop, created = TimeBasedDrop.objects.update_or_create(id=drop_data["id"], defaults=time_based_drop_defaults)
        if created:
            self.stdout.write(self.style.SUCCESS(f"Successfully imported time-based drop {time_based_drop.name} (ID: {time_based_drop.id})"))

        return time_based_drop

    def drop_campaign_update_or_get(
        self,
        campaign_data: dict[str, Any],
        game: Game,
    ) -> DropCampaign:
        """Update or create a drop campaign.

        Args:
            campaign_data: The drop campaign data to import.
            game: The game this drop campaign is for.
            organization: The company that owns the game. If None, the campaign will not have an owner.

        Returns:
            Returns the DropCampaign object.
        """
        drop_campaign_defaults: dict[str, Any] = {
            "game": game,
            "name": campaign_data.get("name"),
            "description": campaign_data.get("description"),
            "details_url": campaign_data.get("detailsURL"),
            "account_link_url": campaign_data.get("accountLinkURL"),
            "image_url": campaign_data.get("imageURL"),
            "start_at": parse_date(campaign_data.get("startAt") or campaign_data.get("startsAt")),
            "end_at": parse_date(campaign_data.get("endAt") or campaign_data.get("endsAt")),
            "is_account_connected": campaign_data.get("self", {}).get("isAccountConnected"),
        }
        # Filter out None values to avoid overwriting with them
        drop_campaign_defaults = {k: v for k, v in drop_campaign_defaults.items() if v is not None}

        drop_campaign, created = DropCampaign.objects.update_or_create(
            id=campaign_data["id"],
            defaults=drop_campaign_defaults,
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new drop campaign: {drop_campaign.name} (ID: {drop_campaign.id})"))
        return drop_campaign

    def owner_update_or_create(self, campaign_data: dict[str, Any]) -> Organization | None:
        """Update or create an organization.

        Args:
            campaign_data: The drop campaign data to import.

        Returns:
            Returns the Organization object.
        """
        org_data: dict[str, Any] = campaign_data.get("owner", {})
        if not org_data:
            self.stdout.write(self.style.WARNING("No owner data found in campaign data. Attempting to find organization by game."))

        organization: Organization | None = None
        if org_data:
            org_defaults: dict[str, Any] = {"name": org_data.get("name")}
            # Filter out None values to avoid overwriting with them
            org_defaults = {k: v for k, v in org_defaults.items() if v is not None}

            organization, created = Organization.objects.update_or_create(
                id=org_data["id"],
                defaults=org_defaults,
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created new organization: {organization.name} (ID: {organization.id})"))
            return organization
        return None

    def game_update_or_create(self, campaign_data: dict[str, Any]) -> Game:
        """Update or create a game.

        Args:
            campaign_data: The drop campaign data to import.

        Returns:
            Returns the Game object.
        """
        game_data: dict[str, Any] = campaign_data["game"]

        box_art_url: str = str(game_data.get("boxArtURL", "")).strip()
        display_name: str = str(game_data.get("displayName", "")).strip()
        slug: str = str(game_data.get("slug", "")).strip()

        defaults: dict[str, Any] = {}
        if box_art_url:
            defaults["box_art"] = box_art_url

        if display_name:
            defaults["display_name"] = display_name

        if slug:
            defaults["slug"] = slug

        game: Game
        game, created = self.get_or_update_if_changed(
            model=Game,
            lookup={"id": game_data["id"]},
            defaults=defaults,
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new game: {game.display_name} (ID: {game.id})"))
        return game

    def get_or_update_if_changed(self, model: type[Any], lookup: dict[str, Any], defaults: dict[str, Any]) -> tuple[Any, bool]:
        """Get or create and update model instance only when fields change.

        Args:
            model: The Django model class.
            lookup: Field lookup dictionary for get_or_create.
            defaults: Field defaults to update when changed.

        Returns:
            A tuple of (instance, created) where created is a bool.
        """
        obj, created = model.objects.get_or_create(**lookup, defaults=defaults)
        if not created:
            changed_fields = []
            for field, new_value in defaults.items():
                if getattr(obj, field) != new_value:
                    setattr(obj, field, new_value)
                    changed_fields.append(field)
            if changed_fields:
                obj.save(update_fields=changed_fields)
        return obj, created
