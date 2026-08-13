import datetime
import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import pytest
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Max
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from kick.models import KickCategory
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from twitch.management.commands.better_import_drops import Command
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.views import _build_breadcrumb_schema
from twitch.views import _build_pagination_info
from twitch.views import _build_seo_context
from twitch.views import _truncate_description

if TYPE_CHECKING:
    from collections import OrderedDict

    from django.core.handlers.wsgi import WSGIRequest
    from django.db.models import QuerySet
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

    @pytest.mark.parametrize(
        "model_key",
        ["org", "game", "campaign", "drop", "benefit"],
    )
    def test_search_by_twitch_id_short(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
        model_key: Literal["org", "game", "campaign", "drop", "benefit"],
    ) -> None:
        """Short query can find results by twitch_id prefix."""
        # Each model's twitch_id prefix for istartswith matching
        prefixes: dict[str, str] = {
            "org": "12",
            "game": "45",
            "campaign": "78",
            "drop": "10",
            "benefit": "12",
        }
        response: _MonkeyPatchedWSGIResponse = client.get(
            f"/search/?q={prefixes[model_key]}",
        )
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200

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
    def test_search_by_twitch_id_long(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
        model_key: Literal["org", "game", "campaign", "drop", "benefit"],
    ) -> None:
        """Long query can find results by full twitch_id."""
        twitch_ids: dict[str, str] = {
            "org": "123",
            "game": "456",
            "campaign": "789",
            "drop": "1011",
            "benefit": "1213",
        }
        response: _MonkeyPatchedWSGIResponse = client.get(
            f"/search/?q={twitch_ids[model_key]}",
        )
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200

        result_key_map: dict[str, str] = {
            "org": "organizations",
            "game": "games",
            "campaign": "campaigns",
            "drop": "drops",
            "benefit": "benefits",
        }
        result_key: str = result_key_map[model_key]
        assert sample_data[model_key] in context["results"][result_key]

    def test_search_by_primary_key(
        self,
        client: Client,
        sample_data: dict[
            str,
            Organization | Game | DropCampaign | TimeBasedDrop | DropBenefit,
        ],
    ) -> None:
        """Numeric query can find results by database primary key."""
        org: Organization = sample_data["org"]  # type: ignore[assignment]
        response: _MonkeyPatchedWSGIResponse = client.get(f"/search/?q={org.pk}")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        assert org in context["results"]["organizations"]

    def test_search_by_primary_key_no_match(
        self,
        client: Client,
    ) -> None:
        """Numeric primary key with no match returns empty results."""
        response: _MonkeyPatchedWSGIResponse = client.get("/search/?q=999999")
        context: ContextList | dict[str, Any] = self._get_context(response)

        assert response.status_code == 200
        for result_list in context["results"].values():
            assert len(result_list) == 0


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

    def test_channel_list_queryset_only_selects_rendered_fields(self) -> None:
        """Channel list queryset should defer non-rendered fields."""
        channel: Channel = Channel.objects.create(
            twitch_id="channel_minimal_fields",
            name="channelminimalfields",
            display_name="Channel Minimal Fields",
        )

        queryset: QuerySet[Channel] = Channel.for_list_view()
        fetched_channel: Channel | None = queryset.filter(
            twitch_id=channel.twitch_id,
        ).first()

        assert fetched_channel is not None
        assert hasattr(fetched_channel, "campaign_count")

        deferred_fields: set[str] = fetched_channel.get_deferred_fields()
        assert "added_at" in deferred_fields
        assert "updated_at" in deferred_fields
        assert "name" not in deferred_fields
        assert "display_name" not in deferred_fields
        assert "twitch_id" not in deferred_fields

    def test_channel_list_queryset_uses_counter_cache_without_join(self) -> None:
        """Channel list SQL should use cached count and avoid campaign join/grouping."""
        sql: str = str(Channel.for_list_view().query).upper()

        assert "TWITCH_DROPCAMPAIGN_ALLOW_CHANNELS" not in sql
        assert "GROUP BY" not in sql
        assert "ALLOWED_CAMPAIGN_COUNT" in sql

    def test_channel_detail_queryset_only_selects_rendered_fields(self) -> None:
        """Channel detail queryset should defer fields not used by the template/SEO."""
        channel: Channel = Channel.objects.create(
            twitch_id="channel_detail_fields",
            name="channeldetailfields",
            display_name="Channel Detail Fields",
        )

        fetched_channel: Channel | None = (
            Channel
            .for_detail_view()
            .filter(
                twitch_id=channel.twitch_id,
            )
            .first()
        )

        assert fetched_channel is not None
        deferred_fields: set[str] = fetched_channel.get_deferred_fields()
        assert "allowed_campaign_count" in deferred_fields
        assert "name" not in deferred_fields
        assert "display_name" not in deferred_fields
        assert "twitch_id" not in deferred_fields
        assert "added_at" not in deferred_fields
        assert "updated_at" not in deferred_fields

    def test_channel_detail_campaign_queryset_only_selects_rendered_fields(
        self,
    ) -> None:
        """Channel detail campaign queryset should avoid loading unused campaign fields."""
        now: datetime.datetime = timezone.now()

        game: Game = Game.objects.create(
            twitch_id="channel_detail_game_fields",
            name="Channel Detail Game Fields",
            display_name="Channel Detail Game Fields",
        )
        channel: Channel = Channel.objects.create(
            twitch_id="channel_detail_campaign_fields",
            name="channeldetailcampaignfields",
            display_name="Channel Detail Campaign Fields",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="channel_detail_campaign",
            name="Channel Detail Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        campaign.allow_channels.add(channel)

        fetched_campaign: DropCampaign | None = DropCampaign.for_channel_detail(
            channel,
        ).first()

        assert fetched_campaign is not None
        deferred_fields: set[str] = fetched_campaign.get_deferred_fields()
        assert "description" in deferred_fields
        assert "details_url" in deferred_fields
        assert "account_link_url" in deferred_fields
        assert "name" not in deferred_fields
        assert "start_at" not in deferred_fields
        assert "end_at" not in deferred_fields

    def test_channel_detail_prefetch_avoids_dropbenefit_refresh_n_plus_one(
        self,
    ) -> None:
        """Channel detail prefetch should not refresh each DropBenefit row for image dimensions."""
        now: datetime.datetime = timezone.now()

        game: Game = Game.objects.create(
            twitch_id="channel_detail_n_plus_one_game",
            name="Channel Detail N+1 Game",
            display_name="Channel Detail N+1 Game",
        )
        channel: Channel = Channel.objects.create(
            twitch_id="channel_detail_n_plus_one_channel",
            name="channeldetailnplusone",
            display_name="Channel Detail N+1",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="channel_detail_n_plus_one_campaign",
            name="Channel Detail N+1 Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        campaign.allow_channels.add(channel)

        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="channel_detail_n_plus_one_drop",
            name="Channel Detail N+1 Drop",
            campaign=campaign,
        )

        png_1x1: bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        benefits: list[DropBenefit] = []
        for i in range(3):
            benefit: DropBenefit = DropBenefit.objects.create(
                twitch_id=f"channel_detail_n_plus_one_benefit_{i}",
                name=f"Benefit {i}",
                image_asset_url=f"https://example.com/benefit_{i}.png",
            )
            assert benefit.image_file is not None
            benefit.image_file.save(
                f"channel_detail_n_plus_one_benefit_{i}.png",
                ContentFile(png_1x1),
                save=True,
            )
            benefits.append(benefit)

        drop.benefits.add(*benefits)

        with CaptureQueriesContext(connection) as queries:
            campaigns: list[DropCampaign] = list(
                DropCampaign.for_channel_detail(channel),
            )
            assert campaigns
            _ = [
                benefit.name
                for campaign_row in campaigns
                for drop_row in campaign_row.time_based_drops.all()  # pyright: ignore[reportAttributeAccessIssue]
                for benefit in drop_row.benefits.all()
            ]

        refresh_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
            and 'from "twitch_dropbenefit"' in query_info["sql"].lower()
            and 'where "twitch_dropbenefit"."id" =' in query_info["sql"].lower()
        ]

        assert not refresh_queries, (
            "Channel detail queryset triggered per-benefit refresh SELECTs. "
            f"Queries: {refresh_queries}"
        )

    def test_channel_detail_uses_asset_url_when_local_benefit_file_is_missing(
        self,
        client: Client,
    ) -> None:
        """Channel detail should avoid broken local image URLs when cached files are missing."""
        now: datetime.datetime = timezone.now()

        game: Game = Game.objects.create(
            twitch_id="channel_detail_missing_local_file_game",
            name="Channel Detail Missing Local File Game",
            display_name="Channel Detail Missing Local File Game",
        )
        channel: Channel = Channel.objects.create(
            twitch_id="channel_detail_missing_local_file_channel",
            name="missinglocalfilechannel",
            display_name="Missing Local File Channel",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="channel_detail_missing_local_file_campaign",
            name="Channel Detail Missing Local File Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        campaign.allow_channels.add(channel)

        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="channel_detail_missing_local_file_drop",
            name="Channel Detail Missing Local File Drop",
            campaign=campaign,
        )

        remote_asset_url: str = "https://example.com/benefit-missing-local-file.png"
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="channel_detail_missing_local_file_benefit",
            name="Benefit Missing Local File",
            image_asset_url=remote_asset_url,
        )
        DropBenefit.objects.filter(pk=benefit.pk).update(
            image_file="benefits/images/does-not-exist.png",
        )
        drop.benefits.add(benefit)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:channel_detail", args=[channel.twitch_id]),
        )
        assert response.status_code == 200

        html: str = response.content.decode("utf-8")
        assert remote_asset_url in html
        assert "benefits/images/does-not-exist.png" not in html

    def test_channel_allowed_campaign_count_updates_on_add_remove_clear(self) -> None:
        """Counter cache should stay in sync when campaign-channel links change."""
        game: Game = Game.objects.create(
            twitch_id="counter_cache_game",
            name="Counter Cache Game",
            display_name="Counter Cache Game",
        )

        channel: Channel = Channel.objects.create(
            twitch_id="counter_cache_channel",
            name="countercachechannel",
            display_name="Counter Cache Channel",
        )
        campaign1: DropCampaign = DropCampaign.objects.create(
            twitch_id="counter_cache_campaign_1",
            name="Counter Cache Campaign 1",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        campaign2: DropCampaign = DropCampaign.objects.create(
            twitch_id="counter_cache_campaign_2",
            name="Counter Cache Campaign 2",
            game=game,
            operation_names=["DropCampaignDetails"],
        )

        campaign1.allow_channels.add(channel)
        channel.refresh_from_db()
        assert channel.allowed_campaign_count == 1

        campaign2.allow_channels.add(channel)
        channel.refresh_from_db()
        assert channel.allowed_campaign_count == 2

        campaign1.allow_channels.remove(channel)
        channel.refresh_from_db()
        assert channel.allowed_campaign_count == 1

        campaign2.allow_channels.clear()
        channel.refresh_from_db()
        assert channel.allowed_campaign_count == 0

    def test_channel_allowed_campaign_count_updates_on_set(self) -> None:
        """Counter cache should stay in sync when allow_channels.set(...) is used."""
        game: Game = Game.objects.create(
            twitch_id="counter_cache_set_game",
            name="Counter Cache Set Game",
            display_name="Counter Cache Set Game",
        )
        channel1: Channel = Channel.objects.create(
            twitch_id="counter_cache_set_channel_1",
            name="countercachesetchannel1",
            display_name="Counter Cache Set Channel 1",
        )
        channel2: Channel = Channel.objects.create(
            twitch_id="counter_cache_set_channel_2",
            name="countercachesetchannel2",
            display_name="Counter Cache Set Channel 2",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="counter_cache_set_campaign",
            name="Counter Cache Set Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )

        campaign.allow_channels.set([channel1, channel2])
        channel1.refresh_from_db()
        channel2.refresh_from_db()
        assert channel1.allowed_campaign_count == 1
        assert channel2.allowed_campaign_count == 1

        campaign.allow_channels.set([channel2])
        channel1.refresh_from_db()
        channel2.refresh_from_db()
        assert channel1.allowed_campaign_count == 0
        assert channel2.allowed_campaign_count == 1

    @pytest.mark.django_db
    def test_dashboard_view(self, client: Client) -> None:
        """Test dashboard view returns 200 and has grouped campaign data in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:dashboard"))
        assert response.status_code == 200
        assert "campaigns_by_game" in response.context

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
    def test_dashboard_queries_use_indexes(self, client: Client) -> None:
        """Dashboard source queries should use indexes for active-window filtering."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_index_test",
            name="Org Index Test",
        )
        game: Game = Game.objects.create(
            twitch_id="game_index_test",
            name="Game Index Test",
            display_name="Game Index Test",
        )
        game.owners.add(org)

        # Add enough rows so the query planner has a reason to pick indexes.
        campaigns: list[DropCampaign] = []
        for i in range(250):
            campaigns.extend((
                DropCampaign(
                    twitch_id=f"inactive_old_{i}",
                    name=f"Inactive old {i}",
                    game=game,
                    operation_names=["DropCampaignDetails"],
                    start_at=now - timedelta(days=60),
                    end_at=now - timedelta(days=30),
                ),
                DropCampaign(
                    twitch_id=f"inactive_future_{i}",
                    name=f"Inactive future {i}",
                    game=game,
                    operation_names=["DropCampaignDetails"],
                    start_at=now + timedelta(days=30),
                    end_at=now + timedelta(days=60),
                ),
            ))
        campaigns.append(
            DropCampaign(
                twitch_id="active_for_dashboard_index_test",
                name="Active campaign",
                game=game,
                operation_names=["DropCampaignDetails"],
                start_at=now - timedelta(hours=1),
                end_at=now + timedelta(hours=1),
            ),
        )
        DropCampaign.objects.bulk_create(campaigns)

        reward_campaigns: list[RewardCampaign] = []
        for i in range(250):
            reward_campaigns.extend((
                RewardCampaign(
                    twitch_id=f"reward_inactive_old_{i}",
                    name=f"Reward inactive old {i}",
                    game=game,
                    starts_at=now - timedelta(days=60),
                    ends_at=now - timedelta(days=30),
                ),
                RewardCampaign(
                    twitch_id=f"reward_inactive_future_{i}",
                    name=f"Reward inactive future {i}",
                    game=game,
                    starts_at=now + timedelta(days=30),
                    ends_at=now + timedelta(days=60),
                ),
            ))
        reward_campaigns.append(
            RewardCampaign(
                twitch_id="reward_active_for_dashboard_index_test",
                name="Active reward campaign",
                game=game,
                starts_at=now - timedelta(hours=1),
                ends_at=now + timedelta(hours=1),
            ),
        )
        RewardCampaign.objects.bulk_create(reward_campaigns)

        active_campaigns_qs: QuerySet[DropCampaign] = DropCampaign.active_for_dashboard(
            now,
        )
        active_reward_campaigns_qs: QuerySet[RewardCampaign] = (
            RewardCampaign.active_for_dashboard(now)
        )

        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:dashboard"))
        assert response.status_code == 200

        campaigns_plan: str = active_campaigns_qs.explain()
        reward_plan: str = active_reward_campaigns_qs.explain()

        if connection.vendor == "sqlite":
            campaigns_uses_index: bool = "USING INDEX" in campaigns_plan.upper()
            rewards_uses_index: bool = "USING INDEX" in reward_plan.upper()
            campaigns_uses_expected_index: bool = (
                "tw_drop_start_end_idx" in campaigns_plan
                or "tw_drop_start_end_game_idx" in campaigns_plan
                or "tw_drop_start_desc_idx" in campaigns_plan
            )
            rewards_uses_expected_index: bool = (
                "tw_reward_starts_ends_idx" in reward_plan
                or "tw_reward_ends_starts_idx" in reward_plan
                or "tw_reward_starts_desc_idx" in reward_plan
            )
        elif connection.vendor == "postgresql":
            campaigns_uses_index = (
                "INDEX SCAN" in campaigns_plan.upper()
                or "BITMAP INDEX SCAN" in campaigns_plan.upper()
                or "INDEX ONLY SCAN" in campaigns_plan.upper()
            )
            rewards_uses_index = (
                "INDEX SCAN" in reward_plan.upper()
                or "BITMAP INDEX SCAN" in reward_plan.upper()
                or "INDEX ONLY SCAN" in reward_plan.upper()
            )
            campaigns_uses_expected_index = (
                "tw_drop_start_end_idx" in campaigns_plan
                or "tw_drop_start_end_game_idx" in campaigns_plan
                or "tw_drop_start_desc_idx" in campaigns_plan
            )
            rewards_uses_expected_index = (
                "tw_reward_starts_ends_idx" in reward_plan
                or "tw_reward_ends_starts_idx" in reward_plan
                or "tw_reward_starts_desc_idx" in reward_plan
            )
        else:
            pytest.skip(
                f"Unsupported DB vendor for index-plan assertion: {connection.vendor}",
            )

        assert campaigns_uses_index, campaigns_plan
        assert rewards_uses_index, reward_plan
        assert campaigns_uses_expected_index, campaigns_plan
        assert rewards_uses_expected_index, reward_plan

    @pytest.mark.django_db
    def test_dashboard_context_uses_prefetched_data_without_n_plus_one(self) -> None:
        """Dashboard context should not trigger extra queries when rendering-used attrs are accessed."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_dashboard_prefetch",
            name="Org Dashboard Prefetch",
        )
        game: Game = Game.objects.create(
            twitch_id="game_dashboard_prefetch",
            name="Game Dashboard Prefetch",
            display_name="Game Dashboard Prefetch",
        )
        game.owners.add(org)

        channel: Channel = Channel.objects.create(
            twitch_id="channel_dashboard_prefetch",
            name="channeldashboardprefetch",
            display_name="Channel Dashboard Prefetch",
        )

        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="campaign_dashboard_prefetch",
            name="Campaign Dashboard Prefetch",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        campaign.allow_channels.add(channel)

        RewardCampaign.objects.create(
            twitch_id="reward_dashboard_prefetch",
            name="Reward Dashboard Prefetch",
            game=game,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
        )

        dashboard_data: dict[str, Any] = DropCampaign.dashboard_context(now)
        campaigns_by_game: OrderedDict[str, dict[str, Any]] = dashboard_data[
            "campaigns_by_game"
        ]
        reward_campaigns: list[RewardCampaign] = list(
            dashboard_data["active_reward_campaigns"],
        )

        with CaptureQueriesContext(connection) as capture:
            game_bucket: dict[str, Any] = campaigns_by_game[game.twitch_id]
            _ = game_bucket["name"]
            _ = game_bucket["box_art"]
            _ = [owner.name for owner in game_bucket["owners"]]

            campaign_entry: dict[str, Any] = game_bucket["campaigns"][0]
            campaign_obj: DropCampaign = campaign_entry["campaign"]

            _ = campaign_obj.clean_name
            _ = campaign_obj.duration_iso
            _ = campaign_obj.start_at
            _ = campaign_obj.end_at
            _ = campaign_entry["image_url"]
            _ = campaign_entry["game_twitch_directory_url"]
            _ = [c.display_name for c in campaign_entry["allowed_channels"]]

            _ = [r.is_active for r in reward_campaigns]
            _ = [r.game.display_name if r.game else None for r in reward_campaigns]

        assert len(capture) == 0

    @pytest.mark.django_db
    def test_dashboard_uses_single_reward_image_for_campaign_card(self) -> None:
        """Dashboard campaign cards should show the reward image for one-reward campaigns."""
        now: datetime.datetime = timezone.now()

        game: Game = Game.objects.create(
            twitch_id="game_dashboard_single_reward_image",
            name="game_dashboard_single_reward_image",
            display_name="Game Dashboard Single Reward Image",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="campaign_dashboard_single_reward_image",
            name="Campaign Dashboard Single Reward Image",
            game=game,
            operation_names=["DropCampaignDetails"],
            image_url="https://example.com/campaign.png",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit_dashboard_single_reward_image",
            name="Benefit Dashboard Single Reward Image",
            image_asset_url="https://example.com/benefit.png",
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="drop_dashboard_single_reward_image",
            name="Drop Dashboard Single Reward Image",
            campaign=campaign,
        )
        drop.benefits.add(benefit)

        campaigns_by_game: OrderedDict[str, dict[str, Any]] = (
            DropCampaign.campaigns_by_game_for_dashboard(now)
        )

        assert (
            campaigns_by_game[game.twitch_id]["campaigns"][0]["image_url"]
            == "https://example.com/benefit.png"
        )

    @pytest.mark.django_db
    def test_dashboard_query_plans_reference_expected_index_names(self) -> None:
        """Dashboard active-window plans should mention concrete index names."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_index_name_test",
            name="Org Index Name Test",
        )
        game: Game = Game.objects.create(
            twitch_id="game_index_name_test",
            name="Game Index Name Test",
            display_name="Game Index Name Test",
        )
        game.owners.add(org)

        DropCampaign.objects.create(
            twitch_id="active_for_dashboard_index_name_test",
            name="Active campaign index-name test",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        RewardCampaign.objects.create(
            twitch_id="reward_active_for_dashboard_index_name_test",
            name="Active reward campaign index-name test",
            game=game,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
        )

        # Keep this assertion scoped to engines whose plans typically include index names.
        if connection.vendor not in {"sqlite", "postgresql"}:
            pytest.skip(
                f"Unsupported DB vendor for index-name plan assertion: {connection.vendor}",
            )

        def _index_names(table_name: str) -> set[str]:
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor,
                    table_name,
                )

            names: set[str] = set()
            for name, meta in constraints.items():
                if not meta.get("index"):
                    continue
                names.add(name)
            return names

        expected_drop_indexes: set[str] = {
            "tw_drop_start_desc_idx",
            "tw_drop_start_end_idx",
            "tw_drop_start_end_game_idx",
        }
        expected_reward_indexes: set[str] = {
            "tw_reward_starts_desc_idx",
            "tw_reward_starts_ends_idx",
            "tw_reward_ends_starts_idx",
        }

        drop_index_names: set[str] = _index_names(DropCampaign._meta.db_table)
        reward_index_names: set[str] = _index_names(RewardCampaign._meta.db_table)

        missing_drop_indexes: set[str] = expected_drop_indexes - drop_index_names
        missing_reward_indexes: set[str] = expected_reward_indexes - reward_index_names

        assert not missing_drop_indexes, (
            "Missing expected DropCampaign dashboard indexes: "
            f"{sorted(missing_drop_indexes)}"
        )
        assert not missing_reward_indexes, (
            "Missing expected RewardCampaign dashboard indexes: "
            f"{sorted(missing_reward_indexes)}"
        )

        campaigns_plan: str = DropCampaign.active_for_dashboard(now).explain().lower()
        reward_plan: str = RewardCampaign.active_for_dashboard(now).explain().lower()

        assert any(name.lower() in campaigns_plan for name in expected_drop_indexes), (
            "DropCampaign active-for-dashboard plan did not reference an expected "
            "named dashboard index. "
            f"Expected one of {sorted(expected_drop_indexes)}. Plan={campaigns_plan}"
        )
        assert any(name.lower() in reward_plan for name in expected_reward_indexes), (
            "RewardCampaign active-for-dashboard plan did not reference an expected "
            "named dashboard index. "
            f"Expected one of {sorted(expected_reward_indexes)}. Plan={reward_plan}"
        )

    @pytest.mark.django_db
    def test_dashboard_active_window_composite_indexes_exist(self) -> None:
        """Dashboard active-window filters should have supporting composite indexes."""
        with connection.cursor() as cursor:
            drop_constraints = connection.introspection.get_constraints(
                cursor,
                DropCampaign._meta.db_table,
            )
            reward_constraints = connection.introspection.get_constraints(
                cursor,
                RewardCampaign._meta.db_table,
            )

        def _index_columns(constraints: dict[str, Any]) -> list[tuple[str, ...]]:
            columns: list[tuple[str, ...]] = []
            for meta in constraints.values():
                if not meta.get("index"):
                    continue
                index_columns: list[str] = meta.get("columns") or []
                columns.append(tuple(index_columns))
            return columns

        drop_index_columns: list[tuple[str, ...]] = _index_columns(drop_constraints)
        reward_index_columns: list[tuple[str, ...]] = _index_columns(
            reward_constraints,
        )

        assert ("start_at", "end_at") in drop_index_columns
        assert ("starts_at", "ends_at") in reward_index_columns

    @pytest.mark.django_db
    def test_dashboard_query_count_stays_flat_with_more_data(
        self,
        client: Client,
    ) -> None:
        """Dashboard should avoid N+1 queries as campaign volume grows."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_query_count",
            name="Org Query Count",
        )
        game: Game = Game.objects.create(
            twitch_id="game_query_count",
            name="game_query_count",
            display_name="Game Query Count",
        )
        game.owners.add(org)

        def _capture_dashboard_select_count() -> int:
            with CaptureQueriesContext(connection) as queries:
                response: _MonkeyPatchedWSGIResponse = client.get(
                    reverse("twitch:dashboard"),
                )
                assert response.status_code == 200

            select_queries: list[str] = [
                query_info["sql"]
                for query_info in queries.captured_queries
                if query_info["sql"].lstrip().upper().startswith("SELECT")
            ]
            return len(select_queries)

        # Baseline: one active drop campaign and one active reward campaign.
        base_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="baseline_campaign",
            name="Baseline campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        base_channel: Channel = Channel.objects.create(
            twitch_id="baseline_channel",
            name="baselinechannel",
            display_name="BaselineChannel",
        )
        base_campaign.allow_channels.add(base_channel)

        RewardCampaign.objects.create(
            twitch_id="baseline_reward_campaign",
            name="Baseline reward campaign",
            game=game,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
            summary="Baseline summary",
            external_url="https://example.com/reward/baseline",
        )

        baseline_select_count: int = _capture_dashboard_select_count()

        # Scale up active dashboard data substantially.
        extra_campaigns: list[DropCampaign] = [
            DropCampaign(
                twitch_id=f"scaled_campaign_{i}",
                name=f"Scaled campaign {i}",
                game=game,
                operation_names=["DropCampaignDetails"],
                start_at=now - timedelta(hours=2),
                end_at=now + timedelta(hours=2),
            )
            for i in range(12)
        ]
        DropCampaign.objects.bulk_create(extra_campaigns)

        for i, campaign in enumerate(
            DropCampaign.objects.filter(
                twitch_id__startswith="scaled_campaign_",
            ).order_by("twitch_id"),
        ):
            channel: Channel = Channel.objects.create(
                twitch_id=f"scaled_channel_{i}",
                name=f"scaledchannel{i}",
                display_name=f"ScaledChannel{i}",
            )
            campaign.allow_channels.add(channel)

        extra_rewards: list[RewardCampaign] = [
            RewardCampaign(
                twitch_id=f"scaled_reward_{i}",
                name=f"Scaled reward {i}",
                game=game,
                starts_at=now - timedelta(hours=2),
                ends_at=now + timedelta(hours=2),
                summary=f"Scaled summary {i}",
                external_url=f"https://example.com/reward/{i}",
            )
            for i in range(12)
        ]
        RewardCampaign.objects.bulk_create(extra_rewards)

        scaled_select_count: int = _capture_dashboard_select_count()

        assert scaled_select_count <= baseline_select_count + 2, (
            "Dashboard SELECT query count grew with data volume; possible N+1 regression. "
            f"baseline={baseline_select_count}, scaled={scaled_select_count}"
        )

    @pytest.mark.django_db
    def test_dashboard_field_access_after_prefetch_has_no_extra_selects(self) -> None:
        """Dashboard-accessed fields should not trigger deferred model SELECT queries."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_dashboard_field_access",
            name="Org Dashboard Field Access",
        )
        game: Game = Game.objects.create(
            twitch_id="game_dashboard_field_access",
            name="Game Dashboard Field Access",
            display_name="Game Dashboard Field Access",
            slug="game-dashboard-field-access",
            box_art="https://example.com/game-box-art.jpg",
        )
        game.owners.add(org)

        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="campaign_dashboard_field_access",
            name="Campaign Dashboard Field Access",
            game=game,
            operation_names=["DropCampaignDetails"],
            image_url="https://example.com/campaign.jpg",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        channel: Channel = Channel.objects.create(
            twitch_id="channel_dashboard_field_access",
            name="channeldashboardfieldaccess",
            display_name="Channel Dashboard Field Access",
        )
        campaign.allow_channels.add(channel)

        RewardCampaign.objects.create(
            twitch_id="reward_dashboard_field_access",
            name="Reward Dashboard Field Access",
            brand="Brand",
            summary="Reward summary",
            is_sitewide=False,
            game=game,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
        )

        dashboard_rewards_qs: QuerySet[RewardCampaign] = (
            RewardCampaign.active_for_dashboard(now)
        )
        dashboard_campaigns_qs: QuerySet[DropCampaign] = (
            DropCampaign.active_for_dashboard(now)
        )

        rewards_list: list[RewardCampaign] = list(dashboard_rewards_qs)
        list(dashboard_campaigns_qs)

        with CaptureQueriesContext(connection) as queries:
            # Use pre-evaluated queryset to avoid capturing initial SELECT queries
            grouped = DropCampaign.grouped_by_game(dashboard_campaigns_qs)

            for reward in rewards_list:
                _ = reward.twitch_id
                _ = reward.name
                _ = reward.brand
                _ = reward.summary
                _ = reward.starts_at
                _ = reward.ends_at
                _ = reward.is_sitewide
                _ = reward.is_active
                if reward.game:
                    _ = reward.game.twitch_id
                    _ = reward.game.display_name

        assert game.twitch_id in grouped

        deferred_selects: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
        ]
        assert not deferred_selects, (
            "Dashboard model field access triggered unexpected deferred SELECT queries. "
            f"Queries: {deferred_selects}"
        )

    @pytest.mark.django_db
    def test_dashboard_grouping_reuses_selected_game_relation(self) -> None:
        """Dashboard grouping should not issue extra standalone Game queries."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_grouping_no_extra_game_select",
            name="Org Grouping No Extra Game Select",
        )
        game: Game = Game.objects.create(
            twitch_id="game_grouping_no_extra_game_select",
            name="game_grouping_no_extra_game_select",
            display_name="Game Grouping No Extra Game Select",
        )
        game.owners.add(org)

        campaigns: list[DropCampaign] = [
            DropCampaign(
                twitch_id=f"grouping_campaign_{i}",
                name=f"Grouping campaign {i}",
                game=game,
                operation_names=["DropCampaignDetails"],
                start_at=now - timedelta(hours=1),
                end_at=now + timedelta(hours=1),
            )
            for i in range(5)
        ]
        DropCampaign.objects.bulk_create(campaigns)

        with CaptureQueriesContext(connection) as queries:
            grouped: dict[str, dict[str, Any]] = (
                DropCampaign.campaigns_by_game_for_dashboard(now)
            )

        assert game.twitch_id in grouped
        assert len(grouped[game.twitch_id]["campaigns"]) == 5

        game_select_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
            and 'from "twitch_game"' in query_info["sql"].lower()
            and " join " not in query_info["sql"].lower()
        ]

        assert not game_select_queries, (
            "Dashboard grouping should reuse DropCampaign.active_for_dashboard() "
            "select_related game rows instead of standalone Game SELECTs. "
            f"Queries: {game_select_queries}"
        )

    @pytest.mark.django_db
    def test_dashboard_avoids_n_plus_one_game_queries_in_drop_loop(
        self,
        client: Client,
    ) -> None:
        """Dashboard should not issue per-campaign Game SELECTs while rendering drops."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_no_n_plus_one_game",
            name="Org No N+1 Game",
        )
        game: Game = Game.objects.create(
            twitch_id="game_no_n_plus_one_game",
            name="game_no_n_plus_one_game",
            display_name="Game No N+1 Game",
        )
        game.owners.add(org)

        campaigns: list[DropCampaign] = [
            DropCampaign(
                twitch_id=f"no_n_plus_one_campaign_{i}",
                name=f"No N+1 campaign {i}",
                game=game,
                operation_names=["DropCampaignDetails"],
                start_at=now - timedelta(hours=2),
                end_at=now + timedelta(hours=2),
            )
            for i in range(10)
        ]
        DropCampaign.objects.bulk_create(campaigns)

        with CaptureQueriesContext(connection) as queries:
            response: _MonkeyPatchedWSGIResponse = client.get(
                reverse("twitch:dashboard"),
            )
            assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        grouped_campaigns: list[dict[str, Any]] = context["campaigns_by_game"][
            game.twitch_id
        ]["campaigns"]
        assert grouped_campaigns
        assert all(
            "game_display_name" in campaign_data for campaign_data in grouped_campaigns
        )
        assert all(
            "game_twitch_directory_url" in campaign_data
            for campaign_data in grouped_campaigns
        )

        game_select_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
            and "twitch_game" in query_info["sql"].lower()
            and "join" not in query_info["sql"].lower()
        ]

        assert len(game_select_queries) <= 1, (
            "Expected at most one standalone Game SELECT for dashboard drop grouping; "
            f"got {len(game_select_queries)}. Queries: {game_select_queries}"
        )

    @pytest.mark.django_db
    def test_dashboard_avoids_n_plus_one_game_queries_with_multiple_games(
        self,
        client: Client,
    ) -> None:
        """Dashboard should keep standalone Game SELECTs bounded with many campaigns and games."""
        now: datetime.datetime = timezone.now()

        game_ids: list[str] = []
        for i in range(5):
            org: Organization = Organization.objects.create(
                twitch_id=f"org_multi_game_{i}",
                name=f"Org Multi Game {i}",
            )
            game: Game = Game.objects.create(
                twitch_id=f"game_multi_game_{i}",
                name=f"game_multi_game_{i}",
                display_name=f"Game Multi Game {i}",
            )
            game.owners.add(org)
            game_ids.append(game.twitch_id)

            campaigns: list[DropCampaign] = [
                DropCampaign(
                    twitch_id=f"multi_game_campaign_{i}_{j}",
                    name=f"Multi game campaign {i}-{j}",
                    game=game,
                    operation_names=["DropCampaignDetails"],
                    start_at=now - timedelta(hours=2),
                    end_at=now + timedelta(hours=2),
                )
                for j in range(20)
            ]
            DropCampaign.objects.bulk_create(campaigns)

        with CaptureQueriesContext(connection) as queries:
            response: _MonkeyPatchedWSGIResponse = client.get(
                reverse("twitch:dashboard"),
            )
            assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        campaigns_by_game: dict[str, Any] = context["campaigns_by_game"]
        for game_id in game_ids:
            assert game_id in campaigns_by_game
            grouped_campaigns: list[dict[str, Any]] = campaigns_by_game[game_id][
                "campaigns"
            ]
            assert len(grouped_campaigns) == 20
            assert all(
                "game_display_name" in campaign_data
                for campaign_data in grouped_campaigns
            )
            assert all(
                "game_twitch_directory_url" in campaign_data
                for campaign_data in grouped_campaigns
            )

        game_select_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
            and "twitch_game" in query_info["sql"].lower()
            and "join" not in query_info["sql"].lower()
        ]

        assert len(game_select_queries) <= 1, (
            "Expected a bounded number of standalone Game SELECTs for dashboard grouping; "
            f"got {len(game_select_queries)}. Queries: {game_select_queries}"
        )

    @pytest.mark.django_db
    def test_dashboard_does_not_refresh_dropcampaign_rows_for_image_dimensions(
        self,
        client: Client,
    ) -> None:
        """Dashboard should not issue per-row DropCampaign refreshes for image dimensions."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_image_dimensions",
            name="Org Image Dimensions",
        )
        game: Game = Game.objects.create(
            twitch_id="game_image_dimensions",
            name="game_image_dimensions",
            display_name="Game Image Dimensions",
        )
        game.owners.add(org)

        # 1x1 transparent PNG
        png_1x1: bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        campaigns: list[DropCampaign] = []
        for i in range(3):
            campaign: DropCampaign = DropCampaign.objects.create(
                twitch_id=f"image_dim_campaign_{i}",
                name=f"Image dim campaign {i}",
                game=game,
                operation_names=["DropCampaignDetails"],
                start_at=now - timedelta(hours=2),
                end_at=now + timedelta(hours=2),
            )
            assert campaign.image_file is not None
            campaign.image_file.save(
                f"image_dim_campaign_{i}.png",
                ContentFile(png_1x1),
                save=True,
            )
            campaigns.append(campaign)

        with CaptureQueriesContext(connection) as queries:
            response: _MonkeyPatchedWSGIResponse = client.get(
                reverse("twitch:dashboard"),
            )
            assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        grouped_campaigns: list[dict[str, Any]] = context["campaigns_by_game"][
            game.twitch_id
        ]["campaigns"]
        assert len(grouped_campaigns) == len(campaigns)

        per_row_refresh_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
            and 'from "twitch_dropcampaign"' in query_info["sql"].lower()
            and 'where "twitch_dropcampaign"."id" =' in query_info["sql"].lower()
        ]

        assert not per_row_refresh_queries, (
            "Dashboard unexpectedly refreshed DropCampaign rows one-by-one while "
            "resolving image dimensions. Queries: "
            f"{per_row_refresh_queries}"
        )

    @pytest.mark.django_db
    def test_dashboard_does_not_refresh_game_rows_for_box_art_dimensions(
        self,
        client: Client,
    ) -> None:
        """Dashboard should not issue per-row Game refreshes for box art dimensions."""
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="org_box_art_dimensions",
            name="Org Box Art Dimensions",
        )
        game: Game = Game.objects.create(
            twitch_id="game_box_art_dimensions",
            name="game_box_art_dimensions",
            display_name="Game Box Art Dimensions",
        )
        game.owners.add(org)

        # 1x1 transparent PNG
        png_1x1: bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        assert game.box_art_file is not None
        game.box_art_file.save(
            "game_box_art_dimensions.png",
            ContentFile(png_1x1),
            save=True,
        )

        DropCampaign.objects.create(
            twitch_id="game_box_art_campaign",
            name="Game box art campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=2),
        )

        with CaptureQueriesContext(connection) as queries:
            response: _MonkeyPatchedWSGIResponse = client.get(
                reverse("twitch:dashboard"),
            )
            assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        campaigns_by_game: dict[str, Any] = context["campaigns_by_game"]
        assert game.twitch_id in campaigns_by_game

        per_row_refresh_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
            and 'from "twitch_game"' in query_info["sql"].lower()
            and 'where "twitch_game"."id" =' in query_info["sql"].lower()
        ]

        assert not per_row_refresh_queries, (
            "Dashboard unexpectedly refreshed Game rows one-by-one while resolving "
            "box art dimensions. Queries: "
            f"{per_row_refresh_queries}"
        )

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
                is_fully_imported=True,
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
            is_fully_imported=True,
        )

        # Create upcoming campaign
        DropCampaign.objects.create(
            twitch_id="upcoming",
            name="Upcoming Campaign",
            game=game,
            start_at=now + timedelta(days=5),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        # Create expired campaign
        DropCampaign.objects.create(
            twitch_id="expired",
            name="Expired Campaign",
            game=game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
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
        now = timezone.now()

        # Create active campaign
        DropCampaign.objects.create(
            twitch_id="active",
            name="Active Campaign",
            game=game,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        # Create upcoming campaign
        DropCampaign.objects.create(
            twitch_id="upcoming",
            name="Upcoming Campaign",
            game=game,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        # Create expired campaign
        DropCampaign.objects.create(
            twitch_id="expired",
            name="Expired Campaign",
            game=game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
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
            is_fully_imported=True,
        )

        # Create upcoming campaign
        DropCampaign.objects.create(
            twitch_id="upcoming",
            name="Upcoming Campaign",
            game=game,
            start_at=now + timedelta(days=5),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        # Create expired campaign
        _expired_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="expired",
            name="Expired Campaign",
            game=game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
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
            is_fully_imported=True,
        )
        DropCampaign.objects.create(
            twitch_id="c2",
            name="Campaign 2",
            game=game1,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )

        # Create campaign for game 2
        DropCampaign.objects.create(
            twitch_id="c3",
            name="Campaign 3",
            game=game2,
            start_at=now - timedelta(days=5),
            end_at=now + timedelta(days=5),
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
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
                is_fully_imported=True,
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
    def test_drop_campaign_detail_view(self, client: Client, db: None) -> None:
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
    def test_drop_campaign_detail_badge_queries_stay_flat(self, client: Client) -> None:
        """Campaign detail should avoid N+1 ChatBadge lookups across many badge drops."""
        now: datetime.datetime = timezone.now()
        game: Game = Game.objects.create(
            twitch_id="g-badge-flat",
            name="Game",
            display_name="Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="c-badge-flat",
            name="Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )

        badge_set = ChatBadgeSet.objects.create(set_id="badge-flat")

        def _create_badge_drop(i: int) -> None:
            drop = TimeBasedDrop.objects.create(
                twitch_id=f"flat-drop-{i}",
                name=f"Drop {i}",
                campaign=campaign,
                required_minutes_watched=i,
                required_subs=0,
                start_at=now - timedelta(hours=2),
                end_at=now + timedelta(hours=2),
            )
            title = f"Badge {i}"
            benefit = DropBenefit.objects.create(
                twitch_id=f"flat-benefit-{i}",
                name=title,
                distribution_type="BADGE",
            )
            drop.benefits.add(benefit)
            ChatBadge.objects.create(
                badge_set=badge_set,
                badge_id=str(i),
                image_url_1x=f"https://example.com/{i}/1x.png",
                image_url_2x=f"https://example.com/{i}/2x.png",
                image_url_4x=f"https://example.com/{i}/4x.png",
                title=title,
                description=f"Badge description {i}",
            )

        def _select_count() -> int:
            url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
            with CaptureQueriesContext(connection) as capture:
                response: _MonkeyPatchedWSGIResponse = client.get(url)
                assert response.status_code == 200
            return sum(
                1
                for query in capture.captured_queries
                if query["sql"].lstrip().upper().startswith("SELECT")
            )

        _create_badge_drop(1)
        baseline_selects: int = _select_count()

        for i in range(2, 22):
            _create_badge_drop(i)

        expanded_selects: int = _select_count()

        # Query volume should remain effectively constant as badge-drop count grows.
        assert expanded_selects <= baseline_selects + 2

    @pytest.mark.django_db
    def test_games_grid_view(self, client: Client) -> None:
        """Test games grid view returns 200 and has games in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:games_grid"))
        assert response.status_code == 200
        assert "games" in response.context

    @pytest.mark.django_db
    def test_games_grid_view_groups_only_games_with_campaigns(
        self,
        client: Client,
    ) -> None:
        """Games grid should group only games that actually have campaigns."""
        now: datetime.datetime = timezone.now()

        org_with_campaign: Organization = Organization.objects.create(
            twitch_id="org-games-grid-with-campaign",
            name="Org Games Grid With Campaign",
        )
        org_without_campaign: Organization = Organization.objects.create(
            twitch_id="org-games-grid-without-campaign",
            name="Org Games Grid Without Campaign",
        )

        game_with_campaign: Game = Game.objects.create(
            twitch_id="game-games-grid-with-campaign",
            name="Game Games Grid With Campaign",
            display_name="Game Games Grid With Campaign",
        )
        game_with_campaign.owners.add(org_with_campaign)

        game_without_campaign: Game = Game.objects.create(
            twitch_id="game-games-grid-without-campaign",
            name="Game Games Grid Without Campaign",
            display_name="Game Games Grid Without Campaign",
        )
        game_without_campaign.owners.add(org_without_campaign)

        DropCampaign.objects.create(
            twitch_id="campaign-games-grid-with-campaign",
            name="Campaign Games Grid With Campaign",
            game=game_with_campaign,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:games_grid"))
        assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        games: list[Game] = list(context["games"])
        games_by_org: OrderedDict[Organization, list[dict[str, Game]]] = context[
            "games_by_org"
        ]

        game_ids: set[str] = {game.twitch_id for game in games}
        assert game_with_campaign.twitch_id in game_ids
        assert game_without_campaign.twitch_id not in game_ids

        grouped_ids: set[str] = {
            item["game"].twitch_id
            for grouped_games in games_by_org.values()
            for item in grouped_games
        }
        assert game_with_campaign.twitch_id in grouped_ids
        assert game_without_campaign.twitch_id not in grouped_ids

    @pytest.mark.django_db
    def test_games_list_view(self, client: Client) -> None:
        """Test games list view returns 200 and has games in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:games_list"))
        assert response.status_code == 200
        assert "games" in response.context

    @pytest.mark.django_db
    def test_game_detail_view(self, client: Client, db: None) -> None:
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
    def test_game_detail_campaign_query_plan_uses_game_end_index(self) -> None:
        """Game-detail campaign list query should use the game/end_at composite index."""
        now: datetime.datetime = timezone.now()

        game: Game = Game.objects.create(
            twitch_id="game_detail_idx_game",
            name="Game Detail Index Game",
            display_name="Game Detail Index Game",
        )

        campaigns: list[DropCampaign] = []
        for i in range(200):
            campaigns.extend((
                DropCampaign(
                    twitch_id=f"game_detail_idx_old_{i}",
                    name=f"Old campaign {i}",
                    game=game,
                    operation_names=["DropCampaignDetails"],
                    start_at=now - timedelta(days=90),
                    end_at=now - timedelta(days=60),
                ),
                DropCampaign(
                    twitch_id=f"game_detail_idx_future_{i}",
                    name=f"Future campaign {i}",
                    game=game,
                    operation_names=["DropCampaignDetails"],
                    start_at=now + timedelta(days=60),
                    end_at=now + timedelta(days=90),
                ),
            ))
        campaigns.append(
            DropCampaign(
                twitch_id="game_detail_idx_active",
                name="Active campaign",
                game=game,
                operation_names=["DropCampaignDetails"],
                start_at=now - timedelta(hours=1),
                end_at=now + timedelta(hours=1),
            ),
        )
        DropCampaign.objects.bulk_create(campaigns)

        plan: str = DropCampaign.for_game_detail(game).explain().lower()

        if connection.vendor in {"sqlite", "postgresql"}:
            assert "tw_drop_game_end_desc_idx" in plan, plan
        else:
            pytest.skip(
                f"Unsupported DB vendor for index-name plan assertion: {connection.vendor}",
            )

    @pytest.mark.django_db
    def test_game_detail_image_aspect_ratio(self, client: Client, db: None) -> None:
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
        """Game detail view should no longer expose debug JSON payload in context."""
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

        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200
        assert "game_data" not in response.context

    @pytest.mark.django_db
    def test_org_list_view(self, client: Client) -> None:
        """Test org list view returns 200 and has orgs in context."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:org_list"))
        assert response.status_code == 200
        assert "orgs" in response.context

    @pytest.mark.django_db
    def test_org_list_queryset_only_selects_rendered_fields(self) -> None:
        """Organization list queryset should defer non-rendered fields."""
        org: Organization = Organization.objects.create(
            twitch_id="org_list_fields",
            name="Org List Fields",
        )

        fetched_org: Organization | None = (
            Organization
            .for_list_view()
            .filter(
                twitch_id=org.twitch_id,
            )
            .first()
        )

        assert fetched_org is not None
        deferred_fields: set[str] = fetched_org.get_deferred_fields()
        assert "added_at" in deferred_fields
        assert "updated_at" in deferred_fields
        assert "twitch_id" not in deferred_fields
        assert "name" not in deferred_fields

    @pytest.mark.django_db
    def test_organization_detail_view(self, client: Client, db: None) -> None:
        """Test organization detail view returns 200 and has organization in context."""
        org: Organization = Organization.objects.create(twitch_id="o1", name="Org1")
        url: str = reverse("twitch:organization_detail", args=[org.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200
        assert "organization" in response.context

    @pytest.mark.django_db
    def test_organization_detail_queryset_only_selects_rendered_fields(self) -> None:
        """Organization detail queryset should defer non-rendered organization fields."""
        org: Organization = Organization.objects.create(
            twitch_id="org_detail_fields",
            name="Org Detail Fields",
        )
        Game.objects.create(
            twitch_id="org_detail_game_fields",
            name="Org Detail Game Fields",
            display_name="Org Detail Game Fields",
        ).owners.add(org)

        fetched_org: Organization | None = (
            Organization.for_detail_view().filter(twitch_id=org.twitch_id).first()
        )

        assert fetched_org is not None
        deferred_fields: set[str] = fetched_org.get_deferred_fields()
        assert "twitch_id" not in deferred_fields
        assert "name" not in deferred_fields
        assert "added_at" not in deferred_fields
        assert "updated_at" not in deferred_fields

    @pytest.mark.django_db
    def test_organization_detail_prefetched_games_do_not_trigger_extra_queries(
        self,
    ) -> None:
        """Organization detail should prefetch games used by the template."""
        org: Organization = Organization.objects.create(
            twitch_id="org_detail_prefetch",
            name="Org Detail Prefetch",
        )
        game: Game = Game.objects.create(
            twitch_id="org_detail_prefetch_game",
            name="Org Detail Prefetch Game",
            display_name="Org Detail Prefetch Game",
            slug="org-detail-prefetch-game",
        )
        game.owners.add(org)

        fetched_org: Organization = Organization.for_detail_view().get(
            twitch_id=org.twitch_id,
        )

        games_for_detail: list[Game] = list(
            getattr(fetched_org, "games_for_detail", []),
        )
        assert len(games_for_detail) == 1

        with CaptureQueriesContext(connection) as queries:
            game_row: Game = games_for_detail[0]
            _ = game_row.twitch_id
            _ = game_row.display_name
            _ = game_row.name
            _ = str(game_row)

        select_queries: list[str] = [
            query_info["sql"]
            for query_info in queries.captured_queries
            if query_info["sql"].lstrip().upper().startswith("SELECT")
        ]
        assert not select_queries, (
            "Organization detail prefetched games triggered unexpected SELECT queries. "
            f"Queries: {select_queries}"
        )

    @pytest.mark.django_db
    def test_channel_detail_view(self, client: Client, db: None) -> None:
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
        """Test docs RSS view returns 200."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("core:docs_rss"))
        assert response.status_code == 200

        # Add Game with running campaign to ensure it's included in the RSS feed
        game: Game = Game.objects.create(
            twitch_id="g-rss",
            name="Game RSS",
            display_name="Game RSS",
        )

        DropCampaign.objects.create(
            twitch_id="c-rss",
            name="Campaign RSS",
            game=game,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1),
            operation_names=["DropCampaignDetails"],
        )

        response = client.get(reverse("core:docs_rss"))
        assert response.status_code == 200
        assert "g-rss" in response.content.decode("utf-8")


@pytest.mark.django_db
class TestRewardCampaignViews:
    """Tests for Twitch reward campaign list and detail views."""

    def _create_game(self, twitch_id: str, display_name: str) -> Game:
        game: Game = Game.objects.create(
            twitch_id=twitch_id,
            slug=twitch_id,
            name=display_name,
            display_name=display_name,
            box_art=f"https://example.com/{twitch_id}.png",
        )
        org: Organization = Organization.objects.create(
            twitch_id=f"{twitch_id}-org",
            name=f"{display_name} Org",
        )
        game.owners.add(org)
        return game

    def _create_reward_campaign(  # ruff:ignore[too-many-arguments]
        self,
        twitch_id: str,
        *,
        brand: str,
        name: str,
        game: Game | None,
        starts_delta: timedelta,
        ends_delta: timedelta,
    ) -> RewardCampaign:
        now: datetime.datetime = timezone.now()
        return RewardCampaign.objects.create(
            twitch_id=twitch_id,
            brand=brand,
            name=name,
            summary=f"{name} summary",
            instructions=f"{name} instructions",
            external_url=f"https://example.com/{twitch_id}/external",
            about_url=f"https://example.com/{twitch_id}/about",
            image_url=f"https://example.com/{twitch_id}.png",
            starts_at=now + starts_delta,
            ends_at=now + ends_delta,
            status="ACTIVE",
            is_sitewide=game is None,
            game=game,
        )

    def test_reward_campaign_list_renders_expired_campaigns(
        self,
        client: Client,
    ) -> None:
        """Render expired reward campaigns with feed and API links."""
        game: Game = self._create_game("reward-list-game", "Reward List Game")
        expired = self._create_reward_campaign(
            "reward-list-expired",
            brand="Expired Brand",
            name="Expired Reward",
            game=game,
            starts_delta=-timedelta(days=4),
            ends_delta=-timedelta(days=1),
        )
        self._create_reward_campaign(
            "reward-list-active",
            brand="Active Brand",
            name="Active Reward",
            game=game,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:reward_campaign_list"),
        )

        assert response.status_code == 200
        content: str = response.content.decode()
        assert "Expired Brand: Expired Reward" in content
        assert (
            reverse("twitch:reward_campaign_detail", args=[expired.twitch_id])
            in content
        )
        assert reverse("twitch:game_detail", args=[game.twitch_id]) in content
        assert reverse("core:reward_campaign_feed") in content
        assert reverse("api-v1:twitch-api-v1_list_reward_campaigns") in content
        assert response.context["reward_campaigns"].paginator.count == 2

    def test_reward_campaign_list_filters_status_and_game(
        self,
        client: Client,
    ) -> None:
        """Filter reward campaign context by status and game."""
        selected_game: Game = self._create_game("reward-filter-game", "Reward Filter")
        other_game: Game = self._create_game("reward-filter-other", "Reward Other")
        active = self._create_reward_campaign(
            "reward-filter-active",
            brand="Filter Brand",
            name="Active Filter Reward",
            game=selected_game,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )
        self._create_reward_campaign(
            "reward-filter-other-active",
            brand="Other Brand",
            name="Other Active Reward",
            game=other_game,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )
        self._create_reward_campaign(
            "reward-filter-expired",
            brand="Expired Brand",
            name="Expired Filter Reward",
            game=selected_game,
            starts_delta=-timedelta(days=4),
            ends_delta=-timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:reward_campaign_list")
            + f"?status=active&game={selected_game.twitch_id}",
        )

        assert response.status_code == 200
        campaigns = list(response.context["reward_campaigns"])
        assert campaigns == [active]
        assert response.context["selected_status"] == "active"
        assert response.context["selected_game"] == selected_game.twitch_id

    def test_reward_campaign_detail_renders_campaign_data(
        self,
        client: Client,
    ) -> None:
        """Render reward campaign detail fields and resource links."""
        game: Game = self._create_game("reward-detail-game", "Reward Detail Game")
        reward = self._create_reward_campaign(
            "reward-detail",
            brand="Detail Brand",
            name="Detail Reward",
            game=game,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:reward_campaign_detail", args=[reward.twitch_id]),
        )

        assert response.status_code == 200
        content: str = response.content.decode()
        assert "Detail Brand: Detail Reward" in content
        assert "Detail Reward summary" in content
        assert "Detail Reward instructions" in content
        assert reverse("twitch:game_detail", args=[game.twitch_id]) in content
        assert (
            reverse(
                "api-v1:twitch-api-v1_get_reward_campaign",
                args=[reward.twitch_id],
            )
            in content
        )
        assert reward.external_url in content
        assert reward.about_url in content
        assert response.context["is_active"] is True

    def test_reward_campaign_detail_404_for_missing_campaign(
        self,
        client: Client,
    ) -> None:
        """Return 404 for missing reward campaign detail pages."""
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:reward_campaign_detail", args=["missing-reward"]),
        )

        assert response.status_code == 404

    def test_reward_campaign_list_query_count_stays_flat(
        self,
        client: Client,
    ) -> None:
        """Reward campaign list should not issue N+1 queries as rows grow."""
        game: Game = self._create_game("reward-flat-game", "Reward Flat Game")

        def _select_count() -> int:
            with CaptureQueriesContext(connection) as ctx:
                response: _MonkeyPatchedWSGIResponse = client.get(
                    reverse("twitch:reward_campaign_list"),
                )
                assert response.status_code == 200
            return sum(
                1
                for query in ctx.captured_queries
                if query["sql"].lstrip().upper().startswith("SELECT")
            )

        self._create_reward_campaign(
            "reward-flat-base",
            brand="Flat Brand",
            name="Flat Base Reward",
            game=game,
            starts_delta=-timedelta(days=4),
            ends_delta=-timedelta(days=1),
        )
        baseline: int = _select_count()

        for index in range(10):
            self._create_reward_campaign(
                f"reward-flat-extra-{index}",
                brand="Flat Brand",
                name=f"Flat Extra Reward {index}",
                game=game,
                starts_delta=-timedelta(days=4),
                ends_delta=-timedelta(days=1),
            )

        scaled: int = _select_count()
        assert scaled <= baseline + 2, (
            "Reward campaign list SELECT count grew; possible N+1. "
            f"baseline={baseline}, scaled={scaled}"
        )

    def test_sitewide_rewards_returns_200(self, client: Client) -> None:
        """Site-wide rewards page renders successfully with only sitewide campaigns."""
        game: Game = self._create_game("sitewide-test-game", "Sitewide Test Game")

        # Site-wide reward (game=None, is_sitewide=True)
        sitewide = self._create_reward_campaign(
            "sitewide-active",
            brand="Sitewide Brand",
            name="Sitewide Active",
            game=None,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )

        # Game-specific reward (is_sitewide=False) - should NOT appear
        self._create_reward_campaign(
            "sitewide-game-specific",
            brand="Game Brand",
            name="Game Specific",
            game=game,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:sitewide_rewards"),
        )

        assert response.status_code == 200
        content: str = response.content.decode()

        # Site-wide campaign should be visible
        assert "Sitewide Brand: Sitewide Active" in content
        assert (
            reverse("twitch:reward_campaign_detail", args=[sitewide.twitch_id])
            in content
        )

        # Game-specific campaign should NOT appear
        assert "Game Brand: Game Specific" not in content

        # Feed links should be present
        assert reverse("core:sitewide_reward_feed") in content
        assert reverse("core:sitewide_reward_feed_atom") in content
        assert reverse("core:sitewide_reward_feed_discord") in content

    def test_sitewide_rewards_context_splits_by_status(
        self,
        client: Client,
    ) -> None:
        """Site-wide rewards page splits campaigns into active/upcoming/expired."""
        self._create_reward_campaign(
            "sitewide-active-ctx",
            brand="Active Brand",
            name="Active Reward",
            game=None,
            starts_delta=-timedelta(days=1),
            ends_delta=timedelta(days=1),
        )
        self._create_reward_campaign(
            "sitewide-upcoming-ctx",
            brand="Upcoming Brand",
            name="Upcoming Reward",
            game=None,
            starts_delta=timedelta(days=1),
            ends_delta=timedelta(days=3),
        )
        self._create_reward_campaign(
            "sitewide-expired-ctx",
            brand="Expired Brand",
            name="Expired Reward",
            game=None,
            starts_delta=-timedelta(days=4),
            ends_delta=-timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:sitewide_rewards"),
        )
        assert response.status_code == 200

        assert len(response.context["active_campaigns"]) == 1
        assert len(response.context["upcoming_campaigns"]) == 1
        assert len(response.context["expired_campaigns"]) == 1


@pytest.mark.django_db
class TestStatsView:
    """Tests for the Twitch stats view."""

    @pytest.fixture
    def stats_fixture(self) -> dict[str, Any]:
        """Create a small archive with known record holders for stats tests.

        Returns:
            Dict with the created objects for assertions.
        """
        now: datetime.datetime = timezone.now()

        org: Organization = Organization.objects.create(
            twitch_id="stats-org-1",
            name="Stats Org",
        )
        game_a: Game = Game.objects.create(
            twitch_id="stats-game-a",
            name="game_a",
            display_name="Game A",
        )
        game_b: Game = Game.objects.create(
            twitch_id="stats-game-b",
            name="game_b",
            display_name="Game B",
        )
        game_a.owners.add(org)
        game_b.owners.add(org)

        channel: Channel = Channel.objects.create(
            twitch_id="stats-channel-1",
            name="statschannel",
            display_name="Stats Channel",
        )

        # Longest campaign: exactly 5 full days.
        longest_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="stats-campaign-long",
            name="Longest Campaign Ever",
            game=game_a,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
            operation_names=["DropCampaignDetails"],
        )
        # Earliest drop.
        first_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="stats-campaign-first",
            name="First Campaign",
            game=game_b,
            start_at=now - timedelta(days=100),
            end_at=now - timedelta(days=99),
            operation_names=["DropCampaignDetails"],
        )
        # Most rewards: 3 unique benefits across two drops.
        reward_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="stats-campaign-rewards",
            name="Reward Hoarder",
            game=game_a,
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
            operation_names=["DropCampaignDetails"],
        )

        drop_long: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="stats-drop-long",
            name="Long Drop",
            campaign=longest_campaign,
            required_minutes_watched=240,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=5),
        )
        TimeBasedDrop.objects.create(
            twitch_id="stats-drop-first",
            name="First Drop",
            campaign=first_campaign,
            required_minutes_watched=60,
            start_at=now - timedelta(days=100),
            end_at=now - timedelta(days=99),
        )
        drop_reward_1: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="stats-drop-reward-1",
            name="Reward Drop 1",
            campaign=reward_campaign,
            required_minutes_watched=30,
        )
        drop_reward_2: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="stats-drop-reward-2",
            name="Reward Drop 2",
            campaign=reward_campaign,
            required_minutes_watched=45,
        )

        benefit_1: DropBenefit = DropBenefit.objects.create(
            twitch_id="stats-benefit-1",
            name="Benefit One",
        )
        benefit_2: DropBenefit = DropBenefit.objects.create(
            twitch_id="stats-benefit-2",
            name="Benefit Two",
        )
        benefit_3: DropBenefit = DropBenefit.objects.create(
            twitch_id="stats-benefit-3",
            name="Benefit Three",
        )

        drop_reward_1.benefits.add(benefit_1, benefit_2)
        drop_reward_2.benefits.add(benefit_2, benefit_3)
        drop_long.benefits.add(benefit_1)

        reward_campaign.allow_channels.add(channel)
        longest_campaign.allow_channels.add(channel)

        return {
            "org": org,
            "game_a": game_a,
            "game_b": game_b,
            "channel": channel,
            "longest_campaign": longest_campaign,
            "first_campaign": first_campaign,
            "reward_campaign": reward_campaign,
        }

    @staticmethod
    def _context_stats(
        response: _MonkeyPatchedWSGIResponse,
    ) -> dict[str, Any]:
        """Return the stats context dict from a response."""
        context = response.context
        if isinstance(context, list):
            context = context[-1]
        return context["stats"]

    def test_stats_view_returns_200(self, client: Client) -> None:
        """Stats view returns 200 with the expected template."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        assert response.status_code == 200
        assert response.templates[0].name == "twitch/stats.html"
        assert "stats" in response.context
        assert "totals" in response.context
        assert "leaderboards" in response.context

    def test_stats_empty_database(self, client: Client) -> None:
        """Stats view still renders cleanly when the archive is empty."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        assert response.status_code == 200
        counters = self._context_stats(response)["counters"]
        assert counters["campaigns"] == 0
        assert counters["drops"] == 0
        assert counters["games"] == 0
        assert self._context_stats(response)["records"] == []

    def test_stats_counters(
        self,
        client: Client,
        stats_fixture: dict[str, Any],
    ) -> None:
        """Totals match the seeded archive."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        counters = self._context_stats(response)["counters"]
        assert counters["campaigns"] == 3
        assert counters["drops"] == 4
        assert counters["benefits"] == 3
        assert counters["games"] == 2
        assert counters["organizations"] == 1
        assert counters["channels"] == 1
        assert counters["reward_campaigns"] == 0
        assert counters["active_campaigns"] == 1
        assert counters["upcoming_campaigns"] == 0
        assert counters["expired_campaigns"] == 2

    def test_stats_longest_campaign_record(
        self,
        client: Client,
        stats_fixture: dict[str, Any],
    ) -> None:
        """Longest campaign record points at the 5-day campaign."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        records = self._context_stats(response)["records"]
        longest = next(r for r in records if r["title"] == "Longest campaign")
        # Django's timesince joins number and unit with a non-breaking space.
        assert longest["value"].replace("\u00a0", " ") == "5 days"
        assert longest["detail"] == "Longest Campaign Ever"
        assert longest["url"] == reverse(
            "twitch:campaign_detail",
            args=["stats-campaign-long"],
        )

    def test_stats_first_drop_record(
        self,
        client: Client,
        stats_fixture: dict[str, Any],
    ) -> None:
        """First drop record points at the earliest campaign."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        records = self._context_stats(response)["records"]
        first = next(r for r in records if r["title"] == "First drop ever in db")
        assert first["detail"] == "First Campaign"

    def test_stats_game_most_drops(
        self,
        client: Client,
        stats_fixture: dict[str, Any],
    ) -> None:
        """Game A owns the most drops (3 drops across two campaigns)."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        records = self._context_stats(response)["records"]
        game_record = next(r for r in records if r["title"] == "Game with most drops")
        assert game_record["value"] == 3
        assert game_record["detail"] == "Game A"

    def test_stats_reward_hoarder_campaign(
        self,
        client: Client,
        stats_fixture: dict[str, Any],
    ) -> None:
        """Reward hoarder campaign has 3 unique benefits."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        records = self._context_stats(response)["records"]
        hoarder = next(r for r in records if r["title"] == "Reward hoarder campaign")
        assert hoarder["value"] == 3
        assert hoarder["detail"] == "Reward Hoarder"

    def test_stats_leaderboards(
        self,
        client: Client,
        stats_fixture: dict[str, Any],
    ) -> None:
        """Leaderboards render top entries and games-by-drops is correct."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:stats"))
        leaderboards = response.context["leaderboards"]
        by_drops = next(b for b in leaderboards if b["title"] == "Top games by drops")
        assert by_drops["rows"][0]["name"] == "Game A"
        assert by_drops["rows"][0]["count"] == 3


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
            seo_meta={
                "page_image": "https://example.com/image.jpg",
                "og_type": "article",
                "schema_data": {"@context": "https://schema.org"},
            },
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
            seo_meta={
                "page_image": "https://example.com/img.jpg",
                "og_type": "article",
                "schema_data": {},
                "breadcrumb_schema": breadcrumb,
                "pagination_info": [{"rel": "next", "url": "/page/2/"}],
                "published_date": now.isoformat(),
                "modified_date": now.isoformat(),
                "robots_directive": "noindex, follow",
            },
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

    def test_campaign_detail_meta_image_uses_single_reward_image(
        self,
        client: Client,
        game_with_campaign: dict[str, Any],
    ) -> None:
        """Campaign detail SEO image should prefer the reward image for one-reward campaigns."""
        campaign: DropCampaign = game_with_campaign["campaign"]
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="seo-single-reward-benefit",
            name="SEO Single Reward Benefit",
            image_asset_url="https://example.com/seo-benefit.png",
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="seo-single-reward-drop",
            name="SEO Single Reward Drop",
            campaign=campaign,
        )
        drop.benefits.add(benefit)

        url = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        assert response.status_code == 200
        assert response.context["page_image"] == "https://example.com/seo-benefit.png"

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

        now: datetime.datetime = timezone.now()
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp1",
            name="Test Campaign",
            description="Desc",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - datetime.timedelta(days=1),
            end_at=now + datetime.timedelta(days=1),
            is_fully_imported=True,
        )
        inactive_campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp2",
            name="Inactive Campaign",
            description="Desc",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - datetime.timedelta(days=10),
            end_at=now - datetime.timedelta(days=5),
            is_fully_imported=True,
        )

        kick_org: KickOrganization = KickOrganization.objects.create(
            kick_id="org1",
            name="Kick Org",
        )
        kick_cat: KickCategory = KickCategory.objects.create(
            kick_id=1,
            name="Kick Game",
            slug="kick-game",
        )
        kick_active: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="kcamp1",
            name="Kick Active Campaign",
            organization=kick_org,
            category=kick_cat,
            starts_at=now - datetime.timedelta(days=1),
            ends_at=now + datetime.timedelta(days=1),
            is_fully_imported=True,
        )
        kick_inactive: KickDropCampaign = KickDropCampaign.objects.create(
            kick_id="kcamp2",
            name="Kick Inactive Campaign",
            organization=kick_org,
            category=kick_cat,
            starts_at=now - datetime.timedelta(days=10),
            ends_at=now - datetime.timedelta(days=5),
            is_fully_imported=True,
        )

        badge: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="badge1")
        return {
            "org": org,
            "game": game,
            "channel": channel,
            "campaign": campaign,
            "inactive_campaign": inactive_campaign,
            "kick_active": kick_active,
            "kick_inactive": kick_inactive,
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

    def test_sitemap_contains_sitemap_index(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap index contains sitemap locations."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap.xml")
        content: str = response.content.decode()
        assert "<sitemapindex" in content
        assert "</sitemapindex>" in content
        assert "/sitemap-static.xml" in content
        assert "/sitemap-twitch-channels.xml" in content
        assert "/sitemap-twitch-drops.xml" in content
        assert "/sitemap-kick.xml" in content
        assert "/sitemap-youtube.xml" in content

        # Ensure at least one entry includes a lastmod (there are entities created by the fixture)
        assert "<lastmod>" in content

    def test_import_does_not_update_lastmod_on_repeated_imports(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Ensure repeated imports do not change sitemap lastmod timestamps."""
        command = Command()

        payload: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "17658559",
                    "inventory": {
                        "dropCampaignsInProgress": [
                            {
                                "id": "inventory-campaign-1",
                                "name": "Test Inventory Campaign",
                                "description": "Campaign from Inventory operation",
                                "startAt": "2025-01-01T00:00:00Z",
                                "endAt": "2025-12-31T23:59:59Z",
                                "accountLinkURL": "https://example.com/link",
                                "detailsURL": "https://example.com/details",
                                "imageURL": "https://example.com/campaign.png",
                                "status": "ACTIVE",
                                "self": {
                                    "isAccountConnected": True,
                                    "__typename": "DropCampaignSelfEdge",
                                },
                                "game": {
                                    "id": "inventory-game-1",
                                    "displayName": "Inventory Game",
                                    "boxArtURL": "https://example.com/boxart.png",
                                    "slug": "inventory-game",
                                    "name": "Inventory Game",
                                    "__typename": "Game",
                                },
                                "owner": {
                                    "id": "inventory-org-1",
                                    "name": "Inventory Organization",
                                    "__typename": "Organization",
                                },
                                "timeBasedDrops": [],
                                "eventBasedDrops": None,
                                "__typename": "DropCampaign",
                            },
                        ],
                        "gameEventDrops": None,
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {"operationName": "Inventory"},
        }

        def _lastmod_values() -> tuple[
            datetime.datetime | None,
            datetime.datetime | None,
        ]:
            twitch_drops_lastmod = max(
                [
                    dt
                    for dt in [
                        DropCampaign.objects.aggregate(max=Max("updated_at"))["max"],
                        RewardCampaign.objects.aggregate(max=Max("updated_at"))["max"],
                    ]
                    if dt is not None
                ],
                default=None,
            )
            twitch_others_lastmod = max(
                [
                    dt
                    for dt in [
                        Game.objects.aggregate(max=Max("updated_at"))["max"],
                        Organization.objects.aggregate(max=Max("updated_at"))["max"],
                        ChatBadgeSet.objects.aggregate(max=Max("updated_at"))["max"],
                    ]
                    if dt is not None
                ],
                default=None,
            )
            return twitch_drops_lastmod, twitch_others_lastmod

        # Initial import
        success, _ = command.process_responses(
            responses=[payload],
            file_path=Path("test_inventory.json"),
            options={},
        )
        assert success is True

        first_drops, first_others = _lastmod_values()

        # Second import should not change lastmod values for related models
        success, _ = command.process_responses(
            responses=[payload],
            file_path=Path("test_inventory.json"),
            options={},
        )
        assert success is True

        second_drops, second_others = _lastmod_values()

        assert first_drops == second_drops
        assert first_others == second_others

    def test_sitemap_contains_static_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes static pages."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-static.xml")
        content: str = response.content.decode()

        # Check for the homepage and a few key list views across apps.
        assert (
            "<loc>https://ttvdrops.lovinator.space/</loc>" in content
            or "<loc>http://localhost:8000/</loc>" in content
        )
        assert "https://ttvdrops.lovinator.space/twitch/" in content
        assert "https://ttvdrops.lovinator.space/kick/" in content
        assert "https://ttvdrops.lovinator.space/youtube/" in content
        assert "https://ttvdrops.lovinator.space/twitch/campaigns/" in content
        assert "https://ttvdrops.lovinator.space/twitch/games/" in content

    def test_sitemap_contains_game_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes game detail pages."""
        game: Game = sample_entities["game"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-twitch-others.xml")
        content: str = response.content.decode()
        assert f"/games/{game.twitch_id}/" in content

    def test_sitemap_contains_campaign_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes campaign detail pages."""
        campaign: DropCampaign = sample_entities["campaign"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-twitch-drops.xml")
        content: str = response.content.decode()
        assert f"/campaigns/{campaign.twitch_id}/" in content

    def test_sitemap_prioritizes_active_drops(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test active drops are prioritised and crawled more frequently."""
        active_campaign: DropCampaign = sample_entities["campaign"]
        inactive_campaign: DropCampaign = sample_entities["inactive_campaign"]

        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-twitch-drops.xml")
        content: str = response.content.decode()

        active_loc: str = f"<loc>https://ttvdrops.lovinator.space/twitch/campaigns/{active_campaign.twitch_id}/</loc>"
        active_index: int = content.find(active_loc)
        assert active_index != -1
        active_end: int = content.find("</url>", active_index)
        assert active_end != -1

        inactive_loc: str = f"<loc>https://ttvdrops.lovinator.space/twitch/campaigns/{inactive_campaign.twitch_id}/</loc>"
        inactive_index: int = content.find(inactive_loc)
        assert inactive_index != -1
        inactive_end: int = content.find("</url>", inactive_index)
        assert inactive_end != -1

    def test_sitemap_prioritizes_active_kick_campaigns(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test active Kick campaigns are prioritised and crawled more frequently."""
        active_campaign: KickDropCampaign = sample_entities["kick_active"]
        inactive_campaign: KickDropCampaign = sample_entities["kick_inactive"]

        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-kick.xml")
        content: str = response.content.decode()

        active_loc: str = f"<loc>https://ttvdrops.lovinator.space/kick/campaigns/{active_campaign.kick_id}/</loc>"
        active_index: int = content.find(active_loc)
        assert active_index != -1
        active_end: int = content.find("</url>", active_index)
        assert active_end != -1

        inactive_loc: str = f"<loc>https://ttvdrops.lovinator.space/kick/campaigns/{inactive_campaign.kick_id}/</loc>"
        inactive_index: int = content.find(inactive_loc)
        assert inactive_index != -1
        inactive_end: int = content.find("</url>", inactive_index)
        assert inactive_end != -1

    def test_sitemap_contains_organization_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes organization detail pages."""
        org: Organization = sample_entities["org"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-twitch-others.xml")
        content: str = response.content.decode()
        assert f"/organizations/{org.twitch_id}/" in content

    def test_sitemap_contains_channel_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes channel detail pages."""
        channel: Channel = sample_entities["channel"]
        response: _MonkeyPatchedWSGIResponse = client.get(
            "/sitemap-twitch-channels.xml",
        )
        content: str = response.content.decode()
        assert f"/twitch/channels/{channel.twitch_id}/" in content

    def test_sitemap_contains_badge_detail_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes badge detail pages."""
        badge: ChatBadge = sample_entities["badge"]
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-twitch-others.xml")
        content: str = response.content.decode()
        assert f"/badges/{badge.set_id}/" in content  # pyright: ignore[reportAttributeAccessIssue]

    def test_sitemap_contains_youtube_pages(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes YouTube landing page."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-youtube.xml")
        content: str = response.content.decode()
        assert "/youtube/" in content

    def test_sitemap_includes_lastmod(
        self,
        client: Client,
        sample_entities: dict[str, Any],
    ) -> None:
        """Test sitemap includes lastmod for detail pages."""
        response: _MonkeyPatchedWSGIResponse = client.get("/sitemap-twitch-others.xml")
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
                is_fully_imported=True,
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
                is_fully_imported=True,
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

    def test_meta_image_url_prefers_single_reward_image(self) -> None:
        """Meta image should use the reward image when a campaign has one reward."""
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

        assert campaign.meta_image_url == "https://example.com/benefit.png"

    def test_meta_image_url_uses_campaign_image_with_multiple_rewards(self) -> None:
        """Meta image should keep the campaign image when a campaign has many rewards."""
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
        first_benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit1",
            name="Test Benefit 1",
            image_asset_url="https://example.com/benefit-1.png",
        )
        second_benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit2",
            name="Test Benefit 2",
            image_asset_url="https://example.com/benefit-2.png",
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="drop1",
            name="Test Drop",
            campaign=campaign,
        )
        drop.benefits.add(first_benefit, second_benefit)

        assert campaign.meta_image_url == "https://example.com/campaign.png"

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


@pytest.mark.django_db
class TestImageObjectStructuredData:
    """Tests for ImageObject structured data in game and campaign schema_data."""

    @pytest.fixture
    def org(self) -> Organization:
        """Create an organization for testing.

        Returns:
            Organization: The created organization instance.
        """
        return Organization.objects.create(twitch_id="org-img", name="Acme Corp")

    @pytest.fixture
    def game(self, org: Organization) -> Game:
        """Create a game with box art for testing.

        Args:
            org (Organization): The organization to associate with the game.

        Returns:
            Game: The created game instance.
        """
        g: Game = Game.objects.create(
            twitch_id="game-img",
            name="img_game",
            display_name="Image Game",
            box_art="https://example.com/boxart.jpg",
        )
        g.owners.add(org)
        return g

    @pytest.fixture
    def campaign(self, game: Game) -> DropCampaign:
        """Create a campaign with an image for testing.

        Args:
            game (Game): The game to associate with the campaign.

        Returns:
            DropCampaign: The created campaign instance.
        """
        return DropCampaign.objects.create(
            twitch_id="camp-img",
            name="Image Campaign",
            game=game,
            image_url="https://example.com/campaign.jpg",
            operation_names=["DropCampaignDetails"],
        )

    # --- game detail ---

    def test_game_schema_image_is_image_object(
        self,
        client: Client,
        game: Game,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """VideoGame schema image should be an ImageObject, not a plain URL."""
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["@type"] == "VideoGame"
        img: dict[str, Any] = schema["image"]
        assert isinstance(img, dict), "image should be a dict, not a plain URL string"
        assert img["@type"] == "ImageObject"
        assert img["contentUrl"].endswith(game.box_art_best_url)
        assert img["contentUrl"].startswith("http")

    def test_game_schema_image_has_credit_fields(
        self,
        client: Client,
        game: Game,
        org: Organization,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """VideoGame ImageObject should carry attribution metadata."""
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        img: dict[str, Any] = schema["image"]
        assert img["creditText"] == org.name
        assert org.name in img["copyrightNotice"]
        assert img["creator"] == {
            "@type": "Organization",
            "name": org.name,
            "url": f"https://ttvdrops.lovinator.space{reverse('twitch:organization_detail', args=[org.twitch_id])}",
        }

    def test_game_schema_no_image_when_no_box_art(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """VideoGame schema should omit image key when box_art is empty."""
        game_no_art: Game = Game.objects.create(
            twitch_id="game-no-art",
            name="no_art_game",
            display_name="No Art Game",
            box_art="",
        )
        url: str = reverse("twitch:game_detail", args=[game_no_art.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert "image" not in schema

    def test_game_schema_publisher_uses_owner_name(
        self,
        client: Client,
        game: Game,
        org: Organization,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """VideoGame schema publisher name should match the owning organization."""
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["publisher"]["name"] == org.name

    def test_game_schema_owner_name_matches_credit_text(
        self,
        client: Client,
        game: Game,
        org: Organization,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """publisher.name and image.creditText should be the same value."""
        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["publisher"]["name"] == schema["image"]["creditText"]

    def test_game_schema_owner_falls_back_to_twitch_id(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When owner.name is empty, twitch_id is used as credit fallback."""
        nameless_org: Organization = Organization.objects.create(
            twitch_id="org-nameless",
            name="",
        )
        game: Game = Game.objects.create(
            twitch_id="game-nameless-owner",
            name="nameless_owner_game",
            display_name="Nameless Owner Game",
            box_art="https://example.com/boxart.jpg",
        )
        game.owners.add(nameless_org)

        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["image"]["creditText"] == nameless_org.twitch_id

    # --- campaign detail ---

    def test_campaign_schema_image_is_image_object(
        self,
        client: Client,
        campaign: DropCampaign,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Event schema image should be an ImageObject, not a plain URL string."""
        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["@type"] == "Event"
        img: dict[str, Any] = schema["image"]
        assert isinstance(img, dict), "image should be a dict, not a plain URL string"
        assert img["@type"] == "ImageObject"
        assert img["contentUrl"].endswith(campaign.image_best_url)
        assert img["contentUrl"].startswith("http")

    def test_campaign_schema_image_has_credit_fields(
        self,
        client: Client,
        campaign: DropCampaign,
        org: Organization,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Event ImageObject should carry attribution metadata."""
        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        img: dict[str, Any] = schema["image"]
        assert img["creditText"] == org.name
        assert org.name in img["copyrightNotice"]
        assert img["creator"] == {
            "@type": "Organization",
            "name": org.name,
            "url": f"https://ttvdrops.lovinator.space{reverse('twitch:organization_detail', args=[org.twitch_id])}",
        }

    def test_campaign_schema_no_image_when_no_image_url(
        self,
        client: Client,
        game: Game,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Event schema should omit image key when campaign has no image."""
        campaign_no_img: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp-no-img",
            name="No Image Campaign",
            game=game,
            image_url="",
            operation_names=["DropCampaignDetails"],
        )
        url: str = reverse("twitch:campaign_detail", args=[campaign_no_img.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert "image" not in schema

    def test_campaign_schema_organizer_uses_owner_name(
        self,
        client: Client,
        campaign: DropCampaign,
        org: Organization,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Event schema organizer name should match the owning organization."""
        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["organizer"]["name"] == org.name

    def test_campaign_schema_owner_name_matches_credit_text(
        self,
        client: Client,
        campaign: DropCampaign,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """organizer.name and image.creditText should be the same value."""
        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["organizer"]["name"] == schema["image"]["creditText"]

    def test_campaign_schema_owner_falls_back_to_twitch(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When campaign has no owning org, creditText falls back to 'Twitch'."""
        game_no_owner: Game = Game.objects.create(
            twitch_id="game-no-owner",
            name="no_owner_game",
            display_name="No Owner Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp-no-owner",
            name="No Owner Campaign",
            game=game_no_owner,
            image_url="https://example.com/campaign.jpg",
            operation_names=["DropCampaignDetails"],
        )
        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["image"]["creditText"] == "Twitch"
        assert schema["image"]["creator"] == {
            "@type": "Organization",
            "name": "Twitch",
            "url": "https://www.twitch.tv/",
        }
        assert "organizer" not in schema

    # --- _pick_owner / Twitch Gaming skipping ---

    def test_game_schema_skips_twitch_gaming_owner(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When one owner is 'Twitch Gaming' and another is not, the non-generic one is used."""
        twitch_gaming: Organization = Organization.objects.create(
            twitch_id="twitch-gaming",
            name="Twitch Gaming",
        )
        real_publisher: Organization = Organization.objects.create(
            twitch_id="real-pub",
            name="Real Publisher",
        )
        game: Game = Game.objects.create(
            twitch_id="game-multi-owner",
            name="multi_owner_game",
            display_name="Multi Owner Game",
            box_art="https://example.com/boxart.jpg",
        )
        game.owners.add(twitch_gaming, real_publisher)

        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["image"]["creditText"] == "Real Publisher"
        assert schema["publisher"]["name"] == "Real Publisher"

    def test_game_schema_uses_twitch_gaming_when_only_owner(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the only owner is 'Twitch Gaming', it is still used (no other choice)."""
        twitch_gaming: Organization = Organization.objects.create(
            twitch_id="twitch-gaming-solo",
            name="Twitch Gaming",
        )
        game: Game = Game.objects.create(
            twitch_id="game-tg-only",
            name="tg_only_game",
            display_name="TG Only Game",
            box_art="https://example.com/boxart.jpg",
        )
        game.owners.add(twitch_gaming)

        url: str = reverse("twitch:game_detail", args=[game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["image"]["creditText"] == "Twitch Gaming"

    def test_campaign_schema_skips_twitch_gaming_owner(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Campaign schema prefers a non-generic publisher over 'Twitch Gaming'."""
        twitch_gaming: Organization = Organization.objects.create(
            twitch_id="twitch-gaming-camp",
            name="Twitch Gaming",
        )
        real_publisher: Organization = Organization.objects.create(
            twitch_id="real-pub-camp",
            name="Real Campaign Publisher",
        )
        game: Game = Game.objects.create(
            twitch_id="game-camp-multi",
            name="camp_multi_game",
            display_name="Camp Multi Game",
        )
        game.owners.add(twitch_gaming, real_publisher)
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="camp-multi-owner",
            name="Multi Owner Campaign",
            game=game,
            image_url="https://example.com/campaign.jpg",
            operation_names=["DropCampaignDetails"],
        )

        url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)

        schema: dict[str, Any] = json.loads(response.context["schema_data"])
        assert schema["image"]["creditText"] == "Real Campaign Publisher"
        assert schema["organizer"]["name"] == "Real Campaign Publisher"


@pytest.mark.django_db
class TestBadgeListView:
    """Tests for the badge_list_view function."""

    def test_badge_list_returns_200(self, client: Client) -> None:
        """Badge list view renders successfully with no badge sets."""
        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:badge_list"))
        assert response.status_code == 200

    def test_badge_list_context_has_badge_data(self, client: Client) -> None:
        """Badge list view passes badge_data list (not badge_sets queryset) to template."""
        badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="test_vip")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="VIP",
            description="VIP badge",
        )

        response: _MonkeyPatchedWSGIResponse = client.get(reverse("twitch:badge_list"))
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        assert "badge_data" in context
        assert len(context["badge_data"]) == 1
        assert context["badge_data"][0]["set"].set_id == "test_vip"
        assert len(context["badge_data"][0]["badges"]) == 1
        assert "badge_sets" not in context

    def test_badge_list_query_count_stays_flat(self, client: Client) -> None:
        """badge_list_view should not issue N+1 queries as badge set count grows."""
        for i in range(3):
            bs: ChatBadgeSet = ChatBadgeSet.objects.create(set_id=f"set_flat_{i}")
            for j in range(4):
                ChatBadge.objects.create(
                    badge_set=bs,
                    badge_id=str(j),
                    image_url_1x="https://example.com/1x.png",
                    image_url_2x="https://example.com/2x.png",
                    image_url_4x="https://example.com/4x.png",
                    title=f"Badge {i}-{j}",
                    description="desc",
                )

        def _count_selects() -> int:
            with CaptureQueriesContext(connection) as ctx:
                resp: _MonkeyPatchedWSGIResponse = client.get(
                    reverse("twitch:badge_list"),
                )
                assert resp.status_code == 200
            return sum(
                1
                for q in ctx.captured_queries
                if q["sql"].lstrip().upper().startswith("SELECT")
            )

        baseline: int = _count_selects()

        # Add 10 more badge sets with badges
        for i in range(3, 13):
            bs = ChatBadgeSet.objects.create(set_id=f"set_flat_{i}")
            for j in range(4):
                ChatBadge.objects.create(
                    badge_set=bs,
                    badge_id=str(j),
                    image_url_1x="https://example.com/1x.png",
                    image_url_2x="https://example.com/2x.png",
                    image_url_4x="https://example.com/4x.png",
                    title=f"Badge {i}-{j}",
                    description="desc",
                )

        scaled: int = _count_selects()
        assert scaled <= baseline + 1, (
            f"badge_list_view SELECT count grew with data; possible N+1. "
            f"baseline={baseline}, scaled={scaled}"
        )


@pytest.mark.django_db
class TestBadgeSetDetailView:
    """Tests for the badge_set_detail_view function."""

    @pytest.fixture
    def badge_set_with_badges(self) -> dict[str, Any]:
        """Create a badge set with numeric badge IDs and a campaign awarding one badge.

        Returns:
            Dict with badge_set, badge1-3, campaign, and benefit instances.
        """
        org: Organization = Organization.objects.create(
            twitch_id="org_badge_test",
            name="Badge Test Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game_badge_test",
            name="badge_test_game",
            display_name="Badge Test Game",
        )
        game.owners.add(org)

        badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="drops")
        badge1: ChatBadge = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Drop 1",
            description="First drop badge",
        )
        badge2: ChatBadge = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="10",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Drop 10",
            description="Tenth drop badge",
        )
        badge3: ChatBadge = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="2",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Drop 2",
            description="Second drop badge",
        )

        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="badge_test_campaign",
            name="Badge Test Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="badge_test_drop",
            name="Badge Test Drop",
            campaign=campaign,
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="badge_test_benefit",
            name="Drop 1",
            distribution_type="BADGE",
        )
        drop.benefits.add(benefit)

        return {
            "badge_set": badge_set,
            "badge1": badge1,
            "badge2": badge2,
            "badge3": badge3,
            "campaign": campaign,
            "benefit": benefit,
        }

    def test_badge_set_detail_returns_200(
        self,
        client: Client,
        badge_set_with_badges: dict[str, Any],
    ) -> None:
        """Badge set detail view renders successfully."""
        set_id: str = badge_set_with_badges["badge_set"].set_id
        url: str = reverse("twitch:badge_set_detail", args=[set_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 200

    def test_badge_set_detail_404_for_missing_set(self, client: Client) -> None:
        """Badge set detail view returns 404 for unknown set_id."""
        url: str = reverse("twitch:badge_set_detail", args=["nonexistent"])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        assert response.status_code == 404

    def test_badges_sorted_numerically(
        self,
        client: Client,
        badge_set_with_badges: dict[str, Any],
    ) -> None:
        """Numeric badge_ids should be sorted as integers (1, 2, 10) not strings (1, 10, 2)."""
        set_id: str = badge_set_with_badges["badge_set"].set_id
        url: str = reverse("twitch:badge_set_detail", args=[set_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        badge_ids: list[str] = [b.badge_id for b in context["badges"]]
        assert badge_ids == ["1", "2", "10"], (
            f"Expected numeric sort order [1, 2, 10], got {badge_ids}"
        )

    def test_award_campaigns_attached_to_badges(
        self,
        client: Client,
        badge_set_with_badges: dict[str, Any],
    ) -> None:
        """Badges with matching BADGE benefits should have award_campaigns populated."""
        set_id: str = badge_set_with_badges["badge_set"].set_id
        url: str = reverse("twitch:badge_set_detail", args=[set_id])
        response: _MonkeyPatchedWSGIResponse = client.get(url)
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        badges: list[ChatBadge] = list(context["badges"])
        badge_titled_drop1: ChatBadge = next(b for b in badges if b.title == "Drop 1")
        badge_titled_drop2: ChatBadge = next(b for b in badges if b.title == "Drop 2")

        assert len(badge_titled_drop1.award_campaigns) == 1  # pyright: ignore[reportAttributeAccessIssue]
        assert badge_titled_drop1.award_campaigns[0].twitch_id == "badge_test_campaign"  # pyright: ignore[reportAttributeAccessIssue]
        assert len(badge_titled_drop2.award_campaigns) == 0  # pyright: ignore[reportAttributeAccessIssue]

    def test_badge_set_detail_avoids_n_plus_one(
        self,
        client: Client,
    ) -> None:
        """badge_set_detail_view should not issue per-badge queries for award campaigns."""
        org: Organization = Organization.objects.create(
            twitch_id="org_n1_badge",
            name="N+1 Badge Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game_n1_badge",
            name="game_n1_badge",
            display_name="N+1 Badge Game",
        )
        game.owners.add(org)

        badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="n1_test")

        def _make_badge_and_campaign(idx: int) -> None:
            badge: ChatBadge = ChatBadge.objects.create(
                badge_set=badge_set,
                badge_id=str(idx),
                image_url_1x="https://example.com/1x.png",
                image_url_2x="https://example.com/2x.png",
                image_url_4x="https://example.com/4x.png",
                title=f"N1 Badge {idx}",
                description="desc",
            )
            campaign: DropCampaign = DropCampaign.objects.create(
                twitch_id=f"n1_campaign_{idx}",
                name=f"N+1 Campaign {idx}",
                game=game,
                operation_names=["DropCampaignDetails"],
            )
            drop: TimeBasedDrop = TimeBasedDrop.objects.create(
                twitch_id=f"n1_drop_{idx}",
                name=f"N+1 Drop {idx}",
                campaign=campaign,
            )
            benefit: DropBenefit = DropBenefit.objects.create(
                twitch_id=f"n1_benefit_{idx}",
                name=badge.title,
                distribution_type="BADGE",
            )
            drop.benefits.add(benefit)

        for i in range(3):
            _make_badge_and_campaign(i)

        url: str = reverse("twitch:badge_set_detail", args=[badge_set.set_id])

        def _count_selects() -> int:
            with CaptureQueriesContext(connection) as ctx:
                resp: _MonkeyPatchedWSGIResponse = client.get(url)
                assert resp.status_code == 200
            return sum(
                1
                for q in ctx.captured_queries
                if q["sql"].lstrip().upper().startswith("SELECT")
            )

        baseline: int = _count_selects()

        # Add 10 more badges, each with their own campaigns
        for i in range(3, 13):
            _make_badge_and_campaign(i)

        scaled: int = _count_selects()
        assert scaled <= baseline + 1, (
            f"badge_set_detail_view SELECT count grew with badge count; possible N+1. "
            f"baseline={baseline}, scaled={scaled}"
        )

    def test_drop_benefit_index_used_for_badge_award_lookup(self) -> None:
        """DropBenefit queries filtering by distribution_type+name should use indexes."""
        org: Organization = Organization.objects.create(
            twitch_id="org_benefit_idx",
            name="Benefit Index Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game_benefit_idx",
            name="game_benefit_idx",
            display_name="Benefit Index Game",
        )
        game.owners.add(org)

        # Create enough non-BADGE benefits so the planner has reason to use an index
        for i in range(300):
            DropBenefit.objects.create(
                twitch_id=f"non_badge_{i}",
                name=f"Emote {i}",
                distribution_type="EMOTE",
            )

        badge_titles: list[str] = []
        for i in range(5):
            DropBenefit.objects.create(
                twitch_id=f"badge_benefit_idx_{i}",
                name=f"Badge Title {i}",
                distribution_type="BADGE",
            )
            badge_titles.append(f"Badge Title {i}")

        qs = DropBenefit.objects.filter(
            distribution_type="BADGE",
            name__in=badge_titles,
        )
        plan: str = qs.explain()

        if connection.vendor == "sqlite":
            uses_index: bool = "USING INDEX" in plan.upper()
        elif connection.vendor == "postgresql":
            uses_index = (
                "INDEX SCAN" in plan.upper()
                or "BITMAP INDEX SCAN" in plan.upper()
                or "INDEX ONLY SCAN" in plan.upper()
            )
        else:
            pytest.skip(
                f"Unsupported DB vendor for index-plan assertion: {connection.vendor}",
            )

        assert uses_index, (
            f"DropBenefit query on (distribution_type, name) did not use an index.\n{plan}"
        )


@pytest.mark.django_db
class TestEmoteGalleryView:
    """Tests for emote gallery model delegation and query safety."""

    def test_emote_gallery_view_uses_model_helper(
        self,
        client: Client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Emote gallery view should delegate data loading to the model layer."""
        game: Game = Game.objects.create(
            twitch_id="emote_gallery_delegate_game",
            name="Emote Delegate Game",
            display_name="Emote Delegate Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="emote_gallery_delegate_campaign",
            name="Emote Delegate Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        expected: list[dict[str, str | DropCampaign]] = [
            {
                "image_url": "https://example.com/emote.png",
                "campaign": campaign,
            },
        ]

        calls: dict[str, int] = {"count": 0}

        def _fake_emotes_for_gallery(
            _cls: type[DropBenefit],
        ) -> list[dict[str, str | DropCampaign]]:
            calls["count"] += 1
            return expected

        monkeypatch.setattr(
            DropBenefit,
            "emotes_for_gallery",
            classmethod(_fake_emotes_for_gallery),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:emote_gallery"),
        )
        assert response.status_code == 200

        context: ContextList | dict[str, Any] = response.context
        if isinstance(context, list):
            context = context[-1]

        assert calls["count"] == 1
        assert context["emotes"] == expected

    def test_emotes_for_gallery_uses_prefetched_fields_without_extra_queries(
        self,
    ) -> None:
        """Accessing template-used fields should not issue follow-up SELECT queries."""
        now: datetime.datetime = timezone.now()

        game: Game = Game.objects.create(
            twitch_id="emote_gallery_fields_game",
            name="Emote Fields Game",
            display_name="Emote Fields Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="emote_gallery_fields_campaign",
            name="Emote Fields Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="emote_gallery_fields_drop",
            name="Emote Fields Drop",
            campaign=campaign,
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="emote_gallery_fields_benefit",
            name="Emote Fields Benefit",
            distribution_type="EMOTE",
            image_asset_url="https://example.com/emote_fields.png",
        )
        drop.benefits.add(benefit)

        emotes: list[dict[str, str | DropCampaign]] = DropBenefit.emotes_for_gallery()
        assert len(emotes) == 1

        with CaptureQueriesContext(connection) as capture:
            for emote in emotes:
                _ = emote["image_url"]
                campaign_obj = emote["campaign"]
                assert isinstance(campaign_obj, DropCampaign)
                _ = campaign_obj.twitch_id
                _ = campaign_obj.name

        assert len(capture) == 0

    def test_emotes_for_gallery_skips_emotes_without_campaign_link(self) -> None:
        """Gallery should only include EMOTE benefits reachable from a campaign drop."""
        game: Game = Game.objects.create(
            twitch_id="emote_gallery_skip_game",
            name="Emote Skip Game",
            display_name="Emote Skip Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="emote_gallery_skip_campaign",
            name="Emote Skip Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="emote_gallery_skip_drop",
            name="Emote Skip Drop",
            campaign=campaign,
        )

        included: DropBenefit = DropBenefit.objects.create(
            twitch_id="emote_gallery_included_benefit",
            name="Included Emote",
            distribution_type="EMOTE",
            image_asset_url="https://example.com/included-emote.png",
        )
        orphaned: DropBenefit = DropBenefit.objects.create(
            twitch_id="emote_gallery_orphaned_benefit",
            name="Orphaned Emote",
            distribution_type="EMOTE",
            image_asset_url="https://example.com/orphaned-emote.png",
        )

        drop.benefits.add(included)

        emotes: list[dict[str, str | DropCampaign]] = DropBenefit.emotes_for_gallery()

        image_urls: list[str] = [str(item["image_url"]) for item in emotes]
        campaign_ids: list[str] = [
            campaign_obj.twitch_id
            for campaign_obj in (item["campaign"] for item in emotes)
            if isinstance(campaign_obj, DropCampaign)
        ]

        assert included.image_asset_url in image_urls
        assert orphaned.image_asset_url not in image_urls
        assert campaign.twitch_id in campaign_ids

    def test_emote_gallery_view_renders_only_campaign_linked_emotes(
        self,
        client: Client,
    ) -> None:
        """Emote gallery page should not render EMOTE benefits without campaign-linked drops."""
        game: Game = Game.objects.create(
            twitch_id="emote_gallery_view_game",
            name="Emote View Game",
            display_name="Emote View Game",
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="emote_gallery_view_campaign",
            name="Emote View Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="emote_gallery_view_drop",
            name="Emote View Drop",
            campaign=campaign,
        )

        linked: DropBenefit = DropBenefit.objects.create(
            twitch_id="emote_gallery_view_linked",
            name="Linked Emote",
            distribution_type="EMOTE",
            image_asset_url="https://example.com/linked-view-emote.png",
        )
        orphaned: DropBenefit = DropBenefit.objects.create(
            twitch_id="emote_gallery_view_orphaned",
            name="Orphaned View Emote",
            distribution_type="EMOTE",
            image_asset_url="https://example.com/orphaned-view-emote.png",
        )

        drop.benefits.add(linked)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:emote_gallery"),
        )
        assert response.status_code == 200

        html: str = response.content.decode("utf-8")
        assert linked.image_asset_url in html
        assert orphaned.image_asset_url not in html
        assert reverse("twitch:campaign_detail", args=[campaign.twitch_id]) in html


@pytest.mark.django_db
class TestDropCampaignListView:
    """Tests for drop_campaign_list_view index usage and fat-model delegation."""

    @pytest.fixture
    def game_with_campaigns(self) -> dict[str, Any]:
        """Create a game with a mix of imported/not-imported campaigns.

        Returns:
            Dict with 'org' and 'game' keys for the created Organization and Game.
        """
        org: Organization = Organization.objects.create(
            twitch_id="org_list_test",
            name="List Test Org",
        )
        game: Game = Game.objects.create(
            twitch_id="game_list_test",
            name="game_list_test",
            display_name="List Test Game",
        )
        game.owners.add(org)
        return {"org": org, "game": game}

    def test_campaign_list_returns_200(self, client: Client) -> None:
        """Campaign list view loads successfully."""
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list"),
        )
        assert response.status_code == 200

    def test_only_fully_imported_campaigns_shown(
        self,
        client: Client,
        game_with_campaigns: dict[str, Any],
    ) -> None:
        """Only campaigns with is_fully_imported=True appear in the list."""
        game: Game = game_with_campaigns["game"]
        imported: DropCampaign = DropCampaign.objects.create(
            twitch_id="cl_imported",
            name="Imported Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            is_fully_imported=True,
        )
        DropCampaign.objects.create(
            twitch_id="cl_not_imported",
            name="Not Imported Campaign",
            game=game,
            operation_names=["DropCampaignDetails"],
            is_fully_imported=False,
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list"),
        )
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        campaign_ids = {c.twitch_id for c in context["campaigns"].object_list}
        assert imported.twitch_id in campaign_ids
        assert "cl_not_imported" not in campaign_ids

    def test_status_filter_active(
        self,
        client: Client,
        game_with_campaigns: dict[str, Any],
    ) -> None:
        """Status=active returns only currently-running campaigns."""
        game: Game = game_with_campaigns["game"]
        now = timezone.now()
        active: DropCampaign = DropCampaign.objects.create(
            twitch_id="cl_active",
            name="Active",
            game=game,
            operation_names=[],
            is_fully_imported=True,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        DropCampaign.objects.create(
            twitch_id="cl_expired",
            name="Expired",
            game=game,
            operation_names=[],
            is_fully_imported=True,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=1),
        )

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("twitch:campaign_list") + "?status=active",
        )
        context: ContextList | dict[str, Any] = response.context  # type: ignore[assignment]
        if isinstance(context, list):
            context = context[-1]

        campaign_ids = {c.twitch_id for c in context["campaigns"].object_list}
        assert active.twitch_id in campaign_ids
        assert "cl_expired" not in campaign_ids

    def test_campaign_list_indexes_exist(self) -> None:
        """Required composite indexes for the campaign list query must exist on DropCampaign."""
        expected: set[str] = {
            "tw_drop_imported_start_idx",
            "tw_drop_imported_start_end_idx",
        }
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                DropCampaign._meta.db_table,
            )
        actual: set[str] = {
            name for name, meta in constraints.items() if meta.get("index")
        }
        missing = expected - actual
        assert not missing, (
            f"Missing expected DropCampaign campaign-list indexes: {sorted(missing)}"
        )

    @pytest.mark.django_db
    def test_campaign_list_query_uses_index(self) -> None:
        """for_campaign_list() should use an index when filtering is_fully_imported."""
        now: datetime.datetime = timezone.now()
        game: Game = Game.objects.create(
            twitch_id="game_cl_idx",
            name="game_cl_idx",
            display_name="CL Idx Game",
        )
        # Bulk-create enough rows to give the query planner a reason to use indexes.
        rows: list[DropCampaign] = [
            DropCampaign(
                twitch_id=f"cl_idx_not_imported_{i}",
                name=f"Not imported {i}",
                game=game,
                operation_names=[],
                is_fully_imported=False,
                start_at=now - timedelta(days=i + 1),
                end_at=now + timedelta(days=1),
            )
            for i in range(300)
        ]
        rows.append(
            DropCampaign(
                twitch_id="cl_idx_imported",
                name="Imported",
                game=game,
                operation_names=[],
                is_fully_imported=True,
                start_at=now - timedelta(hours=1),
                end_at=now + timedelta(hours=1),
            ),
        )
        DropCampaign.objects.bulk_create(rows)

        plan: str = DropCampaign.for_campaign_list(now).explain()

        if connection.vendor == "sqlite":
            uses_index: bool = "USING INDEX" in plan.upper()
        elif connection.vendor == "postgresql":
            uses_index = (
                "INDEX SCAN" in plan.upper()
                or "BITMAP INDEX SCAN" in plan.upper()
                or "INDEX ONLY SCAN" in plan.upper()
            )
        else:
            pytest.skip(
                f"Unsupported DB vendor for index assertion: {connection.vendor}",
            )

        assert uses_index, f"for_campaign_list() did not use an index.\n{plan}"

    def test_campaign_list_query_count_stays_flat(self, client: Client) -> None:
        """Campaign list should not issue N+1 queries as campaign volume grows."""
        game: Game = Game.objects.create(
            twitch_id="game_cl_flat",
            name="game_cl_flat",
            display_name="CL Flat Game",
        )
        now = timezone.now()

        def _select_count() -> int:
            with CaptureQueriesContext(connection) as ctx:
                resp: _MonkeyPatchedWSGIResponse = client.get(
                    reverse("twitch:campaign_list"),
                )
                assert resp.status_code == 200
            return sum(
                1
                for q in ctx.captured_queries
                if q["sql"].lstrip().upper().startswith("SELECT")
            )

        DropCampaign.objects.create(
            twitch_id="cl_flat_base",
            name="Base campaign",
            game=game,
            operation_names=[],
            is_fully_imported=True,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        baseline: int = _select_count()

        extra = [
            DropCampaign(
                twitch_id=f"cl_flat_extra_{i}",
                name=f"Extra {i}",
                game=game,
                operation_names=[],
                is_fully_imported=True,
                start_at=now - timedelta(hours=2),
                end_at=now + timedelta(hours=2),
            )
            for i in range(15)
        ]
        DropCampaign.objects.bulk_create(extra)
        scaled: int = _select_count()

        assert scaled <= baseline + 2, (
            f"Campaign list SELECT count grew; possible N+1. "
            f"baseline={baseline}, scaled={scaled}"
        )
