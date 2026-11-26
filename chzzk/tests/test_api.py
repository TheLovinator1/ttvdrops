from __future__ import annotations

from datetime import UTC
from datetime import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward


class ChzzkImageFilenameTest(TestCase):
    """Tests for decoded image filenames."""

    def test_image_filename_decodes_percent_encoded_url(self) -> None:
        """Percent-encoded image names should be readable in the UI and API."""
        image_url = (
            "https://example.com/%E9%B8%A3%E6%BD%AEicon"
            "%E5%9C%86%E8%A7%92-%E6%97%A0%E6%96%87%E5%AD%97.png"
        )
        campaign = ChzzkCampaign(
            campaign_no=1,
            title="Encoded campaign",
            image_url=image_url,
        )
        reward = ChzzkReward(
            reward_no=1,
            title="Encoded reward",
            image_url=image_url,
        )

        expected = "鸣潮icon圆角-无文字.png"
        assert campaign.image_filename == expected
        assert reward.image_filename == expected

    def test_chzzk_api_url_points_at_the_source_campaign(self) -> None:
        """Campaigns should link to their raw Chzzk API response."""
        campaign = ChzzkCampaign(campaign_no=2053, title="API URL campaign")

        assert (
            campaign.chzzk_api_url
            == "https://api.chzzk.naver.com/service/v2/drops/campaigns/2053"
        )

    def test_category_url_uses_game_or_category_route(self) -> None:
        """GAME categories use the game route; others use the generic route."""
        game_campaign = ChzzkCampaign(
            category_type="GAME",
            category_id="dokkaebi",
        )
        other_campaign = ChzzkCampaign(
            category_type="ETC",
            category_id="sci_tech",
        )

        assert game_campaign.category_url == "/chzzk/games/dokkaebi/"
        assert other_campaign.category_url == "/chzzk/categories/ETC/sci_tech/"

    def test_category_display_includes_id_when_it_differs(self) -> None:
        """Category labels should include the machine ID for readability."""
        campaign = ChzzkCampaign(
            category_id="League_of_Legends",
            category_value="리그 오브 레전드",
        )

        assert campaign.category_display == "리그 오브 레전드 (League of Legends)"


class ChzzkCampaignApiTest(TestCase):
    """Tests for the public Chzzk campaign JSON API."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Create representative Chzzk campaign data for API tests."""
        cls.campaign = ChzzkCampaign.objects.create(
            campaign_no=5001,
            title="API Chzzk Campaign",
            image_url="https://example.com/chzzk-campaign.png",
            description="API campaign description",
            category_type="GAME",
            category_id="category-api",
            category_value="API Game",
            pc_link_url="https://example.com/campaign",
            mobile_link_url="https://example.com/mobile",
            service_id="chzzk",
            state="ACTIVE",
            start_date=datetime(2026, 7, 1, tzinfo=UTC),
            end_date=datetime(2026, 8, 1, tzinfo=UTC),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            campaign_reward_type="STANDARD",
            reward_type="ITEM",
            account_link_url="https://example.com/connect",
            source_api="unit-test",
        )
        cls.reward = ChzzkReward.objects.create(
            campaign=cls.campaign,
            reward_no=7001,
            image_url="https://example.com/chzzk-reward.png",
            title="API Reward",
            reward_type="ITEM",
            campaign_reward_type="STANDARD",
            condition_type="WATCH",
            condition_for_minutes=30,
            ios_based_reward=False,
            code_remaining_count=10,
        )
        ChzzkCampaign.objects.create(
            campaign_no=5002,
            title="Testing Campaign",
            description="",
            category_type="GAME",
            category_id="category-test",
            category_value="Test Game",
            service_id="chzzk",
            state="TESTING",
            start_date=datetime(2026, 7, 1, tzinfo=UTC),
            end_date=datetime(2026, 8, 1, tzinfo=UTC),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
        )

    def test_stats_excludes_testing_campaigns(self) -> None:
        """Stats should include only non-testing campaigns and rewards."""
        with mock.patch(
            "chzzk.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            response = self.client.get(reverse("api-v1:chzzk-api-v1_stats"))

        assert response.status_code == 200
        assert response.json() == {
            "total_campaigns": 1,
            "active": 1,
            "upcoming": 0,
            "expired": 0,
            "total_rewards": 1,
        }

    def test_list_returns_campaign_summary(self) -> None:
        """A campaign list item should contain the expected summary fields."""
        with mock.patch(
            "chzzk.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            response = self.client.get(
                reverse("api-v1:chzzk-api-v1_list_campaigns"),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 100

        item = data["items"][0]
        assert item["campaign_no"] == 5001
        assert item["title"] == "API Chzzk Campaign"
        assert item["status"] == "active"
        assert item["image_url"] == "https://example.com/chzzk-campaign.png"
        assert item["image_filename"] == "chzzk-campaign.png"
        assert item["category_type"] == "GAME"
        assert item["category_id"] == "category-api"
        assert item["category_value"] == "API Game"
        assert item["reward_type"] == "ITEM"
        assert item["starts_at"] == "2026-07-01T00:00:00Z"
        assert item["ends_at"] == "2026-08-01T00:00:00Z"
        assert item["reward_count"] == 1

    def test_filters_campaigns_by_status_category_and_search(self) -> None:
        """Status, category, and search filters should narrow the results."""
        with mock.patch(
            "chzzk.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            status_response = self.client.get(
                reverse("api-v1:chzzk-api-v1_list_campaigns"),
                {"status": "active"},
            )
            category_response = self.client.get(
                reverse("api-v1:chzzk-api-v1_list_campaigns"),
                {"category_id": self.campaign.category_id},
            )
            search_response = self.client.get(
                reverse("api-v1:chzzk-api-v1_list_campaigns"),
                {"search": "API Chzzk"},
            )
            no_match_response = self.client.get(
                reverse("api-v1:chzzk-api-v1_list_campaigns"),
                {"search": "does-not-exist"},
            )

        assert [item["campaign_no"] for item in status_response.json()["items"]] == [
            5001,
        ]
        assert [item["campaign_no"] for item in category_response.json()["items"]] == [
            5001,
        ]
        assert [item["campaign_no"] for item in search_response.json()["items"]] == [
            5001,
        ]
        assert no_match_response.json()["items"] == []

    def test_detail_includes_rewards(self) -> None:
        """Campaign detail should include its rewards."""
        response = self.client.get(
            reverse("api-v1:chzzk-api-v1_get_campaign", args=[5001]),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["campaign_no"] == 5001
        assert data["image_filename"] == "chzzk-campaign.png"
        assert data["description"] == "API campaign description"
        assert data["pc_link_url"] == "https://example.com/campaign"
        assert data["account_link_url"] == "https://example.com/connect"
        assert data["campaign_reward_type"] == "STANDARD"
        assert data["rewards"] == [
            {
                "reward_no": 7001,
                "title": "API Reward",
                "image_url": "https://example.com/chzzk-reward.png",
                "image_filename": "chzzk-reward.png",
                "reward_type": "ITEM",
                "campaign_reward_type": "STANDARD",
                "condition_type": "WATCH",
                "condition_for_minutes": 30,
                "ios_based_reward": False,
                "code_remaining_count": 10,
            },
        ]

    def test_detail_returns_404_for_unknown_campaign(self) -> None:
        """Unknown campaign numbers should return 404."""
        response = self.client.get(
            reverse("api-v1:chzzk-api-v1_get_campaign", args=[999999]),
        )

        assert response.status_code == 404
