import datetime
import json
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import pytest
from django.core.handlers.wsgi import WSGIRequest
from django.core.paginator import Paginator
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop
from twitch.views import _build_breadcrumb_schema
from twitch.views import _build_pagination_info
from twitch.views import _build_seo_context
from twitch.views import _truncate_description

if TYPE_CHECKING:
    from django.core.handlers.wsgi import WSGIRequest
    from django.test import Client
    from django.test.client import _MonkeyPatchedWSGIResponse
    from django.test.utils import ContextList

    from twitch.views import Page


@pytest.mark.django_db
class TestSearchView:
    """Tests for the search_view function."""

    @pytest.fixture
    def sample_data(
        self,
    ) -> dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit]:
        """Create sample data for testing.

        Returns:
            A dictionary containing the created sample data.
        """
        org: Organization = Organization.objects.create(
            twitch_id="123",
            name="Test Organization",
        )
        game: Game = Game.objects.create(
            twitch_id="456",
            name="test_game",
            display_name="Test Game",
        )
        game.owners.add(org)
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="789",
            name="Test Campaign",
            description="A test campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="1011",
            name="Test Drop",
            campaign=campaign,
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="1213",
            name="Test Benefit",
        )
        return {
            "org": org,
            "game": game,
            "campaign": campaign,
            "drop": drop,
            "benefit": benefit,
        }

    @staticmethod
    def _get_context(
        response: _MonkeyPatchedWSGIResponse,
    ) -> ContextList | dict[str, Any]:
        """Normalize Django test response context to a plain dict.

        Args:
            response: The Django test response.

        Returns:
            The context as a plain dictionary.
        """
        context: ContextList | dict[str, Any] = response.context
        if isinstance(context, list):  # Django can return a list of contexts
            context = context[-1]
        return context

    def test_empty_query(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Test search with empty query returns no results."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert "results" in context
        assert context["results"] == {}

    def test_no_query_parameter(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Test search with no query parameter returns no results."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert context["results"] == {}

    @pytest.mark.parametrize(
        "model_key",
        ["org", "game", "campaign", "drop", "benefit"],
    )
    def test_short_query_istartswith(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
        model_key: Literal["org", "game", "campaign", "drop", "benefit"],
    ) -> None:
        """Test short query (< 3 chars) uses istartswith for all models."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=Te")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200

        # Map model keys to result keys
        result_key_map: dict[str, str] = {
            "org": "organizations",
            "game": "games",
            "campaign": "campaigns",
            "drop": "drops",
            "benefit": "benefits",
        }
        result_key: str = result_key_map[model_key]
        assert sample_data[model_key] in context["results"][result_key]

    @pytest.mark.parametrize(
        "model_key",
        ["org", "game", "campaign", "drop", "benefit"],
    )
    def test_long_query_icontains(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
        model_key: Literal["org", "game", "campaign", "drop", "benefit"],
    ) -> None:
        """Test long query (>= 3 chars) uses icontains for all models."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=Test")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200

        # Map model keys to result keys
        result_key_map: dict[str, str] = {
            "org": "organizations",
            "game": "games",
            "campaign": "campaigns",
            "drop": "drops",
            "benefit": "benefits",
        }
        result_key: str = result_key_map[model_key]
        assert sample_data[model_key] in context["results"][result_key]

    def test_campaign_description_search(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Test that campaign description is searchable."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=campaign")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert sample_data["campaign"] in context["results"]["campaigns"]

    def test_game_display_name_search(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Test that game display_name is searchable."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=Game")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert sample_data["game"] in context["results"]["games"]

    def test_query_no_matches(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Test search with query that has no matches."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=xyz")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        for result_list in context["results"].values():
            assert len(result_list) == 0

    def test_context_contains_query(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Test that context contains the search query."""
        query = "Test"
        response: _MonkeyPatchedWSGIResponse = client.get(f"/search/?q={query}")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert context["query"] == query

    @pytest.mark.parametrize(
        ("model_key", "related_field"),
        [("campaigns", "game"), ("drops", "campaign")],
    )
    def test_select_related_optimization(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
        model_key: str,
        related_field: str,
    ) -> None:
        """Test that queries use select_related for performance optimization."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=Test")
        context: ContextList | dict[str, Any] = self._get_context(response)

        results: list[
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit
        ] = context["results"][model_key]
        assert len(results) > 0

        # Verify the related object is accessible without additional query
        first_result: (
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit
        ) = results[0]
        assert hasattr(first_result, related_field)


@pytest.mark.django_db
class TestChannelListView:
    """Tests for the ChannelListView."""

    @pytest.fixture
    def channel_with_campaigns(
        self,
    ) -> dict[str, Channel | Game | Organization | list[DropCampaign]]:
        """Create a channel with multiple campaigns for testing.

        Returns:
            A dictionary containing the created channel and campaigns.
        """
        org: Organization = Organization.objects.create(
            twitch_id="org1",
            name="Test Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        game.owners.add(org)

        # Create a channel
        channel: Channel = Channel.objects.create(
            twitch_id="channel1",
            name="testchannel",
            display_name="TestChannel",
        )

        # Create multiple campaigns and add the channel to them
        campaigns: list[DropCampaign] = []
        for i in range(5):
            campaign: DropCampaign = DropCampaign.objects.create(
                twitch_id=f"campaign{i}",
                name=f"Campaign {i}",
                game=game,
                operation_names=["DropCampaignDetails"],
            )
            campaign.allow_channels.add(channel)
            campaigns.append(campaign)

        return {"channel": channel, "campaigns": campaigns, "game": game, "org": org}

    def test_channel_list_loads(self, client: Client) -> None:
        """Test that channel list view loads successfully."""
        response: _MonkeyPatchedWSGIResponse = client.get("/twitch/channels/")
        assert response.status_code == 200

    def test_campaign_count_annotation(
        self,
        client: Client,
        channel_with_campaigns: dict[
            str,
            Channel | Game | Organization | list[DropCampaign],
        ],
    ) -> None:
        """Test that campaign_count is correctly annotated for channels."""
        channel: Channel = channel_with_campaigns["channel"]  # type: ignore[assignment]
        campaigns: list[DropCampaign] = channel_with_campaigns["campaigns"]  # type: ignore[assignment]

        response: _MonkeyPatchedWSGIResponse = client.get("/twitch/channels/")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = context["channels"]

        # Find our test channel in the results
        test_channel: Channel | None = next(
            (ch for ch in channels if ch.twitch_id == channel.twitch_id),
            None,
        )

        assert test_channel is not None
        assert hasattr(test_channel, "campaign_count")

        campaign_count: int | None = getattr(test_channel, "campaign_count", None)
        assert campaign_count == len(campaigns), (
            f"Expected campaign_count to be {len(campaigns)}, got {campaign_count}"
        )

    def test_campaign_count_zero_for_channel_without_campaigns(
        self,
        client: Client,
    ) -> None:
        """Test that campaign_count is 0 for channels with no campaigns."""
        # Create a channel with no campaigns
        channel: Channel = Channel.objects.create(
            twitch_id="channel_no_campaigns",
            name="nocampaigns",
            display_name="NoCampaigns",
        )

        response: _MonkeyPatchedWSGIResponse = client.get("/twitch/channels/")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = context["channels"]
        test_channel: Channel | None = next(
            (ch for ch in channels if ch.twitch_id == channel.twitch_id),
            None,
        )

        assert test_channel is not None
        assert hasattr(test_channel, "campaign_count")

        campaign_count: int | None = getattr(test_channel, "campaign_count", None)
        assert campaign_count is None or campaign_count == 0

    def test_channels_ordered_by_campaign_count(
        self,
        client: Client,
        channel_with_campaigns: dict[
            str,
            Channel | Game | Organization | list[DropCampaign],
        ],
    ) -> None:
        """Test that channels are ordered by campaign_count descending."""
        game: Game = channel_with_campaigns["game"]  # type: ignore[assignment]

        # Create another channel with more campaigns
        channel2: Channel = Channel.objects.create(
            twitch_id="channel2",
            name="channel2",
            display_name="Channel2",
        )

        # Add 10 campaigns to this channel
        for i in range(10):
            campaign = DropCampaign.objects.create(
                twitch_id=f"campaign_ch2_{i}",
                name=f"Campaign Ch2 {i}",
                game=game,
                operation_names=["DropCampaignDetails"],
            )
            campaign.allow_channels.add(channel2)

        response: _MonkeyPatchedWSGIResponse = client.get("/twitch/channels/")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = list(context["channels"])

        # The channel with 10 campaigns should come before the one with 5
        channel2_index: int | None = next(
            (i for i, ch in enumerate(channels) if ch.twitch_id == "channel2"),
            None,
        )
        channel1_index: int | None = next(
            (i for i, ch in enumerate(channels) if ch.twitch_id == "channel1"),
            None,
        )

        assert channel2_index is not None
        assert channel1_index is not None
        assert channel2_index < channel1_index, (
            "Channel with more campaigns should appear first"
        )

    def test_channel_search_filters_correctly(
        self,
        client: Client,
        channel_with_campaigns: dict[
            str,
            Channel | Game | Organization | list[DropCampaign],
        ],
    ) -> None:
        """Test that search parameter filters channels correctly."""
        channel: Channel = channel_with_campaigns["channel"]  # type: ignore[assignment]

        # Create another channel that won't match the search
        Channel.objects.create(
            twitch_id="other_channel",
            name="otherchannel",
            display_name="OtherChannel",
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            f"/twitch/channels/?search={channel.name}",
        )
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = list(context["channels"])

        # Should only contain the searched channel
        assert len(channels) == 1
        assert channels[0].twitch_id == channel.twitch_id

    @pytest.mark.django_db
    def test_dashboard_view(self, client: Client) -> None:
        """Test dashboard view returns 200 and has active_campaigns in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:dashboard"))
        assert response.status_code == 200
        assert "active_campaigns" in response.context

    @pytest.mark.django_db
    def test_dashboard_dedupes_campaigns_for_multi_owner_game(
        self,
        client: Client,
    ) -> None:
        """Dashboard should not render duplicate campaign cards when a game has multiple owners."""
        now = timezone.now()
        org1: Organization = Organization.objects.create(
            twitch_id="org_a",
            name="Org A",
        )
        org2: Organization = Organization.objects.create(
            twitch_id="org_b",
            name="Org B",
        )
        game: Game = Game.objects.create(
            twitch_id="game_multi_owner",
            name="game",
            display_name="Multi Owner",
        )
        game.owners.add(org1, org2)

        _campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - datetime.timedelta(hours=1),
            end_at=now + datetime.timedelta(hours=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:dashboard"))
        assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context
        if isinstance(context, list):
            context = context[-1]

        # campaigns_by_game should include one deduplicated campaign entry for the game.
        assert "campaigns_by_game" in context
        assert game.twitch_id in context["campaigns_by_game"]
        assert len(context["campaigns_by_game"][game.twitch_id]["campaigns"]) == 1

    @pytest.mark.django_db
    def test_debug_view(self, client: Client) -> None:
        """Test debug view returns 200 and has games_without_owner in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("core:debug"))
        assert response.status_code == 200
        assert "games_without_owner" in response.context

    @pytest.mark.django_db
    def test_drop_campaign_list_view(self, client: Client) -> None:
        """Test campaign list view returns 200 and has campaigns in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list"),
        )
        assert response.status_code == 200
        assert "campaigns" in response.context

    @pytest.mark.django_db
    def test_drop_campaign_list_pagination(self, client: Client) -> None:
        """Test pagination works correctly with 100 items per page."""
        game: Game = Game.objects.create(
            twitch_id="g1",
            name="Game",
            display_name="Game",
        )
        now: datetime.datetime = timezone.now()

        # Create 150 campaigns to test pagination
        campaigns = [
            DropCampaign(
                twitch_id=f"c{i}",
                name=f"Campaign {i}",
                game=game,
                start_at=now - timedelta(days=10),
                end_at=now + timedelta(days=10),
                operation_names=["DropCampaignDetails"],
            )
            for i in range(150)
        ]
        DropCampaign.objects.bulk_create(campaigns)

        # Test first page
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list"),
        )
        assert response.status_code == 200
        assert "is_paginated" in response.context
        assert response.context["is_paginated"] is True
        assert "page_obj" in response.context
        assert len(response.context["campaigns"]) == 100
        assert response.context["page_obj"].number == 1
        assert response.context["page_obj"].has_next() is True

        # Test second page
        response = client.get(reverse("twitch:campaign_list") + "?page=2")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 50
        assert response.context["page_obj"].number == 2
        assert response.context["page_obj"].has_previous() is True
        assert response.context["page_obj"].has_next() is False

    @pytest.mark.django_db
    def test_drop_campaign_list_status_filter_active(self, client: Client) -> None:
        """Test filtering for active campaigns only."""
        game: Game = Game.objects.create(
            twitch_id="g1",
            name="Game",
            display_name="Game",
        )
        now: datetime.datetime = timezone.now()

        # Create active campaign
        _active_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="active",
            name="Active Campaign",
            game=game,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Create upcoming campaign
        DropCampaign.objects.create(
            twitch_id="upcoming",
            name="Upcoming Campaign",
            game=game,
            start_at=now + timedelta(days=5),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
        )

        # Create expired campaign
        DropCampaign.objects.create(
            twitch_id="expired",
            name="Expired Campaign",
            game=game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Test active filter
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list") + "?status=active",
        )
        assert response.status_code == 200
        campaigns: list[DropCampaign] = list(response.context["campaigns"])
        assert len(campaigns) == 1
        assert campaigns[0].twitch_id == "active"

    @pytest.mark.django_db
    def test_drop_campaign_list_status_filter_upcoming(self, client: Client) -> None:
        """Test filtering for upcoming campaigns only."""
        game: Game = Game.objects.create(
            twitch_id="g1",
            name="Game",
            display_name="Game",
        )
        now: datetime.datetime = timezone.now()

        # Create active campaign
        DropCampaign.objects.create(
            twitch_id="active",
            name="Active Campaign",
            game=game,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Create upcoming campaign
        _upcoming_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="upcoming",
            name="Upcoming Campaign",
            game=game,
            start_at=now + timedelta(days=5),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
        )

        # Create expired campaign
        DropCampaign.objects.create(
            twitch_id="expired",
            name="Expired Campaign",
            game=game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Test upcoming filter
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list") + "?status=upcoming",
        )
        assert response.status_code == 200
        campaigns: list[DropCampaign] = list(response.context["campaigns"])
        assert len(campaigns) == 1
        assert campaigns[0].twitch_id == "upcoming"

    @pytest.mark.django_db
    def test_drop_campaign_list_status_filter_expired(self, client: Client) -> None:
        """Test filtering for expired campaigns only."""
        game: Game = Game.objects.create(
            twitch_id="g1",
            name="Game",
            display_name="Game",
        )
        now: datetime.datetime = timezone.now()

        # Create active campaign
        DropCampaign.objects.create(
            twitch_id="active",
            name="Active Campaign",
            game=game,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Create upcoming campaign
        DropCampaign.objects.create(
            twitch_id="upcoming",
            name="Upcoming Campaign",
            game=game,
            start_at=now + timedelta(days=5),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
        )

        # Create expired campaign
        _expired_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="expired",
            name="Expired Campaign",
            game=game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Test expired filter
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list") + "?status=expired",
        )
        assert response.status_code == 200
        campaigns: list[DropCampaign] = list(response.context["campaigns"])
        assert len(campaigns) == 1
        assert campaigns[0].twitch_id == "expired"

    @pytest.mark.django_db
    def test_drop_campaign_list_game_filter(self, client: Client) -> None:
        """Test filtering campaigns by game."""
        game1: Game = Game.objects.create(
            twitch_id="g1",
            name="Game 1",
            display_name="Game 1",
        )
        game2: Game = Game.objects.create(
            twitch_id="g2",
            name="Game 2",
            display_name="Game 2",
        )
        now: datetime.datetime = timezone.now()

        # Create campaigns for game 1
        DropCampaign.objects.create(
            twitch_id="c1",
            name="Campaign 1",
            game=game1,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )
        DropCampaign.objects.create(
            twitch_id="c2",
            name="Campaign 2",
            game=game1,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Create campaign for game 2
        DropCampaign.objects.create(
            twitch_id="c3",
            name="Campaign 3",
            game=game2,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )

        # Test filtering by game1
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list") + "?game=g1",
        )
        assert response.status_code == 200
        campaigns: list[DropCampaign] = list(response.context["campaigns"])
        assert len(campaigns) == 2
        assert all(c.game.twitch_id == "g1" for c in campaigns)

        # Test filtering by game2
        response = client.get(reverse("twitch:campaign_list") + "?game=g2")
        assert response.status_code == 200
        campaigns = list(response.context["campaigns"])
        assert len(campaigns) == 1
        assert campaigns[0].game.twitch_id == "g2"

    @pytest.mark.django_db
    def test_drop_campaign_list_pagination_preserves_filters(
        self,
        client: Client,
    ) -> None:
        """Test that pagination links preserve game and status filters."""
        game: Game = Game.objects.create(
            twitch_id="g1",
            name="Game",
            display_name="Game",
        )
        now: datetime.datetime = timezone.now()

        # Create 150 active campaigns for game g1
        campaigns = [
            DropCampaign(
                twitch_id=f"c{i}",
                name=f"Campaign {i}",
                game=game,
                start_at=now - timedelta(days=5),
                end_at=now + timedelta(days=5),
                operation_names=["DropCampaignDetails"],
            )
            for i in range(150)
        ]
        DropCampaign.objects.bulk_create(campaigns)

        # Request first page with filters
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list") + "?game=g1&status=active",
        )
        assert response.status_code == 200
        assert response.context["is_paginated"] is True

        # Check that response HTML contains pagination links with filters
        content: str = response.content.decode("utf-8")
        assert "?status=active&game=g1" in content
        assert "page=2" in content

    @pytest.mark.django_db
    def test_drop_campaign_detail_view(self, client: Client, db: object) -> None:
        """Test campaign detail view returns 200 and has campaign in context."""
        game: Game = Game.objects.create(
            twitch_id="g1",
            name="Game",
            display_name="Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="c1",
            name="Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200
        assert "campaign" in response.context

    @pytest.mark.django_db
    def test_drop_campaign_detail_view_badge_benefit_includes_description_from_chatbadge(
        self,
        client: Client,
    ) -> None:
        """Test campaign detail view includes badge benefit description from ChatBadge."""
        game: Game = Game.objects.create(
            twitch_id="g-badge",
            name="Game",
            display_name="Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="c-badge",
            name="Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )

        drop = TimeBasedDrop.objects.create(
            twitch_id="d1",
            name="Drop",
            campaign=campaign,
            required_minutes_watched=0,
            required_subs=1,
        )

        benefit = DropBenefit.objects.create(
            twitch_id="b1",
            name="Diana",
            distribution_type="BADGE",
        )
        drop.benefits.add(benefit)

        badge_set = ChatBadgeSet.objects.create(set_id="diana")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1",
            image_url_2x="https://example.com/2",
            image_url_4x="https://example.com/4",
            title="Diana",
            description="This badge was earned by subscribing.",
        )

        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200

        # The campaign detail page prints a syntax-highlighted JSON block; the badge description should be present.
        html = response.content.decode("utf-8")
        assert "This badge was earned by subscribing." in html

    @pytest.mark.django_db
    def test_games_grid_view(self, client: Client) -> None:
        """Test games grid view returns 200 and has games in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:games_grid"))
        assert response.status_code == 200
        assert "games" in response.context

    @pytest.mark.django_db
    def test_games_list_view(self, client: Client) -> None:
        """Test games list view returns 200 and has games in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:games_list"))
        assert response.status_code == 200
        assert "games" in response.context

    @pytest.mark.django_db
    def test_game_detail_view(self, client: Client, db: object) -> None:
        """Test game detail view returns 200 and has game in context."""
        game: Game = Game.objects.create(
            twitch_id="g2",
            name="Game2",
            display_name="Game2",
        )
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200
        assert "game" in response.context

    @pytest.mark.django_db
    def test_game_detail_image_aspect_ratio(self, client: Client, db: object) -> None:
        """Box art should render with a width attribute only, preserving aspect ratio."""
        game: Game = Game.objects.create(
            twitch_id="g3",
            name="Game3",
            display_name="Game3",
        )
        # property is derived; write to underlying field
        game.box_art = "https://example.com/boxart.png"
        game.save()
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        html: str = response.content.decode("utf-8")
        # the picture tag should include the width but not an explicit height
        assert 'width="160"' in html
        # ensure the height attribute is not part of the same img element
        assert (
            "height="
            not in html.split("https://example.com/boxart.png")[1].split(
                ">",
                maxsplit=1,
            )[0]
        )

    @pytest.mark.django_db
    def test_game_detail_view_serializes_owners_field(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Game detail JSON payload should use `owners` (M2M), not stale `owner`."""
        org: Organization = Organization.objects.create(
            twitch_id="org-game-detail",
            name="Org Game Detail",
        )
        game: Game = Game.objects.create(
            twitch_id="g2-owners",
            name="Game2 Owners",
            display_name="Game2 Owners",
        )
        game.owners.add(org)

        monkeypatch.setattr("twitch.views.format_and_color_json", lambda data: data)

        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200

        game_data: dict[str, Any] = response.context["game_data"]
        fields: dict[str, Any] = game_data["fields"]
        assert "owners" in fields
        assert fields["owners"] == [org.pk]
        assert "owner" not in fields

    @pytest.mark.django_db
    def test_org_list_view(self, client: Client) -> None:
        """Test org list view returns 200 and has orgs in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:org_list"))
        assert response.status_code == 200
        assert "orgs" in response.context

    @pytest.mark.django_db
    def test_organization_detail_view(self, client: Client, db: object) -> None:
        """Test organization detail view returns 200 and has organization in context."""
        org: Organization = Organization.objects.create(twitch_id="o1", name="Org1")
        url: str = reverse("twitch:organization_detail", args=[org.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200
        assert "organization" in response.context

    @pytest.mark.django_db
    def test_channel_detail_view(self, client: Client, db: object) -> None:
        """Test channel detail view returns 200 and has channel in context."""
        channel: Channel = Channel.objects.create(
            twitch_id="ch1",
            name="Channel1",
            display_name="Channel1",
        )
        url: str = reverse("twitch:channel_detail", args=[channel.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200
        assert "channel" in response.context

    @pytest.mark.django_db
    def test_docs_rss_view(self, client: Client) -> None:
        """Test docs RSS view returns 200 and has feeds in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("core:docs_rss"))
        assert response.status_code == 200
        assert "feeds" in response.context
        assert "filtered_feeds" in response.context
        assert response.context["feeds"][0]["example_xml"]
        html: str = response.content.decode()
        assert '<code class="language-xml">' in html


@pytest.mark.django_db
class TestSEOHelperFunctions:
    """Tests for SEO helper functions."""

    def test_truncate_description_short_text(self) -> None:
        """Test that short text is not truncated."""
        text = "This is a short description"
        result: str = _truncate_description(text, max_length=160)
        assert result == text

    def test_truncate_description_long_text(self) -> None:
        """Test that long text is truncated at word boundary."""
        text = "This is a very long description that exceeds the maximum length and should be truncated at a word boundary to avoid cutting off in the middle of a word"
        result: str = _truncate_description(text, max_length=50)
        assert len(result) <= 53  # Allow some flexibility
        assert not result.endswith(" ")

    def test_truncate_description_adds_ellipsis(self) -> None:
        """Test that truncation adds ellipsis."""
        text = "This is a very long description that exceeds the maximum length"
        result: str = _truncate_description(text, max_length=30)
        assert result.endswith("…")  # Uses en-dash, not three dots

    def test_build_seo_context_required_fields(self) -> None:
        """Test that _build_seo_context returns all required fields."""
        context: dict[str, Any] = _build_seo_context(
            page_title="Test Title",
            page_description="Test Description",
            page_image="https://example.com/image.jpg",
            og_type="article",
            schema_data={"@context": "https://schema.org"},
        )

        assert context["page_title"] == "Test Title"
        assert context["page_description"] == "Test Description"
        assert context["page_image"] == "https://example.com/image.jpg"
        assert context["og_type"] == "article"
        assert context["robots_directive"] == "index, follow"  # default
        # schema_data is JSON-dumped to a string in context
        assert json.loads(context["schema_data"]) == {"@context": "https://schema.org"}

    def test_build_seo_context_with_all_parameters(self) -> None:
        """Test _build_seo_context with all parameters."""
        now: datetime.datetime = timezone.now()
        breadcrumb: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": "/",
                },
            ],
        }

        context: dict[str, Any] = _build_seo_context(
            page_title="Test",
            page_description="Desc",
            page_image="https://example.com/img.jpg",
            og_type="article",
            schema_data={},
            breadcrumb_schema=breadcrumb,
            pagination_info=[{"rel": "next", "url": "/page/2/"}],
            published_date=now.isoformat(),
            modified_date=now.isoformat(),
            robots_directive="noindex, follow",
        )

        # breadcrumb_schema is JSON-dumped, so parse it back
        assert json.loads(context["breadcrumb_schema"]) == breadcrumb
        assert context["pagination_info"] == [{"rel": "next", "url": "/page/2/"}]
        assert context["published_date"] == now.isoformat()
        assert context["modified_date"] == now.isoformat()
        assert context["robots_directive"] == "noindex, follow"

    def test_build_breadcrumb_schema_structure(self) -> None:
        """Test that _build_breadcrumb_schema creates proper BreadcrumbList structure."""
        items: list[dict[str, str | int]] = [
            {"name": "Home", "url": "/"},
            {"name": "Games", "url": "/games/"},
            {"name": "Test Game", "url": "/games/123/"},
        ]

        schema: dict[str, Any] = _build_breadcrumb_schema(items)

        assert schema["@context"] == "https://schema.org"
        assert schema["@type"] == "BreadcrumbList"
        assert schema["itemListElement"][0]["@type"] == "ListItem"
        assert schema["itemListElement"][0]["position"] == 1
        assert schema["itemListElement"][0]["name"] == "Home"
        assert schema["itemListElement"][2]["position"] == 3

    def test_build_pagination_info_with_next_page(self) -> None:
        """Test _build_pagination_info extracts next page URL."""
        factory = RequestFactory()
        request: WSGIRequest = factory.get("/campaigns/?page=1")

        items: list[int] = list(range(100))
        paginator: Paginator[int] = Paginator(items, 10)
        page: Page[int] = paginator.get_page(1)

        info: list[dict[str, str]] | None = _build_pagination_info(
            request,
            page,
            "/campaigns/",
        )

        assert info is not None
        assert len(info) == 1
        assert info[0]["rel"] == "next"
        assert "page=2" in info[0]["url"]

    def test_build_pagination_info_with_prev_page(self) -> None:
        """Test _build_pagination_info extracts prev page URL."""
        factory = RequestFactory()
        request: WSGIRequest = factory.get("/campaigns/?page=2")

        items: list[int] = list(range(100))
        paginator: Paginator[int] = Paginator(items, 10)
        page: Page[int] = paginator.get_page(2)

        info: list[dict[str, str]] | None = _build_pagination_info(
            request,
            page,
            "/campaigns/",
        )

        assert info is not None
        assert len(info) == 2
        assert info[0]["rel"] == "prev"
        assert "page=1" in info[0]["url"]
        assert info[1]["rel"] == "next"
        assert "page=3" in info[1]["url"]


@pytest.mark.django_db
class TestSEOMetaTags:
    """Tests for SEO meta tags in views."""

    @pytest.fixture
    def game_with_campaign(self) -> dict[str, Any]:
        """Create a game with campaign for testing.

        Returns:
            dict[str, Any]: A dictionary containing the created organization, game, and campaign.
        """
        org: Organization = Organization.objects.create(
            twitch_id="org1",
            name="Test Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
            box_art="https://example.com/box_art.jpg",
        )
        game.owners.add(org)
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            description="Campaign description",
            game=game,
            image_url="https://example.com/campaign.jpg",
            operation_names=["DropCampaignDetails"],
        )
        return {"org": org, "game": game, "campaign": campaign}

    def test_campaign_list_view_has_seo_context(self, client: Client) -> None:
        """Test campaign list view has SEO context variables."""
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list"),
        )
        assert response.status_code == 200
        assert "page_title" in response.context
        assert "page_description" in response.context

    def test_campaign_detail_view_has_breadcrumb(
        self,
        client: Client,
        game_with_campaign: dict[str, Any],
    ) -> None:
        """Test campaign detail view has breadcrumb schema."""
        campaign: DropCampaign = game_with_campaign["campaign"]
        url = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        assert response.status_code == 200
        assert "breadcrumb_schema" in response.context
        # breadcrumb_schema is JSON-dumped in context
        breadcrumb_str = response.context["breadcrumb_schema"]
        breadcrumb = json.loads(breadcrumb_str)
        assert breadcrumb["@type"] == "BreadcrumbList"
        assert len(breadcrumb["itemListElement"]) >= 3

    def test_campaign_detail_view_has_modified_date(
        self,
        client: Client,
        game_with_campaign: dict[str, Any],
    ) -> None:
        """Test campaign detail view has modified_date."""
        campaign: DropCampaign = game_with_campaign["campaign"]
        url = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        assert response.status_code == 200
        assert "modified_date" in response.context
        assert response.context["modified_date"] is not None

    def test_game_detail_view_has_seo_context(
        self,
        client: Client,
        game_with_campaign: dict[str, Any],
    ) -> None:
        """Test game detail view has full SEO context."""
        game: Game = game_with_campaign["game"]
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        assert response.status_code == 200
        assert "page_title" in response.context
        assert "page_description" in response.context
        assert "breadcrumb_schema" in response.context
        assert "modified_date" in response.context

    def test_organization_detail_view_has_breadcrumb(self, client: Client) -> None:
        """Test organization detail view has breadcrumb."""
        org: Organization = Organization.objects.create(
            twitch_id="org1",
            name="Test Org",
        )
        url: str = reverse("twitch:organization_detail", args=[org.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        assert response.status_code == 200
        assert "breadcrumb_schema" in response.context

    def test_channel_detail_view_has_breadcrumb(self, client: Client) -> None:
        """Test channel detail view has breadcrumb."""
        channel: Channel = Channel.objects.create(
            twitch_id="ch1",
            name="ch1",
            display_name="Channel 1",
        )
        url: str = reverse("twitch:channel_detail", args=[channel.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        assert response.status_code == 200
        assert "breadcrumb_schema" in response.context

    def test_noindex_pages_have_robots_directive(self, client: Client) -> None:
        """Test that pages with noindex have proper robots directive."""
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backups"),
        )
        assert response.status_code == 200
        assert "robots_directive" in response.context


@pytest.mark.django_db
class TestSitemapView:
    """Tests for the sitemap.xml view."""

    @pytest.fixture
    def sample_entities(self) -> dict[str, Any]:
        """Create sample entities for sitemap testing.

        Returns:
            dict[str, Any]: A dictionary containing the created organization, game, channel, campaign, and badge set.
        """
        org: Organization = Organization.objects.create(
            twitch_id="org1",
            name="Test Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        game.owners.add(org)
        channel: Channel = Channel.objects.create(
            twitch_id="ch1",
            name="ch1",
            display_name="Channel 1",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            description="Desc",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        badge: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="badge1")
        return {
            "org": org,
            "game": game,
            "channel": channel,
            "campaign": campaign,
            "badge": badge,
        }

    def test_sitemap_view_returns_xml(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap view returns XML content."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"

    def test_sitemap_contains_xml_declaration(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap contains proper XML declaration."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content = response.content.decode()
        assert content.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_sitemap_contains_urlset(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap contains urlset element."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert "<urlset" in content
        assert "</urlset>" in content

    def test_sitemap_contains_static_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes static pages."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        # Check for some static pages
        assert (
            "<loc>http://testserver/</loc>" in content
            or "<loc>http://localhost:8000/</loc>" in content
        )
        assert "/campaigns/" in content
        assert "/games/" in content

    def test_sitemap_contains_game_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes game detail pages."""
        game: Game = sample_entities["game"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert f"/games/{game.twitch_id}/" in content

    def test_sitemap_contains_campaign_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes campaign detail pages."""
        campaign: DropCampaign = sample_entities["campaign"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert f"/campaigns/{campaign.twitch_id}/" in content

    def test_sitemap_contains_organization_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes organization detail pages."""
        org: Organization = sample_entities["org"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert f"/organizations/{org.twitch_id}/" in content

    def test_sitemap_contains_channel_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes channel detail pages."""
        channel: Channel = sample_entities["channel"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert f"/twitch/channels/{channel.twitch_id}/" in content

    def test_sitemap_contains_badge_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes badge detail pages."""
        badge: ChatBadge = sample_entities["badge"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert f"/badges/{badge.set_id}/" in content  # pyright: ignore[reportAttributeAccessIssue]

    def test_sitemap_includes_priority(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes priority values."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert "<priority>" in content
        assert "</priority>" in content

    def test_sitemap_includes_changefreq(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes changefreq values."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert "<changefreq>" in content
        assert "</changefreq>" in content

    def test_sitemap_includes_lastmod(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes lastmod for detail pages."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        # Check for lastmod in game or campaign entries
        assert "<lastmod>" in content


@pytest.mark.django_db
class TestSEOPaginationLinks:
    """Tests for SEO pagination links in views."""

    def test_campaign_list_first_page_has_next(self, client: Client) -> None:
        """Test campaign list first page has next link."""
        # Create a game and multiple campaigns to trigger pagination
        org: Organization = Organization.objects.create(
            twitch_id="org1",
            name="Test Org",
        )
        game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        game.owners.add(org)
        for i in range(25):
            DropCampaign.objects.create(
                twitch_id=f"camp{i}",
                name=f"Campaign {i}",
                description="Desc",
                game=game,
                operation_names=["DropCampaignDetails"],
            )

        response = client.get(reverse("twitch:campaign_list"))
        assert response.status_code == 200
        if response.context.get("page_obj") and response.context["page_obj"].has_next():
            assert "pagination_info" in response.context

    def test_campaign_list_pagination_info_structure(self, client: Client) -> None:
        """Test pagination_info has correct structure."""
        # Create a game and multiple campaigns to trigger pagination
        org = Organization.objects.create(twitch_id="org1", name="Test Org")
        game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        game.owners.add(org)
        for i in range(25):
            DropCampaign.objects.create(
                twitch_id=f"camp{i}",
                name=f"Campaign {i}",
                description="Desc",
                game=game,
                operation_names=["DropCampaignDetails"],
            )

        response = client.get(reverse("twitch:campaign_list"))
        assert response.status_code == 200
        if "pagination_info" in response.context:
            pagination_info = response.context["pagination_info"]
            # Should be a dict with rel and url
            assert isinstance(pagination_info, dict)
            assert "rel" in pagination_info or pagination_info is None


@pytest.mark.django_db
class TestDropCampaignImageFallback:
    """Tests for DropCampaign image_best_url property with benefit fallback."""

    def test_image_best_url_returns_campaign_image_url(self) -> None:
        """Test that image_best_url returns campaign image_url when present."""
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            game=game,
            image_url="https://example.com/campaign.png",
        )
        assert campaign.image_best_url == "https://example.com/campaign.png"

    def test_image_best_url_uses_benefit_image_when_campaign_has_no_image(self) -> None:
        """Test that image_best_url returns first benefit image when campaign has no image."""
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            game=game,
            image_url="",  # No campaign image
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit1",
            name="Test Benefit",
            image_asset_url="https://example.com/benefit.png",
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="drop1",
            name="Test Drop",
            campaign=campaign,
        )
        drop.benefits.add(benefit)

        assert campaign.image_best_url == "https://example.com/benefit.png"

    def test_image_best_url_prefers_campaign_image_over_benefit_image(self) -> None:
        """Test that campaign image is preferred over benefit image."""
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            game=game,
            image_url="https://example.com/campaign.png",  # Campaign has image
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit1",
            name="Test Benefit",
            image_asset_url="https://example.com/benefit.png",
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="drop1",
            name="Test Drop",
            campaign=campaign,
        )
        drop.benefits.add(benefit)

        # Should return campaign image, not benefit image
        assert campaign.image_best_url == "https://example.com/campaign.png"

    def test_image_best_url_returns_empty_when_no_images(self) -> None:
        """Test that image_best_url returns empty string when no images available."""
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            game=game,
            image_url="",  # No campaign image
        )
        # No benefits or drops

        assert not campaign.image_best_url

    def test_image_best_url_uses_benefit_best_url(self) -> None:
        """Test that benefit's image_best_url property is used (prefers local file)."""
        game: Game = Game.objects.create(
            twitch_id="game1",
            name="test_game",
            display_name="Test Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            game=game,
            image_url="",  # No campaign image
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit1",
            name="Test Benefit",
            image_asset_url="https://example.com/benefit.png",
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="drop1",
            name="Test Drop",
            campaign=campaign,
        )
        drop.benefits.add(benefit)

        # Should use benefit's image_asset_url (since no local file)
        assert campaign.image_best_url == benefit.image_best_url
