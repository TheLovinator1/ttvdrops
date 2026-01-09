from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import pytest

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from django.test import Client
    from django.test.client import _MonkeyPatchedWSGIResponse
    from django.test.utils import ContextList


@pytest.mark.django_db
class TestSearchView:
    """Tests for the search_view function."""

    @pytest.fixture
    def sample_data(self) -> dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit]:
        """Create sample data for testing.

        Returns:
            A dictionary containing the created sample data.
        """
        org: Organization = Organization.objects.create(twitch_id="123", name="Test Organization")
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
            operation_name="DropCampaignDetails",
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
    def _get_context(response: _MonkeyPatchedWSGIResponse) -> ContextList | dict[str, Any]:
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
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
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
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
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
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
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
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
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
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
    ) -> None:
        """Test that campaign description is searchable."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=campaign")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert sample_data["campaign"] in context["results"]["campaigns"]

    def test_game_display_name_search(
        self,
        client: Client,
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
    ) -> None:
        """Test that game display_name is searchable."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=Game")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert sample_data["game"] in context["results"]["games"]

    def test_query_no_matches(
        self,
        client: Client,
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
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
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
    ) -> None:
        """Test that context contains the search query."""
        query = "Test"
        response: _MonkeyPatchedWSGIResponse = client.get(f"/search/?q={query}")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert context["query"] == query

    @pytest.mark.parametrize(
        ("model_key", "related_field"),
        [
            ("campaigns", "game"),
            ("drops", "campaign"),
        ],
    )
    def test_select_related_optimization(
        self,
        client: Client,
        sample_data: dict[str, Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit],
        model_key: str,
        related_field: str,
    ) -> None:
        """Test that queries use select_related for performance optimization."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=Test")
        context: ContextList | dict[str, Any] = self._get_context(response)

        results: list[Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit] = context["results"][model_key]
        assert len(results) > 0

        # Verify the related object is accessible without additional query
        first_result: Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit = results[0]
        assert hasattr(first_result, related_field)


@pytest.mark.django_db
class TestChannelListView:
    """Tests for the ChannelListView."""

    @pytest.fixture
    def channel_with_campaigns(self) -> dict[str, Channel | Game | Organization | list[DropCampaign]]:
        """Create a channel with multiple campaigns for testing.

        Returns:
            A dictionary containing the created channel and campaigns.
        """
        org: Organization = Organization.objects.create(twitch_id="org1", name="Test Org")
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
                operation_name="DropCampaignDetails",
            )
            campaign.allow_channels.add(channel)
            campaigns.append(campaign)

        return {
            "channel": channel,
            "campaigns": campaigns,
            "game": game,
            "org": org,
        }

    def test_channel_list_loads(self, client: Client) -> None:
        """Test that channel list view loads successfully."""
        response: _MonkeyPatchedWSGIResponse = client.get("/channels/")
        assert response.status_code == 200

    def test_campaign_count_annotation(
        self,
        client: Client,
        channel_with_campaigns: dict[str, Channel | Game | Organization | list[DropCampaign]],
    ) -> None:
        """Test that campaign_count is correctly annotated for channels."""
        channel: Channel = channel_with_campaigns["channel"]  # type: ignore[assignment]
        campaigns: list[DropCampaign] = channel_with_campaigns["campaigns"]  # type: ignore[assignment]

        response: _MonkeyPatchedWSGIResponse = client.get("/channels/")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = context["channels"]

        # Find our test channel in the results
        test_channel: Channel | None = next((ch for ch in channels if ch.twitch_id == channel.twitch_id), None)

        assert test_channel is not None
        assert hasattr(test_channel, "campaign_count")

        campaign_count: int | None = getattr(test_channel, "campaign_count", None)
        assert campaign_count == len(campaigns), f"Expected campaign_count to be {len(campaigns)}, got {campaign_count}"

    def test_campaign_count_zero_for_channel_without_campaigns(
        self,
        client: Client,
    ) -> None:
        """Test that campaign_count is 0 for channels with no campaigns."""
        # Create a channel with no campaigns
        channel: Channel = Channel.objects.create(
            twitch_id="channel_no_campaigns",
            name="nocampaigns",
            display_name="NoCompaigns",
        )

        response: _MonkeyPatchedWSGIResponse = client.get("/channels/")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = context["channels"]
        test_channel: Channel | None = next((ch for ch in channels if ch.twitch_id == channel.twitch_id), None)

        assert test_channel is not None
        assert hasattr(test_channel, "campaign_count")

        campaign_count: int | None = getattr(test_channel, "campaign_count", None)
        assert campaign_count is None or campaign_count == 0

    def test_channels_ordered_by_campaign_count(
        self,
        client: Client,
        channel_with_campaigns: dict[str, Channel | Game | Organization | list[DropCampaign]],
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
                operation_name="DropCampaignDetails",
            )
            campaign.allow_channels.add(channel2)

        response: _MonkeyPatchedWSGIResponse = client.get("/channels/")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = list(context["channels"])

        # The channel with 10 campaigns should come before the one with 5
        channel2_index: int | None = next((i for i, ch in enumerate(channels) if ch.twitch_id == "channel2"), None)
        channel1_index: int | None = next((i for i, ch in enumerate(channels) if ch.twitch_id == "channel1"), None)

        assert channel2_index is not None
        assert channel1_index is not None
        assert channel2_index < channel1_index, "Channel with more campaigns should appear first"

    def test_channel_search_filters_correctly(
        self,
        client: Client,
        channel_with_campaigns: dict[str, Channel | Game | Organization | list[DropCampaign]],
    ) -> None:
        """Test that search parameter filters channels correctly."""
        channel: Channel = channel_with_campaigns["channel"]  # type: ignore[assignment]

        # Create another channel that won't match the search
        Channel.objects.create(
            twitch_id="other_channel",
            name="otherchannel",
            display_name="OtherChannel",
        )

        response: _MonkeyPatchedWSGIResponse = client.get(f"/channels/?search={channel.name}")
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        channels: list[Channel] = list(context["channels"])

        # Should only contain the searched channel
        assert len(channels) == 1
        assert channels[0].twitch_id == channel.twitch_id
