from datetime import timedelta

from django.db import connection
from django.test import Client
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from twitch import api as twitch_api
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop


class TwitchApiV1TestCase(TestCase):
    """Tests for the versioned Twitch API."""

    def setUp(self) -> None:
        """Create representative Twitch API fixture data."""
        self.client = Client()
        now = timezone.now()

        self.org = Organization.objects.create(
            twitch_id="org123",
            name="Test Organization",
        )
        self.game = Game.objects.create(
            twitch_id="game123",
            slug="test-game",
            name="Test Game",
            display_name="Test Game",
            box_art="https://example.com/game.png",
        )
        self.game.owners.add(self.org)

        self.channel = Channel.objects.create(
            twitch_id="channel123",
            name="testchannel",
            display_name="TestChannel",
            allowed_campaign_count=1,
        )

        self.campaign = DropCampaign.objects.create(
            twitch_id="campaign123",
            name="Test Campaign",
            description="A test campaign",
            details_url="https://example.com/details",
            account_link_url="https://example.com/link",
            image_url="https://example.com/campaign.png",
            game=self.game,
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )
        self.campaign.allow_channels.add(self.channel)

        self.drop = TimeBasedDrop.objects.create(
            twitch_id="drop123",
            name="Test Drop",
            campaign=self.campaign,
            required_minutes_watched=30,
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
        )
        self.benefit = DropBenefit.objects.create(
            twitch_id="benefit123",
            name="Test Benefit",
            image_asset_url="https://example.com/benefit.png",
            distribution_type="ITEM",
        )
        self.drop.benefits.add(self.benefit)

        self.reward_campaign = RewardCampaign.objects.create(
            twitch_id="reward123",
            name="Test Reward",
            brand="Test Brand",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
            status="ACTIVE",
            summary="Reward summary",
            external_url="https://example.com/reward",
            game=self.game,
        )

        self.badge_set = ChatBadgeSet.objects.create(set_id="test-badge-set")
        ChatBadge.objects.create(
            badge_set=self.badge_set,
            badge_id="1",
            image_url_1x="https://example.com/badge-1x.png",
            image_url_2x="https://example.com/badge-2x.png",
            image_url_4x="https://example.com/badge-4x.png",
            title="Test Badge",
            description="Test badge description",
        )

    def _create_secondary_api_fixture(self) -> None:
        now = timezone.now()
        org = Organization.objects.create(
            twitch_id="org456",
            name="Second Organization",
        )
        game = Game.objects.create(
            twitch_id="game456",
            slug="second-game",
            name="Second Game",
            display_name="Second Game",
            box_art="https://example.com/second-game.png",
        )
        game.owners.add(org)

        channel = Channel.objects.create(
            twitch_id="channel456",
            name="secondchannel",
            display_name="SecondChannel",
            allowed_campaign_count=1,
        )

        campaign = DropCampaign.objects.create(
            twitch_id="campaign456",
            name="Second Campaign",
            description="Another test campaign",
            details_url="https://example.com/second-details",
            account_link_url="https://example.com/second-link",
            image_url="https://example.com/second-campaign.png",
            game=game,
            start_at=now - timedelta(days=2),
            end_at=now + timedelta(days=2),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )
        campaign.allow_channels.add(channel)

        drop = TimeBasedDrop.objects.create(
            twitch_id="drop456",
            name="Second Drop",
            campaign=campaign,
            required_minutes_watched=60,
            start_at=now - timedelta(days=2),
            end_at=now + timedelta(days=2),
        )
        benefit = DropBenefit.objects.create(
            twitch_id="benefit456",
            name="Second Benefit",
            image_asset_url="https://example.com/second-benefit.png",
            distribution_type="ITEM",
        )
        drop.benefits.add(benefit)

        RewardCampaign.objects.create(
            twitch_id="reward456",
            name="Second Reward",
            brand="Second Brand",
            starts_at=now - timedelta(days=2),
            ends_at=now + timedelta(days=2),
            status="ACTIVE",
            summary="Second reward summary",
            external_url="https://example.com/second-reward",
            game=game,
        )

        badge_set = ChatBadgeSet.objects.create(set_id="second-badge-set")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/second-badge-1x.png",
            image_url_2x="https://example.com/second-badge-2x.png",
            image_url_4x="https://example.com/second-badge-4x.png",
            title="Second Badge",
            description="Second badge description",
        )

    def test_v1_campaign_list(self) -> None:
        """Return active campaigns from the v1 list endpoint."""
        response = self.client.get("/twitch/api/v1/campaigns/?status=active")

        assert response.status_code == 200
        assert "Content-Disposition" not in response
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["items"][0]["twitch_id"] == "campaign123"
        assert data["items"][0]["status"] == "active"
        assert data["items"][0]["game"]["twitch_id"] == "game123"

    def test_v1_campaign_detail(self) -> None:
        """Return nested campaign detail data from the v1 endpoint."""
        response = self.client.get("/twitch/api/v1/campaigns/campaign123/")

        assert response.status_code == 200
        data = response.json()
        assert data["operation_names"] == ["DropCampaignDetails"]
        assert data["game"]["box_art_url"] == "https://example.com/game.png"
        assert data["allowed_channels"][0]["twitch_id"] == "channel123"
        assert data["drops"][0]["benefits"][0]["twitch_id"] == "benefit123"

    def test_v1_campaign_detail_game_box_art_does_not_load_deferred_file(self) -> None:
        """Serialize campaign game box art without lazy-loading ImageField data."""
        campaign = DropCampaign.for_detail_view("campaign123")

        image_fields = {"box_art_file", "box_art_width", "box_art_height"}
        assert campaign.game.get_deferred_fields().isdisjoint(image_fields)
        with CaptureQueriesContext(connection) as capture:
            box_art_url = twitch_api._game_box_art_url(campaign.game)

        assert box_art_url == "https://example.com/game.png"
        assert len(capture) == 0

    def test_v1_campaign_detail_uses_local_game_box_art(self) -> None:
        """Return locally cached game box art from campaign detail responses."""
        self.game.box_art_file = "games/box_art/local.png"
        self.game.box_art_width = 285
        self.game.box_art_height = 380
        self.game.save(
            update_fields=["box_art_file", "box_art_width", "box_art_height"],
        )

        response = self.client.get("/twitch/api/v1/campaigns/campaign123/")

        assert response.status_code == 200
        data = response.json()
        assert data["game"]["box_art_url"] == self.game.box_art_file.url

    def test_v1_all_endpoints_handle_multiple_rows(self) -> None:
        """Exercise all v1 routes with enough rows to catch deferred loads."""
        self._create_secondary_api_fixture()
        list_urls: list[tuple[str, int]] = [
            ("/twitch/api/v1/campaigns/?page_size=50", 2),
            ("/twitch/api/v1/games/?page_size=50", 2),
            ("/twitch/api/v1/organizations/?page_size=50", 2),
            ("/twitch/api/v1/channels/?page_size=50", 2),
            ("/twitch/api/v1/reward-campaigns/?page_size=50", 2),
            ("/twitch/api/v1/badges/?page_size=50", 2),
        ]
        detail_urls = [
            "/twitch/api/v1/campaigns/campaign123/",
            "/twitch/api/v1/campaigns/campaign456/",
            "/twitch/api/v1/games/game123/",
            "/twitch/api/v1/games/game456/",
            "/twitch/api/v1/organizations/org123/",
            "/twitch/api/v1/organizations/org456/",
            "/twitch/api/v1/channels/channel123/",
            "/twitch/api/v1/channels/channel456/",
            "/twitch/api/v1/reward-campaigns/reward123/",
            "/twitch/api/v1/reward-campaigns/reward456/",
            "/twitch/api/v1/badges/test-badge-set/",
            "/twitch/api/v1/badges/second-badge-set/",
        ]

        for url, expected_total in list_urls:
            response = self.client.get(url)
            assert response.status_code == 200, url
            data = response.json()
            assert data["total"] == expected_total
            assert len(data["items"]) == expected_total

        for url in detail_urls:
            response = self.client.get(url)
            assert response.status_code == 200, url
            assert response.json()

        schema_response = self.client.get(reverse("twitch:twitch-api-v1:openapi-json"))
        assert schema_response.status_code == 200
        assert schema_response.json()

        docs_response = self.client.get(reverse("twitch:twitch-api-v1:openapi-view"))
        assert docs_response.status_code == 200

    def test_v1_collection_endpoints(self) -> None:
        """Return v1 list responses for all Twitch API collections."""
        checks = [
            ("/twitch/api/v1/games/", "game123"),
            ("/twitch/api/v1/organizations/", "org123"),
            ("/twitch/api/v1/channels/", "channel123"),
            ("/twitch/api/v1/reward-campaigns/", "reward123"),
            ("/twitch/api/v1/badges/", "test-badge-set"),
        ]

        for url, expected_id in checks:
            response = self.client.get(url)
            assert response.status_code == 200
            data = response.json()
            actual_id = data["items"][0].get(
                "twitch_id",
                data["items"][0].get("set_id"),
            )
            assert actual_id == expected_id

        games_response = self.client.get("/twitch/api/v1/games/")
        games_data = games_response.json()
        assert games_data["items"][0]["campaign_count"] == 1
        assert games_data["items"][0]["active_campaign_count"] == 1

    def test_v1_organization_detail_includes_games_and_campaigns(self) -> None:
        """Return concrete game counts and detailed organization campaigns."""
        response = self.client.get("/twitch/api/v1/organizations/org123/")

        assert response.status_code == 200
        data = response.json()
        assert data["games"][0]["twitch_id"] == "game123"
        assert data["games"][0]["campaign_count"] == 1
        assert data["games"][0]["active_campaign_count"] == 1
        assert data["campaigns"][0]["twitch_id"] == "campaign123"
        assert data["campaigns"][0]["operation_names"] == ["DropCampaignDetails"]
        assert data["campaigns"][0]["allowed_channels"][0]["twitch_id"] == "channel123"
        assert data["campaigns"][0]["drops"][0]["twitch_id"] == "drop123"
        assert (
            data["campaigns"][0]["drops"][0]["benefits"][0]["twitch_id"] == "benefit123"
        )

    def test_v1_game_and_channel_detail_include_campaign_data(self) -> None:
        """Return campaign API fields on game and channel detail responses."""
        checks = [
            "/twitch/api/v1/games/game123/",
            "/twitch/api/v1/channels/channel123/",
        ]

        for url in checks:
            response = self.client.get(url)
            assert response.status_code == 200
            data = response.json()
            campaign = data["campaigns"][0]
            assert campaign["description"] == "A test campaign"
            assert campaign["details_url"] == "https://example.com/details"
            assert campaign["account_link_url"] == "https://example.com/link"
            assert campaign["image_url"] == "https://example.com/campaign.png"

    def test_v1_detail_not_found(self) -> None:
        """Return 404 for missing v1 campaign detail records."""
        response = self.client.get("/twitch/api/v1/campaigns/missing/")

        assert response.status_code == 404

    def test_v1_docs_endpoint(self) -> None:
        """Render the versioned Twitch API documentation page."""
        response = self.client.get("/twitch/api/v1/docs")

        assert response.status_code == 200
        assert reverse("twitch:twitch-api-v1:openapi-json") in response.content.decode()

    def test_v1_docs_links_render_on_twitch_pages(self) -> None:
        """Expose API docs in nav and resource API links in feed link groups."""
        checks = [
            (
                reverse("core:docs_rss"),
                "API Docs",
                "/twitch/api/v1/docs",
                reverse("twitch:twitch-api-v1:openapi-view"),
            ),
            (
                reverse("twitch:dashboard"),
                "API Docs",
                "[api]",
                reverse("twitch:twitch-api-v1:list_campaigns"),
            ),
            (
                reverse("twitch:campaign_list"),
                "API Docs",
                "[api]",
                reverse("twitch:twitch-api-v1:list_campaigns"),
            ),
            (
                reverse("twitch:campaign_detail", args=[self.campaign.twitch_id]),
                "API Docs",
                "[api]",
                reverse(
                    "twitch:twitch-api-v1:get_campaign",
                    args=[self.campaign.twitch_id],
                ),
            ),
            (
                reverse("twitch:game_detail", args=[self.game.twitch_id]),
                "API Docs",
                "[api]",
                reverse("twitch:twitch-api-v1:get_game", args=[self.game.twitch_id]),
            ),
            (
                reverse("twitch:games_grid"),
                "API Docs",
                "[api]",
                reverse("twitch:twitch-api-v1:list_games"),
            ),
            (
                reverse("twitch:org_list"),
                "API Docs",
                "[api]",
                reverse("twitch:twitch-api-v1:list_organizations"),
            ),
            (
                reverse("twitch:reward_campaign_list"),
                "API Docs",
                "[api]",
                reverse("twitch:twitch-api-v1:list_reward_campaigns"),
            ),
            (
                reverse(
                    "twitch:reward_campaign_detail",
                    args=[self.reward_campaign.twitch_id],
                ),
                "API Docs",
                "[api]",
                reverse(
                    "twitch:twitch-api-v1:get_reward_campaign",
                    args=[self.reward_campaign.twitch_id],
                ),
            ),
        ]

        for url, nav_text, feed_text, api_href in checks:
            response = self.client.get(url)
            assert response.status_code == 200
            content = response.content.decode()
            assert reverse("twitch:twitch-api-v1:openapi-view") in content
            assert api_href in content
            assert nav_text in content
            assert feed_text in content

    def test_campaign_detail_api_link_targets_campaign_endpoint(self) -> None:
        """Link campaign detail [api] directly to that campaign JSON endpoint."""
        response = self.client.get(
            reverse("twitch:campaign_detail", args=[self.campaign.twitch_id]),
        )

        assert response.status_code == 200
        content = response.content.decode()
        campaign_api_url = reverse(
            "twitch:twitch-api-v1:get_campaign",
            args=[self.campaign.twitch_id],
        )
        assert f'href="{campaign_api_url}"' in content
        assert 'title="Twitch campaign API">[api]</a>' in content
