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
        response = self.client.get("/api/v1/twitch/campaigns/?status=active")

        assert response.status_code == 200
        assert "Content-Disposition" not in response
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["items"][0]["twitch_id"] == "campaign123"
        assert data["items"][0]["status"] == "active"
        assert data["items"][0]["game"]["twitch_id"] == "game123"

    def test_v1_campaign_list_filters_by_game(self) -> None:
        """Filter campaigns by game twitch_id."""
        response = self.client.get(
            f"/api/v1/twitch/campaigns/?game={self.game.twitch_id}",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["twitch_id"] == "campaign123"

    def test_v1_campaign_list_game_filter_with_no_matches(self) -> None:
        """Return empty list when game filter matches no campaigns."""
        response = self.client.get("/api/v1/twitch/campaigns/?game=nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_v1_campaign_list_status_upcoming(self) -> None:
        """Filter campaigns by status=upcoming."""
        now = timezone.now()
        DropCampaign.objects.create(
            twitch_id="upcoming_campaign",
            name="Upcoming Campaign",
            game=self.game,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=2),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        response = self.client.get("/api/v1/twitch/campaigns/?status=upcoming")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "upcoming"

    def test_v1_campaign_list_status_expired(self) -> None:
        """Filter campaigns by status=expired."""
        now = timezone.now()
        DropCampaign.objects.create(
            twitch_id="expired_campaign",
            name="Expired Campaign",
            game=self.game,
            start_at=now - timedelta(days=3),
            end_at=now - timedelta(days=1),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        response = self.client.get("/api/v1/twitch/campaigns/?status=expired")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "expired"

    def test_v1_campaign_list_default_page_size(self) -> None:
        """Return all campaigns when no page_size is specified."""
        response = self.client.get("/api/v1/twitch/campaigns/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert data["page"] == 1

    def test_v1_campaign_list_page_size_param(self) -> None:
        """Respect custom page_size parameter."""
        response = self.client.get("/api/v1/twitch/campaigns/?page_size=1")

        assert response.status_code == 200
        data = response.json()
        assert data["page_size"] == 1
        assert len(data["items"]) == 1

    def test_v1_campaign_list_summary_fields(self) -> None:
        """Return correct field shape for campaign summary items."""
        response = self.client.get("/api/v1/twitch/campaigns/")

        assert response.status_code == 200
        data = response.json()
        item = data["items"][0]
        assert item["twitch_id"] == "campaign123"
        assert item["name"] == "Test Campaign"
        assert item["description"] == "A test campaign"
        assert item["status"] == "active"
        assert item["image_url"] == "https://example.com/campaign.png"
        assert item["details_url"] == "https://example.com/details"
        assert item["account_link_url"] == "https://example.com/link"
        assert item["allow_is_enabled"] is True
        assert item["is_fully_imported"] is True
        assert "start_at" in item
        assert "end_at" in item
        assert "added_at" in item
        assert "updated_at" in item
        assert item["game"]["twitch_id"] == "game123"

    def test_v1_campaign_detail(self) -> None:
        """Return nested campaign detail data from the v1 endpoint."""
        response = self.client.get("/api/v1/twitch/campaigns/campaign123/")

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

        response = self.client.get("/api/v1/twitch/campaigns/campaign123/")

        assert response.status_code == 200
        data = response.json()
        assert data["game"]["box_art_url"] == self.game.box_art_file.url

    def test_v1_campaign_detail_avoids_n_plus_one_on_benefits(self) -> None:
        """Fetch campaign detail without N+1 queries on DropBenefit fields.

        Regression test: _serialize_benefit accesses created_at,
        entitlement_limit, and is_ios_available; these must be included
        in the Prefetch .only() in DropCampaign.for_detail_view to avoid
        one extra query per benefit.
        """
        with CaptureQueriesContext(connection) as capture:
            response = self.client.get("/api/v1/twitch/campaigns/campaign123/")

        assert response.status_code == 200
        data = response.json()
        benefits = data["drops"][0]["benefits"]
        assert len(benefits) == 1
        assert benefits[0]["created_at"] is None
        assert benefits[0]["entitlement_limit"] == 1
        assert benefits[0]["is_ios_available"] is False

        # Expect 7 queries (5 core prefetches + 2 campaign COUNTs):
        #   1. campaign + game (select_related)
        #   2. game owners (Prefetch → owners_for_detail)
        #   3. allow_channels (Prefetch → channels_ordered)
        #   4. time_based_drops (Prefetch)
        #   5. benefits through DropBenefitEdge (Prefetch)
        #   6. campaign count for game
        #   7. active campaign count for game
        assert len(capture) <= 7

    def test_v1_all_endpoints_handle_multiple_rows(self) -> None:
        """Exercise all v1 routes with enough rows to catch deferred loads."""
        self._create_secondary_api_fixture()
        list_urls: list[tuple[str, int]] = [
            ("/api/v1/twitch/campaigns/?page_size=50", 2),
            ("/api/v1/twitch/games/?page_size=50", 2),
            ("/api/v1/twitch/organizations/?page_size=50", 2),
            ("/api/v1/twitch/channels/?page_size=50", 2),
            ("/api/v1/twitch/reward-campaigns/?page_size=50", 2),
            ("/api/v1/twitch/badges/?page_size=50", 2),
        ]
        detail_urls = [
            "/api/v1/twitch/campaigns/campaign123/",
            "/api/v1/twitch/campaigns/campaign456/",
            "/api/v1/twitch/games/game123/",
            "/api/v1/twitch/games/game456/",
            "/api/v1/twitch/organizations/org123/",
            "/api/v1/twitch/organizations/org456/",
            "/api/v1/twitch/channels/channel123/",
            "/api/v1/twitch/channels/channel456/",
            "/api/v1/twitch/reward-campaigns/reward123/",
            "/api/v1/twitch/reward-campaigns/reward456/",
            "/api/v1/twitch/badges/test-badge-set/",
            "/api/v1/twitch/badges/second-badge-set/",
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

        schema_response = self.client.get(reverse("api-v1:openapi-json"))
        assert schema_response.status_code == 200
        assert schema_response.json()

        docs_response = self.client.get(reverse("api-v1:openapi-view"))
        assert docs_response.status_code == 200

    def test_v1_collection_endpoints(self) -> None:
        """Return v1 list responses for all Twitch API collections."""
        checks = [
            ("/api/v1/twitch/games/", "game123"),
            ("/api/v1/twitch/organizations/", "org123"),
            ("/api/v1/twitch/channels/", "channel123"),
            ("/api/v1/twitch/reward-campaigns/", "reward123"),
            ("/api/v1/twitch/badges/", "test-badge-set"),
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

        games_response = self.client.get("/api/v1/twitch/games/")
        games_data = games_response.json()
        assert games_data["items"][0]["campaign_count"] == 1
        assert games_data["items"][0]["active_campaign_count"] == 1

    def test_v1_organization_detail_includes_games_and_campaigns(self) -> None:
        """Return concrete game counts and detailed organization campaigns."""
        response = self.client.get("/api/v1/twitch/organizations/org123/")

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
            "/api/v1/twitch/games/game123/",
            "/api/v1/twitch/channels/channel123/",
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
        response = self.client.get("/api/v1/twitch/campaigns/missing/")

        assert response.status_code == 404

    def test_v1_docs_endpoint(self) -> None:
        """Render the combined TTVDrops API documentation page."""
        response = self.client.get("/api/v1/docs/")

        assert response.status_code == 200
        assert reverse("api-v1:openapi-json") in response.content.decode()

    def test_v1_docs_links_render_on_twitch_pages(self) -> None:
        """Expose API docs in nav and resource API links in feed link groups."""
        checks = [
            (
                reverse("core:docs_rss"),
                "API Docs",
                "/api/v1/docs/",
                reverse("api-v1:openapi-view"),
            ),
            (
                reverse("twitch:dashboard"),
                "API Docs",
                "[api]",
                reverse("api-v1:twitch-api-v1_list_campaigns"),
            ),
            (
                reverse("twitch:campaign_list"),
                "API Docs",
                "[api]",
                reverse("api-v1:twitch-api-v1_list_campaigns"),
            ),
            (
                reverse("twitch:campaign_detail", args=[self.campaign.twitch_id]),
                "API Docs",
                "[api]",
                reverse(
                    "api-v1:twitch-api-v1_get_campaign",
                    args=[self.campaign.twitch_id],
                ),
            ),
            (
                reverse("twitch:game_detail", args=[self.game.twitch_id]),
                "API Docs",
                "[api]",
                reverse("api-v1:twitch-api-v1_get_game", args=[self.game.twitch_id]),
            ),
            (
                reverse("twitch:games_grid"),
                "API Docs",
                "[api]",
                reverse("api-v1:twitch-api-v1_list_games"),
            ),
            (
                reverse("twitch:org_list"),
                "API Docs",
                "[api]",
                reverse("api-v1:twitch-api-v1_list_organizations"),
            ),
            (
                reverse("twitch:reward_campaign_list"),
                "API Docs",
                "[api]",
                reverse("api-v1:twitch-api-v1_list_reward_campaigns"),
            ),
            (
                reverse(
                    "twitch:reward_campaign_detail",
                    args=[self.reward_campaign.twitch_id],
                ),
                "API Docs",
                "[api]",
                reverse(
                    "api-v1:twitch-api-v1_get_reward_campaign",
                    args=[self.reward_campaign.twitch_id],
                ),
            ),
        ]

        for url, nav_text, feed_text, api_href in checks:
            response = self.client.get(url)
            assert response.status_code == 200
            content = response.content.decode()
            assert reverse("api-v1:openapi-view") in content
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
            "api-v1:twitch-api-v1_get_campaign",
            args=[self.campaign.twitch_id],
        )
        assert f'href="{campaign_api_url}"' in content
        assert 'title="Twitch campaign API">[api]</a>' in content

    # ── Games ──────────────────────────────────────────────────────────

    def test_v1_game_list_fields(self) -> None:
        """Return correct field shape for game list items."""
        response = self.client.get("/api/v1/twitch/games/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        item = data["items"][0]
        assert item["twitch_id"] == "game123"
        assert item["slug"] == "test-game"
        assert item["name"] == "Test Game"
        assert item["display_name"] == "Test Game"
        assert isinstance(item["box_art_url"], str)
        assert isinstance(item["organizations"], list)
        assert item["organizations"][0]["twitch_id"] == "org123"
        assert item["campaign_count"] >= 1
        assert item["active_campaign_count"] >= 1
        assert "added_at" in item
        assert "updated_at" in item

    def test_v1_game_list_pagination(self) -> None:
        """Paginate game list results."""
        response = self.client.get("/api/v1/twitch/games/?page_size=1")

        assert response.status_code == 200
        data = response.json()
        assert data["page_size"] == 1
        assert len(data["items"]) == 1

    def test_v1_game_detail_campaigns_and_owners(self) -> None:
        """Return campaigns and organizations in game detail."""
        response = self.client.get("/api/v1/twitch/games/game123/")

        assert response.status_code == 200
        data = response.json()
        assert data["campaigns"][0]["twitch_id"] == "campaign123"
        assert data["campaigns"][0]["status"] == "active"
        assert data["organizations"][0]["twitch_id"] == "org123"

    def test_v1_game_detail_not_found(self) -> None:
        """Return 404 for missing game."""
        response = self.client.get("/api/v1/twitch/games/nonexistent/")

        assert response.status_code == 404

    # ── Organizations ───────────────────────────────────────────────────

    def test_v1_organization_list_fields(self) -> None:
        """Return correct field shape for organization list items."""
        response = self.client.get("/api/v1/twitch/organizations/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        item = data["items"][0]
        assert item["twitch_id"] == "org123"
        assert item["name"] == "Test Organization"
        assert "added_at" in item
        assert "updated_at" in item

    def test_v1_organization_detail_not_found(self) -> None:
        """Return 404 for missing organization."""
        response = self.client.get("/api/v1/twitch/organizations/nonexistent/")

        assert response.status_code == 404

    # ── Channels ────────────────────────────────────────────────────────

    def test_v1_channel_list_search(self) -> None:
        """Filter channels by search query."""
        response = self.client.get("/api/v1/twitch/channels/?search=testchannel")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["twitch_id"] == "channel123"

    def test_v1_channel_list_search_no_match(self) -> None:
        """Return empty list when search matches no channels."""
        response = self.client.get("/api/v1/twitch/channels/?search=zzzzz")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_v1_channel_list_fields(self) -> None:
        """Return correct field shape for channel list items."""
        response = self.client.get("/api/v1/twitch/channels/")

        assert response.status_code == 200
        data = response.json()
        item = data["items"][0]
        assert item["twitch_id"] == "channel123"
        assert item["name"] == "testchannel"
        assert item["display_name"] == "TestChannel"
        assert item["allowed_campaign_count"] == 1
        assert "added_at" in item
        assert "updated_at" in item

    def test_v1_channel_detail_campaigns(self) -> None:
        """Return campaign summaries in channel detail."""
        response = self.client.get("/api/v1/twitch/channels/channel123/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["campaigns"]) == 1
        assert data["campaigns"][0]["twitch_id"] == "campaign123"
        assert data["campaigns"][0]["game"]["twitch_id"] == "game123"

    def test_v1_channel_detail_not_found(self) -> None:
        """Return 404 for missing channel."""
        response = self.client.get("/api/v1/twitch/channels/nonexistent/")

        assert response.status_code == 404

    # ── Reward Campaigns ────────────────────────────────────────────────

    def test_v1_reward_campaign_list_filters_by_game(self) -> None:
        """Filter reward campaigns by game twitch_id."""
        response = self.client.get(
            f"/api/v1/twitch/reward-campaigns/?game={self.game.twitch_id}",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["twitch_id"] == "reward123"

    def test_v1_reward_campaign_list_status_active(self) -> None:
        """Filter reward campaigns by status=active."""
        response = self.client.get("/api/v1/twitch/reward-campaigns/?status=active")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["computed_status"] == "active"

    def test_v1_reward_campaign_list_status_expired(self) -> None:
        """Filter reward campaigns by status=expired."""
        now = timezone.now()
        RewardCampaign.objects.create(
            twitch_id="expired_reward",
            name="Expired Reward",
            brand="Expired Brand",
            starts_at=now - timedelta(days=3),
            ends_at=now - timedelta(days=1),
            status="ACTIVE",
            summary="Expired summary",
        )

        response = self.client.get("/api/v1/twitch/reward-campaigns/?status=expired")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["computed_status"] == "expired"

    def test_v1_reward_campaign_list_fields(self) -> None:
        """Return correct field shape for reward campaign list items."""
        response = self.client.get("/api/v1/twitch/reward-campaigns/")

        assert response.status_code == 200
        data = response.json()
        item = data["items"][0]
        assert item["twitch_id"] == "reward123"
        assert item["name"] == "Test Reward"
        assert item["brand"] == "Test Brand"
        assert item["status"] == "ACTIVE"
        assert item["computed_status"] == "active"
        assert item["summary"] == "Reward summary"
        assert item["is_sitewide"] is False
        assert item["game"]["twitch_id"] == "game123"
        assert "starts_at" in item
        assert "ends_at" in item
        assert "added_at" in item
        assert "updated_at" in item

    def test_v1_reward_campaign_detail_fields(self) -> None:
        """Return correct field shape for reward campaign detail."""
        response = self.client.get("/api/v1/twitch/reward-campaigns/reward123/")

        assert response.status_code == 200
        data = response.json()
        assert data["twitch_id"] == "reward123"
        assert data["name"] == "Test Reward"
        assert data["brand"] == "Test Brand"
        assert isinstance(data["instructions"], str)
        assert isinstance(data["external_url"], str)
        assert isinstance(data["about_url"], str)
        assert data["game"]["twitch_id"] == "game123"

    def test_v1_reward_campaign_detail_no_game(self) -> None:
        """Return reward campaign detail with null game."""
        now = timezone.now()
        RewardCampaign.objects.create(
            twitch_id="no_game_reward",
            name="No Game Reward",
            brand="Standalone Brand",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
            status="ACTIVE",
            summary="No game attached",
            is_sitewide=True,
        )

        response = self.client.get("/api/v1/twitch/reward-campaigns/no_game_reward/")

        assert response.status_code == 200
        data = response.json()
        assert data["game"] is None
        assert data["is_sitewide"] is True

    def test_v1_reward_campaign_detail_not_found(self) -> None:
        """Return 404 for missing reward campaign."""
        response = self.client.get("/api/v1/twitch/reward-campaigns/nonexistent/")

        assert response.status_code == 404

    # ── Badges ──────────────────────────────────────────────────────────

    def test_v1_badge_list_fields(self) -> None:
        """Return correct field shape for badge set list items."""
        response = self.client.get("/api/v1/twitch/badges/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        item = data["items"][0]
        assert item["set_id"] == "test-badge-set"
        assert len(item["badges"]) == 1
        assert "added_at" in item
        assert "updated_at" in item

    def test_v1_badge_detail_fields(self) -> None:
        """Return correct field shape for badge set detail."""
        response = self.client.get("/api/v1/twitch/badges/test-badge-set/")

        assert response.status_code == 200
        data = response.json()
        assert data["set_id"] == "test-badge-set"
        assert len(data["badges"]) == 1
        badge = data["badges"][0]
        assert badge["badge_id"] == "1"
        assert badge["title"] == "Test Badge"
        assert badge["description"] == "Test badge description"
        assert "image_url_1x" in badge
        assert "image_url_2x" in badge
        assert "image_url_4x" in badge
        assert "click_action" in badge
        assert "click_url" in badge

    def test_v1_badge_detail_not_found(self) -> None:
        """Return 404 for missing badge set."""
        response = self.client.get("/api/v1/twitch/badges/nonexistent/")

        assert response.status_code == 404
