from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from django.core.management.base import BaseCommand
from django.db import transaction
from pydantic import ValidationError

from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from kick.models import KickUser
from kick.schemas import KickDropsResponseSchema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.core.management.base import CommandParser

    from kick.schemas import KickCategorySchema
    from kick.schemas import KickDropCampaignSchema
    from kick.schemas import KickOrganizationSchema
    from kick.schemas import KickUserSchema

logger: logging.Logger = logging.getLogger("ttvdrops")

type KickImportModel = (
    KickOrganization
    | KickCategory
    | KickDropCampaign
    | KickUser
    | KickChannel
    | KickReward
)
type KickFieldValue = (
    str
    | bool
    | int
    | datetime
    | KickOrganization
    | KickCategory
    | KickDropCampaign
    | KickUser
    | None
)

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
        parser.add_argument(
            "--reimport",
            action="store_true",
            help="Clear existing Kick import data before importing the fetched response.",
        )

    @staticmethod
    def _save_if_changed(
        obj: KickImportModel,
        defaults: Mapping[str, KickFieldValue],
    ) -> None:
        """Persist only changed fields to avoid unnecessary updates."""
        changed_fields: list[str] = []
        for field, new_value in defaults.items():
            if getattr(obj, field, None) != new_value:
                setattr(obj, field, new_value)
                changed_fields.append(field)

        if changed_fields:
            obj.save(update_fields=changed_fields)

    @staticmethod
    def _clear_existing_import_data() -> None:
        """Delete existing Kick import data before rebuilding from the API response."""
        with transaction.atomic():
            KickDropCampaign.objects.all().delete()
            KickReward.objects.all().delete()
            KickChannel.objects.all().delete()
            KickUser.objects.all().delete()
            KickOrganization.objects.all().delete()
            KickCategory.objects.all().delete()

    def handle(
        self,
        *_args: str,
        **options: str | bool | int | None,
    ) -> None:
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
        except Exception as exc:  # ruff:ignore[blind-except]
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
        if options.get("reimport"):
            self.stdout.write(
                "Reimport requested. Clearing existing Kick import data ...",
            )
            self._clear_existing_import_data()

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

    def _import_campaign(self, data: KickDropCampaignSchema) -> None:  # ruff:ignore[too-many-locals, too-many-statements]
        """Import a single campaign and all its related objects."""
        # Organization
        org_data: KickOrganizationSchema = data.organization
        org_defaults: dict[str, str | bool] = {
            "name": org_data.name,
            "logo_url": org_data.logo_url,
            "url": org_data.url,
            "restricted": org_data.restricted,
        }
        org: KickOrganization | None = KickOrganization.objects.filter(
            kick_id=org_data.id,
        ).first()
        created: bool = org is None
        if org is None:
            org = KickOrganization.objects.create(kick_id=org_data.id, **org_defaults)
        else:
            self._save_if_changed(org, org_defaults)
        if created:
            logger.info("Created new organization: %s", org.kick_id)

        # Category
        cat_data: KickCategorySchema | None = data.category
        category: KickCategory | None = None
        if cat_data is not None:
            category_defaults: dict[str, KickFieldValue] = {
                "name": cat_data.name,
                "slug": cat_data.slug,
                "image_url": cat_data.image_url,
            }
            category = KickCategory.objects.filter(
                kick_id=cat_data.id,
            ).first()
            created = category is None
            if category is None:
                category = KickCategory.objects.create(
                    kick_id=cat_data.id,
                    **category_defaults,
                )
            else:
                self._save_if_changed(category, category_defaults)
            if created:
                logger.info("Created new category: %s", category.kick_id)

        # Campaign
        campaign_defaults: dict[str, KickFieldValue] = {
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
        }
        campaign: KickDropCampaign | None = KickDropCampaign.objects.filter(
            kick_id=data.id,
        ).first()
        created = campaign is None
        if campaign is None:
            campaign = KickDropCampaign.objects.create(
                kick_id=data.id,
                **campaign_defaults,
            )
        else:
            self._save_if_changed(campaign, campaign_defaults)
        if created:
            logger.info("Created new campaign: %s", campaign.kick_id)

        # Channels
        channel_objs: list[KickChannel] = []
        for ch_data in data.channels:
            user_data: KickUserSchema = ch_data.user
            user_defaults: dict[str, KickFieldValue] = {
                "username": user_data.username,
                "profile_picture": user_data.profile_picture,
            }
            user: KickUser | None = KickUser.objects.filter(
                kick_id=user_data.id,
            ).first()
            created = user is None
            if user is None:
                user = KickUser.objects.create(kick_id=user_data.id, **user_defaults)
            else:
                self._save_if_changed(user, user_defaults)
            if created:
                logger.info("Created new user: %s", user.kick_id)

            channel_defaults: dict[str, KickFieldValue] = {
                "slug": ch_data.slug,
                "description": ch_data.description,
                "banner_picture_url": ch_data.banner_picture_url,
                "user": user,
            }
            channel: KickChannel | None = KickChannel.objects.filter(
                kick_id=ch_data.id,
            ).first()
            created = channel is None
            if channel is None:
                channel = KickChannel.objects.create(
                    kick_id=ch_data.id,
                    **channel_defaults,
                )
            else:
                self._save_if_changed(channel, channel_defaults)
            if created:
                logger.info("Created new channel: %s", channel.kick_id)

            channel_objs.append(channel)

        campaign.channels.set(channel_objs)

        for reward_data in data.rewards:
            # Resolve reward's category (may differ from campaign category)
            reward_category: KickCategory | None = category
            if reward_data.category_id > 0 and (
                cat_data is None or reward_data.category_id != cat_data.id
            ):
                reward_category, created = KickCategory.objects.get_or_create(
                    kick_id=reward_data.category_id,
                    defaults={
                        "name": "",
                        "slug": "",
                        "image_url": "",
                    },
                )
                if created:
                    logger.info("Created new category: %s", reward_category.kick_id)

            # Resolve reward's organization (may differ from campaign org)
            reward_org: KickOrganization = org
            if reward_data.organization_id != org_data.id:
                reward_org = KickOrganization.objects.filter(
                    kick_id=reward_data.organization_id,
                ).first() or KickOrganization.objects.create(
                    kick_id=reward_data.organization_id,
                    name="",
                    logo_url="",
                    url="",
                    restricted=False,
                )
                created = not reward_org.name and not reward_org.url
                if created:
                    logger.info("Created new organization: %s", reward_org.kick_id)

            reward_defaults: dict[str, KickFieldValue] = {
                "name": reward_data.name,
                "image_url": reward_data.image_url,
                "required_units": reward_data.required_units,
                "campaign": campaign,
                "category": reward_category,
                "organization": reward_org,
            }
            reward: KickReward | None = KickReward.objects.filter(
                kick_id=reward_data.id,
            ).first()
            if reward is None:
                KickReward.objects.create(kick_id=reward_data.id, **reward_defaults)
            else:
                self._save_if_changed(reward, reward_defaults)
