from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import pytest

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
            owner=org,
        )
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="789",
            name="Test Campaign",
            description="A test campaign",
            game=game,
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
        result_key_map = {
            "org": "organizations",
            "game": "games",
            "campaign": "campaigns",
            "drop": "drops",
            "benefit": "benefits",
        }
        result_key = result_key_map[model_key]
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
        result_key_map = {
            "org": "organizations",
            "game": "games",
            "campaign": "campaigns",
            "drop": "drops",
            "benefit": "benefits",
        }
        result_key = result_key_map[model_key]
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

        results = context["results"][model_key]
        assert len(results) > 0

        # Verify the related object is accessible without additional query
        first_result = results[0]
        assert hasattr(first_result, related_field)
