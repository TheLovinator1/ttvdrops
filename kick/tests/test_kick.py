import datetime
import re
from datetime import UTC
from datetime import datetime as dt
from datetime import timedelta
from io import StringIO
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest
from django.core.management import call_command
from django.test import Client
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pydantic import ValidationError

from kick.feeds import discord_timestamp
from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from kick.models import KickUser
from kick.schemas import KickDropCampaignSchema
from kick.schemas import KickDropsResponseSchema

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse
    from pytest_django.asserts import QuerySet

    from kick.schemas import KickDropCampaignSchema
    from kick.schemas import KickRewardSchema

# Minimal valid campaign fixture (single campaign, active status)
SINGLE_CAMPAIGN_JSON: dict[str, list[dict[str, Any]] | str] = {
    "data": [
        {
            "id": "01KKBNEM8TZG7ASRG42TK7RKRB",
            "name": "PUBG 9th Anniversary",
            "status": "active",
            "starts_at": "2026-03-11T08:00:00Z",
            "ends_at": "2026-03-31T07:59:00Z",
            "created_at": "2026-03-10T10:42:29.793929Z",
            "updated_at": "2026-03-10T10:42:30.248864Z",
            "connect_url": "https://accounts.krafton.com/auth/kick/callback",
            "url": "https://accounts.krafton.com",
            "category": {
                "id": 53,
                "name": "PUBG: Battlegrounds",
                "slug": "pubg-battlegrounds",
                "image_url": "https://files.kick.com/images/subcategories/53/banner/pubg-battlegrounds.jpg",
            },
            "organization": {
                "id": "01KFF1CP2Q2X6EFDN9M85M3M97",
                "name": "KRAFTON",
                "logo_url": "https://krafton.com/wp-content/uploads/2021/06/logo-krafton-brandcenter.png",
                "url": "https://krafton.com",
                "restricted": False,
                "email_notification": False,
            },
            "channels": [],
            "rewards": [
                {
                    "id": "01KKBM2YDPSBJD437HF7CDPTQT",
                    "name": "9th Anniversary Flamin' Cake",
                    "image_url": "drops/reward-image/01kkbm2ydpsbjd437hf7cdptqt-01kkbm2ydpsbjd437hf8yhrzqm.png",
                    "required_units": 30,
                    "category_id": 53,
                    "organization_id": "01KFF1CP2Q2X6EFDN9M85M3M97",
                },
            ],
            "rule": {"id": 1, "name": "Watch to redeem"},
        },
    ],
    "message": "Success",
}

# Campaign with channels fixture
CAMPAIGN_WITH_CHANNELS_JSON: dict[str, list[dict[str, Any]] | str] = {
    "data": [
        {
            "id": "01K8X4WHMVTF4HQNT9RRTGBACK",
            "name": "Team Ricoy MP5",
            "status": "expired",
            "starts_at": "2025-11-13T21:00:00Z",
            "ends_at": "2025-11-23T23:59:00Z",
            "created_at": "2025-10-31T12:46:39.78377Z",
            "updated_at": "2025-11-05T20:42:33.761611Z",
            "connect_url": "https://kick.facepunch.com",
            "url": "https://kick.facepunch.com",
            "category": {
                "id": 13,
                "name": "Rust",
                "slug": "rust",
                "image_url": "https://files.kick.com/images/subcategories/13/banner/77786fd4-4f33-4221-b8ee-2bf3cb4d041e",
            },
            "organization": {
                "id": "01K6WKP5BBMPZJ89G5Y7QK1E9P",
                "name": "Facepunch Studios",
                "logo_url": "https://images.squarespace-cdn.com/content/v1/627cb6fa4355783e5e375440/b47ec50e-bc8b-4801-87cb-dee12da27748/default-light.png?format=1500w",
                "url": "https://kick.facepunch.com",
                "restricted": False,
                "email_notification": False,
            },
            "channels": [
                {
                    "id": 1661881,
                    "slug": "ricoy",
                    "description": "",
                    "banner_picture_url": "https://files.kick.com/images/channel/1661881/banner_image/default-banner-2.jpg",
                    "user": {
                        "id": 1711034,
                        "username": "Ricoy",
                        "profile_picture": "https://files.kick.com/images/user/1711034/profile_image/conversion/101aa7a7-6f75-43d2-a0d3-8fde7aa7f664-medium.webp",
                    },
                },
                {
                    "id": 969446,
                    "slug": "dilanzito",
                    "description": "",
                    "banner_picture_url": "https://files.kick.com/images/channel/969446/banner_image/default-banner-2.jpg",
                    "user": {
                        "id": 1011977,
                        "username": "dilanzito",
                        "profile_picture": "https://files.kick.com/images/user/1011977/profile_image/conversion/32a59b29-c319-4210-b5a0-ac14dd112f13-medium.webp",
                    },
                },
            ],
            "rewards": [
                {
                    "id": "01K8X3P1D3AE2PNEBP1VJCVXAR",
                    "name": "Team Ricoy MP5",
                    "image_url": "drops/reward-image/01k8x3p1d3ae2pnebp1vjcvxar.png",
                    "required_units": 120,
                    "category_id": 13,
                    "organization_id": "01K6WKP5BBMPZJ89G5Y7QK1E9P",
                },
            ],
            "rule": {"id": 1, "name": "Watch to redeem"},
        },
    ],
    "message": "Success",
}


# MARK: Schema tests
class KickDropsResponseSchemaTest(TestCase):
    """Tests for Pydantic schema validation of the Kick drops API response."""

    def test_valid_single_campaign(self) -> None:
        """Schema validates a minimal valid campaign without channels."""
        result: KickDropsResponseSchema = KickDropsResponseSchema.model_validate(
            SINGLE_CAMPAIGN_JSON,
        )
        assert result.message == "Success"
        assert len(result.data) == 1
        campaign: KickDropCampaignSchema = result.data[0]
        assert campaign.id == "01KKBNEM8TZG7ASRG42TK7RKRB"
        assert campaign.name == "PUBG 9th Anniversary"
        assert campaign.status == "active"
        assert len(campaign.channels) == 0
        assert len(campaign.rewards) == 1

    def test_campaign_with_channels(self) -> None:
        """Schema validates a campaign that includes channel data."""
        result: KickDropsResponseSchema = KickDropsResponseSchema.model_validate(
            CAMPAIGN_WITH_CHANNELS_JSON,
        )
        campaign: KickDropCampaignSchema = result.data[0]
        assert len(campaign.channels) == 2
        assert campaign.channels[0].slug == "ricoy"
        assert campaign.channels[0].user.username == "Ricoy"

    def test_reward_fields(self) -> None:
        """Reward fields parse correctly including relative image URL."""
        result: KickDropsResponseSchema = KickDropsResponseSchema.model_validate(
            SINGLE_CAMPAIGN_JSON,
        )
        reward: KickRewardSchema = result.data[0].rewards[0]
        assert reward.id == "01KKBM2YDPSBJD437HF7CDPTQT"
        assert reward.required_units == 30
        assert "drops/reward-image/" in reward.image_url

    def test_empty_channels_list(self) -> None:
        """Schema accepts an empty channels list."""
        result: KickDropsResponseSchema = KickDropsResponseSchema.model_validate(
            SINGLE_CAMPAIGN_JSON,
        )
        assert result.data[0].channels == []

    def test_extra_fields_rejected(self) -> None:
        """Extra fields in the API response cause a ValidationError."""
        bad_payload: dict[str, str | list] = {
            "data": [],
            "message": "Success",
            "unexpected_field": "oops",
        }
        with pytest.raises(ValidationError):
            KickDropsResponseSchema.model_validate(bad_payload)


# MARK: Model tests
class KickRewardFullImageUrlTest(TestCase):
    """Tests for KickReward.full_image_url property."""

    def _make_reward(self, image_url: str) -> KickReward:
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-1",
            name="Org",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=99,
            name="Cat",
            slug="cat",
        )
        campaign: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="camp-1",
            name="Test Campaign",
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
        )
        return KickReward.objects.create(
            kick_id="reward-1",
            name="Reward",
            image_url=image_url,
            required_units=60,
            campaign=campaign,
            category=cat,
            organization=org,
        )

    def test_relative_image_url_gets_base_prefix(self) -> None:
        """If image_url is relative, full_image_url should prepend the Kick files base URL."""
        reward: KickReward = self._make_reward("drops/reward-image/abc.png")
        assert (
            reward.full_image_url
            == "https://ext.cdn.kick.com/drops/reward-image/abc.png"
        )

    def test_absolute_image_url_unchanged(self) -> None:
        """If image_url is already absolute, full_image_url should return it unchanged."""
        reward: KickReward = self._make_reward("https://example.com/image.png")
        assert reward.full_image_url == "https://example.com/image.png"

    def test_empty_image_url_returns_empty(self) -> None:
        """If image_url is empty, full_image_url should also be empty."""
        reward: KickReward = self._make_reward("")
        assert not reward.full_image_url


class KickChannelUrlTest(TestCase):
    """Tests for KickChannel.channel_url property."""

    def test_url_with_slug(self) -> None:
        """If slug is present, channel_url should return the full Kick channel URL."""
        user: KickUser = KickUser.objects.create(kick_id=1, username="testuser")
        channel: KickChannel = KickChannel.objects.create(
            kick_id=100,
            slug="testuser",
            user=user,
        )
        assert channel.channel_url == "https://kick.com/testuser"

    def test_url_without_slug(self) -> None:
        """If slug is empty, channel_url should return an empty string."""
        channel: KickChannel = KickChannel.objects.create(kick_id=101, slug="")
        assert not channel.channel_url


class KickDropCampaignIsActiveTest(TestCase):
    """Tests for KickDropCampaign.is_active property."""

    def _make_campaign(
        self,
        starts_at: dt | None,
        ends_at: dt | None,
    ) -> KickDropCampaign:
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-active",
            name="Org",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=1,
            name="Cat",
            slug="cat",
        )
        return KickDropCampaign.objects.create(
            kick_id="camp-active",
            name="Active Campaign",
            starts_at=starts_at,
            ends_at=ends_at,
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
        )

    def test_no_dates_is_not_active(self) -> None:
        """If starts_at and ends_at are None, is_active should return False."""
        campaign: KickDropCampaign = self._make_campaign(None, None)
        assert not campaign.is_active

    def test_expired_campaign_is_not_active(self) -> None:
        """If current date is past ends_at, is_active should return False."""
        campaign: KickDropCampaign = self._make_campaign(
            dt(2020, 1, 1, tzinfo=UTC),
            dt(2020, 12, 31, tzinfo=UTC),
        )
        assert not campaign.is_active

    def test_future_campaign_is_not_active(self) -> None:
        """If current date is before starts_at, is_active should return False."""
        campaign: KickDropCampaign = self._make_campaign(
            dt(2099, 1, 1, tzinfo=UTC),
            dt(2099, 12, 31, tzinfo=UTC),
        )
        assert not campaign.is_active


class KickDropCampaignMergedRewardsTest(TestCase):
    """Tests for KickDropCampaign.merged_rewards property."""

    def _make_campaign(self) -> KickDropCampaign:
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-merge",
            name="Merge Org",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=801,
            name="Merge Cat",
            slug="merge-cat",
        )
        return KickDropCampaign.objects.create(
            kick_id="camp-merge",
            name="Merge Campaign",
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
        )

    def test_merges_reward_with_con_suffix(self) -> None:
        """Rewards with and without '(Con)' should be treated as one entry."""
        campaign: KickDropCampaign = self._make_campaign()
        KickReward.objects.create(
            kick_id="reward-base",
            name="Anniversary Cake",
            image_url="drops/reward-image/base.png",
            required_units=30,
            campaign=campaign,
            category=campaign.category,
            organization=campaign.organization,
        )
        KickReward.objects.create(
            kick_id="reward-con",
            name="Anniversary Cake (Con)",
            image_url="drops/reward-image/con.png",
            required_units=30,
            campaign=campaign,
            category=campaign.category,
            organization=campaign.organization,
        )

        merged: list[KickReward] = campaign.merged_rewards
        assert len(merged) == 1
        assert merged[0].name == "Anniversary Cake"

    def test_keeps_con_name_when_only_variant_available(self) -> None:
        """If only '(Con)' exists, it should still appear in merged rewards."""
        campaign: KickDropCampaign = self._make_campaign()
        KickReward.objects.create(
            kick_id="reward-only-con",
            name="Anniversary Cake(Con)",
            image_url="drops/reward-image/con-only.png",
            required_units=30,
            campaign=campaign,
            category=campaign.category,
            organization=campaign.organization,
        )

        merged: list[KickReward] = campaign.merged_rewards
        assert len(merged) == 1
        assert merged[0].name == "Anniversary Cake(Con)"

    def test_merges_rewards_with_ampersand_spacing_difference(self) -> None:
        """Rewards that only differ by ampersand spacing should be treated as one entry."""
        campaign: KickDropCampaign = self._make_campaign()
        KickReward.objects.create(
            kick_id="reward-anniversary-base",
            name="9th Anniversary Cake & Confetti",
            image_url="drops/reward-image/cake-confetti-base.png",
            required_units=60,
            campaign=campaign,
            category=campaign.category,
            organization=campaign.organization,
        )
        KickReward.objects.create(
            kick_id="reward-anniversary-con",
            name="9th Anniversary Cake&Confetti (Con)",
            image_url="drops/reward-image/cake-confetti-con.png",
            required_units=60,
            campaign=campaign,
            category=campaign.category,
            organization=campaign.organization,
        )

        merged: list[KickReward] = campaign.merged_rewards
        assert len(merged) == 1
        assert merged[0].name == "9th Anniversary Cake & Confetti"


# MARK: Management command tests
class ImportKickDropsCommandTest(TestCase):
    """Tests for the import_kick_drops management command."""

    def _run_command(self, json_payload: dict) -> tuple[str, str]:

        mock_response = MagicMock()
        mock_response.json.return_value = json_payload
        mock_response.raise_for_status.return_value = None

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "kick.management.commands.import_kick_drops.httpx.get",
            return_value=mock_response,
        ):
            call_command("import_kick_drops", stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def test_imports_single_campaign(self) -> None:
        """Command creates campaign and related objects from valid API response."""
        self._run_command(SINGLE_CAMPAIGN_JSON)
        assert KickDropCampaign.objects.count() == 1
        assert KickOrganization.objects.count() == 1
        assert KickCategory.objects.count() == 1
        assert KickReward.objects.count() == 1

        campaign: KickDropCampaign = KickDropCampaign.objects.get()
        assert campaign.name == "PUBG 9th Anniversary"
        assert campaign.status == "active"
        assert campaign.organization is not None
        assert campaign.category is not None
        assert campaign.organization.name == "KRAFTON"
        assert campaign.category.name == "PUBG: Battlegrounds"

    def test_imports_campaign_with_channels(self) -> None:
        """Command creates channels and links them to the campaign."""
        self._run_command(CAMPAIGN_WITH_CHANNELS_JSON)
        campaign: KickDropCampaign = KickDropCampaign.objects.get()
        assert campaign.channels.count() == 2
        assert KickUser.objects.count() == 2
        assert KickChannel.objects.count() == 2
        slugs: set[str] = set(campaign.channels.values_list("slug", flat=True))
        assert slugs == {"ricoy", "dilanzito"}

    def test_import_is_idempotent(self) -> None:
        """Running the import twice does not duplicate records."""
        self._run_command(SINGLE_CAMPAIGN_JSON)
        self._run_command(SINGLE_CAMPAIGN_JSON)
        assert KickDropCampaign.objects.count() == 1
        assert KickOrganization.objects.count() == 1
        assert KickReward.objects.count() == 1

    def test_http_error_is_handled_gracefully(self) -> None:
        """HTTP error during fetch writes to stderr and does not crash."""
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "kick.management.commands.import_kick_drops.httpx.get",
            side_effect=httpx.HTTPError("connection refused"),
        ):
            call_command("import_kick_drops", stdout=stdout, stderr=stderr)

        assert "HTTP error" in stderr.getvalue()
        assert KickDropCampaign.objects.count() == 0

    def test_validation_error_is_handled_gracefully(self) -> None:
        """Invalid JSON structure writes to stderr and does not crash."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"totally": "wrong"}
        mock_response.raise_for_status.return_value = None

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "kick.management.commands.import_kick_drops.httpx.get",
            return_value=mock_response,
        ):
            call_command("import_kick_drops", stdout=stdout, stderr=stderr)

        assert "validation failed" in stderr.getvalue()
        assert KickDropCampaign.objects.count() == 0

    def test_success_message_printed(self) -> None:
        """Success output should report the number of campaigns imported."""
        stdout, _ = self._run_command(SINGLE_CAMPAIGN_JSON)
        assert "1/1" in stdout


# MARK: View tests
class KickDashboardViewTest(TestCase):
    """Tests for the kick dashboard view."""

    def _make_active_campaign(self) -> KickDropCampaign:
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-view-1",
            name="Org View",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=200,
            name="View Cat",
            slug="view-cat",
        )
        return KickDropCampaign.objects.create(
            kick_id="camp-view-1",
            name="Active View Campaign",
            status="active",
            starts_at=dt(2020, 1, 1, tzinfo=UTC),
            ends_at=dt(2099, 12, 31, tzinfo=UTC),
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
            is_fully_imported=True,
        )

    def test_dashboard_returns_200(self) -> None:
        """Dashboard view should return HTTP 200 status code."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:dashboard"),
        )
        assert response.status_code == 200

    def test_dashboard_shows_active_campaigns(self) -> None:
        """Active campaigns should be displayed on the dashboard."""
        campaign: KickDropCampaign = self._make_active_campaign()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:dashboard"),
        )
        assert campaign.name in response.content.decode()


class KickCampaignListViewTest(TestCase):
    """Tests for the kick campaign list view."""

    def _make_campaign(
        self,
        kick_id: str,
        name: str,
        status: str = "active",
    ) -> KickDropCampaign:
        org, _ = KickOrganization.objects.get_or_create(
            kick_id="org-list",
            defaults={"name": "List Org"},
        )
        cat, _ = KickCategory.objects.get_or_create(
            kick_id=300,
            defaults={"name": "List Cat", "slug": "list-cat"},
        )
        # Set dates so the active/expired filter works correctly
        if status == "active":
            starts_at = dt(2020, 1, 1, tzinfo=UTC)
            ends_at = dt(2099, 12, 31, tzinfo=UTC)
        else:
            starts_at = dt(2020, 1, 1, tzinfo=UTC)
            ends_at = dt(2020, 12, 31, tzinfo=UTC)
        return KickDropCampaign.objects.create(
            kick_id=kick_id,
            name=name,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
            is_fully_imported=True,
        )

    def test_campaign_list_returns_200(self) -> None:
        """Campaign list view should return HTTP 200 status code."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:campaign_list"),
        )
        assert response.status_code == 200

    def test_campaign_list_shows_campaigns(self) -> None:
        """Campaigns should be displayed in the campaign list view."""
        campaign: KickDropCampaign = self._make_campaign("camp-list-1", "List Campaign")
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:campaign_list"),
        )
        assert campaign.name in response.content.decode()

    def test_campaign_list_status_filter(self) -> None:
        """Filtering by status should show only campaigns with that status."""
        active: KickDropCampaign = self._make_campaign(
            "camp-list-a",
            "Active Camp",
            status="active",
        )
        expired: KickDropCampaign = self._make_campaign(
            "camp-list-e",
            "Expired Camp",
            status="expired",
        )
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:campaign_list") + "?status=active",
        )
        content: str = response.content.decode()
        assert active.name in content
        assert expired.name not in content


class KickCampaignDetailViewTest(TestCase):
    """Tests for the kick campaign detail view."""

    def _make_campaign(self) -> KickDropCampaign:
        """Helper method to create a campaign with related org and category for detail view tests.

        Returns:
            KickDropCampaign: The created campaign instance.
        """
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-det-1",
            name="Detail Org",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=400,
            name="Detail Cat",
            slug="detail-cat",
        )
        return KickDropCampaign.objects.create(
            kick_id="camp-det-1",
            name="Detail Campaign",
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
        )

    def test_campaign_detail_returns_200(self) -> None:
        """Campaign detail view should return HTTP 200 status code for existing campaign."""
        campaign: KickDropCampaign = self._make_campaign()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:campaign_detail", kwargs={"kick_id": campaign.kick_id}),
        )
        assert response.status_code == 200

    def test_campaign_detail_shows_name(self) -> None:
        """Campaign detail view should display the campaign name."""
        campaign: KickDropCampaign = self._make_campaign()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:campaign_detail", kwargs={"kick_id": campaign.kick_id}),
        )
        assert campaign.name in response.content.decode()

    def test_campaign_detail_404_for_unknown(self) -> None:
        """Campaign detail view should return HTTP 404 status code for unknown campaign."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:campaign_detail", kwargs={"kick_id": "nonexistent-id"}),
        )
        assert response.status_code == 404


class KickCategoryListViewTest(TestCase):
    """Tests for the kick category list view."""

    def test_category_list_returns_200(self) -> None:
        """Category list view should return HTTP 200 status code."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:game_list"),
        )
        assert response.status_code == 200

    def test_category_list_shows_categories(self) -> None:
        """Category list view should display the categories."""
        cat: KickCategory = KickCategory.objects.create(
            kick_id=500,
            name="Category View",
            slug="category-view",
        )
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:game_list"),
        )
        assert cat.name in response.content.decode()


class KickCategoryDetailViewTest(TestCase):
    """Tests for the kick category detail view."""

    def _make_category(self) -> KickCategory:
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-catdet-1",
            name="Catdet Org",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=600,
            name="Catdet Cat",
            slug="catdet-cat",
        )
        KickDropCampaign.objects.create(
            kick_id="camp-catdet-1",
            name="Catdet Campaign",
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
        )
        return cat

    def test_category_detail_returns_200(self) -> None:
        """Category detail view should return HTTP 200 status code for existing category."""
        cat: KickCategory = self._make_category()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:game_detail", kwargs={"kick_id": cat.kick_id}),
        )
        assert response.status_code == 200

    def test_category_detail_shows_name(self) -> None:
        """Category detail view should display the category name."""
        cat: KickCategory = self._make_category()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:game_detail", kwargs={"kick_id": cat.kick_id}),
        )
        assert cat.name in response.content.decode()

    def test_category_detail_404_for_unknown(self) -> None:
        """Category detail view should return HTTP 404 status code for unknown category."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:game_detail", kwargs={"kick_id": 99999}),
        )
        assert response.status_code == 404


class KickOrganizationListViewTest(TestCase):
    """Tests for the kick organization list view."""

    def test_organization_list_returns_200(self) -> None:
        """Organization list view should return HTTP 200 status code."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:organization_list"),
        )
        assert response.status_code == 200

    def test_organization_list_shows_orgs(self) -> None:
        """Organization list view should display the organizations."""
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-list-1",
            name="List Org View",
        )
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:organization_list"),
        )
        assert org.name in response.content.decode()


class KickOrganizationDetailViewTest(TestCase):
    """Tests for the kick organization detail view."""

    def _make_org(self) -> KickOrganization:
        org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-orgdet-1",
            name="Orgdet Org",
        )
        cat: KickCategory = KickCategory.objects.create(
            kick_id=700,
            name="Orgdet Cat",
            slug="orgdet-cat",
        )
        KickDropCampaign.objects.create(
            kick_id="camp-orgdet-1",
            name="Orgdet Campaign",
            organization=org,
            category=cat,
            rule_id=1,
            rule_name="Watch to redeem",
        )
        return org

    def test_organization_detail_returns_200(self) -> None:
        """Organization detail view should return HTTP 200 status code for existing organization."""
        org: KickOrganization = self._make_org()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:organization_detail", kwargs={"kick_id": org.kick_id}),
        )
        assert response.status_code == 200

    def test_organization_detail_shows_name(self) -> None:
        """Organization detail view should display the organization name."""
        org: KickOrganization = self._make_org()
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("kick:organization_detail", kwargs={"kick_id": org.kick_id}),
        )
        assert org.name in response.content.decode()

    def test_organization_detail_404_for_unknown(self) -> None:
        """Organization detail view should return HTTP 404 status code for unknown organization."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse(
                "kick:organization_detail",
                kwargs={"kick_id": "nonexistent-org-id"},
            ),
        )
        assert response.status_code == 404


class KickFeedsTest(TestCase):
    """Tests for Kick RSS/Atom/Discord feed endpoints."""

    def setUp(self) -> None:
        """Create a minimal active campaign fixture for feed tests."""
        self.org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-feed-1",
            name="Feed Org",
            logo_url="https://example.com/org-logo.png",
            url="https://example.com/org",
        )
        self.category: KickCategory = KickCategory.objects.create(
            kick_id=123,
            name="Feed Category",
            slug="feed-category",
            image_url="https://example.com/category.png",
        )
        self.campaign: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="camp-feed-1",
            name="Feed Campaign",
            status="active",
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(days=1),
            organization=self.org,
            category=self.category,
            url="https://example.com/campaign",
            connect_url="https://example.com/connect",
            rule_id=1,
            rule_name="Watch to redeem",
        )
        self.user: KickUser = KickUser.objects.create(
            kick_id=2001,
            username="feeduser",
        )
        self.channel: KickChannel = KickChannel.objects.create(
            kick_id=2002,
            slug="feedchannel",
            user=self.user,
        )
        self.campaign.channels.add(self.channel)
        KickReward.objects.create(
            kick_id="reward-feed-1",
            name="Feed Reward",
            image_url="https://example.com/reward.png",
            required_units=30,
            campaign=self.campaign,
            category=self.category,
            organization=self.org,
        )

    def test_rss_feed_routes_return_200(self) -> None:
        """Kick RSS feeds should return 200 and browser-friendly content type."""
        urls: list[str] = [
            reverse("kick:campaign_feed"),
            reverse("kick:game_feed"),
            reverse("kick:game_campaign_feed", args=[self.category.kick_id]),
            reverse("kick:organization_feed"),
        ]

        for url in urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200
            assert response["Content-Type"] == "application/xml; charset=utf-8"
            assert response["Content-Disposition"] == "inline"

    def test_atom_feed_routes_return_200(self) -> None:
        """Kick Atom feeds should return 200 and include Atom XML root."""
        urls: list[str] = [
            reverse("kick:campaign_feed_atom"),
            reverse("kick:game_feed_atom"),
            reverse("kick:game_campaign_feed_atom", args=[self.category.kick_id]),
            reverse("kick:organization_feed_atom"),
        ]

        for url in urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200
            assert response["Content-Type"] == "application/xml; charset=utf-8"
            content: str = response.content.decode("utf-8")
            assert "<feed" in content
            assert "http://www.w3.org/2005/Atom" in content

    def test_discord_feed_routes_return_200(self) -> None:
        """Kick Discord feeds should return 200 and include Atom XML root."""
        urls: list[str] = [
            reverse("kick:campaign_feed_discord"),
            reverse("kick:game_feed_discord"),
            reverse(
                "kick:game_campaign_feed_discord",
                args=[self.category.kick_id],
            ),
            reverse("kick:organization_feed_discord"),
        ]

        for url in urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200
            assert response["Content-Type"] == "application/xml; charset=utf-8"
            content: str = response.content.decode("utf-8")
            assert "<feed" in content
            assert "http://www.w3.org/2005/Atom" in content

    def test_discord_campaign_feeds_contain_discord_timestamps(self) -> None:
        """Kick Discord campaign feeds should include escaped Discord relative timestamps."""
        urls: list[str] = [
            reverse("kick:campaign_feed_discord"),
            reverse(
                "kick:game_campaign_feed_discord",
                args=[self.category.kick_id],
            ),
        ]

        for url in urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200
            content: str = response.content.decode("utf-8")
            discord_pattern: re.Pattern[str] = re.compile(r"&amp;lt;t:\d+:R&amp;gt;")
            assert discord_pattern.search(content), (
                f"Expected Discord timestamp in feed {url}, got: {content}"
            )

    def test_discord_timestamp_helper(self) -> None:
        """discord_timestamp helper should return escaped Discord token and handle None."""
        sample_dt: dt = dt(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
        result: str = str(discord_timestamp(sample_dt))
        assert result.startswith("&lt;t:")
        assert result.endswith(":R&gt;")
        assert not str(discord_timestamp(None))


class KickDropCampaignFullyImportedTest(TestCase):
    """Tests for KickDropCampaign.is_fully_imported field and filtering."""

    def setUp(self) -> None:
        """Create common org and category fixtures for campaign import tests."""
        self.org: KickOrganization = KickOrganization.objects.create(
            kick_id="org-fi",
            name="Org",
        )
        self.cat: KickCategory = KickCategory.objects.create(
            kick_id=1,
            name="Cat",
            slug="cat",
        )

    def test_campaign_not_fully_imported_by_default(self) -> None:
        """By default, a newly created campaign should have is_fully_imported set to False."""
        campaign: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="camp-fi-1",
            name="Not Imported",
            organization=self.org,
            category=self.cat,
            rule_id=1,
            rule_name="Rule",
        )
        assert campaign.is_fully_imported is False

    def test_campaign_fully_imported_flag(self) -> None:
        """When creating a campaign with is_fully_imported=True, the flag should be set correctly."""
        campaign: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="camp-fi-2",
            name="Imported",
            organization=self.org,
            category=self.cat,
            rule_id=1,
            rule_name="Rule",
            is_fully_imported=True,
        )
        assert campaign.is_fully_imported is True

    def test_queryset_filters_only_fully_imported(self) -> None:
        """Filtering campaigns by is_fully_imported should return only those with the flag set to True."""
        KickDropCampaign.objects.create(
            kick_id="camp-fi-3",
            name="Not Imported",
            organization=self.org,
            category=self.cat,
            rule_id=1,
            rule_name="Rule",
        )
        imported: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="camp-fi-4",
            name="Imported",
            organization=self.org,
            category=self.cat,
            rule_id=1,
            rule_name="Rule",
            is_fully_imported=True,
        )
        qs: QuerySet[KickDropCampaign, KickDropCampaign] = (
            KickDropCampaign.objects.filter(is_fully_imported=True)
        )
        assert list(qs) == [imported]

    def test_dashboard_view_only_shows_fully_imported(self) -> None:
        """Dashboard view should only show fully imported and active campaigns."""
        now: dt = timezone.now()

        # Not imported, but active
        KickDropCampaign.objects.create(
            kick_id="camp-fi-5",
            name="Not Imported",
            organization=self.org,
            category=self.cat,
            rule_id=1,
            rule_name="Rule",
            starts_at=now - datetime.timedelta(days=1),
            ends_at=now + datetime.timedelta(days=1),
        )

        # Imported and active
        imported: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="camp-fi-6",
            name="Imported",
            organization=self.org,
            category=self.cat,
            rule_id=1,
            rule_name="Rule",
            is_fully_imported=True,
            starts_at=now - datetime.timedelta(days=1),
            ends_at=now + datetime.timedelta(days=1),
        )

        client = Client()
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("kick:dashboard"))
        assert response.status_code == 200
        campaigns: QuerySet[KickDropCampaign, KickDropCampaign] = response.context[
            "active_campaigns"
        ]
        assert list(campaigns) == [imported]
