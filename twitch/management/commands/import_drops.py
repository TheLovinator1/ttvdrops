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
            "paths",
            nargs="+",
            type=str,
            help="Path to the JSON file or directory containing JSON files.",
        )
        parser.add_argument(
            "--processed-dir",
            type=str,
            default="processed",
            help="Subdirectory to move processed files to",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="Continue processing if an error occurs.",
        )

    def handle(self, **options) -> None:
        """Execute the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the file/directory doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
            ValueError: If the JSON file has an invalid structure.
            TypeError: If the JSON file has an invalid structure.
            AttributeError: If the JSON file has an invalid structure.
            KeyError: If the JSON file has an invalid structure.
            IndexError: If the JSON file has an invalid structure.
        """
        paths: list[str] = options["paths"]
        processed_dir: str = options["processed_dir"]
        continue_on_error: bool = options["continue_on_error"]

        for p in paths:
            try:
                path: Path = Path(p)
                processed_path: Path = path / processed_dir
                processed_path.mkdir(exist_ok=True)

                self.validate_path(path)
                self.process_drops(continue_on_error=continue_on_error, path=path, processed_path=processed_path)

            except CommandError as e:
                if not continue_on_error:
                    raise
                self.stdout.write(self.style.ERROR(f"Error processing path {p}: {e}"))
            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                if not continue_on_error:
                    raise
                self.stdout.write(self.style.ERROR(f"Data error processing path {p}"))
                self.stdout.write(self.style.ERROR(traceback.format_exc()))

    def process_drops(self, *, continue_on_error: bool, path: Path, processed_path: Path) -> None:
        """Process drops from a file or directory.

        Args:
            continue_on_error: Continue processing if an error occurs.
            path: The path to process.
            processed_path: Name of subdirectory to move processed files to.

        Raises:
            CommandError: If the file/directory doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
        """
        if path.is_file():
            self._process_file(file_path=path, processed_path=processed_path)
        elif path.is_dir():
            self._process_directory(
                directory=path,
                processed_path=processed_path,
                continue_on_error=continue_on_error,
            )
        else:
            msg: str = f"Path {path} is neither a file nor a directory"
            raise CommandError(msg)

    def validate_path(self, path: Path) -> None:
        """Validate that the path exists.

        Args:
            path: The path to validate.

        Raises:
            CommandError: If the path does not exist.
        """
        if not path.exists():
            msg: str = f"Path {path} does not exist"
            raise CommandError(msg)

    def _process_directory(self, *, directory: Path, processed_path: Path, continue_on_error: bool) -> None:
        """Process all JSON files in a directory using parallel processing.

        Args:
            directory: Path to the directory.
            processed_path: Name of subdirectory to move processed files to.
            continue_on_error: Continue processing if an error occurs.

        Raises:
            CommandError: If the file/directory doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
            ValueError: If the JSON file has an invalid structure.
            TypeError: If the JSON file has an invalid structure.
            AttributeError: If the JSON file has an invalid structure.
            KeyError: If the JSON file has an invalid structure.
            IndexError: If the JSON file has an invalid structure.
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
                if not continue_on_error:
                    raise
                self.stdout.write(self.style.ERROR(f"Error processing {json_file}: {e}"))
            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                if not continue_on_error:
                    raise
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

            # Move file to "we_should_double_check" directory for manual review
            we_should_double_check_dir: Path = processed_path / "we_should_double_check"
            we_should_double_check_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(file_path, we_should_double_check_dir / file_path.name)
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
        """
        # Add this check: If this is a known "empty" response, ignore it silently.
        if (
            "data" in data
            and "channel" in data["data"]
            and isinstance(data["data"]["channel"], dict)
            and data["data"]["channel"].get("viewerDropCampaigns") is None
        ):
            return

        def try_import_from_data(d: dict[str, Any]) -> bool:
            """Try importing drop campaign data from the 'data' dict.

            Args:
                d: The dictionary to check for drop campaign data.

            Returns:
                True if any drop campaign data was imported, False otherwise.
            """
            if not isinstance(d, dict):
                return False

            campaigns_found = []

            # Structure: {"data": {"user": {"dropCampaign": ...}}}
            if "user" in d and d["user"] and "dropCampaign" in d["user"]:
                campaigns_found.append(d["user"]["dropCampaign"])

            # Structure: {"data": {"currentUser": {"dropCampaigns": [...]}}}
            if d.get("currentUser"):
                current_user = d["currentUser"]
                if current_user.get("dropCampaigns"):
                    campaigns_found.extend(current_user["dropCampaigns"])

                # Structure: {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [...]}}}}
                if "inventory" in current_user and "dropCampaignsInProgress" in current_user["inventory"]:
                    campaigns_found.extend(current_user["inventory"]["dropCampaignsInProgress"])

            # Structure: {"data": {"channel": {"viewerDropCampaigns": [...]}}}
            if "channel" in d and d["channel"] and "viewerDropCampaigns" in d["channel"]:
                viewer_campaigns = d["channel"]["viewerDropCampaigns"]
                if isinstance(viewer_campaigns, list):
                    campaigns_found.extend(viewer_campaigns)
                elif isinstance(viewer_campaigns, dict):
                    campaigns_found.append(viewer_campaigns)

            if campaigns_found:
                for campaign in campaigns_found:
                    if campaign:  # Ensure campaign data is not null
                        self.import_to_db(campaign, file_path=file_path)
                return True

            return False

        if "data" in data and isinstance(data["data"], dict) and try_import_from_data(data["data"]):
            return

        # Handle cases where the campaign data is nested inside a list of responses
        if "responses" in data and isinstance(data["responses"], list):
            for response in data["responses"]:
                if isinstance(response, dict) and "data" in response and try_import_from_data(response["data"]):
                    return

        # Fallback for top-level campaign data if no 'data' key exists
        if "timeBasedDrops" in data and "game" in data:
            self.import_to_db(data, file_path=file_path)
            return

        self.stdout.write(self.style.WARNING(f"No valid drop campaign data found in {file_path.name}"))

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

            # Run .strip() on all string fields to remove leading/trailing whitespace
            for key, value in benefit_defaults.items():
                if isinstance(value, str):
                    benefit_defaults[key] = value.strip()

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

        # Run .strip() on all string fields to remove leading/trailing whitespace
        for key, value in time_based_drop_defaults.items():
            if isinstance(value, str):
                time_based_drop_defaults[key] = value.strip()

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
        # Run .strip() on all string fields to remove leading/trailing whitespace
        for key, value in drop_campaign_defaults.items():
            if isinstance(value, str):
                drop_campaign_defaults[key] = value.strip()

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
        if org_data:
            org_defaults: dict[str, Any] = {"name": org_data.get("name")}

            # Run .strip() on all string fields to remove leading/trailing whitespace
            for key, value in org_defaults.items():
                if isinstance(value, str):
                    org_defaults[key] = value.strip()

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

        game_defaults: dict[str, Any] = {
            "name": game_data.get("name"),
            "display_name": game_data.get("displayName"),
            "box_art": game_data.get("boxArtURL"),
            "slug": game_data.get("slug"),
        }
        # Filter out None values to avoid overwriting with them
        game_defaults = {k: v for k, v in game_defaults.items() if v is not None}

        game, created = Game.objects.update_or_create(
            id=game_data["id"],
            defaults=game_defaults,
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new game: {game.display_name} (ID: {game.id})"))
        return game
