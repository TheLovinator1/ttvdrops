from __future__ import annotations

import json
import os
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Literal
from urllib.parse import urlparse

import httpx
import json_repair
from colorama import Fore
from colorama import Style
from colorama import init as colorama_init
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser
from json_repair import JSONReturnType
from pydantic import ValidationError
from tqdm import tqdm

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.schemas import ChannelInfoSchema
from twitch.schemas import CurrentUserSchema
from twitch.schemas import DropBenefitEdgeSchema
from twitch.schemas import DropBenefitSchema
from twitch.schemas import DropCampaignACLSchema
from twitch.schemas import DropCampaignSchema
from twitch.schemas import GameSchema
from twitch.schemas import GraphQLResponse
from twitch.schemas import OrganizationSchema
from twitch.schemas import RewardCampaign as RewardCampaignSchema
from twitch.schemas import TimeBasedDropSchema
from twitch.utils import is_twitch_box_art_url
from twitch.utils import normalize_twitch_box_art_url
from twitch.utils import parse_date


def get_broken_directory_root() -> Path:
    """Get the root broken directory path from environment or default.

    Reads from TTVDROPS_BROKEN_DIR environment variable if set,
    otherwise defaults to a directory in the current user's home.

    Returns:
        Path to the root broken directory.
    """
    env_path: str | None = os.environ.get("TTVDROPS_BROKEN_DIR")
    if env_path:
        return Path(env_path)

    # Default to ~/ttvdrops/broken/
    home: Path = Path.home()
    return home / "ttvdrops" / "broken"


def get_imported_directory_root() -> Path:
    """Get the root imported directory path from environment or default.

    Reads from TTVDROPS_IMPORTED_DIR environment variable if set,
    otherwise defaults to a directory in the current user's home.

    Returns:
        Path to the root imported directory.
    """
    env_path: str | None = os.environ.get("TTVDROPS_IMPORTED_DIR")
    if env_path:
        return Path(env_path)

    # Default to ~/ttvdrops/imported/
    home: Path = Path.home()
    return home / "ttvdrops" / "imported"


def _build_broken_directory(
    reason: str,
    operation_name: str | None = None,
) -> Path:
    """Compute a deeply nested broken directory for triage.

    Directory pattern: <broken_root>/<reason>/<operation>/<YYYY>/<MM>/<DD>
    This keeps unrelated failures isolated and easy to browse later.

    Args:
        reason: High-level reason bucket (e.g., validation_failed).
        operation_name: Optional operationName extracted from the payload.

    Returns:
        Path to the directory where the file should live.
    """
    safe_reason: str = reason.replace(" ", "_")
    now: datetime = datetime.now(tz=UTC)

    # If operation_name matches reason, skip it to avoid duplicate directories
    if operation_name and operation_name.replace(" ", "_") == safe_reason:
        broken_dir: Path = get_broken_directory_root() / safe_reason / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
    else:
        op_segment: str = (operation_name or "unknown_op").replace(" ", "_")
        broken_dir = get_broken_directory_root() / safe_reason / op_segment / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"

    broken_dir.mkdir(parents=True, exist_ok=True)
    return broken_dir


def move_failed_validation_file(file_path: Path, operation_name: str | None = None) -> Path:
    """Moves a file that failed validation to a 'broken' subdirectory.

    Args:
        file_path: Path to the file that failed validation
        operation_name: Optional GraphQL operation name for finer grouping

    Returns:
        Path to the 'broken' directory where the file was moved
    """
    broken_dir: Path = _build_broken_directory(
        reason="validation_failed",
        operation_name=operation_name,
    )

    target_file: Path = broken_dir / file_path.name
    file_path.rename(target_file)

    return broken_dir


def move_file_to_broken_subdir(
    file_path: Path,
    subdir: str,
    operation_name: str | None = None,
) -> Path:
    """Move file to broken/<subdir> and return that directory path.

    Args:
        file_path: The file to move.
        subdir: Subdirectory name under "broken" (e.g., the matched keyword).
        operation_name: Optional GraphQL operation name for finer grouping

    Returns:
        Path to the directory where the file was moved.
    """
    broken_dir: Path = _build_broken_directory(
        reason=subdir,
        operation_name=operation_name,
    )

    target_file: Path = broken_dir / file_path.name
    file_path.rename(target_file)

    return broken_dir


def move_completed_file(
    file_path: Path,
    operation_name: str | None = None,
    campaign_structure: str | None = None,
) -> Path:
    """Move a successfully processed file into an operation-named directory.

    Moves to <imported_root>/<operation_name>/ or
    <imported_root>/<operation_name>/<campaign_structure>/ if campaign_structure is provided.

    Args:
        file_path: Path to the processed JSON file.
        operation_name: GraphQL operationName extracted from the payload.
        campaign_structure: Optional campaign structure type (e.g., "user_drop_campaign").

    Returns:
        Path to the directory where the file was moved.
    """
    safe_op: str = (operation_name or "unknown_op").replace(" ", "_").replace("/", "_").replace("\\", "_")
    target_dir: Path = get_imported_directory_root() / safe_op

    if campaign_structure:
        target_dir /= campaign_structure

    target_dir.mkdir(parents=True, exist_ok=True)

    target_file: Path = target_dir / file_path.name
    file_path.rename(target_file)

    return target_dir


# Pre-compute keyword search patterns for faster detection
_KNOWN_NON_CAMPAIGN_PATTERNS: dict[str, str] = {
    keyword: f'"operationName": "{keyword}"'
    for keyword in [
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
}


def detect_non_campaign_keyword(raw_text: str) -> str | None:
    """Detect if payload is a known non-drop-campaign response.

    Looks for operationName values that are commonly present in unrelated
    Twitch API responses. Returns the matched keyword if found.

    Args:
        raw_text: The raw JSON text to scan.

    Returns:
        The matched keyword, or None if no match found.
    """
    for keyword, pattern in _KNOWN_NON_CAMPAIGN_PATTERNS.items():
        if pattern in raw_text:
            return keyword
    return None


def detect_error_only_response(
    parsed_json: JSONReturnType | tuple[JSONReturnType, list[dict[str, str]]] | str,
) -> str | None:
    """Detect if response contains only GraphQL errors and no data.

    Args:
        parsed_json: The parsed JSON data from the file.

    Returns:
        Error description if only errors present, None if data exists.
    """
    # Handle tuple from json_repair
    if isinstance(parsed_json, tuple):
        parsed_json = parsed_json[0]

    # Check if it's a list of responses
    if isinstance(parsed_json, list):
        for item in parsed_json:
            if isinstance(item, dict) and "errors" in item:
                errors: Any = item.get("errors")
                data: Any = item.get("data")
                # Data is missing if key doesn't exist or value is None
                if errors and data is None and isinstance(errors, list) and len(errors) > 0:
                    first_error: dict[str, Any] = errors[0]
                    message: str = first_error.get("message", "unknown error")
                    return f"error_only: {message}"
        return None

    if not isinstance(parsed_json, dict):
        return None

    # Check if it's a single response dict
    if "errors" in parsed_json:
        errors = parsed_json.get("errors")
        data = parsed_json.get("data")

        # Data is missing if key doesn't exist or value is None
        if errors and data is None and isinstance(errors, list) and len(errors) > 0:
            first_error = errors[0]
            message = first_error.get("message", "unknown error")
            return f"error_only: {message}"

    return None


def extract_operation_name_from_parsed(
    payload: JSONReturnType | tuple[JSONReturnType, list[dict[str, str]]] | str,
) -> str | None:
    """Extract GraphQL operationName from an already parsed JSON payload.

    This is safer than substring scanning. The expected location is
    `payload["extensions"]["operationName"]`, but we guard against missing
    keys.

    Args:
        payload: Parsed JSON object or list.

    Returns:
        The operation name if found, otherwise None.
    """
    # Be defensive; never let provenance extraction break the import.
    # If payload is a list, try to extract from the first item
    if isinstance(payload, list):
        if len(payload) > 0 and isinstance(payload[0], dict):
            return extract_operation_name_from_parsed(payload[0])
        return None

    # json_repair can return (data, repair_log)
    if isinstance(payload, tuple):
        if len(payload) > 0:
            return extract_operation_name_from_parsed(payload[0])
        return None

    if not isinstance(payload, dict):
        return None

    extensions: dict[str, Any] | None = payload.get("extensions")
    if isinstance(extensions, dict):
        op_name: str | None = extensions.get("operationName")
        if isinstance(op_name, str):
            return op_name
    return None


def repair_partially_broken_json(raw_text: str) -> str:  # noqa: PLR0915
    """Attempt to repair partially broken JSON with multiple fallback strategies.

    Handles "half-bad" JSON by:
    1. First attempting json_repair on the whole content
    2. If that fails, tries to extract valid JSON objects from the text
    3. Falls back to wrapping content in an array if possible

    Args:
        raw_text: The potentially broken JSON string.

    Returns:
        A JSON-valid string, either repaired or best-effort fixed.
    """
    # Strategy 1: Direct repair attempt
    try:
        fixed: str = json_repair.repair_json(raw_text)
        # Validate it produces valid JSON
        parsed_data = json.loads(fixed)

        # If it's a list, validate all items are GraphQL responses
        if isinstance(parsed_data, list):
            # Filter to only keep GraphQL responses
            filtered = [
                item for item in parsed_data if isinstance(item, dict) and ("data" in item or "extensions" in item)
            ]
            if filtered:
                # If we filtered anything out, return the filtered version
                if len(filtered) < len(parsed_data):
                    return json.dumps(filtered)
                # Otherwise return as-is
                return fixed
        # Single dict - check if it's a GraphQL response
        elif isinstance(parsed_data, dict):
            if "data" in parsed_data or "extensions" in parsed_data:
                return fixed
    except ValueError, TypeError, json.JSONDecodeError:
        pass

    # Strategy 2: Try wrapping in array brackets and validate the result
    # Only use this if it produces valid GraphQL responses
    try:
        wrapped: str = f"[{raw_text}]"
        wrapped_data = json.loads(wrapped)
        # Validate that all items look like GraphQL responses
        if isinstance(wrapped_data, list) and wrapped_data:  # noqa: SIM102
            # Check if all items have "data" or "extensions" (GraphQL response structure)
            if all(isinstance(item, dict) and ("data" in item or "extensions" in item) for item in wrapped_data):
                return wrapped
    except ValueError, json.JSONDecodeError:
        pass

    # Strategy 3: Try to extract individual valid GraphQL response objects
    # Look for balanced braces and try to parse them, but only keep objects
    # that look like GraphQL responses (have "data" or "extensions" fields)
    valid_objects: list[dict[str, Any]] = []
    depth: int = 0
    current_obj: str = ""

    for char in raw_text:
        if char == "{":
            if depth == 0:
                current_obj = "{"
            else:
                current_obj += char
            depth += 1
        elif char == "}":
            depth -= 1
            current_obj += char
            if depth == 0 and current_obj.strip():
                try:
                    obj: dict[str, Any] = json.loads(current_obj)
                    # Only keep objects that look like GraphQL responses
                    # (have "data" field) or extension metadata (have "extensions")
                    if "data" in obj or "extensions" in obj:
                        valid_objects.append(obj)
                except ValueError, json.JSONDecodeError:
                    pass
                current_obj = ""
        elif depth > 0:
            current_obj += char

    if valid_objects:
        return json.dumps(valid_objects)

    # Strategy 4: Last resort - attempt repair on each line
    # Only keep lines that look like GraphQL responses
    lines: list[str] = raw_text.split("\n")
    valid_lines: list[dict[str, Any]] = []

    for line in lines:
        line: str = line.strip()  # noqa: PLW2901
        if line and line.startswith("{"):
            try:
                fixed_line: str = json_repair.repair_json(line)
                obj = json.loads(fixed_line)
                # Only keep objects that look like GraphQL responses
                if "data" in obj or "extensions" in obj:
                    valid_lines.append(obj)
            except ValueError, TypeError, json.JSONDecodeError:
                pass

    if valid_lines:
        return json.dumps(valid_lines)

    # Final fallback: return the original text and let downstream handle it
    return raw_text


class Command(BaseCommand):
    """Import Twitch drop campaign data from a JSON file or directory."""

    help = "Import Twitch drop campaign data from a JSON file or directory"
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Populate the command with arguments."""
        parser.add_argument(
            "path",
            type=str,
            help="Path to JSON file or directory",
        )
        parser.add_argument(
            "--recursive",
            action="store_true",
            help="Recursively search directories for JSON files",
        )
        parser.add_argument(
            "--crash-on-error",
            dest="crash_on_error",
            action="store_true",
            help="Crash the command on first error instead of continuing",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print per-file success messages",
        )
        parser.add_argument(
            "--skip-broken-moves",
            dest="skip_broken_moves",
            action="store_true",
            help=(
                "Do not move files to the broken directory on failures; useful"
                " during testing to avoid unnecessary file moves"
            ),
        )

    def _validate_responses(
        self,
        responses: list[dict[str, Any]],
        file_path: Path,
        options: dict[str, Any],
    ) -> tuple[list[GraphQLResponse], Path | None]:
        """Validate GraphQL response data using Pydantic schema.

        Args:
            responses: List of raw GraphQL response dictionaries.
            file_path: Path to the file being processed.
            options: Command options.

        Returns:
            Tuple of validated Pydantic GraphQLResponse models and an optional
            broken directory path when the file was moved during validation.

        Raises:
            ValidationError: If response data fails Pydantic validation
                and crash-on-error is enabled.
        """
        valid_responses: list[GraphQLResponse] = []
        broken_dir: Path | None = None

        if isinstance(responses, list):
            for response_data in responses:
                if isinstance(response_data, dict):
                    try:
                        response: GraphQLResponse = GraphQLResponse.model_validate(response_data)
                        valid_responses.append(response)

                    except ValidationError as e:
                        tqdm.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} Validation failed for an entry in {file_path.name}: {e}",
                        )

                        # Move invalid inputs out of the hot path so future runs can progress.
                        if not options.get("skip_broken_moves"):
                            op_name: str | None = extract_operation_name_from_parsed(response_data)
                            broken_dir = move_failed_validation_file(file_path, operation_name=op_name)

                            # Once the file has been moved, bail out so we don't try to move it again later.
                            return [], broken_dir

                        # optionally crash early to surface schema issues.
                        if options.get("crash_on_error"):
                            raise

                        continue

        return valid_responses, broken_dir

    def _get_or_create_organization(
        self,
        org_data: OrganizationSchema,
    ) -> Organization:
        """Get or create an organization.

        Args:
            org_data: Organization data from Pydantic model.

        Returns:
            Organization instance.
        """
        org_obj, created = Organization.objects.update_or_create(
            twitch_id=org_data.twitch_id,
            defaults={
                "name": org_data.name,
            },
        )
        if created:
            tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Created new organization: {org_data.name}")

        return org_obj

    def _get_or_create_game(
        self,
        game_data: GameSchema,
        campaign_org_obj: Organization | None,
    ) -> Game:
        """Get or create a game using correct owner organization.

        Args:
            game_data: Game data from Pydantic model.
            campaign_org_obj: Organization that owns the campaign (fallback).

        Returns:
            Game instance.
        """
        # Collect all possible owner organizations
        owner_orgs = set()
        if hasattr(game_data, "owner_organization") and game_data.owner_organization:
            owner_org_data = game_data.owner_organization
            if isinstance(owner_org_data, dict):
                owner_org_data = OrganizationSchema.model_validate(owner_org_data)
            owner_orgs.add(self._get_or_create_organization(owner_org_data))
        # Add campaign organization as fallback only when provided
        if campaign_org_obj:
            owner_orgs.add(campaign_org_obj)

        game_obj, created = Game.objects.update_or_create(
            twitch_id=game_data.twitch_id,
            defaults={
                "display_name": game_data.display_name or (game_data.name or ""),
                "name": game_data.name or "",
                "slug": game_data.slug or "",
                "box_art": game_data.box_art_url or "",
            },
        )
        # Set owners (ManyToMany)
        if created or owner_orgs:
            game_obj.owners.add(*owner_orgs)
        if created:
            tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Created new game: {game_data.display_name}")
        self._download_game_box_art(game_obj, game_obj.box_art)
        return game_obj

    def _download_game_box_art(self, game_obj: Game, box_art_url: str | None) -> None:
        """Download and cache Twitch box art locally when possible."""
        if not box_art_url:
            return
        if not is_twitch_box_art_url(box_art_url):
            return
        if game_obj.box_art_file and getattr(game_obj.box_art_file, "name", ""):
            return

        normalized_url: str = normalize_twitch_box_art_url(box_art_url)
        parsed_url = urlparse(normalized_url)
        suffix: str = Path(parsed_url.path).suffix or ".jpg"
        file_name: str = f"{game_obj.twitch_id}{suffix}"

        try:
            response = httpx.get(normalized_url, timeout=20)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            tqdm.write(
                f"{Fore.YELLOW}!{Style.RESET_ALL} Failed to download box art for {game_obj.twitch_id}: {exc}",
            )
            return

        game_obj.box_art_file.save(file_name, ContentFile(response.content), save=True)

    def _get_or_create_channel(self, channel_info: ChannelInfoSchema) -> Channel:
        """Get or create a channel.

        Args:
            channel_info: Channel info from Pydantic model.

        Returns:
            Channel instance.
        """
        # Use name as display_name fallback if displayName is None
        display_name: str = channel_info.display_name or channel_info.name

        channel_obj, created = Channel.objects.update_or_create(
            twitch_id=channel_info.twitch_id,
            defaults={
                "name": channel_info.name,
                "display_name": display_name,
            },
        )
        if created:
            tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Created new channel: {display_name}")

        return channel_obj

    def process_responses(
        self,
        responses: list[dict[str, Any]],
        file_path: Path,
        options: dict[str, Any],
    ) -> tuple[bool, Path | None]:
        """Process, validate, and import campaign data from GraphQL responses.

        Args:
            responses: List of raw GraphQL response dictionaries to process.
            file_path: Path to the file being processed.
            options: Command options dictionary.

        Raises:
            ValueError: If datetime parsing fails for campaign dates and
                crash-on-error is enabled.

        Returns:
            Tuple of (success flag, broken directory path if moved).
        """
        valid_responses, broken_dir = self._validate_responses(
            responses=responses,
            file_path=file_path,
            options=options,
        )

        if broken_dir is not None:
            # File already moved due to validation failure; signal caller to skip further handling.
            return False, broken_dir

        for response in valid_responses:
            campaigns_to_process: list[DropCampaignSchema] = []

            # Source 1: User or CurrentUser field (handles plural, singular, inventory)
            user_obj: CurrentUserSchema | None = response.data.current_user or response.data.user
            if user_obj and user_obj.drop_campaigns:
                campaigns_to_process.extend(user_obj.drop_campaigns)

            # Source 2: Channel field (viewer drop campaigns)
            channel_obj = response.data.channel
            if channel_obj and channel_obj.viewer_drop_campaigns:
                if isinstance(channel_obj.viewer_drop_campaigns, list):
                    campaigns_to_process.extend(channel_obj.viewer_drop_campaigns)
                else:
                    campaigns_to_process.append(channel_obj.viewer_drop_campaigns)

            if not campaigns_to_process:
                continue

            for drop_campaign in campaigns_to_process:
                # Handle campaigns without owner (e.g., from Inventory operation)
                owner_data: OrganizationSchema | None = getattr(drop_campaign, "owner", None)
                org_obj: Organization | None = None
                if owner_data:
                    org_obj = self._get_or_create_organization(org_data=owner_data)

                game_obj: Game = self._get_or_create_game(
                    game_data=drop_campaign.game,
                    campaign_org_obj=org_obj,
                )

                start_at_dt: datetime | None = parse_date(drop_campaign.start_at)
                end_at_dt: datetime | None = parse_date(drop_campaign.end_at)

                if start_at_dt is None or end_at_dt is None:
                    tqdm.write(f"{Fore.RED}✗{Style.RESET_ALL} Invalid datetime in campaign: {drop_campaign.name}")
                    if options.get("crash_on_error"):
                        msg: str = f"Failed to parse datetime for campaign {drop_campaign.name}"
                        raise ValueError(msg)
                    continue

                defaults: dict[str, str | datetime | Game | bool] = {
                    "name": drop_campaign.name,
                    "description": drop_campaign.description,
                    "image_url": drop_campaign.image_url,
                    "game": game_obj,
                    "start_at": start_at_dt,
                    "end_at": end_at_dt,
                    "details_url": drop_campaign.details_url,
                    "account_link_url": drop_campaign.account_link_url,
                }

                campaign_obj, created = DropCampaign.objects.update_or_create(
                    twitch_id=drop_campaign.twitch_id,
                    defaults=defaults,
                )
                if created:
                    tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Created new campaign: {drop_campaign.name}")

                action: Literal["Imported new", "Updated"] = "Imported new" if created else "Updated"
                tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} {action} campaign: {drop_campaign.name}")

                if (
                    response.extensions
                    and response.extensions.operation_name
                    and response.extensions.operation_name not in campaign_obj.operation_names
                ):
                    campaign_obj.operation_names.append(response.extensions.operation_name)
                    campaign_obj.save(update_fields=["operation_names"])

                if drop_campaign.time_based_drops:
                    self._process_time_based_drops(
                        time_based_drops_schema=drop_campaign.time_based_drops,
                        campaign_obj=campaign_obj,
                    )

                # Process allowed channels from the campaign's ACL
                if drop_campaign.allow:
                    self._process_allowed_channels(
                        campaign_obj=campaign_obj,
                        allow_schema=drop_campaign.allow,
                    )

            # Process reward campaigns if present
            if response.data.reward_campaigns_available_to_user:
                self._process_reward_campaigns(
                    reward_campaigns=response.data.reward_campaigns_available_to_user,
                    options=options,
                )

        return True, None

    def _process_time_based_drops(
        self,
        time_based_drops_schema: list[TimeBasedDropSchema],
        campaign_obj: DropCampaign,
    ) -> None:
        """Process time-based drops for a campaign.

        Args:
            time_based_drops_schema: List of TimeBasedDrop Pydantic schemas.
            campaign_obj: The DropCampaign database object.
        """
        for drop_schema in time_based_drops_schema:
            start_at_dt: datetime | None = parse_date(drop_schema.start_at)
            end_at_dt: datetime | None = parse_date(drop_schema.end_at)

            drop_defaults: dict[str, str | int | datetime | DropCampaign] = {
                "campaign": campaign_obj,
                "name": drop_schema.name,
                "required_subs": drop_schema.required_subs,
            }

            if drop_schema.required_minutes_watched is not None:
                drop_defaults["required_minutes_watched"] = drop_schema.required_minutes_watched
            if start_at_dt is not None:
                drop_defaults["start_at"] = start_at_dt
            if end_at_dt is not None:
                drop_defaults["end_at"] = end_at_dt

            drop_obj, created = TimeBasedDrop.objects.update_or_create(
                twitch_id=drop_schema.twitch_id,
                defaults=drop_defaults,
            )
            if created:
                tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Created TimeBasedDrop: {drop_schema.name}")

            self._process_benefit_edges(
                benefit_edges_schema=drop_schema.benefit_edges,
                drop_obj=drop_obj,
            )

    def _get_or_update_benefit(self, benefit_schema: DropBenefitSchema) -> DropBenefit:
        """Return a DropBenefit, creating or updating as needed."""
        distribution_type: str = (benefit_schema.distribution_type or "").strip()
        benefit_defaults: dict[str, str | int | datetime | bool | None] = {
            "name": benefit_schema.name,
            "image_asset_url": benefit_schema.image_asset_url,
            "entitlement_limit": benefit_schema.entitlement_limit,
            "is_ios_available": benefit_schema.is_ios_available,
            "distribution_type": distribution_type,
        }

        if benefit_schema.created_at:
            created_at_dt: datetime | None = parse_date(benefit_schema.created_at)
            if created_at_dt:
                benefit_defaults["created_at"] = created_at_dt

        benefit_obj, created = DropBenefit.objects.update_or_create(
            twitch_id=benefit_schema.twitch_id,
            defaults=benefit_defaults,
        )
        if created:
            tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Created DropBenefit: {benefit_schema.name}")

        return benefit_obj

    def _process_benefit_edges(
        self,
        benefit_edges_schema: list[DropBenefitEdgeSchema],
        drop_obj: TimeBasedDrop,
    ) -> None:
        """Process benefit edges for a time-based drop.

        Args:
            benefit_edges_schema: List of DropBenefitEdge Pydantic schemas.
            drop_obj: The TimeBasedDrop database object.
        """
        for edge_schema in benefit_edges_schema:
            benefit_schema: DropBenefitSchema = edge_schema.benefit

            benefit_obj: DropBenefit = self._get_or_update_benefit(benefit_schema=benefit_schema)

            _edge_obj, created = DropBenefitEdge.objects.update_or_create(
                drop=drop_obj,
                benefit=benefit_obj,
                defaults={"entitlement_limit": edge_schema.entitlement_limit},
            )
            if created:
                tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} Linked benefit: {benefit_schema.name} → {drop_obj.name}")

    def _process_allowed_channels(
        self,
        campaign_obj: DropCampaign,
        allow_schema: DropCampaignACLSchema,
    ) -> None:
        """Process allowed channels for a drop campaign.

        Updates the campaign's allow_is_enabled flag and M2M relationship
        with allowed channels from the ACL schema.

        Args:
            campaign_obj: The DropCampaign database object.
            allow_schema: The DropCampaignACL Pydantic schema.
        """
        # Update the allow_is_enabled flag if changed
        # Default to True if is_enabled is None (API doesn't always provide this field)
        is_enabled: bool = allow_schema.is_enabled if allow_schema.is_enabled is not None else True
        if campaign_obj.allow_is_enabled != is_enabled:
            campaign_obj.allow_is_enabled = is_enabled
            campaign_obj.save(update_fields=["allow_is_enabled"])

        # Get or create all channels and collect them
        # Only update the M2M relationship if we have channel data from the API.
        # This prevents clearing existing channel associations when the API returns
        # no channels (which can happen for disabled campaigns or incomplete responses).
        channel_objects: list[Channel] = []
        if allow_schema.channels:
            for channel_schema in allow_schema.channels:
                channel_obj: Channel = self._get_or_create_channel(channel_info=channel_schema)
                channel_objects.append(channel_obj)
            # Only update the M2M relationship if we have channels
            campaign_obj.allow_channels.set(channel_objects)

    def _process_reward_campaigns(
        self,
        reward_campaigns: list[RewardCampaignSchema],
        options: dict[str, Any],
    ) -> None:
        """Process reward campaigns from the API response.

        Args:
            reward_campaigns: List of RewardCampaign Pydantic schemas.
            options: Command options dictionary.

        Raises:
            ValueError: If datetime parsing fails for campaign dates and
                crash-on-error is enabled.
        """
        for reward_campaign in reward_campaigns:
            starts_at_dt: datetime | None = parse_date(reward_campaign.starts_at)
            ends_at_dt: datetime | None = parse_date(reward_campaign.ends_at)

            if starts_at_dt is None or ends_at_dt is None:
                tqdm.write(f"{Fore.RED}✗{Style.RESET_ALL} Invalid datetime in reward campaign: {reward_campaign.name}")
                if options.get("crash_on_error"):
                    msg: str = f"Failed to parse datetime for reward campaign {reward_campaign.name}"
                    raise ValueError(msg)
                continue

            # Handle game reference if present
            game_obj: Game | None = None
            if reward_campaign.game:
                # The game field in reward campaigns is a dict, not a full GameSchema
                # We'll try to find an existing game by twitch_id if available
                game_id = reward_campaign.game.get("id")
                if game_id:
                    try:
                        game_obj = Game.objects.get(twitch_id=game_id)
                    except Game.DoesNotExist:
                        if options.get("verbose"):
                            tqdm.write(
                                f"{Fore.YELLOW}→{Style.RESET_ALL} Game not found for reward campaign: {game_id}",
                            )

            defaults: dict[str, str | datetime | Game | bool | None] = {
                "name": reward_campaign.name,
                "brand": reward_campaign.brand,
                "starts_at": starts_at_dt,
                "ends_at": ends_at_dt,
                "status": reward_campaign.status,
                "summary": reward_campaign.summary,
                "instructions": reward_campaign.instructions,
                "external_url": reward_campaign.external_url,
                "reward_value_url_param": reward_campaign.reward_value_url_param,
                "about_url": reward_campaign.about_url,
                "is_sitewide": reward_campaign.is_sitewide,
                "game": game_obj,
                "image_url": reward_campaign.image.image1x_url if reward_campaign.image else "",
            }

            _reward_campaign_obj, created = RewardCampaign.objects.update_or_create(
                twitch_id=reward_campaign.twitch_id,
                defaults=defaults,
            )

            action: Literal["Imported new", "Updated"] = "Imported new" if created else "Updated"
            display_name = (
                f"{reward_campaign.brand}: {reward_campaign.name}" if reward_campaign.brand else reward_campaign.name
            )
            tqdm.write(f"{Fore.GREEN}✓{Style.RESET_ALL} {action} reward campaign: {display_name}")

    def handle(self, *args, **options) -> None:  # noqa: ARG002
        """Main entry point for the command.

        Raises:
            CommandError: If the provided path does not exist.
        """
        colorama_init(autoreset=True)

        input_path: Path = Path(options["path"]).resolve()

        try:
            if input_path.is_file():
                self.process_file(file_path=input_path, options=options)
            elif input_path.is_dir():
                self.process_json_files(input_path=input_path, options=options)
            else:
                msg: str = f"Path does not exist: {input_path}"
                raise CommandError(msg)
        except KeyboardInterrupt:
            tqdm.write(self.style.WARNING("\n\nInterrupted by user!"))
            tqdm.write(self.style.WARNING("Shutting down gracefully..."))
            sys.exit(130)  # 128 + 2 (Keyboard Interrupt)

    def process_json_files(self, input_path: Path, options: dict) -> None:
        """Process multiple JSON files in a directory.

        Args:
            input_path: Path to the directory containing JSON files
            options: Command options
        """
        json_files: list[Path] = self.collect_json_files(options, input_path)
        tqdm.write(f"Found {len(json_files)} JSON files to process\n")

        success_count = 0
        failed_count = 0
        error_count = 0

        with tqdm(
            total=len(json_files),
            desc="Processing",
            unit="file",
            bar_format=("{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"),
            colour="green",
            dynamic_ncols=True,
        ) as progress_bar:
            for file_path in json_files:
                try:
                    result: dict[str, bool | str] = self.process_file_worker(
                        file_path=file_path,
                        options=options,
                    )
                    if result["success"]:
                        success_count += 1
                        if options.get("verbose"):
                            progress_bar.write(f"{Fore.GREEN}✓{Style.RESET_ALL} {file_path.name}")
                    else:
                        failed_count += 1
                        reason: bool | str | None = result.get("reason") if isinstance(result, dict) else None
                        if reason:
                            progress_bar.write(
                                f"{Fore.RED}✗{Style.RESET_ALL} "
                                f"{file_path.name} → {result['broken_dir']}/"
                                f"{file_path.name} ({reason})",
                            )
                        else:
                            progress_bar.write(
                                f"{Fore.RED}✗{Style.RESET_ALL} "
                                f"{file_path.name} → {result['broken_dir']}/"
                                f"{file_path.name}",
                            )
                except (OSError, ValueError, KeyError) as e:
                    error_count += 1
                    progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} (error: {e})")

                # Update postfix with statistics
                progress_bar.set_postfix_str(f"✓ {success_count} | ✗ {failed_count + error_count}", refresh=True)
                progress_bar.update(1)

        self.print_processing_summary(
            json_files,
            success_count,
            failed_count,
            error_count,
        )

    def print_processing_summary(
        self,
        json_files: list[Path],
        success_count: int,
        failed_count: int,
        error_count: int,
    ) -> None:
        """Print a summary of the batch processing results.

        Args:
            json_files: List of JSON file paths that were processed.
            success_count: Number of files processed successfully.
            failed_count: Number of files that failed validation and were
                moved.
            error_count: Number of files that encountered unexpected errors.
        """
        tqdm.write("\n" + "=" * 50)
        tqdm.write(self.style.SUCCESS(f"✓ Successfully processed: {success_count}"))
        if failed_count > 0:
            tqdm.write(self.style.WARNING(f"✗ Validation failed: {failed_count}"))
        if error_count > 0:
            tqdm.write(self.style.ERROR(f"✗ Errors: {error_count}"))
        tqdm.write(f"Total: {len(json_files)}")
        tqdm.write("=" * 50)

    def _detect_campaign_structure(self, response: dict[str, Any]) -> str | None:
        """Detect which campaign structure is present in the response.

        Used for organizing/categorizing files by their response type.

        Supported structures:
        - "user_drop_campaign": {"data": {"user": {"dropCampaign": {...}}}}
        - "current_user_drop_campaigns": {"data": {"currentUser": {"dropCampaigns": [...]}}}
        - "inventory_campaigns": {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [...]}}}}
        - "channel_viewer_campaigns": {"data": {"channel": {"viewerDropCampaigns": [...] or {...}}}}

        Args:
            response: The parsed JSON response from Twitch API.

        Returns:
            String identifier of the structure type, or None if no campaign structure found.
        """
        if not isinstance(response, dict) or "data" not in response:
            return None

        data: dict[str, Any] = response["data"]

        # Check structures in order of specificity
        # Structure: {"data": {"user": {"dropCampaign": {...}}}}
        if (
            "user" in data
            and isinstance(data["user"], dict)
            and "dropCampaign" in data["user"]
            and data["user"]["dropCampaign"]
        ):
            return "user_drop_campaign"

        # Structure: {"data": {"currentUser": {...}}}
        if "currentUser" in data and isinstance(data["currentUser"], dict):
            current_user: dict[str, Any] = data["currentUser"]

            # Structure: {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [...]}}}}
            if (
                "inventory" in current_user
                and isinstance(current_user["inventory"], dict)
                and "dropCampaignsInProgress" in current_user["inventory"]
                and current_user["inventory"]["dropCampaignsInProgress"]
            ):
                return "inventory_campaigns"

            # Structure: {"data": {"currentUser": {"dropCampaigns": [...]}}}
            if "dropCampaigns" in current_user and isinstance(current_user["dropCampaigns"], list):
                return "current_user_drop_campaigns"

        # Structure: {"data": {"channel": {"viewerDropCampaigns": [...] or {...}}}}
        if "channel" in data and isinstance(data["channel"], dict):
            channel: dict[str, Any] = data["channel"]
            if channel.get("viewerDropCampaigns"):
                return "channel_viewer_campaigns"

        return None

    def collect_json_files(
        self,
        options: dict,
        input_path: Path,
    ) -> list[Path]:
        """Collect JSON files from the specified directory.

        Args:
            options: Command options
            input_path: Path to the directory

        Returns:
            List of JSON file paths
        """
        json_files: list[Path] = []
        if options["recursive"]:
            for root, _dirs, files in os.walk(input_path):
                root_path = Path(root)
                json_files.extend(root_path / file for file in files if file.endswith(".json"))
        else:
            json_files = [f for f in input_path.iterdir() if f.is_file() and f.suffix == ".json"]
        return json_files

    def _normalize_responses(
        self,
        parsed_json: JSONReturnType | tuple[JSONReturnType, list[dict[str, str]]] | str,
    ) -> list[dict[str, Any]]:
        """Normalize various parsed JSON shapes into a list of dict responses.

        Handles:
        - Single dict response: {"data": {...}}
        - List of responses: [{"data": {...}}, {"data": {...}}]
        - Batched format: {"responses": [{"data": {...}}, {"data": {...}}]}
        - Tuple from json_repair: (data, repair_log)

        Args:
            parsed_json: The parsed JSON data from the file.

        Returns:
            A list of response dictionaries.
        """
        if isinstance(parsed_json, dict):
            # Check for batched format: {"responses": [...]}
            if "responses" in parsed_json and isinstance(parsed_json["responses"], list):
                return [item for item in parsed_json["responses"] if isinstance(item, dict)]
            # Single response: {"data": {...}}
            return [parsed_json]
        if isinstance(parsed_json, list):
            return [item for item in parsed_json if isinstance(item, dict)]
        if isinstance(parsed_json, tuple):
            # json_repair returns (data, repair_log). Normalize based on the data portion.
            if len(parsed_json) > 0:
                return self._normalize_responses(parsed_json[0])
            return []
        return []

    def process_file_worker(
        self,
        file_path: Path,
        options: dict,
    ) -> dict[str, bool | str]:
        """Worker function for processing files.

        Args:
            file_path: Path to the JSON file to process
            options: Command options

        Raises:
            ValidationError: If the JSON file fails validation
            json.JSONDecodeError: If the JSON file cannot be parsed

        Returns:
            Dict with success status and optional broken_dir path
        """
        try:
            raw_text: str = file_path.read_text(encoding="utf-8", errors="ignore")

            # Repair potentially broken JSON with multiple fallback strategies
            fixed_json_str: str = repair_partially_broken_json(raw_text)
            parsed_json: JSONReturnType | tuple[JSONReturnType, list[dict[str, str]]] | str = json.loads(
                fixed_json_str,
            )
            operation_name: str | None = extract_operation_name_from_parsed(parsed_json)

            # Check for error-only responses first
            error_description: str | None = detect_error_only_response(parsed_json)
            if error_description:
                if not options.get("skip_broken_moves"):
                    broken_dir: Path | None = move_file_to_broken_subdir(
                        file_path,
                        error_description,
                        operation_name=operation_name,
                    )
                    return {"success": False, "broken_dir": str(broken_dir), "reason": error_description}
                return {"success": False, "broken_dir": "(skipped)", "reason": error_description}

            matched: str | None = detect_non_campaign_keyword(raw_text)
            if matched:
                if not options.get("skip_broken_moves"):
                    broken_dir: Path | None = move_file_to_broken_subdir(
                        file_path,
                        matched,
                        operation_name=operation_name,
                    )
                    return {"success": False, "broken_dir": str(broken_dir), "reason": f"matched '{matched}'"}
                return {"success": False, "broken_dir": "(skipped)", "reason": f"matched '{matched}'"}
            if "dropCampaign" not in raw_text:
                if not options.get("skip_broken_moves"):
                    broken_dir: Path | None = move_file_to_broken_subdir(
                        file_path,
                        "no_dropCampaign",
                        operation_name=operation_name,
                    )
                    return {"success": False, "broken_dir": str(broken_dir), "reason": "no dropCampaign present"}
                return {"success": False, "broken_dir": "(skipped)", "reason": "no dropCampaign present"}

            # Normalize and filter to dict responses only
            responses: list[dict[str, Any]] = self._normalize_responses(parsed_json)
            processed, broken_dir = self.process_responses(
                responses=responses,
                file_path=file_path,
                options=options,
            )

            if not processed:
                # File was already moved to broken during validation
                return {
                    "success": False,
                    "broken_dir": str(broken_dir) if broken_dir else "(unknown)",
                    "reason": "validation failed",
                }

            campaign_structure: str | None = self._detect_campaign_structure(
                responses[0] if responses else {},
            )
            move_completed_file(
                file_path=file_path,
                operation_name=operation_name,
                campaign_structure=campaign_structure,
            )

        except ValidationError, json.JSONDecodeError:
            if options["crash_on_error"]:
                raise

            if not options.get("skip_broken_moves"):
                parsed_json_local: Any | None = locals().get("parsed_json")
                op_name: str | None = (
                    extract_operation_name_from_parsed(parsed_json_local)
                    if isinstance(parsed_json_local, (dict, list))
                    else None
                )
                broken_dir = move_failed_validation_file(file_path, operation_name=op_name)
                return {"success": False, "broken_dir": str(broken_dir)}
            return {"success": False, "broken_dir": "(skipped)"}
        else:
            return {"success": True}

    def process_file(self, file_path: Path, options: dict) -> None:
        """Reads a JSON file and processes the campaign data.

        Args:
            file_path: Path to the JSON file
            options: Command options

        Raises:
            ValidationError: If the JSON file fails validation
            json.JSONDecodeError: If the JSON file cannot be parsed
        """
        with tqdm(
            total=1,
            desc=f"Processing {file_path.name}",
            unit="file",
            colour="green",
            dynamic_ncols=True,
        ) as progress_bar:
            try:
                raw_text: str = file_path.read_text(encoding="utf-8", errors="ignore")

                # Repair potentially broken JSON with multiple fallback strategies
                fixed_json_str: str = repair_partially_broken_json(raw_text)
                parsed_json: JSONReturnType | tuple[JSONReturnType, list[dict[str, str]]] | str = json.loads(
                    fixed_json_str,
                )
                operation_name: str | None = extract_operation_name_from_parsed(parsed_json)

                # Check for error-only responses first
                error_description: str | None = detect_error_only_response(parsed_json)
                if error_description:
                    if not options.get("skip_broken_moves"):
                        broken_dir: Path | None = move_file_to_broken_subdir(
                            file_path,
                            error_description,
                            operation_name=operation_name,
                        )
                        progress_bar.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → "
                            f"{broken_dir}/{file_path.name} "
                            f"({error_description})",
                        )
                    else:
                        progress_bar.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} ({error_description}, move skipped)",
                        )
                    return

                matched: str | None = detect_non_campaign_keyword(raw_text)
                if matched:
                    if not options.get("skip_broken_moves"):
                        broken_dir: Path | None = move_file_to_broken_subdir(
                            file_path,
                            matched,
                            operation_name=operation_name,
                        )
                        progress_bar.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → "
                            f"{broken_dir}/{file_path.name} "
                            f"(matched '{matched}')",
                        )
                    else:
                        progress_bar.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} (matched '{matched}', move skipped)",
                        )
                    return

                if "dropCampaign" not in raw_text:
                    if not options.get("skip_broken_moves"):
                        broken_dir = move_file_to_broken_subdir(
                            file_path,
                            "no_dropCampaign",
                            operation_name=operation_name,
                        )
                        progress_bar.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → "
                            f"{broken_dir}/{file_path.name} "
                            f"(no dropCampaign present)",
                        )
                    else:
                        progress_bar.write(
                            f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} (no dropCampaign present, move skipped)",
                        )
                    return

                # Normalize and filter to dict responses only
                responses: list[dict[str, Any]] = self._normalize_responses(parsed_json)

                processed, broken_dir = self.process_responses(
                    responses=responses,
                    file_path=file_path,
                    options=options,
                )

                if not processed:
                    # File already moved during validation; nothing more to do here.
                    progress_bar.write(
                        f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → "
                        f"{broken_dir}/{file_path.name} (validation failed)",
                    )
                    return

                campaign_structure: str | None = self._detect_campaign_structure(
                    responses[0] if responses else {},
                )
                move_completed_file(
                    file_path=file_path,
                    operation_name=operation_name,
                    campaign_structure=campaign_structure,
                )

                progress_bar.update(1)
                progress_bar.write(f"{Fore.GREEN}✓{Style.RESET_ALL} {file_path.name}")
            except ValidationError, json.JSONDecodeError:
                if options["crash_on_error"]:
                    raise

                if not options.get("skip_broken_moves"):
                    parsed_json_local: Any | None = locals().get("parsed_json")
                    op_name: str | None = (
                        extract_operation_name_from_parsed(parsed_json_local)
                        if isinstance(parsed_json_local, (dict, list))
                        else None
                    )
                    broken_dir = move_failed_validation_file(file_path, operation_name=op_name)
                    progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} → {broken_dir}/{file_path.name}")
                else:
                    progress_bar.write(f"{Fore.RED}✗{Style.RESET_ALL} {file_path.name} (move skipped)")
