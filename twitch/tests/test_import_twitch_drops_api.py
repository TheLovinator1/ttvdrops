"""Tests for the import_twitch_drops_api management command."""

from __future__ import annotations

import json
import subprocess  # ruff:ignore[suspicious-subprocess-import]
from typing import TYPE_CHECKING
from typing import LiteralString
from typing import Self
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest
from django.core.management.base import CommandError
from django.test import TestCase

from twitch.management.commands.import_twitch_drops_api import Command
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import DropCampaignAllowedChannel
from twitch.models import Game
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.schemas import SunkwiBotDropCampaignACLSchema

if TYPE_CHECKING:
    from types import TracebackType

# ---------------------------------------------------------------------------
# Sample data matching the SunkwiBOT/twitch-drops-api JSON formats
# ---------------------------------------------------------------------------

SAMPLE_DROPS_JSON: list[dict] = [
    {
        "endAt": "2026-06-21T10:59:00Z",
        "gameBoxArtURL": "https://static-cdn.jtvnw.net/ttv-boxart/515025_IGDB-285x380.jpg",
        "gameDisplayName": "Overwatch 2",
        "gameId": "515025",
        "rewards": [
            {
                "id": "reward-campaign-1",
                "self": None,
                "allow": {
                    "channels": [
                        {
                            "id": "123456789",
                            "displayName": "SomeStreamer",
                            "name": "somestreamer",
                        },
                    ],
                    "isEnabled": True,
                },
                "accountLinkURL": "https://www.twitch.tv/drops/campaigns",
                "description": "Earn Overwatch 2 drops by watching!",
                "detailsURL": "https://www.twitch.tv/drops/campaigns/reward-campaign-1",
                "endAt": "2026-06-21T10:59:00Z",
                "eventBasedDrops": [],
                "game": {
                    "id": "515025",
                    "slug": "overwatch-2",
                    "displayName": "Overwatch 2",
                },
                "imageURL": "https://example.com/campaign.png",
                "name": "Overwatch 2 x Twitch Drops",
                "owner": {
                    "id": "org-blizzard",
                    "name": "Blizzard Entertainment",
                },
                "startAt": "2026-06-12T15:00:00Z",
                "status": "ACTIVE",
                "timeBasedDrops": [
                    {
                        "id": "time-drop-1",
                        "requiredSubs": 0,
                        "benefitEdges": [
                            {
                                "benefit": {
                                    "id": "benefit-1",
                                    "createdAt": "2026-06-10T12:00:00Z",
                                    "entitlementLimit": 1,
                                    "game": {
                                        "id": "515025",
                                        "name": "Overwatch 2",
                                    },
                                    "imageAssetURL": "https://example.com/reward.png",
                                    "isIosAvailable": True,
                                    "name": "Ana's Cybermedic Skin",
                                    "ownerOrganization": {
                                        "id": "org-blizzard",
                                        "name": "Blizzard Entertainment",
                                    },
                                    "distributionType": "MANUAL",
                                },
                                "entitlementLimit": 1,
                            },
                        ],
                        "endAt": "2026-06-21T10:59:00Z",
                        "name": "Watch 2 hours",
                        "preconditionDrops": None,
                        "requiredMinutesWatched": 120,
                        "startAt": "2026-06-12T15:00:00Z",
                    },
                ],
            },
        ],
        "startAt": "2026-06-12T15:00:00Z",
    },
]

SAMPLE_REWARDS_JSON: list[dict] = [
    {
        "aboutURL": "https://example.com/redeem",
        "brand": "Mojang",
        "endsAt": "2026-06-15T05:59:59.999Z",
        "externalURL": "https://example.com/redeem",
        "game": {
            "displayName": "Minecraft",
            "id": "27471",
            "slug": "minecraft",
        },
        "id": "reward-campaign-2",
        "image": {
            "image1xURL": "https://example.com/reward-campaign.png",
        },
        "instructions": "",
        "isSitewide": False,
        "name": "Tubbo's WatchTime",
        "rewards": [
            {
                "id": "reward-1",
                "name": "Test Cape",
                "bannerImage": {
                    "image1xURL": "https://example.com/banner.png",
                },
                "thumbnailImage": {
                    "image1xURL": "https://example.com/thumbnail.png",
                },
                "earnableUntil": "2026-06-15T05:59:59.999Z",
                "redemptionInstructions": "Redeem on the Mojang website.",
                "redemptionURL": "https://example.com/redeem/cape",
            },
        ],
        "rewardValueURLParam": "",
        "startsAt": "2026-05-31T16:00:00Z",
        "status": "UNKNOWN",
        "summary": "Watch 5 minutes of Minecraft gameplay!",
        "unlockRequirements": {
            "minuteWatchedGoal": 5,
            "subsGoal": 0,
        },
    },
]


def mock_httpx_get(data: list[dict]) -> MagicMock:
    """Return a mock httpx.Response that returns the given JSON data.

    Args:
        data: The JSON data to return from response.json().

    Returns:
        A MagicMock configured as an httpx response.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = data
    mock_response.raise_for_status.return_value = None
    return mock_response


class MockResponse:
    """Minimal mock for httpx.Response used as a context-manager return."""

    def __init__(self, json_data: list[dict] | dict) -> None:
        """Initialise with JSON data to return from .json().

        Args:
            json_data: The data to return.
        """
        self._json_data = json_data

    def json(self) -> list[dict] | dict:
        """Return the mocked JSON data.

        Returns:
            The JSON data.
        """
        return self._json_data

    def raise_for_status(self) -> None:
        """No-op for mock."""


class MockHttpxClient:
    """Mock for httpx.Client that returns canned data from its get() method."""

    def __init__(
        self,
        drops_data: list[dict] | dict,
        rewards_data: list[dict] | dict,
    ) -> None:
        """Initialise with data for each endpoint.

        Args:
            drops_data: Data to return for the drops URL.
            rewards_data: Data to return for the rewards URL.
        """
        self.drops_data = drops_data
        self.rewards_data = rewards_data

    def get(self, url: str) -> MockResponse:
        """Return canned data based on the URL.

        Args:
            url: The request URL.

        Returns:
            A MockResponse with the appropriate data.

        Raises:
            ValueError: If the URL is not recognised.
        """
        if "drops.json" in url:
            return MockResponse(self.drops_data)
        if "rewards.json" in url:
            return MockResponse(self.rewards_data)
        msg = f"Unexpected URL: {url}"
        raise ValueError(msg)

    def __enter__(self) -> Self:
        """Support context manager usage.

        Returns:
            self.
        """
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """No-op for context manager."""


@pytest.mark.no_zeal
class ImportTwitchDropsApiTests(TestCase):
    """Tests for the import_twitch_drops_api management command."""

    drops_url = (
        "https://raw.githubusercontent.com/SunkwiBOT/twitch-drops-api/main/drops.json"
    )
    rewards_url = (
        "https://raw.githubusercontent.com/SunkwiBOT/twitch-drops-api/main/rewards.json"
    )

    def setUp(self) -> None:
        """Set up the command instance for each test."""
        self.command = Command()
        self.command._org_cache = {}
        self.command._game_cache = {}
        self.command._channel_cache = {}
        self.command._benefit_cache = {}

    # ------------------------------------------------------------------
    # drops.json import tests
    # ------------------------------------------------------------------

    @patch("httpx.Client")
    def test_import_drops_creates_campaign(self, mock_client: MagicMock) -> None:
        """Verify a basic drops.json import creates a DropCampaign with correct data."""
        mock_client.return_value = MockHttpxClient(
            drops_data=SAMPLE_DROPS_JSON,
            rewards_data=[],
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        # Game
        game = Game.objects.get(twitch_id="515025")
        assert game.display_name == "Overwatch 2"

        # Organization
        org = Organization.objects.get(twitch_id="org-blizzard")
        assert org.name == "Blizzard Entertainment"

        # DropCampaign
        campaign = DropCampaign.objects.get(twitch_id="reward-campaign-1")
        assert campaign.name == "Overwatch 2 x Twitch Drops"
        assert campaign.data_source == "SunkwiBOT/twitch-drops-api"
        assert campaign.operation_names == ["SunkwiBOT/twitch-drops-api"]
        assert campaign.is_fully_imported is True

        # TimeBasedDrop
        tbd = TimeBasedDrop.objects.get(twitch_id="time-drop-1")
        assert tbd.name == "Watch 2 hours"
        assert tbd.required_minutes_watched == 120

        # DropBenefit
        benefit = DropBenefit.objects.get(twitch_id="benefit-1")
        assert benefit.name == "Ana's Cybermedic Skin"
        assert benefit.distribution_type == "MANUAL"

        # DropBenefitEdge
        edge = DropBenefitEdge.objects.get(drop=tbd, benefit=benefit)
        assert edge.entitlement_limit == 1

    @patch("httpx.Client")
    def test_import_drops_appends_operation_names(self, mock_client: MagicMock) -> None:
        """Repeated imports should accumulate operation/source names."""
        mock_client.return_value = MockHttpxClient(
            drops_data=SAMPLE_DROPS_JSON,
            rewards_data=[],
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="CustomSource",
            verbose=False,
            crash_on_error=True,
        )

        campaign = DropCampaign.objects.get(twitch_id="reward-campaign-1")
        assert campaign.operation_names == [
            "SunkwiBOT/twitch-drops-api",
            "CustomSource",
        ]

    @patch("httpx.Client")
    def test_import_drops_new_wrapped_format(self, mock_client: MagicMock) -> None:
        """Verify the newer {"games": [...]} drops.json format is imported."""
        mock_client.return_value = MockHttpxClient(
            drops_data={"games": SAMPLE_DROPS_JSON},
            rewards_data=[],
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        campaign = DropCampaign.objects.get(twitch_id="reward-campaign-1")
        assert campaign.name == "Overwatch 2 x Twitch Drops"
        assert Game.objects.filter(twitch_id="515025").exists()

    @patch("httpx.Client")
    def test_import_rewards_new_wrapped_format(self, mock_client: MagicMock) -> None:
        """Verify the newer {"campaigns": [...]} rewards.json format is imported."""
        Game.objects.create(
            twitch_id="27471",
            display_name="Minecraft",
            name="Minecraft",
        )
        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data={"campaigns": SAMPLE_REWARDS_JSON},
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        campaign = RewardCampaign.objects.get(twitch_id="reward-campaign-2")
        assert campaign.name == "Tubbo's WatchTime"

    @patch("httpx.Client")
    def test_import_drops_idempotent(self, mock_client: MagicMock) -> None:
        """Verify running the import twice doesn't create duplicates."""
        mock_client.return_value = MockHttpxClient(
            drops_data=SAMPLE_DROPS_JSON,
            rewards_data=[],
        )

        # First run
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        # Second run
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        assert DropCampaign.objects.count() == 1
        assert Game.objects.count() == 1
        assert Organization.objects.count() == 1
        assert TimeBasedDrop.objects.count() == 1
        assert DropBenefit.objects.count() == 1
        assert DropBenefitEdge.objects.count() == 1

    @patch("httpx.Client")
    def test_import_drops_sets_data_source(self, mock_client: MagicMock) -> None:
        """Verify the --source argument is stored on the DropCampaign."""
        mock_client.return_value = MockHttpxClient(
            drops_data=SAMPLE_DROPS_JSON,
            rewards_data=[],
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="CustomSource",
            verbose=False,
            crash_on_error=True,
        )

        campaign = DropCampaign.objects.get(twitch_id="reward-campaign-1")
        assert campaign.data_source == "CustomSource"

    # ------------------------------------------------------------------
    # rewards.json import tests
    # ------------------------------------------------------------------

    @patch("httpx.Client")
    def test_import_rewards_creates_reward_campaign(
        self,
        mock_client: MagicMock,
    ) -> None:
        """Verify a basic rewards.json import creates a RewardCampaign."""
        # Create the game first since rewards.json references existing games
        Game.objects.create(
            twitch_id="27471",
            display_name="Minecraft",
            name="Minecraft",
        )

        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data=SAMPLE_REWARDS_JSON,
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        reward = RewardCampaign.objects.get(twitch_id="reward-campaign-2")
        assert reward.name == "Tubbo's WatchTime"
        assert reward.brand == "Mojang"
        assert reward.data_source == "SunkwiBOT/twitch-drops-api"
        assert reward.is_sitewide is False
        assert reward.game is not None
        assert reward.game.twitch_id == "27471"

        # Individual rewards inside the campaign are imported too
        individual = Reward.objects.get(twitch_id="reward-1")
        assert individual.reward_campaign == reward
        assert individual.name == "Test Cape"
        assert individual.banner_image_url == "https://example.com/banner.png"
        assert individual.thumbnail_image_url == "https://example.com/thumbnail.png"
        assert individual.earnable_until is not None
        assert individual.redemption_instructions == "Redeem on the Mojang website."
        assert individual.redemption_url == "https://example.com/redeem/cape"

    @patch("httpx.Client")
    def test_import_rewards_sets_data_source(self, mock_client: MagicMock) -> None:
        """Verify the --source argument is stored on the RewardCampaign."""
        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data=SAMPLE_REWARDS_JSON,
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="CustomSource",
            verbose=False,
            crash_on_error=True,
        )

        reward = RewardCampaign.objects.get(twitch_id="reward-campaign-2")
        assert reward.data_source == "CustomSource"

    @patch("httpx.Client")
    def test_import_rewards_idempotent(self, mock_client: MagicMock) -> None:
        """Verify running the rewards import twice doesn't create duplicates."""
        Game.objects.create(
            twitch_id="27471",
            display_name="Minecraft",
            name="Minecraft",
        )

        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data=SAMPLE_REWARDS_JSON,
        )

        # First run
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        # Second run
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        assert RewardCampaign.objects.count() == 1
        assert Reward.objects.count() == 1

    def test_import_rewards_skips_existing_campaign_but_backfills_rewards(
        self,
    ) -> None:
        """Verify re-importing with the default skip mode still syncs rewards."""
        Game.objects.create(
            twitch_id="27471",
            display_name="Minecraft",
            name="Minecraft",
        )

        campaign = RewardCampaign.objects.create(
            twitch_id="reward-campaign-2",
            name="Tubbo's WatchTime",
            brand="Mojang",
            status="UNKNOWN",
        )

        mock_client = MagicMock()
        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data=SAMPLE_REWARDS_JSON,
        )
        with patch("httpx.Client", mock_client):
            self.command.handle(
                drops_url=self.drops_url,
                rewards_url=self.rewards_url,
                drops_only=False,
                rewards_only=True,
                source="SunkwiBOT/twitch-drops-api",
                verbose=False,
                crash_on_error=True,
            )

        # Campaign itself was skipped (name untouched), but rewards were added.
        campaign.refresh_from_db()
        assert campaign.name == "Tubbo's WatchTime"
        assert Reward.objects.count() == 1
        individual = Reward.objects.get(twitch_id="reward-1")
        assert individual.reward_campaign == campaign

    @patch("httpx.Client")
    def test_import_rewards_update_existing_syncs_rewards(
        self,
        mock_client: MagicMock,
    ) -> None:
        """Verify --update-existing updates changed rewards and drops stale ones."""
        Game.objects.create(
            twitch_id="27471",
            display_name="Minecraft",
            name="Minecraft",
        )

        # First import with the current payload
        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data=SAMPLE_REWARDS_JSON,
        )
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        assert Reward.objects.count() == 1

        # Payload now renames the reward and drops it in favour of a new one
        updated_data = json.loads(json.dumps(SAMPLE_REWARDS_JSON))
        updated_data[0]["rewards"] = [
            {
                "id": "reward-2",
                "name": "New Cape",
                "bannerImage": None,
                "thumbnailImage": None,
                "earnableUntil": "2026-06-20T05:59:59.999Z",
                "redemptionInstructions": "",
                "redemptionURL": "https://example.com/redeem/new-cape",
            },
        ]
        mock_client.return_value = MockHttpxClient(
            drops_data=[],
            rewards_data=updated_data,
        )
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
            update_existing=True,
        )

        # Stale reward removed, new reward imported.
        assert Reward.objects.count() == 1
        assert not Reward.objects.filter(twitch_id="reward-1").exists()
        individual = Reward.objects.get(twitch_id="reward-2")
        assert individual.name == "New Cape"

    # ------------------------------------------------------------------
    # Combined import tests
    # ------------------------------------------------------------------

    @patch("httpx.Client")
    def test_import_both(self, mock_client: MagicMock) -> None:
        """Verify importing both drops.json and rewards.json works together."""
        Game.objects.create(
            twitch_id="27471",
            display_name="Minecraft",
            name="Minecraft",
        )

        mock_client.return_value = MockHttpxClient(
            drops_data=SAMPLE_DROPS_JSON,
            rewards_data=SAMPLE_REWARDS_JSON,
        )

        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )

        assert DropCampaign.objects.count() == 1
        assert RewardCampaign.objects.count() == 1
        assert Game.objects.count() == 2  # 515025 + 27471

    # ------------------------------------------------------------------
    # Error handling tests
    # ------------------------------------------------------------------

    @patch("httpx.Client")
    def test_handles_invalid_json(self, mock_client: MagicMock) -> None:
        """Verify the command handles a non-list response gracefully."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"not": "a list"}
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # The error is caught internally and logged; no data should be imported
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=True,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=False,
        )

        assert DropCampaign.objects.count() == 0

    @patch("httpx.Client")
    def test_handles_http_error(self, mock_client: MagicMock) -> None:
        """Verify the command handles HTTP errors gracefully."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(),
        )
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Should not crash when crash_on_error is False
        self.command.handle(
            drops_url=self.drops_url,
            rewards_url=self.rewards_url,
            drops_only=False,
            rewards_only=False,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=False,
        )

        # No data should have been imported
        assert DropCampaign.objects.count() == 0
        assert RewardCampaign.objects.count() == 0


# ---------------------------------------------------------------------------
# _extract_json_items tests
# ---------------------------------------------------------------------------


class ExtractJsonItemsTests(TestCase):
    """Tests for Command._extract_json_items."""

    def test_accepts_old_list_format(self) -> None:
        """Old files are bare arrays."""
        items = [{"id": "1"}]
        assert Command._extract_json_items(items, source="drops.json") == items

    def test_accepts_new_drops_format(self) -> None:
        """New drops.json wraps items under "games"."""
        items = [{"gameId": "1"}]
        data = {"games": items, "refreshedAt": "2026-11-18T22:16:01.397Z"}
        assert Command._extract_json_items(data, source="drops.json") == items

    def test_accepts_new_rewards_format(self) -> None:
        """New rewards.json wraps items under "campaigns"."""
        items = [{"id": "reward-1"}]
        data = {"campaigns": items, "refreshedAt": "2026-11-18T14:28:03.716Z"}
        assert Command._extract_json_items(data, source="rewards.json") == items

    def test_rejects_unknown_object(self) -> None:
        """An object without a known item array is rejected."""
        with pytest.raises(TypeError, match="Expected a JSON array"):
            Command._extract_json_items(
                {"not": "an item array"},
                source="drops.json",
            )


# ---------------------------------------------------------------------------
# _strip_typename tests
# ---------------------------------------------------------------------------


class StripTypenameTests(TestCase):
    """Tests for Command._strip_typename."""

    def test_removes_typename_from_dict(self) -> None:
        """Verify __typename is removed from a flat dict."""
        result = Command._strip_typename({
            "id": "123",
            "__typename": "DropCampaign",
            "name": "Test",
        })
        assert result == {"id": "123", "name": "Test"}

    def test_removes_typename_nested(self) -> None:
        """Verify __typename is removed from nested dicts."""
        result = Command._strip_typename({
            "game": {
                "id": "g1",
                "__typename": "Game",
            },
            "__typename": "DropCampaign",
        })
        assert result == {"game": {"id": "g1"}}

    def test_removes_typename_in_lists(self) -> None:
        """Verify __typename is removed from items inside lists."""
        result = Command._strip_typename({
            "rewards": [
                {"id": "r1", "__typename": "Reward"},
                {"id": "r2", "__typename": "Reward"},
            ],
        })
        assert result == {"rewards": [{"id": "r1"}, {"id": "r2"}]}

    def test_handles_scalars(self) -> None:
        """Verify scalars and None pass through unchanged."""
        assert Command._strip_typename("hello") == "hello"
        assert Command._strip_typename(42) == 42
        assert Command._strip_typename(None) is None
        assert Command._strip_typename([1, 2, 3]) == [1, 2, 3]

    def test_removes_typename_deeply_nested(self) -> None:
        """Verify deeply nested __typename is removed."""
        data = {
            "data": {
                "user": {
                    "id": "u1",
                    "__typename": "User",
                    "campaigns": [
                        {
                            "id": "c1",
                            "__typename": "DropCampaign",
                            "game": {
                                "id": "g1",
                                "__typename": "Game",
                            },
                        },
                    ],
                },
            },
            "__typename": "Response",
        }
        result = Command._strip_typename(data)
        # Should have no __typename anywhere
        raw = str(result)
        assert "__typename" not in raw


# ---------------------------------------------------------------------------
# Channel display name tests
# ---------------------------------------------------------------------------


class ChannelDisplayNameTests(TestCase):
    """Display names must not degrade to lowercase logins."""

    def _campaign(self) -> DropCampaign:
        game: Game = Game.objects.create(
            twitch_id="display-name-game",
            display_name="Display Name Game",
        )
        return DropCampaign.objects.create(
            twitch_id="display-name-campaign",
            name="Display Name Campaign",
            game=game,
        )

    def test_lowercase_login_does_not_replace_capitalized_display_name(self) -> None:
        """A missing displayName fallback must not overwrite capitalization."""
        channel: Channel = Channel.objects.create(
            twitch_id="volcatwitch",
            name="volcatwitch",
            display_name="VolcaTwitch",
        )
        allow_schema: SunkwiBotDropCampaignACLSchema = (
            SunkwiBotDropCampaignACLSchema.model_validate({
                "isEnabled": True,
                "channels": [{"id": "volcatwitch", "name": "volcatwitch"}],
            })
        )

        Command()._process_allow_channels(
            allow_schema=allow_schema,
            campaign_obj=self._campaign(),
            verbose=False,
        )

        channel.refresh_from_db()
        assert channel.display_name == "VolcaTwitch"

    def test_capitalized_display_name_replaces_lowercase_fallback(self) -> None:
        """An explicit capitalized displayName should upgrade a lowercase row."""
        channel: Channel = Channel.objects.create(
            twitch_id="twynetv",
            name="twynetv",
            display_name="twynetv",
        )
        allow_schema: SunkwiBotDropCampaignACLSchema = (
            SunkwiBotDropCampaignACLSchema.model_validate({
                "isEnabled": True,
                "channels": [
                    {
                        "id": "twynetv",
                        "name": "twynetv",
                        "displayName": "TwyneTV",
                    },
                ],
            })
        )

        Command()._process_allow_channels(
            allow_schema=allow_schema,
            campaign_obj=self._campaign(),
            verbose=False,
        )

        channel.refresh_from_db()
        assert channel.display_name == "TwyneTV"

    def test_non_latin_display_name_is_not_replaced_by_login(self) -> None:
        """Uncased scripts like CJK and Korean must survive login fallbacks."""
        channel: Channel = Channel.objects.create(
            twitch_id="zousenpai",
            name="zousenpai",
            display_name="象先輩",
        )
        allow_schema: SunkwiBotDropCampaignACLSchema = (
            SunkwiBotDropCampaignACLSchema.model_validate({
                "isEnabled": True,
                "channels": [{"id": "zousenpai", "name": "zousenpai"}],
            })
        )

        Command()._process_allow_channels(
            allow_schema=allow_schema,
            campaign_obj=self._campaign(),
            verbose=False,
        )

        channel.refresh_from_db()
        assert channel.display_name == "象先輩"
        assert channel.display_names == ["象先輩", "zousenpai"]


# ---------------------------------------------------------------------------
# Historical import tests
# ---------------------------------------------------------------------------


@pytest.mark.no_zeal
class AllowChannelAdditiveTests(TestCase):
    """SunkwiBOT channel imports must never remove existing channels."""

    def _campaign(self) -> DropCampaign:
        game: Game = Game.objects.create(
            twitch_id="additive-game",
            display_name="Additive Game",
        )
        return DropCampaign.objects.create(
            twitch_id="additive-campaign",
            name="Additive Campaign",
            game=game,
        )

    def test_missing_channel_is_not_removed(self) -> None:
        """A SunkwiBOT response that omits a channel must leave it in place."""
        campaign: DropCampaign = self._campaign()
        dakillzor: Channel = Channel.objects.create(
            twitch_id="dakillzor",
            name="dakillzor",
            display_name="Dakillzor",
        )
        campaign.allow_channels.add(dakillzor)
        assert not DropCampaignAllowedChannel.pgh_event_model.objects.filter(
            pgh_label="channel_removed",
        ).exists()

        allow_schema: SunkwiBotDropCampaignACLSchema = (
            SunkwiBotDropCampaignACLSchema.model_validate({
                "isEnabled": True,
                "channels": [{"id": "someoneelse", "name": "someoneelse"}],
            })
        )
        Command()._process_allow_channels(
            allow_schema=allow_schema,
            campaign_obj=campaign,
            verbose=False,
        )

        assert campaign.allow_channels.filter(pk=dakillzor.pk).exists()
        assert campaign.allow_channels.count() == 2
        assert not DropCampaignAllowedChannel.pgh_event_model.objects.filter(
            pgh_label="channel_removed",
        ).exists()

    def test_empty_channel_list_does_not_clear(self) -> None:
        """An empty SunkwiBOT channel list must not clear the campaign ACL."""
        campaign: DropCampaign = self._campaign()
        channel: Channel = Channel.objects.create(
            twitch_id="kept-channel",
            name="kept_channel",
            display_name="KeptChannel",
        )
        campaign.allow_channels.add(channel)

        allow_schema: SunkwiBotDropCampaignACLSchema = (
            SunkwiBotDropCampaignACLSchema.model_validate({
                "isEnabled": True,
                "channels": [],
            })
        )
        Command()._process_allow_channels(
            allow_schema=allow_schema,
            campaign_obj=campaign,
            verbose=False,
        )

        assert campaign.allow_channels.filter(pk=channel.pk).exists()
        assert not DropCampaignAllowedChannel.pgh_event_model.objects.filter(
            pgh_label="channel_removed",
        ).exists()


@pytest.mark.no_zeal
class HistoricalImportTests(TestCase):
    """Tests for the --historical mode of import_twitch_drops_api."""

    def _init_caches(self, command: Command) -> None:
        """Initialise instance caches needed by import helpers."""
        command._org_cache = {}
        command._game_cache = {}
        command._channel_cache = {}
        command._benefit_cache = {}

    def test_parse_and_import_drops_from_raw(self) -> None:
        """Verify _parse_and_import_drops works with pre-loaded data."""
        command = Command()
        self._init_caches(command)
        count = command._parse_and_import_drops(
            raw_data=SAMPLE_DROPS_JSON,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        assert count == 1
        assert DropCampaign.objects.count() == 1
        campaign = DropCampaign.objects.get(twitch_id="reward-campaign-1")
        assert campaign.data_source == "SunkwiBOT/twitch-drops-api"

    def test_parse_and_import_rewards_from_raw(self) -> None:
        """Verify _parse_and_import_rewards works with pre-loaded data."""
        Game.objects.create(twitch_id="27471", display_name="Minecraft")
        command = Command()
        self._init_caches(command)
        count = command._parse_and_import_rewards(
            raw_data=SAMPLE_REWARDS_JSON,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        assert count == 1
        assert RewardCampaign.objects.count() == 1
        reward = RewardCampaign.objects.get(twitch_id="reward-campaign-2")
        assert reward.data_source == "SunkwiBOT/twitch-drops-api"

    def test_parse_and_import_drops_handles_typename(self) -> None:
        """Verify _parse_and_import_drops strips __typename before validation."""
        raw = [
            {
                "endAt": "2026-06-21T10:59:00Z",
                "gameBoxArtURL": "https://example.com/art.png",
                "gameDisplayName": "Test Game",
                "gameId": "test-game-1",
                "rewards": [
                    {
                        "id": "hist-campaign-1",
                        "self": None,
                        "allow": {"isEnabled": True},
                        "accountLinkURL": "https://example.com",
                        "description": "Historical",
                        "detailsURL": "https://example.com",
                        "endAt": "2026-06-21T10:59:00Z",
                        "eventBasedDrops": [],
                        "game": {
                            "id": "test-game-1",
                            "slug": "test",
                            "displayName": "Test Game",
                        },
                        "imageURL": "",
                        "name": "Historical Campaign",
                        "owner": {"id": "org-1", "name": "Test Org"},
                        "startAt": "2026-06-12T15:00:00Z",
                        "status": "ACTIVE",
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                ],
                "startAt": "2026-06-12T15:00:00Z",
                "__typename": "DropGroup",
            },
        ]
        command = Command()
        self._init_caches(command)
        count = command._parse_and_import_drops(
            raw_data=raw,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        assert count == 1
        assert DropCampaign.objects.filter(twitch_id="hist-campaign-1").exists()

    def test_parse_and_import_drops_with_null_owner(self) -> None:
        """Verify drops with owner: null validate and import without an org."""
        raw = [
            {
                "endAt": "2026-06-21T10:59:00Z",
                "gameBoxArtURL": "https://example.com/art.png",
                "gameDisplayName": "No Owner Game",
                "gameId": "no-owner-game-1",
                "rewards": [
                    {
                        "id": "no-owner-campaign-1",
                        "self": None,
                        "allow": {"isEnabled": True},
                        "accountLinkURL": "https://example.com",
                        "description": "No owner",
                        "detailsURL": "https://example.com",
                        "endAt": "2026-06-21T10:59:00Z",
                        "eventBasedDrops": [],
                        "game": {
                            "id": "no-owner-game-1",
                            "slug": "no-owner",
                            "displayName": "No Owner Game",
                        },
                        "imageURL": "",
                        "name": "No Owner Campaign",
                        "owner": None,
                        "startAt": "2026-06-12T15:00:00Z",
                        "status": "ACTIVE",
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                ],
                "startAt": "2026-06-12T15:00:00Z",
                "__typename": "DropGroup",
            },
        ]
        command = Command()
        self._init_caches(command)
        count: int = command._parse_and_import_drops(
            raw_data=raw,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        assert count == 1
        campaign: DropCampaign = DropCampaign.objects.get(
            twitch_id="no-owner-campaign-1",
        )
        assert campaign.data_source == "SunkwiBOT/twitch-drops-api"
        game: Game = Game.objects.get(twitch_id="no-owner-game-1")
        assert not game.owners.exists()
        assert Organization.objects.count() == 0

    def test_parse_and_import_drops_with_null_game(self) -> None:
        """Verify drops with rewards[].game: null validate and import."""
        raw = [
            {
                "endAt": "2026-06-21T10:59:00Z",
                "gameBoxArtURL": "https://example.com/art.png",
                "gameDisplayName": "No Game Game",
                "gameId": "no-game-group-1",
                "rewards": [
                    {
                        "id": "no-game-campaign-1",
                        "self": None,
                        "allow": {"isEnabled": True},
                        "accountLinkURL": "https://example.com",
                        "description": "No game",
                        "detailsURL": "https://example.com",
                        "endAt": "2026-06-21T10:59:00Z",
                        "eventBasedDrops": [],
                        "game": None,
                        "imageURL": "",
                        "name": "No Game Campaign",
                        "owner": None,
                        "startAt": "2026-06-12T15:00:00Z",
                        "status": "ACTIVE",
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                ],
                "startAt": "2026-06-12T15:00:00Z",
                "__typename": "DropGroup",
            },
        ]
        command = Command()
        self._init_caches(command)
        count: int = command._parse_and_import_drops(
            raw_data=raw,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=True,
        )
        assert count == 1
        campaign: DropCampaign = DropCampaign.objects.get(
            twitch_id="no-game-campaign-1",
        )
        assert campaign.data_source == "SunkwiBOT/twitch-drops-api"
        # Group-level game is still imported
        game: Game = Game.objects.get(twitch_id="no-game-group-1")
        assert game.display_name == "No Game Game"

    @patch.object(Command, "_process_historical")
    def test_historical_flag_calls_process_historical(
        self,
        mock_process: MagicMock,
    ) -> None:
        """Verify --historical calls _process_historical instead of fetching URLs."""
        mock_process.return_value = (5, 2, [])
        command = Command()
        command.handle(
            historical=True,
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=False,
            drops_url="",
            rewards_url="",
            drops_only=False,
            rewards_only=False,
            no_skip_latest=False,
            max_commits=0,
            git_dir="",
        )
        mock_process.assert_called_once()

    def test_start_from_requires_historical(self) -> None:
        """Verify --start-from without --historical raises CommandError."""
        command = Command()
        with pytest.raises(CommandError):
            command.handle(
                historical=False,
                start_from="02dabf1",
                source="SunkwiBOT/twitch-drops-api",
                verbose=False,
                crash_on_error=False,
                drops_url="",
                rewards_url="",
                drops_only=False,
                rewards_only=False,
                no_skip_latest=False,
                max_commits=0,
                git_dir="",
            )

    @patch.object(Command, "_process_historical")
    def test_start_from_forwarded_to_process_historical(
        self,
        mock_process: MagicMock,
    ) -> None:
        """Verify --start-from is forwarded to _process_historical."""
        mock_process.return_value = (0, 0, [])
        command = Command()
        command.handle(
            historical=True,
            start_from="02dabf1",
            source="SunkwiBOT/twitch-drops-api",
            verbose=False,
            crash_on_error=False,
            drops_url="",
            rewards_url="",
            drops_only=False,
            rewards_only=False,
            no_skip_latest=False,
            max_commits=0,
            git_dir="",
        )
        mock_process.assert_called_once()
        assert mock_process.call_args.kwargs["start_from"] == "02dabf1"

    def _fake_git_for_from_commit(self) -> MagicMock:
        """Return a mocked _git that resolves 02dabf1 to a full SHA."""
        full_sha: LiteralString = "02dabf1" + "c" * 33

        def fake_git(git_dir: str, *args: str, check: bool = True) -> str:
            if args and args[0] == "rev-parse":
                return f"{full_sha}\n"
            if args and args[0] == "show":
                return "1700000200\n"
            return ""

        return patch.object(Command, "_git", side_effect=fake_git)  # pyright: ignore[reportReturnType]

    def test_filter_from_commit_matches_sha(self) -> None:
        """Verify _filter_from_commit keeps commits at/after the given SHA."""
        command = Command()
        self._init_caches(command)
        lines: list[str] = [
            f"{'a' * 40} 1700000000",
            f"{'b' * 40} 1700000100",
            "02dabf1" + "c" * 33 + " 1700000200",
            f"{'d' * 40} 1700000300",
        ]
        with self._fake_git_for_from_commit():
            kept: list[str] = command._filter_from_commit(
                git_dir="repo",
                lines=lines,
                file_path="drops.json",
                start_from="02dabf1",
            )
        assert kept == lines[2:]

    def test_filter_from_commit_falls_back_to_timestamp(self) -> None:
        """Verify a commit not in a file's history still skips older commits."""
        command = Command()
        self._init_caches(command)
        lines: list[str] = [
            f"{'a' * 40} 1700000000",
            f"{'b' * 40} 1700000100",
            f"{'c' * 40} 1700000200",
            f"{'d' * 40} 1700000300",
        ]
        with self._fake_git_for_from_commit():
            kept = command._filter_from_commit(
                git_dir="repo",
                lines=lines,
                file_path="rewards.json",
                start_from="02dabf1",
            )
        # 02dabf1 never touched rewards.json; fall back to its commit time
        assert kept == lines[2:]

    def test_filter_from_commit_unknown_sha(self) -> None:
        """Verify an unresolvable SHA raises CommandError."""
        command = Command()
        self._init_caches(command)

        def fake_git(git_dir: str, *args: str, check: bool = True) -> str:
            raise subprocess.CalledProcessError(128, "git")

        with (
            patch.object(Command, "_git", side_effect=fake_git),
            pytest.raises(CommandError),
        ):
            command._filter_from_commit(
                git_dir="repo",
                lines=[f"{'a' * 40} 1700000000"],
                file_path="drops.json",
                start_from="deadbeef",
            )

    def test_import_historical_file_start_from(self) -> None:
        """Verify --start-from only imports commits at/after the given SHA."""
        command = Command()
        self._init_caches(command)
        sha_a: LiteralString = "a" * 40
        sha_b: LiteralString = "02dabf1" + "c" * 33
        sha_d: LiteralString = "d" * 40
        lines: list[str] = [
            f"{sha_a} 1700000000",
            f"{sha_b} 1700000200",
            f"{sha_d} 1700000300",
        ]
        contents: dict[str, str] = {
            sha_a: json.dumps([
                {"gameId": "g1", "gameDisplayName": "G1", "rewards": []},
            ]),
            sha_b: json.dumps([
                {"gameId": "g2", "gameDisplayName": "G2", "rewards": []},
            ]),
            sha_d: json.dumps([
                {"gameId": "g3", "gameDisplayName": "G3", "rewards": []},
            ]),
        }

        def fake_git(git_dir: str, *args: str, check: bool = True) -> str:
            if args and args[0] == "log":
                return "\n".join(lines) + "\n"
            if args and args[0] == "rev-parse":
                return f"{sha_b}\n"
            if args and args[0] == "show":
                return (
                    "1700000200\n"
                    if args[1] == "-s"
                    else contents[args[1].split(":")[0]]
                )
            return ""

        with (
            patch.object(Command, "_git", side_effect=fake_git),
            patch.object(
                Command,
                "_parse_and_import_drops",
                return_value=0,
            ) as mock_import,
        ):
            command._import_historical_file(
                git_dir="repo",
                file_path="drops.json",
                source="test",
                verbose=False,
                crash_on_error=False,
                skip_latest=False,
                max_commits=0,
                skip_existing=False,
                start_from="02dabf1",
            )

        labels = [call.kwargs["label"] for call in mock_import.call_args_list]
        assert labels == ["drops@02dabf1", "drops@ddddddd"]
