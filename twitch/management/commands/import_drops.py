from __future__ import annotations

import concurrent.futures
import shutil
import threading
import traceback
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import dateparser
import json_repair
from django.core.exceptions import MultipleObjectsReturned
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser
from django.db import DatabaseError
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone
from tqdm import tqdm

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from datetime import datetime


@lru_cache(maxsize=4096)
def parse_date(value: str | None) -> datetime | None:
    """Parse a datetime string into a timezone-aware datetime using dateparser.

    Args:
        value: The datetime string to parse.

    Returns:
        A timezone-aware datetime object or None if parsing fails.
    """
    value = (value or "").strip()

    if not value:
        return None

    dateparser_settings: dict[str, bool | int] = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "CACHE_SIZE_LIMIT": 0,
    }
    dt: datetime | None = dateparser.parse(
        date_string=value,
        settings=dateparser_settings,  # pyright: ignore[reportArgumentType]
    )
    if not dt:
        return None

    # Ensure aware in Django's current timezone
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class Command(BaseCommand):
    """Import Twitch drop campaign data from JSON."""

    help = "Import Twitch drop campaign data from a JSON file or directory"
    requires_migrations_checks = True

    # In-memory caches
    _game_cache: dict[str, Game] = {}
    _organization_cache: dict[str, Organization] = {}
    _drop_campaign_cache: dict[str, DropCampaign] = {}
    _channel_cache: dict[str, Channel] = {}
    _benefit_cache: dict[str, DropBenefit] = {}

    # Locks for thread-safety
    _cache_locks: dict[str, threading.RLock] = {
        "game": threading.RLock(),
        "org": threading.RLock(),
        "campaign": threading.RLock(),
        "channel": threading.RLock(),
        "benefit": threading.RLock(),
    }

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
        parser.add_argument(
            "--no-preload",
            action="store_true",
            help="Do not preload existing DB objects into memory.",
        )

    def handle(self, **options) -> None:
        """Execute the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If a critical error occurs and --continue-on-error is not set.
            ValueError: If the input data is invalid.
            TypeError: If the input data is of an unexpected type.
            AttributeError: If expected attributes are missing in the data.
            KeyError: If expected keys are missing in the data.
            IndexError: If list indices are out of range in the data.
        """
        paths: list[str] = options["paths"]
        processed_dir: str = options["processed_dir"]
        continue_on_error: bool = options["continue_on_error"]
        no_preload: bool = options.get("no_preload", False)

        # Preload DB objects into caches (unless disabled)
        if not no_preload:
            try:
                self.stdout.write(
                    "Preloading existing database objects into memory...",
                )
                self._preload_caches()
                self.stdout.write(
                    f"Preloaded {len(self._game_cache)} games, "
                    f"{len(self._organization_cache)} orgs, "
                    f"{len(self._drop_campaign_cache)} campaigns, "
                    f"{len(self._channel_cache)} channels, "
                    f"{len(self._benefit_cache)} benefits.",
                )
            except (FileNotFoundError, OSError, RuntimeError):
                # If preload fails for any reason, continue without it
                msg = "Warning: Preloading caches failed — continuing without preload."
                self.stdout.write(self.style.WARNING(msg))
                self.stdout.write(self.style.ERROR(traceback.format_exc()))
                self._game_cache = {}
                self._organization_cache = {}
                self._drop_campaign_cache = {}
                self._channel_cache = {}
                self._benefit_cache = {}

        for p in paths:
            try:
                path: Path = Path(p)
                self.validate_path(path)

                # For files, use the parent directory for processed files
                if path.is_file():
                    processed_path: Path = path.parent / processed_dir
                else:
                    processed_path: Path = path / processed_dir

                processed_path.mkdir(exist_ok=True)
                self.process_drops(
                    continue_on_error=continue_on_error,
                    path=path,
                    processed_path=processed_path,
                )

            except CommandError as e:
                if not continue_on_error:
                    raise
                self.stdout.write(
                    self.style.ERROR(f"Error processing path {p}: {e}"),
                )
            except (
                ValueError,
                TypeError,
                AttributeError,
                KeyError,
                IndexError,
            ):
                if not continue_on_error:
                    raise
                self.stdout.write(
                    self.style.ERROR(f"Data error processing path {p}"),
                )
                self.stdout.write(self.style.ERROR(traceback.format_exc()))
            except KeyboardInterrupt:
                # Gracefully handle Ctrl+C
                self.stdout.write(
                    self.style.WARNING("Interrupted by user, exiting import."),
                )
                return

    def _preload_caches(self) -> None:
        """Load DB objects into in-memory caches to avoid repeated queries."""
        with self._cache_locks["game"]:
            self._game_cache = {}  # Clear existing cache
            for game_instance in Game.objects.all():
                twitch_id = str(game_instance.twitch_id)
                self._game_cache[twitch_id] = game_instance

        with self._cache_locks["org"]:
            self._organization_cache = {}
            for organization_instance in Organization.objects.all():
                twitch_id = str(organization_instance.twitch_id)
                self._organization_cache[twitch_id] = organization_instance

        with self._cache_locks["campaign"]:
            self._drop_campaign_cache = {}
            for drop_campaign_instance in DropCampaign.objects.all():
                twitch_id = str(drop_campaign_instance.twitch_id)
                self._drop_campaign_cache[twitch_id] = drop_campaign_instance

        with self._cache_locks["channel"]:
            self._channel_cache = {}
            for channel_instance in Channel.objects.all():
                twitch_id = str(channel_instance.twitch_id)
                self._channel_cache[twitch_id] = channel_instance

        with self._cache_locks["benefit"]:
            self._benefit_cache = {}
            for benefit_instance in DropBenefit.objects.all():
                twitch_id = str(benefit_instance.twitch_id)
                self._benefit_cache[twitch_id] = benefit_instance

    def process_drops(
        self,
        *,
        continue_on_error: bool,
        path: Path,
        processed_path: Path,
    ) -> None:
        """Process drops from a file or directory.

        Args:
            continue_on_error: Continue processing if an error occurs.
            path: The path to process.
            processed_path: Name of subdirectory to move processed files to.

        Raises:
            CommandError: If the path is neither a file nor a directory.
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

    def _process_directory(
        self,
        *,
        directory: Path,
        processed_path: Path,
        continue_on_error: bool,
    ) -> None:
        """Process all JSON files in a directory using parallel processing.

        Args:
            directory: The directory containing JSON files.
            processed_path: Name of subdirectory to move processed files to.
            continue_on_error: Continue processing if an error occurs.

        Raises:
            AttributeError: If expected attributes are missing in the data.
            CommandError: If a critical error occurs and --continue-on-error is not set.
            IndexError: If list indices are out of range in the data.
            KeyboardInterrupt: If the process is interrupted by the user.
            KeyError: If expected keys are missing in the data.
            TypeError: If the input data is of an unexpected type.
            ValueError: If the input data is invalid.
        """
        json_files: list[Path] = list(directory.glob("*.json"))
        if not json_files:
            self.stdout.write(
                self.style.WARNING(f"No JSON files found in {directory}"),
            )
            return

        total_files: int = len(json_files)
        self.stdout.write(f"Found {total_files} JSON files to process")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            try:
                future_to_file: dict[concurrent.futures.Future[None], Path] = {
                    executor.submit(
                        self._process_file,
                        json_file,
                        processed_path,
                    ): json_file
                    for json_file in json_files
                }
                # Wrap the as_completed iterator with tqdm for a progress bar
                for future in tqdm(
                    concurrent.futures.as_completed(future_to_file),
                    total=total_files,
                    desc="Processing files",
                ):
                    json_file: Path = future_to_file[future]
                    try:
                        future.result()
                    except CommandError as e:
                        if not continue_on_error:
                            # To stop all processing, we shut down the executor and re-raise
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise
                        self.stdout.write(
                            self.style.ERROR(
                                f"Error processing {json_file}: {e}",
                            ),
                        )
                    except (
                        ValueError,
                        TypeError,
                        AttributeError,
                        KeyError,
                        IndexError,
                    ):
                        if not continue_on_error:
                            # To stop all processing, we shut down the executor and re-raise
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise
                        self.stdout.write(
                            self.style.ERROR(
                                f"Data error processing {json_file}",
                            ),
                        )
                        self.stdout.write(
                            self.style.ERROR(traceback.format_exc()),
                        )

                msg: str = (
                    f"Processed {total_files} JSON files in {directory}. Moved processed files to {processed_path}."
                )
                self.stdout.write(self.style.SUCCESS(msg))

            except KeyboardInterrupt:
                self.stdout.write(
                    self.style.WARNING(
                        "Interruption received, shutting down threads immediately...",
                    ),
                )
                executor.shutdown(wait=False, cancel_futures=True)
                # Re-raise the exception to allow the main `handle` method to catch it and exit
                raise

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
                tqdm.write(
                    f"Moved {file_path} to {target_dir} (matched '{keyword}')",
                )
                return

        # Some responses have errors:
        # {"errors": [{"message": "service timeout", "path": ["currentUser", "dropCampaigns"]}]}
        # Move them to the "actual_error" directory
        if isinstance(data, dict) and data.get("errors"):
            actual_error_dir: Path = processed_path / "actual_error"
            actual_error_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(file_path, actual_error_dir / file_path.name)
            tqdm.write(
                f"Moved {file_path} to {actual_error_dir} (contains Twitch errors)",
            )
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
            tqdm.write(
                f"Removed {file_path} (only contains empty viewerDropCampaigns)",
            )
            return

        # If file only contains {"data": {"user": null}} remove the file
        if isinstance(data, dict) and data.get("data", {}).keys() == {"user"} and data["data"]["user"] is None:
            file_path.unlink()
            tqdm.write(f"Removed {file_path} (only contains empty user)")
            return

        # If file only contains {"data": {"game": {}}} remove the file
        if isinstance(data, dict) and data.get("data", {}).keys() == {"game"} and len(data["data"]) == 1:
            game_data = data["data"]["game"]
            if isinstance(game_data, dict) and game_data.get("__typename") == "Game":
                file_path.unlink()
                tqdm.write(f"Removed {file_path} (only contains game data)")
                return

        # If file has "__typename": "DropCurrentSession" move it to the "drop_current_session" directory so we can process it separately.  # noqa: E501
        if (
            isinstance(data, dict)
            and data.get("data", {}).get("currentUser", {}).get("dropCurrentSession", {}).get("__typename")
            == "DropCurrentSession"
        ):
            drop_current_session_dir: Path = processed_path / "drop_current_session"
            drop_current_session_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(
                file_path,
                drop_current_session_dir / file_path.name,
            )
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
            tqdm.write(f"Removed {file_path} (list with one item: empty user)")
            return

        if isinstance(data, list):
            for item in data:
                self.import_drop_campaign(item, file_path=file_path)
        elif isinstance(data, dict):
            self.import_drop_campaign(data, file_path=file_path)
        else:
            msg: str = f"Invalid JSON structure in {file_path}: Expected dict or list at top level"

            # Move file to "we_should_double_check" directory for manual review
            we_should_double_check_dir: Path = processed_path / "we_should_double_check"
            we_should_double_check_dir.mkdir(parents=True, exist_ok=True)
            self.move_file(
                file_path,
                we_should_double_check_dir / file_path.name,
            )
            raise CommandError(msg)

        self.move_file(file_path, processed_path)

    def move_file(self, file_path: Path, processed_path: Path) -> None:
        """Move file and check if already exists."""
        try:
            shutil.move(str(file_path), str(processed_path))
        except FileExistsError:
            # Rename the file if contents is different than the existing one
            try:
                with (
                    file_path.open("rb") as f1,
                    (processed_path / file_path.name).open("rb") as f2,
                ):
                    if f1.read() != f2.read():
                        new_name: Path = processed_path / f"{file_path.stem}_duplicate{file_path.suffix}"
                        shutil.move(str(file_path), str(new_name))
                        tqdm.write(
                            f"Moved {file_path!s} to {new_name!s} (content differs)",
                        )
                    else:
                        tqdm.write(
                            f"{file_path!s} already exists in {processed_path!s}, removing original file.",
                        )
                        file_path.unlink()
            except FileNotFoundError:
                tqdm.write(
                    f"{file_path!s} not found when handling duplicate case, skipping.",
                )
        except FileNotFoundError:
            tqdm.write(f"{file_path!s} not found, skipping.")
        except (PermissionError, OSError, shutil.Error) as e:
            self.stdout.write(
                self.style.ERROR(
                    f"Error moving {file_path!s} to {processed_path!s}: {e}",
                ),
            )
            traceback.print_exc()

    def import_drop_campaign(
        self,
        data: dict[str, Any],
        file_path: Path,
    ) -> None:
        """Find and import drop campaign data from various JSON structures."""
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
              True if import was attempted, False otherwise.
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
                    campaigns_found.extend(
                        current_user["inventory"]["dropCampaignsInProgress"],
                    )

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

        tqdm.write(
            self.style.WARNING(
                f"No valid drop campaign data found in {file_path.name}",
            ),
        )

    def import_to_db(
        self,
        campaign_data: dict[str, Any],
        file_path: Path,
    ) -> None:
        """Import drop campaign data into the database with retry logic for SQLite locks.

        Args:
            campaign_data: The drop campaign data to import.
            file_path: The path to the file being processed.
        """
        with transaction.atomic():
            game: Game = self.game_update_or_create(
                campaign_data=campaign_data,
            )
            organization: Organization | None = self.owner_update_or_create(
                campaign_data=campaign_data,
            )

            if organization and game.owner != organization:
                game.owner = organization
                game.save(update_fields=["owner"])

            drop_campaign: DropCampaign = self.drop_campaign_update_or_get(
                campaign_data=campaign_data,
                game=game,
            )

            for drop_data in campaign_data.get("timeBasedDrops", []):
                self._process_time_based_drop(
                    drop_data,
                    drop_campaign,
                    file_path,
                )

    def _process_time_based_drop(
        self,
        drop_data: dict[str, Any],
        drop_campaign: DropCampaign,
        file_path: Path,
    ) -> None:
        time_based_drop: TimeBasedDrop = self.create_time_based_drop(
            drop_campaign=drop_campaign,
            drop_data=drop_data,
        )

        benefit_edges: list[dict[str, Any]] = drop_data.get("benefitEdges", [])
        if not benefit_edges:
            tqdm.write(
                self.style.WARNING(
                    f"No benefit edges found for drop {time_based_drop.name} (ID: {time_based_drop.twitch_id})",
                ),
            )
            self.move_file(
                file_path,
                Path("no_benefit_edges") / file_path.name,
            )
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
            for key, value in list(benefit_defaults.items()):
                if isinstance(value, str):
                    benefit_defaults[key] = value.strip()

            # Filter out None values to avoid overwriting with them
            benefit_defaults = {k: v for k, v in benefit_defaults.items() if v is not None}

            # Use cached create/update for benefits
            benefit = self._get_or_create_benefit(
                benefit_data["id"],
                benefit_defaults,
            )

            try:
                with transaction.atomic():
                    drop_benefit_edge, created = DropBenefitEdge.objects.update_or_create(
                        drop=time_based_drop,
                        benefit=benefit,
                        defaults={
                            "entitlement_limit": benefit_edge.get(
                                "entitlementLimit",
                                1,
                            ),
                        },
                    )
                    if created:
                        tqdm.write(f"Added {drop_benefit_edge}")
            except MultipleObjectsReturned as e:
                msg = f"Error: Multiple DropBenefitEdge objects found for drop {time_based_drop.twitch_id} and benefit {benefit.twitch_id}. Cannot update or create."  # noqa: E501
                raise CommandError(msg) from e
            except (IntegrityError, DatabaseError, TypeError, ValueError) as e:
                msg = f"Database or validation error creating DropBenefitEdge for drop {time_based_drop.twitch_id} and benefit {benefit.twitch_id}: {e}"  # noqa: E501
                raise CommandError(msg) from e

    def create_time_based_drop(
        self,
        drop_campaign: DropCampaign,
        drop_data: dict[str, Any],
    ) -> TimeBasedDrop:
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

        Raises:
            CommandError: If there is a database error or multiple objects are returned.

        Returns:
            TimeBasedDrop: The created or updated TimeBasedDrop instance.
        """
        time_based_drop_defaults: dict[str, Any] = {
            "campaign": drop_campaign,
            "name": drop_data.get("name"),
            "required_minutes_watched": drop_data.get(
                "requiredMinutesWatched",
            ),
            "required_subs": drop_data.get("requiredSubs"),
            "start_at": parse_date(drop_data.get("startAt")),
            "end_at": parse_date(drop_data.get("endAt")),
        }

        # Run .strip() on all string fields to remove leading/trailing whitespace
        for key, value in list(time_based_drop_defaults.items()):
            if isinstance(value, str):
                time_based_drop_defaults[key] = value.strip()

        # Filter out None values to avoid overwriting with them
        time_based_drop_defaults = {k: v for k, v in time_based_drop_defaults.items() if v is not None}

        try:
            with transaction.atomic():
                time_based_drop, created = TimeBasedDrop.objects.update_or_create(
                    id=drop_data["id"],
                    defaults=time_based_drop_defaults,
                )
                if created:
                    tqdm.write(f"Added {time_based_drop}")
        except MultipleObjectsReturned as e:
            msg = f"Error: Multiple TimeBasedDrop objects found for drop {drop_data['id']}. Cannot update or create."
            raise CommandError(msg) from e
        except (IntegrityError, DatabaseError, TypeError, ValueError) as e:
            msg = f"Database or validation error creating TimeBasedDrop for drop {drop_data['id']}: {e}"
            raise CommandError(msg) from e

        return time_based_drop

    def _get_or_create_cached(
        self,
        model_name: str,
        model_class: type[Game | Organization | DropCampaign | Channel | DropBenefit],
        obj_id: str | int,
        defaults: dict[str, Any] | None = None,
    ) -> Game | Organization | DropCampaign | Channel | DropBenefit | str | int | None:
        """Generic get-or-create that uses the in-memory cache and writes only if needed.

        This implementation is thread-safe and transaction-aware.

        Args:
            model_name: The name of the model (used for cache and lock).
            model_class: The Django model class.
            obj_id: The ID of the object to get or create.
            defaults: A dictionary of fields to set on creation or update.

        Returns:
            The retrieved or created object.
        """
        sid = str(obj_id)
        defaults = defaults or {}

        lock = self._cache_locks.get(model_name)
        if lock is None:
            # Fallback for models without a dedicated cache/lock
            obj, created = model_class.objects.update_or_create(
                id=obj_id,
                defaults=defaults,
            )
            if created:
                tqdm.write(f"Added {obj}")
            return obj

        with lock:
            cache = getattr(self, f"_{model_name}_cache", None)
            if cache is None:
                cache = {}
                setattr(self, f"_{model_name}_cache", cache)

            # First, check the cache.
            cached_obj = cache.get(sid)
            if cached_obj:
                return cached_obj

            # Not in cache, so we need to go to the database.
            # Use get_or_create which is safer in a race. It might still fail if two threads
            # try to create at the exact same time, so we wrap it.
            try:
                obj, created = model_class.objects.get_or_create(
                    id=obj_id,
                    defaults=defaults,
                )
            except IntegrityError:
                # Another thread created it between our `get` and `create` attempt.
                # The object is guaranteed to exist now, so we can just fetch it.
                obj = model_class.objects.get(id=obj_id)
                created = False

            if not created:
                # The object already existed, check if our data is newer and update if needed.
                changed = False
                update_fields = []
                for key, val in defaults.items():
                    if hasattr(obj, key) and getattr(obj, key) != val:
                        setattr(obj, key, val)
                        changed = True
                        update_fields.append(key)
                if changed:
                    obj.save(update_fields=update_fields)

            # IMPORTANT: Defer the cache update until the transaction is successful.
            # This is the key to preventing the race condition.
            transaction.on_commit(lambda: cache.update({sid: obj}))

            if created:
                tqdm.write(f"Added {obj}")

            return obj

    def _get_or_create_benefit(
        self,
        benefit_id: str | int,
        defaults: dict[str, Any],
    ) -> DropBenefit:
        return self._get_or_create_cached(
            "benefit",
            DropBenefit,
            benefit_id,
            defaults,
        )  # pyright: ignore[reportReturnType]

    def game_update_or_create(self, campaign_data: dict[str, Any]) -> Game:
        """Update or create a game with caching.

        Args:
            campaign_data: The campaign data containing game information.

        Raises:
            TypeError: If the retrieved object is not a Game instance.

        Returns:
            The retrieved or created Game object.
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

        game: Game | Organization | DropCampaign | Channel | DropBenefit | str | int | None = (
            self._get_or_create_cached(
                model_name="game",
                model_class=Game,
                obj_id=game_data["id"],
                defaults=game_defaults,
            )
        )
        if not isinstance(game, Game):
            msg = "Expected a Game instance from _get_or_create_cached"
            raise TypeError(msg)

        return game

    def owner_update_or_create(
        self,
        campaign_data: dict[str, Any],
    ) -> Organization | None:
        """Update or create an organization with caching.

        Args:
            campaign_data: The campaign data containing owner information.

        Raises:
            TypeError: If the retrieved object is not an Organization instance.

        Returns:
            The retrieved or created Organization object, or None if no owner data is present.
        """
        org_data: dict[str, Any] = campaign_data.get("owner", {})
        if org_data:
            org_defaults: dict[str, Any] = {"name": org_data.get("name")}
            org_defaults = {k: v.strip() if isinstance(v, str) else v for k, v in org_defaults.items() if v is not None}

            owner = self._get_or_create_cached(
                model_name="org",
                model_class=Organization,
                obj_id=org_data["id"],
                defaults=org_defaults,
            )
            if not isinstance(owner, Organization):
                msg = "Expected an Organization instance from _get_or_create_cached"
                raise TypeError(msg)

            return owner
        return None

    def drop_campaign_update_or_get(
        self,
        campaign_data: dict[str, Any],
        game: Game,
    ) -> DropCampaign:
        """Update or create a drop campaign with caching and channel handling.

        Args:
            campaign_data: The campaign data containing drop campaign information.
            game: The associated Game object.

        Raises:
            TypeError: If the retrieved object is not a DropCampaign instance.

        Returns:
            The retrieved or created DropCampaign object.
        """
        allow_data = campaign_data.get("allow", {})
        allow_is_enabled = allow_data.get("isEnabled")

        drop_campaign_defaults: dict[str, Any] = {
            "game": game,
            "name": campaign_data.get("name"),
            "description": campaign_data.get("description"),
            "details_url": campaign_data.get("detailsURL"),
            "account_link_url": campaign_data.get("accountLinkURL"),
            "image_url": campaign_data.get("imageURL"),
            "start_at": parse_date(
                campaign_data.get("startAt") or campaign_data.get("startsAt"),
            ),
            "end_at": parse_date(
                campaign_data.get("endAt") or campaign_data.get("endsAt"),
            ),
            "is_account_connected": (
                campaign_data.get(
                    "self",
                    {},
                ).get("isAccountConnected")
            ),
            "allow_is_enabled": allow_is_enabled,
        }

        # Run .strip() on all string fields to remove leading/trailing whitespace
        for key, value in list(drop_campaign_defaults.items()):
            if isinstance(value, str):
                drop_campaign_defaults[key] = value.strip()

        # Filter out None values to avoid overwriting with them
        drop_campaign_defaults = {k: v for k, v in drop_campaign_defaults.items() if v is not None}

        drop_campaign = self._get_or_create_cached(
            model_name="campaign",
            model_class=DropCampaign,
            obj_id=campaign_data["id"],
            defaults=drop_campaign_defaults,
        )
        if not isinstance(drop_campaign, DropCampaign):
            msg = "Expected a DropCampaign instance from _get_or_create_cached"
            raise TypeError(msg)

        # Handle allow_channels (many-to-many relationship)
        allow_channels: list[dict[str, str]] = allow_data.get("channels", [])
        if allow_channels:
            channel_objects: list[Channel] = []
            for channel_data in allow_channels:
                channel_defaults: dict[str, str | None] = {
                    "name": channel_data.get("name"),
                    "display_name": channel_data.get("displayName"),
                }
                # Run .strip() on all string fields to remove leading/trailing whitespace
                for key, value in channel_defaults.items():
                    if isinstance(value, str):
                        channel_defaults[key] = value.strip()

                # Filter out None values
                channel_defaults = {k: v for k, v in channel_defaults.items() if v is not None}

                # Use cached helper for channels
                channel = self._get_or_create_cached(
                    model_name="channel",
                    model_class=Channel,
                    obj_id=channel_data["id"],
                    defaults=channel_defaults,
                )
                if not isinstance(channel, Channel):
                    msg = "Expected a Channel instance from _get_or_create_cached"
                    raise TypeError(msg)

                channel_objects.append(channel)

            # Set the many-to-many relationship (save only if different)
            current_ids = set(
                drop_campaign.allow_channels.values_list("id", flat=True),
            )
            new_ids = {ch.twitch_id for ch in channel_objects}
            if current_ids != new_ids:
                drop_campaign.allow_channels.set(channel_objects)

        return drop_campaign
