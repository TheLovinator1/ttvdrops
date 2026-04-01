from datetime import timedelta
from typing import TYPE_CHECKING

from django.test import TestCase
from django.test.client import _MonkeyPatchedWSGIResponse
from django.urls import reverse
from django.utils import timezone

from chzzk.models import ChzzkCampaign

if TYPE_CHECKING:
    from datetime import datetime

    from django.test.client import _MonkeyPatchedWSGIResponse


class ChzzkDashboardViewTests(TestCase):
    """Test cases for the dashboard view of the chzzk app."""

    def test_dashboard_view_excludes_testing_state_campaigns(self) -> None:
        """Test that the dashboard view excludes campaigns in the TESTING state."""
        now: datetime = timezone.now()

        ChzzkCampaign.objects.create(
            campaign_no=1001,
            title="Testing campaign",
            description="Should be excluded",
            category_type="game",
            category_id="1",
            category_value="TestGame",
            service_id="chzzk",
            state="TESTING",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
        )

        included: ChzzkCampaign = ChzzkCampaign.objects.create(
            campaign_no=1002,
            title="Active campaign",
            description="Should be included",
            category_type="game",
            category_id="1",
            category_value="TestGame",
            service_id="chzzk",
            state="ACTIVE",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("chzzk:dashboard"),
        )
        assert response.status_code == 200
        campaigns: list[ChzzkCampaign] = list(response.context["active_campaigns"])

        assert included in campaigns
        assert all(c.state != "TESTING" for c in campaigns)

    def test_campaign_detail_view_renders_chzzk_campaign_and_rewards(self) -> None:
        """Test that the campaign detail view correctly renders the details of a chzzk campaign and its rewards."""
        now: datetime = timezone.now()

        campaign: ChzzkCampaign = ChzzkCampaign.objects.create(
            campaign_no=2001,
            title="Campaign Detail Test",
            description="Detailed campaign description",
            category_type="game",
            category_id="1",
            category_value="TestGame",
            service_id="chzzk",
            state="ACTIVE",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
        )

        campaign.rewards.create(  # pyright: ignore[reportAttributeAccessIssue]
            reward_no=10,
            title="Reward A",
            reward_type="ITEM",
            campaign_reward_type="Standard",
            condition_type="watch",
            condition_for_minutes=15,
            ios_based_reward=False,
            code_remaining_count=100,
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("chzzk:campaign_detail", args=[campaign.campaign_no]),
        )
        assert response.status_code == 200

        content: str = response.content.decode()
        assert campaign.title in content
        assert "Reward A" in content
        assert "watch" in content
        assert "100" in content
        assert "No" in content  # ios_based_reward=False
