from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from chzzk.models import ChzzkCampaign

if TYPE_CHECKING:
    from datetime import datetime

    from django.test.client import _MonkeyPatchedWSGIResponse


class ChzzkDashboardViewTests(TestCase):
    """Test cases for the dashboard view of the chzzk app."""

    def test_dashboard_view_no_n_plus_one_on_rewards(self) -> None:
        """Test that the dashboard view does not trigger an N+1 query for rewards."""
        now = timezone.now()
        base_kwargs = {
            "category_type": "game",
            "category_id": "1",
            "category_value": "TestGame",
            "service_id": "chzzk",
            "state": "ACTIVE",
            "start_date": now - timedelta(days=1),
            "end_date": now + timedelta(days=1),
            "has_ios_based_reward": False,
            "drops_campaign_not_started": False,
            "source_api": "unit-test",
        }
        reward_kwargs = {
            "reward_type": "ITEM",
            "campaign_reward_type": "Standard",
            "condition_type": "watch",
            "ios_based_reward": False,
            "code_remaining_count": 100,
        }

        campaign1 = ChzzkCampaign.objects.create(
            campaign_no=9001,
            title="C1",
            **base_kwargs,
        )
        campaign1.rewards.create(  # pyright: ignore[reportAttributeAccessIssue]
            reward_no=901,
            title="R1",
            condition_for_minutes=10,
            **reward_kwargs,
        )  # pyright: ignore[reportAttributeAccessIssue]
        campaign2 = ChzzkCampaign.objects.create(
            campaign_no=9002,
            title="C2",
            **base_kwargs,
        )
        campaign2.rewards.create(  # pyright: ignore[reportAttributeAccessIssue]
            reward_no=902,
            title="R2",
            condition_for_minutes=20,
            **reward_kwargs,
        )  # pyright: ignore[reportAttributeAccessIssue]

        with CaptureQueriesContext(connection) as one_campaign_ctx:
            self.client.get(reverse("chzzk:dashboard"))
        query_count_two = len(one_campaign_ctx)

        campaign3 = ChzzkCampaign.objects.create(
            campaign_no=9003,
            title="C3",
            **base_kwargs,
        )
        campaign3.rewards.create(  # pyright: ignore[reportAttributeAccessIssue]
            reward_no=903,
            title="R3",
            condition_for_minutes=30,
            **reward_kwargs,
        )  # pyright: ignore[reportAttributeAccessIssue]

        with CaptureQueriesContext(connection) as three_campaign_ctx:
            self.client.get(reverse("chzzk:dashboard"))
        query_count_three = len(three_campaign_ctx)

        # With prefetch_related, adding more campaigns should not add extra queries per campaign.
        assert query_count_two == query_count_three

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

    def test_campaign_feed_only_includes_campaigns_with_raw_json(self) -> None:
        """Test that the RSS feed only includes campaigns with raw JSON data."""
        now: datetime = timezone.now()

        excluded: ChzzkCampaign = ChzzkCampaign.objects.create(
            campaign_no=3001,
            title="No JSON campaign",
            description="Excluded because no raw JSON",
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

        included_v1: ChzzkCampaign = ChzzkCampaign.objects.create(
            campaign_no=3002,
            title="JSON v1 campaign",
            description="Included because raw_json_v1 is present",
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
            raw_json_v1={"foo": "bar"},
        )

        included_v2: ChzzkCampaign = ChzzkCampaign.objects.create(
            campaign_no=3003,
            title="JSON v2 campaign",
            description="Included because raw_json_v2 is present",
            category_type="game",
            category_id="1",
            category_value="TestGame",
            service_id="chzzk",
            state="ACTIVE",
            start_date=now - timedelta(days=2),
            end_date=now + timedelta(days=2),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
            raw_json_v2={"foo": "bar"},
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("chzzk:campaign_feed"),
        )
        assert response.status_code == 200

        content: str = response.content.decode()
        assert included_v1.title in content
        assert included_v2.title in content
        assert excluded.title not in content
