import logging
from typing import TYPE_CHECKING

import httpx
from django.core.management.base import BaseCommand
from pydantic import ValidationError

from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from kick.models import KickUser
from kick.schemas import KickDropsResponseSchema

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

    from kick.schemas import KickCategorySchema
    from kick.schemas import KickDropCampaignSchema
    from kick.schemas import KickOrganizationSchema
    from kick.schemas import KickUserSchema

logger: logging.Logger = logging.getLogger("ttvdrops")

KICK_DROPS_API_URL = "https://web.kick.com/api/v1/drops/campaigns"

# Kick's public API requires a browser-like User-Agent.
REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0"
    ),
    "Accept": "application/json",
    "Referer": "https://kick.com/",
}


class Command(BaseCommand):
    """Import drop campaigns from the Kick public API."""

    help = "Fetch and import Kick drop campaigns from the public API."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command-line arguments for the import_kick_drops command."""
        parser.add_argument(
            "--url",
            default=KICK_DROPS_API_URL,
            help="API endpoint to fetch (default: %(default)s).",
        )

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        """Main entry point for the command."""
        url: str = str(options["url"])
        self.stdout.write(f"Fetching Kick drops from {url} ...")

        try:
            response: httpx.Response = httpx.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=30,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.stderr.write(
                self.style.ERROR(f"HTTP error fetching Kick drops: {exc}"),
            )
            return

        try:
            payload: dict = response.json()
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.ERROR(f"Failed to parse JSON response: {exc}"))
            return

        try:
            drops_response: KickDropsResponseSchema = (
                KickDropsResponseSchema.model_validate(payload)
            )
        except ValidationError as exc:
            self.stderr.write(self.style.ERROR(f"Response validation failed: {exc}"))
            return

        campaigns: list[KickDropCampaignSchema] = drops_response.data
        self.stdout.write(f"Found {len(campaigns)} campaign(s). Importing ...")

        imported = 0
        for campaign_data in campaigns:
            try:
                self._import_campaign(campaign_data)
                imported += 1
            except Exception as exc:
                logger.exception("Failed to import campaign %s", campaign_data.id)
                self.stderr.write(
                    self.style.WARNING(f"Skipped campaign {campaign_data.id!r}: {exc}"),
                )

        self.stdout.write(
            self.style.SUCCESS(f"Imported {imported}/{len(campaigns)} campaign(s)."),
        )

    def _import_campaign(self, data: KickDropCampaignSchema) -> None:
        """Import a single campaign and all its related objects."""
        # Organisation
        org_data: KickOrganizationSchema = data.organization
        org, created = KickOrganization.objects.update_or_create(
            kick_id=org_data.id,
            defaults={
                "name": org_data.name,
                "logo_url": org_data.logo_url,
                "url": org_data.url,
                "restricted": org_data.restricted,
            },
        )
        if created:
            logger.info("Created new organization: %s", org.kick_id)

        # Category
        cat_data: KickCategorySchema = data.category
        category, created = KickCategory.objects.update_or_create(
            kick_id=cat_data.id,
            defaults={
                "name": cat_data.name,
                "slug": cat_data.slug,
                "image_url": cat_data.image_url,
            },
        )
        if created:
            logger.info("Created new category: %s", category.kick_id)

        # Campaign
        campaign, created = KickDropCampaign.objects.update_or_create(
            kick_id=data.id,
            defaults={
                "name": data.name,
                "status": data.status,
                "starts_at": data.starts_at,
                "ends_at": data.ends_at,
                "connect_url": data.connect_url,
                "url": data.url,
                "rule_id": data.rule.id,
                "rule_name": data.rule.name,
                "organization": org,
                "category": category,
                "created_at": data.created_at,
                "api_updated_at": data.updated_at,
                "is_fully_imported": True,
            },
        )
        if created:
            logger.info("Created new campaign: %s", campaign.kick_id)

        # Channels
        channel_objs: list[KickChannel] = []
        for ch_data in data.channels:
            user_data: KickUserSchema = ch_data.user
            user, created = KickUser.objects.update_or_create(
                kick_id=user_data.id,
                defaults={
                    "username": user_data.username,
                    "profile_picture": user_data.profile_picture,
                },
            )
            if created:
                logger.info("Created new user: %s", user.kick_id)

            channel, created = KickChannel.objects.update_or_create(
                kick_id=ch_data.id,
                defaults={
                    "slug": ch_data.slug,
                    "description": ch_data.description,
                    "banner_picture_url": ch_data.banner_picture_url,
                    "user": user,
                },
            )
            if created:
                logger.info("Created new channel: %s", channel.kick_id)

            channel_objs.append(channel)

        campaign.channels.set(channel_objs)

        for reward_data in data.rewards:
            # Resolve reward's category (may differ from campaign category)
            reward_category: KickCategory = category
            if reward_data.category_id != cat_data.id:
                reward_category, created = KickCategory.objects.get_or_create(
                    kick_id=reward_data.category_id,
                    defaults={"name": "", "slug": "", "image_url": ""},
                )
                if created:
                    logger.info("Created new category: %s", reward_category.kick_id)

            # Resolve reward's organization (may differ from campaign org)
            reward_org: KickOrganization = org
            if reward_data.organization_id != org_data.id:
                reward_org, created = KickOrganization.objects.get_or_create(
                    kick_id=reward_data.organization_id,
                    defaults={
                        "name": "",
                        "logo_url": "",
                        "url": "",
                        "restricted": False,
                    },
                )
                if created:
                    logger.info("Created new organization: %s", reward_org.kick_id)

            KickReward.objects.update_or_create(
                kick_id=reward_data.id,
                defaults={
                    "name": reward_data.name,
                    "image_url": reward_data.image_url,
                    "required_units": reward_data.required_units,
                    "campaign": campaign,
                    "category": reward_category,
                    "organization": reward_org,
                },
            )
