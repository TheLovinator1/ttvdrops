"""Management command to import Twitch global chat badges."""

import logging
import os
from typing import TYPE_CHECKING
from typing import Any

import httpx
from colorama import Fore
from colorama import Style
from colorama import init as colorama_init
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from pydantic import ValidationError

from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.schemas import GlobalChatBadgesResponse

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

    from twitch.schemas import ChatBadgeSetSchema
    from twitch.schemas import ChatBadgeVersionSchema

logger: logging.Logger = logging.getLogger("ttvdrops")


class Command(BaseCommand):
    """Import Twitch global chat badges from the Twitch Helix API."""

    help = "Import Twitch global chat badges from the Twitch Helix API"
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--client-id",
            type=str,
            help="Twitch Client ID (or set TWITCH_CLIENT_ID environment variable)",
        )
        parser.add_argument(
            "--client-secret",
            type=str,
            help="Twitch Client Secret (or set TWITCH_CLIENT_SECRET environment variable)",
        )
        parser.add_argument(
            "--access-token",
            type=str,
            help="Twitch Access Token (optional - will be obtained automatically if not provided)",
        )

    def handle(self, *args, **options) -> None:  # noqa: ARG002
        """Main entry point for the command.

        Raises:
            CommandError: If required arguments are missing or API calls fail
        """
        colorama_init(autoreset=True)

        # Get credentials from arguments or environment
        client_id: str | None = options.get("client_id") or os.getenv(
            "TWITCH_CLIENT_ID",
        )
        client_secret: str | None = options.get("client_secret") or os.getenv(
            "TWITCH_CLIENT_SECRET",
        )
        access_token: str | None = options.get("access_token") or os.getenv(
            "TWITCH_ACCESS_TOKEN",
        )

        if not client_id:
            msg = (
                "Twitch Client ID is required. "
                "Provide it via --client-id argument or set TWITCH_CLIENT_ID environment variable."
            )
            raise CommandError(msg)

        # If access token is not provided, obtain it automatically using client credentials
        if not access_token:
            if not client_secret:
                msg = (
                    "Either --access-token or --client-secret must be provided. "
                    "Set TWITCH_ACCESS_TOKEN or TWITCH_CLIENT_SECRET environment variable, "
                    "or provide them via command arguments."
                )
                raise CommandError(msg)

            self.stdout.write("Obtaining access token from Twitch...")
            try:
                access_token = self._get_app_access_token(client_id, client_secret)
                self.stdout.write(
                    self.style.SUCCESS("✓ Access token obtained successfully"),
                )
            except httpx.HTTPError as e:
                msg = f"Failed to obtain access token: {e}"
                raise CommandError(msg) from e

        self.stdout.write("Fetching global chat badges from Twitch API...")

        try:
            badges_data: GlobalChatBadgesResponse = self._fetch_global_chat_badges(
                client_id=client_id,
                access_token=access_token,
            )
        except httpx.HTTPError as e:
            msg: str = f"Failed to fetch chat badges from Twitch API: {e}"
            raise CommandError(msg) from e
        except ValidationError as e:
            msg: str = f"Failed to validate chat badges response: {e}"
            raise CommandError(msg) from e

        self.stdout.write(f"Received {len(badges_data.data)} badge sets")

        # Process and store badge data
        created_sets = 0
        updated_sets = 0
        created_badges = 0
        updated_badges = 0

        for badge_set_schema in badges_data.data:
            badge_set_obj, set_created = self._process_badge_set(badge_set_schema)

            if set_created:
                created_sets += 1
            else:
                updated_sets += 1

            # Process each badge version in the set
            for version_schema in badge_set_schema.versions:
                badge_created: bool = self._process_badge_version(
                    badge_set_obj=badge_set_obj,
                    version_schema=version_schema,
                )

                if badge_created:
                    created_badges += 1
                else:
                    updated_badges += 1

        # Print summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Created {created_sets} new badge sets, updated {updated_sets} existing",
            ),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Created {created_badges} new badges, updated {updated_badges} existing",
            ),
        )
        self.stdout.write(f"Total badge sets: {created_sets + updated_sets}")
        self.stdout.write(f"Total badges: {created_badges + updated_badges}")
        self.stdout.write("=" * 50)

    def _fetch_global_chat_badges(
        self,
        client_id: str,
        access_token: str,
    ) -> GlobalChatBadgesResponse:
        """Fetch global chat badges from Twitch Helix API.

        Args:
            client_id: Twitch Client ID
            access_token: Twitch Access Token (app or user token)

        Returns:
            Validated GlobalChatBadgesResponse
        """
        url = "https://api.twitch.tv/helix/chat/badges/global"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "Client-Id": client_id,
        }

        with httpx.Client() as client:
            response: httpx.Response = client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()

            data: dict[str, Any] = response.json()
            logger.debug("Received chat badges response: %s", data)

            # Validate response with Pydantic
            return GlobalChatBadgesResponse.model_validate(data)

    def _process_badge_set(
        self,
        badge_set_schema: ChatBadgeSetSchema,
    ) -> tuple[ChatBadgeSet, bool]:
        """Get or create a ChatBadgeSet from the schema.

        Args:
            badge_set_schema: Validated badge set schema

        Returns:
            Tuple of (ChatBadgeSet instance, created flag)
        """
        badge_set_obj: ChatBadgeSet | None = ChatBadgeSet.objects.filter(
            set_id=badge_set_schema.set_id,
        ).first()
        created: bool = badge_set_obj is None
        if badge_set_obj is None:
            badge_set_obj = ChatBadgeSet.objects.create(set_id=badge_set_schema.set_id)

        if created:
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} Created new badge set: {badge_set_schema.set_id}",
            )
        else:
            self.stdout.write(
                f"{Fore.YELLOW}→{Style.RESET_ALL} Badge set already exists: {badge_set_schema.set_id}",
            )

        return badge_set_obj, created

    def _get_app_access_token(self, client_id: str, client_secret: str) -> str:
        """Get an app access token from Twitch.

        Args:
            client_id: Twitch Client ID
            client_secret: Twitch Client Secret

        Returns:
            Access token string
        """
        url = "https://id.twitch.tv/oauth2/token"
        params: dict[str, str] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }

        with httpx.Client() as client:
            response: httpx.Response = client.post(url, params=params, timeout=30.0)
            response.raise_for_status()
            data: dict[str, str] = response.json()
            return data["access_token"]

    def _process_badge_version(
        self,
        badge_set_obj: ChatBadgeSet,
        version_schema: ChatBadgeVersionSchema,
    ) -> bool:
        """Get or create a ChatBadge from the schema.

        Args:
            badge_set_obj: Parent ChatBadgeSet instance
            version_schema: Validated badge version schema

        Returns:
            True if created, False if updated
        """
        defaults: dict[str, str | None] = {
            "image_url_1x": version_schema.image_url_1x,
            "image_url_2x": version_schema.image_url_2x,
            "image_url_4x": version_schema.image_url_4x,
            "title": version_schema.title,
            "description": version_schema.description,
            "click_action": version_schema.click_action,
            "click_url": version_schema.click_url,
        }

        badge_obj: ChatBadge | None = ChatBadge.objects.filter(
            badge_set=badge_set_obj,
            badge_id=version_schema.badge_id,
        ).first()
        created: bool = badge_obj is None
        if badge_obj is None:
            badge_obj = ChatBadge.objects.create(
                badge_set=badge_set_obj,
                badge_id=version_schema.badge_id,
                **defaults,
            )
        else:
            changed_fields: list[str] = []
            for field, value in defaults.items():
                if getattr(badge_obj, field) != value:
                    setattr(badge_obj, field, value)
                    changed_fields.append(field)
            if changed_fields:
                badge_obj.save(update_fields=changed_fields)

        if created:
            msg: str = (
                f"{Fore.GREEN}✓{Style.RESET_ALL} Created badge: "
                f"{badge_set_obj.set_id}/{version_schema.badge_id} - {version_schema.title}"
            )
            self.stdout.write(msg)
        else:
            msg: str = (
                f"{Fore.YELLOW}→{Style.RESET_ALL} Updated badge: "
                f"{badge_set_obj.set_id}/{version_schema.badge_id} - {version_schema.title}"
            )
            self.stdout.write(msg)

        return created
