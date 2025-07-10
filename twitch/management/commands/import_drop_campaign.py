from __future__ import annotations

import concurrent.futures
import json
import shutil
import time
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import OperationalError, transaction

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
        parser.add_argument(
            "--max-workers",
            type=int,
            default=4,
            help="Maximum number of worker processes to use for parallel importing (default: 4)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of files to process in each batch (default: 100)",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=5,
            help="Maximum number of retries for database operations when SQLite is locked (default: 5)",
        )
        parser.add_argument(
            "--retry-delay",
            type=float,
            default=0.5,
            help="Delay in seconds between retries for database operations (default: 0.5)",
        )

    def handle(self, **options) -> None:  # noqa: ANN003
        """Execute the command.

        Args:
            **options: Arbitrary keyword arguments.

        Raises:
            CommandError: If the file/directory doesn't exist, isn't a JSON file,
                or has an invalid JSON structure.
        """
        path: str = options["path"]
        processed_dir: str = options["processed_dir"]
        max_workers: int = options["max_workers"]
        batch_size: int = options["batch_size"]
        max_retries: int = options["max_retries"]
        retry_delay: float = options["retry_delay"]
        path_obj = Path(path)

        # Store retry configuration in instance variables to make them available to other methods
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Check if path exists
        if not path_obj.exists():
            msg = f"Path {path} does not exist"
            raise CommandError(msg)

        # Process single file or directory
        if path_obj.is_file():
            self._process_file(path_obj, processed_dir)
        elif path_obj.is_dir():
            self._process_directory(path_obj, processed_dir, max_workers, batch_size)
        else:
            msg = f"Path {path} is neither a file nor a directory"
            raise CommandError(msg)

    def _process_directory(self, directory: Path, processed_dir: str, max_workers: int = 4, batch_size: int = 100) -> None:
        """Process all JSON files in a directory using parallel processing.

        Args:
            directory: Path to the directory.
            processed_dir: Name of subdirectory to move processed files to.
            max_workers: Maximum number of worker processes to use.
            batch_size: Number of files to process in each batch.
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

        total_files = len(json_files)
        self.stdout.write(f"Found {total_files} JSON files to process")

        # Process files in batches to avoid memory issues
        processed_files = 0
        error_count = 0
        imported_campaigns = 0

        # Process files in batches with parallel workers
        for i in range(0, total_files, batch_size):
            batch = json_files[i : i + batch_size]
            batch_size_actual = len(batch)
            self.stdout.write(f"Processing batch {i // batch_size + 1} with {batch_size_actual} files...")

            # Process batch files concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all files in the batch for processing
                future_to_file = {executor.submit(self._process_file, json_file, processed_dir): json_file for json_file in batch}

                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_file):
                    json_file = future_to_file[future]
                    try:
                        # Get the number of campaigns imported from this file
                        num_campaigns = future.result()
                        processed_files += 1
                        imported_campaigns += num_campaigns

                        if processed_files % 100 == 0 or processed_files == total_files:
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"Progress: {processed_files}/{total_files} files processed "
                                    f"({processed_files / total_files * 100:.1f}%), "
                                    f"{imported_campaigns} campaigns imported"
                                )
                            )
                    except CommandError as e:
                        error_count += 1
                        self.stdout.write(self.style.ERROR(f"Error processing {json_file}: {e}"))
                    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
                        # Handle common errors explicitly instead of catching all exceptions
                        error_count += 1
                        self.stdout.write(self.style.ERROR(f"Data error processing {json_file}: {e!s}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed processing {processed_files} files with {error_count} errors. Imported {imported_campaigns} drop campaigns."
            )
        )

    def _process_file(self, file_path: Path, processed_dir: str) -> int:
        """Process a single JSON file.

        Args:
            file_path: Path to the JSON file.
            processed_dir: Name of subdirectory to move processed files to.

        Returns:
            int: Number of drop campaigns imported from this file.

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

        # Counter for imported campaigns
        campaigns_imported = 0

        # Check if data is a list (array of objects)
        if isinstance(data, list):
            # Process each item in the list
            for item in data:
                if "data" in item and "user" in item["data"] and "dropCampaign" in item["data"]["user"]:
                    drop_campaign_data = item["data"]["user"]["dropCampaign"]
                    # Process the data with retry logic for database locks
                    self._import_drop_campaign_with_retry(drop_campaign_data)
                    campaigns_imported += 1
        else:
            # Check if the JSON has the expected structure for a single object
            if "data" not in data or "user" not in data["data"] or "dropCampaign" not in data["data"]["user"]:
                msg = "Invalid JSON structure: Missing data.user.dropCampaign"
                raise CommandError(msg)

            # Extract drop campaign data for a single object
            drop_campaign_data = data["data"]["user"]["dropCampaign"]
            # Process the data with retry logic for database locks
            self._import_drop_campaign_with_retry(drop_campaign_data)
            campaigns_imported += 1

        # Move the processed file to the processed directory
        if processed_dir:
            processed_path = file_path.parent / processed_dir
            if not processed_path.exists():
                processed_path.mkdir()

            # Move the file to the processed directory
            new_path = processed_path / file_path.name
            shutil.move(str(file_path), str(new_path))

        # Return the number of campaigns imported
        return campaigns_imported

    def _import_drop_campaign_with_retry(self, campaign_data: dict[str, Any]) -> None:
        """Import drop campaign data into the database with retry logic for SQLite locks.

        Args:
            campaign_data: The drop campaign data to import.

        Raises:
            OperationalError: If the database is still locked after max retries.
        """
        # Retry logic for database operations
        max_retries = getattr(self, "max_retries", 5)  # Default to 5 if not set
        retry_delay = getattr(self, "retry_delay", 0.5)  # Default to 0.5 seconds if not set

        for attempt in range(max_retries):
            try:
                with transaction.atomic():
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
                # If we get here, the transaction completed successfully
                break
            except OperationalError as e:
                # Check if this is a database lock error
                if "database is locked" in str(e).lower():
                    if attempt < max_retries - 1:  # Don't sleep on the last attempt
                        sleep_time = retry_delay * (2**attempt)  # Exponential backoff
                        self.stdout.write(
                            self.style.WARNING(f"Database locked, retrying in {sleep_time:.2f}s (attempt {attempt + 1}/{max_retries})")
                        )
                        time.sleep(sleep_time)
                    else:
                        self.stdout.write(self.style.ERROR(f"Database still locked after {max_retries} attempts"))
                        raise
                else:
                    # Not a lock error, re-raise
                    raise
