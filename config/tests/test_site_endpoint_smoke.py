from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chzzk.models import ChzzkCampaign
from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from kick.models import KickUser
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from django.test.client import _MonkeyPatchedWSGIResponse


class SiteEndpointSmokeTest(TestCase):
    """Smoke-test all named site endpoints with realistic fixture data."""

    def setUp(self) -> None:
        """Set up representative Twitch, Kick, and CHZZK data for endpoint smoke tests."""
        now: datetime = timezone.now()

        # Twitch fixtures
        self.twitch_org: Organization = Organization.objects.create(
            twitch_id="smoke-org-1",
            name="Smoke Organization",
        )
        self.twitch_game: Game = Game.objects.create(
            twitch_id="smoke-game-1",
            slug="smoke-game",
            name="Smoke Game",
            display_name="Smoke Game",
            box_art="https://example.com/smoke-game.png",
        )
        self.twitch_game.owners.add(self.twitch_org)

        self.twitch_channel: Channel = Channel.objects.create(
            twitch_id="smoke-channel-1",
            name="smokechannel",
            display_name="SmokeChannel",
        )

        self.twitch_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="smoke-campaign-1",
            name="Smoke Campaign",
            description="Smoke campaign description",
            game=self.twitch_game,
            image_url="https://example.com/smoke-campaign.png",
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )
        self.twitch_campaign.allow_channels.add(self.twitch_channel)

        self.twitch_drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="smoke-drop-1",
            name="Smoke Drop",
            campaign=self.twitch_campaign,
            required_minutes_watched=15,
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
        )
        self.twitch_benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="smoke-benefit-1",
            name="Smoke Benefit",
            image_asset_url="https://example.com/smoke-benefit.png",
        )
        self.twitch_drop.benefits.add(self.twitch_benefit)

        self.twitch_reward_campaign: RewardCampaign = RewardCampaign.objects.create(
            twitch_id="smoke-reward-campaign-1",
            name="Smoke Reward Campaign",
            brand="Smoke Brand",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=2),
            status="ACTIVE",
            summary="Smoke reward summary",
            external_url="https://example.com/smoke-reward",
            is_sitewide=False,
            game=self.twitch_game,
        )

        self.badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(
            set_id="smoke-badge-set",
        )
        ChatBadge.objects.create(
            badge_set=self.badge_set,
            badge_id="1",
            image_url_1x="https://example.com/badge-1x.png",
            image_url_2x="https://example.com/badge-2x.png",
            image_url_4x="https://example.com/badge-4x.png",
            title="Smoke Badge",
            description="Smoke badge description",
        )

        # Kick fixtures
        self.kick_org: KickOrganization = KickOrganization.objects.create(
            kick_id="smoke-kick-org-1",
            name="Smoke Kick Organization",
        )
        self.kick_category: KickCategory = KickCategory.objects.create(
            kick_id=9101,
            name="Smoke Kick Category",
            slug="smoke-kick-category",
            image_url="https://example.com/smoke-kick-category.png",
        )
        self.kick_campaign: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="smoke-kick-campaign-1",
            name="Smoke Kick Campaign",
            status="active",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
            organization=self.kick_org,
            category=self.kick_category,
            rule_id=1,
            rule_name="Watch to redeem",
            is_fully_imported=True,
        )
        kick_user: KickUser = KickUser.objects.create(
            kick_id=990001,
            username="smokekickuser",
        )
        kick_channel: KickChannel = KickChannel.objects.create(
            kick_id=990002,
            slug="smokekickchannel",
            user=kick_user,
        )
        self.kick_campaign.channels.add(kick_channel)
        KickReward.objects.create(
            kick_id="smoke-kick-reward-1",
            name="Smoke Kick Reward",
            image_url="drops/reward-image/smoke-kick-reward.png",
            required_units=20,
            campaign=self.kick_campaign,
            category=self.kick_category,
            organization=self.kick_org,
        )

        # CHZZK fixtures
        self.chzzk_campaign: ChzzkCampaign = ChzzkCampaign.objects.create(
            campaign_no=9901,
            title="Smoke CHZZK Campaign",
            description="Smoke CHZZK description",
            category_type="game",
            category_id="1",
            category_value="SmokeGame",
            service_id="chzzk",
            state="ACTIVE",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
            raw_json_v1={"ok": True},
        )
        self.chzzk_campaign.rewards.create(  # pyright: ignore[reportAttributeAccessIssue]
            reward_no=991,
            title="Smoke CHZZK Reward",
            reward_type="ITEM",
            campaign_reward_type="Standard",
            condition_type="watch",
            condition_for_minutes=10,
            ios_based_reward=False,
            code_remaining_count=100,
        )

        # Core dataset download fixture
        self.dataset_dir: Path = settings.DATA_DIR / "datasets"
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        self.dataset_name = "smoke-dataset.zst"
        (self.dataset_dir / self.dataset_name).write_bytes(b"smoke")

    def tearDown(self) -> None:
        """Clean up any files created for testing."""
        dataset_path: Path = self.dataset_dir / self.dataset_name
        if dataset_path.exists():
            dataset_path.unlink()

    def test_all_site_endpoints_return_success(self) -> None:
        """Test that all named site endpoints return a successful response with representative data."""
        endpoints: list[tuple[str, dict[str, str | int], int]] = [
            # Top-level config endpoints
            ("sitemap", {}, 200),
            ("sitemap-static", {}, 200),
            ("sitemap-twitch-channels", {}, 200),
            ("sitemap-twitch-drops", {}, 200),
            ("sitemap-twitch-others", {}, 200),
            ("sitemap-kick", {}, 200),
            ("sitemap-youtube", {}, 200),
            # Core endpoints
            ("core:dashboard", {}, 200),
            ("core:search", {}, 200),
            ("core:debug", {}, 200),
            ("core:dataset_backups", {}, 200),
            ("core:dataset_backup_download", {"relative_path": self.dataset_name}, 200),
            ("core:docs_rss", {}, 200),
            ("core:campaign_feed", {}, 200),
            ("core:game_feed", {}, 200),
            ("core:game_campaign_feed", {"twitch_id": self.twitch_game.twitch_id}, 200),
            ("core:organization_feed", {}, 200),
            ("core:reward_campaign_feed", {}, 200),
            ("core:campaign_feed_atom", {}, 200),
            ("core:game_feed_atom", {}, 200),
            (
                "core:game_campaign_feed_atom",
                {"twitch_id": self.twitch_game.twitch_id},
                200,
            ),
            ("core:organization_feed_atom", {}, 200),
            ("core:reward_campaign_feed_atom", {}, 200),
            ("core:campaign_feed_discord", {}, 200),
            ("core:game_feed_discord", {}, 200),
            (
                "core:game_campaign_feed_discord",
                {"twitch_id": self.twitch_game.twitch_id},
                200,
            ),
            ("core:organization_feed_discord", {}, 200),
            ("core:reward_campaign_feed_discord", {}, 200),
            # Twitch endpoints
            ("twitch:dashboard", {}, 200),
            ("twitch:badge_list", {}, 200),
            ("twitch:badge_set_detail", {"set_id": self.badge_set.set_id}, 200),
            ("twitch:campaign_list", {}, 200),
            (
                "twitch:campaign_detail",
                {"twitch_id": self.twitch_campaign.twitch_id},
                200,
            ),
            ("twitch:channel_list", {}, 200),
            (
                "twitch:channel_detail",
                {"twitch_id": self.twitch_channel.twitch_id},
                200,
            ),
            ("twitch:emote_gallery", {}, 200),
            ("twitch:games_grid", {}, 200),
            ("twitch:games_list", {}, 200),
            ("twitch:game_detail", {"twitch_id": self.twitch_game.twitch_id}, 200),
            ("twitch:org_list", {}, 200),
            (
                "twitch:organization_detail",
                {"twitch_id": self.twitch_org.twitch_id},
                200,
            ),
            ("twitch:reward_campaign_list", {}, 200),
            (
                "twitch:reward_campaign_detail",
                {"twitch_id": self.twitch_reward_campaign.twitch_id},
                200,
            ),
            ("twitch:export_campaigns_csv", {}, 200),
            ("twitch:export_campaigns_json", {}, 200),
            ("twitch:export_games_csv", {}, 200),
            ("twitch:export_games_json", {}, 200),
            ("twitch:export_organizations_csv", {}, 200),
            ("twitch:export_organizations_json", {}, 200),
            # Kick endpoints
            ("kick:campaign_api_list", {}, 200),
            ("kick:dashboard", {}, 200),
            ("kick:campaign_list", {}, 200),
            ("kick:campaign_detail", {"kick_id": self.kick_campaign.kick_id}, 200),
            ("kick:game_list", {}, 200),
            ("kick:game_detail", {"kick_id": self.kick_category.kick_id}, 200),
            ("kick:category_list", {}, 200),
            ("kick:category_detail", {"kick_id": self.kick_category.kick_id}, 200),
            ("kick:organization_list", {}, 200),
            ("kick:organization_detail", {"kick_id": self.kick_org.kick_id}, 200),
            ("kick:campaign_feed", {}, 200),
            ("kick:game_feed", {}, 200),
            ("kick:game_campaign_feed", {"kick_id": self.kick_category.kick_id}, 200),
            ("kick:category_feed", {}, 200),
            (
                "kick:category_campaign_feed",
                {"kick_id": self.kick_category.kick_id},
                200,
            ),
            ("kick:organization_feed", {}, 200),
            ("kick:campaign_feed_atom", {}, 200),
            ("kick:game_feed_atom", {}, 200),
            (
                "kick:game_campaign_feed_atom",
                {"kick_id": self.kick_category.kick_id},
                200,
            ),
            ("kick:category_feed_atom", {}, 200),
            (
                "kick:category_campaign_feed_atom",
                {"kick_id": self.kick_category.kick_id},
                200,
            ),
            ("kick:organization_feed_atom", {}, 200),
            ("kick:campaign_feed_discord", {}, 200),
            ("kick:game_feed_discord", {}, 200),
            (
                "kick:game_campaign_feed_discord",
                {"kick_id": self.kick_category.kick_id},
                200,
            ),
            ("kick:category_feed_discord", {}, 200),
            (
                "kick:category_campaign_feed_discord",
                {"kick_id": self.kick_category.kick_id},
                200,
            ),
            ("kick:organization_feed_discord", {}, 200),
            # CHZZK endpoints
            ("chzzk:dashboard", {}, 200),
            ("chzzk:campaign_list", {}, 200),
            (
                "chzzk:campaign_detail",
                {"campaign_no": self.chzzk_campaign.campaign_no},
                200,
            ),
            ("chzzk:campaign_feed", {}, 200),
            ("chzzk:campaign_feed_atom", {}, 200),
            ("chzzk:campaign_feed_discord", {}, 200),
            # YouTube endpoint
            ("youtube:index", {}, 200),
        ]

        for route_name, kwargs, expected_status in endpoints:
            response: _MonkeyPatchedWSGIResponse = self.client.get(
                reverse(route_name, kwargs=kwargs),
            )
            assert response.status_code == expected_status, (
                f"{route_name} returned {response.status_code}, expected {expected_status}"
            )
            response.close()
