from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
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

    def test_embed_buttons_only_link_to_chzzk(self) -> None:
        """Chzzk embeds should not cross-link to Twitch or Kick."""
        for route_name in ("chzzk:dashboard", "chzzk:campaign_list"):
            response: _MonkeyPatchedWSGIResponse = self.client.get(
                reverse(route_name),
            )
            buttons: list[dict[str, str]] = response.context["discord_embed_buttons"]

            assert [button["label"] for button in buttons] == [
                "Chzzk Drops",
                "Campaigns",
            ]
            assert all(
                button["url"] == "https://chzzk.naver.com/"
                or button["url"].startswith(f"{settings.BASE_URL}/chzzk/")
                for button in buttons
            )

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


class ChzzkCategoryViewTests(TestCase):
    """Tests for Chzzk game and category detail pages."""

    def _make_campaign(
        self,
        campaign_no: int,
        *,
        category_type: str = "GAME",
        category_id: str = "dokkaebi",
        category_value: str = "Dokkaebi",
        title: str = "Category Campaign",
    ) -> ChzzkCampaign:
        now = timezone.now()
        return ChzzkCampaign.objects.create(
            campaign_no=campaign_no,
            title=title,
            description="Category campaign description",
            category_type=category_type,
            category_id=category_id,
            category_value=category_value,
            service_id="chzzk",
            state="ACTIVE",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
            source_api="unit-test",
        )

    def test_game_detail_lists_all_campaigns_for_category(self) -> None:
        """The game page should list every campaign in the category."""
        first = self._make_campaign(8101, title="First Category Campaign")
        second = self._make_campaign(8102, title="Second Category Campaign")
        self._make_campaign(
            8103,
            category_id="other-game",
            category_value="Other Game",
            title="Other Campaign",
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("chzzk:game_detail", args=[first.category_id]),
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert first.title in content
        assert second.title in content
        assert "Other Campaign" not in content
        assert (
            f'href="{reverse("chzzk:game_detail", args=[first.category_id])}"'
            not in content
        )

    def test_category_detail_lists_non_game_campaigns(self) -> None:
        """Non-game categories should render through the generic route."""
        campaign = self._make_campaign(
            8201,
            category_type="ETC",
            category_id="sci_tech",
            category_value="Science & Tech",
            title="Science Category Campaign",
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse(
                "chzzk:category_detail",
                args=[campaign.category_type, campaign.category_id],
            ),
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert campaign.title in content
        assert "Science &amp; Tech" in content
        assert "ETC" in content

    def test_unknown_category_returns_404(self) -> None:
        """Unknown categories should return 404."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("chzzk:game_detail", args=["does-not-exist"]),
        )

        assert response.status_code == 404


class ChzzkCampaignListFilterTests(TestCase):
    """Tests for Chzzk campaign list status tabs."""

    def _make_campaign(
        self,
        campaign_no: int,
        *,
        title: str,
        start_date: datetime,
        end_date: datetime,
        is_ios: bool = False,
    ) -> ChzzkCampaign:
        return ChzzkCampaign.objects.create(
            campaign_no=campaign_no,
            title=title,
            description="",
            category_type="GAME",
            category_id="test-game",
            category_value="Test Game",
            service_id="chzzk",
            state="ACTIVE",
            start_date=start_date,
            end_date=end_date,
            has_ios_based_reward=is_ios,
            drops_campaign_not_started=False,
            source_api="unit-test",
        )

    def test_status_tabs_filter_campaigns(self) -> None:
        """Status tabs should only show campaigns in the selected state."""
        now = timezone.now()
        active = self._make_campaign(
            9101,
            title="Active Filter Campaign",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )
        expired = self._make_campaign(
            9102,
            title="Expired Filter Campaign",
            start_date=now - timedelta(days=3),
            end_date=now - timedelta(days=2),
        )
        upcoming = self._make_campaign(
            9103,
            title="Upcoming Filter Campaign",
            start_date=now + timedelta(days=1),
            end_date=now + timedelta(days=2),
        )

        for status, included, excluded in (
            ("active", active, expired),
            ("expired", expired, upcoming),
            ("upcoming", upcoming, active),
        ):
            response: _MonkeyPatchedWSGIResponse = self.client.get(
                reverse("chzzk:campaign_list"),
                {"status": status},
            )
            content = response.content.decode()
            assert response.status_code == 200
            assert included.title in content
            assert excluded.title not in content
            assert 'class="nav-link active"' in content

    def test_ios_only_tab_filters_ios_rewards(self) -> None:
        """The iOS only tab should show only iOS-based campaigns."""
        now = timezone.now()
        ios_campaign = self._make_campaign(
            9201,
            title="iOS Campaign",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            is_ios=True,
        )
        other_campaign = self._make_campaign(
            9202,
            title="Non iOS Campaign",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("chzzk:campaign_list"),
            {"status": "ios"},
        )
        content = response.content.decode()

        assert response.status_code == 200
        assert ios_campaign.title in content
        assert other_campaign.title not in content
        assert "iOS only" in content
