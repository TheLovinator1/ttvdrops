import json
from datetime import timedelta
from typing import TYPE_CHECKING

from django.test import Client
from django.test import TestCase
from django.utils import timezone

from chzzk.models import ChzzkCampaign
from kick.models import KickCategory
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from django.http import HttpResponse


def _rpc(
    client: Client,
    method: str,
    params: dict[str, object] | None = None,
    *,
    request_id: int = 1,
) -> HttpResponse:
    """POST a JSON-RPC message to the MCP server.

    Returns:
        The HTTP response from the MCP view.
    """
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    return client.post(
        "/mcp",
        data=json.dumps(body),
        content_type="application/json",
    )


def _call_tool(
    client: Client,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Call an MCP tool and return its structured result.

    Returns:
        The structured content returned by the tool.
    """
    response = _rpc(
        client,
        "tools/call",
        {"name": name, "arguments": arguments},
    )
    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["isError"] is False, payload["content"]
    return payload["structuredContent"]


class TestMCPServer(TestCase):
    """Tests for the TTVDrops MCP server."""

    def setUp(self) -> None:
        """Create campaigns across all three platforms."""
        self.now = timezone.now()
        self.twitch_org = Organization.objects.create(
            twitch_id="org-1",
            name="Acme Games",
        )
        self.twitch_game = Game.objects.create(
            twitch_id="game-1",
            name="Rust",
            display_name="Rust",
            slug="rust",
        )
        self.twitch_game.owners.add(self.twitch_org)
        self.twitch_campaign = DropCampaign.objects.create(
            twitch_id="twitch-campaign-1",
            name="Rust Twitch Drop",
            description="Watch streams to earn a skin.",
            game=self.twitch_game,
            start_at=self.now - timedelta(hours=1),
            end_at=self.now + timedelta(hours=1),
            is_fully_imported=True,
        )
        drop = TimeBasedDrop.objects.create(
            twitch_id="twitch-drop-1",
            name="Rust Skin",
            campaign=self.twitch_campaign,
            required_minutes_watched=30,
        )
        benefit = DropBenefit.objects.create(
            twitch_id="benefit-1",
            name="Rust Skin",
            distribution_type="DIRECT_ENTITLEMENT",
        )
        drop.benefits.add(benefit)

        kick_org = KickOrganization.objects.create(
            kick_id="kick-org-1",
            name="Acme Kick Games",
        )
        kick_category = KickCategory.objects.create(
            kick_id=42,
            name="Rust",
            slug="rust",
        )
        KickDropCampaign.objects.create(
            kick_id="kick-campaign-1",
            name="Rust Kick Drop",
            category=kick_category,
            organization=kick_org,
            starts_at=self.now + timedelta(days=2),
            ends_at=self.now + timedelta(days=3),
            is_fully_imported=True,
        )

        ChzzkCampaign.objects.create(
            campaign_no=9001,
            title="Rust Chzzk Drop",
            description="Chzzk rewards.",
            category_type="GAME",
            category_id="chzzk-rust",
            category_value="Rust",
            service_id="chzzk",
            state="ACTIVE",
            start_date=self.now + timedelta(days=5),
            end_date=self.now + timedelta(days=6),
            has_ios_based_reward=False,
            drops_campaign_not_started=False,
        )

    def test_tools_list(self) -> None:
        """The server advertises all search and lookup tools."""
        response = _rpc(self.client, "tools/list")
        assert response.status_code == 200
        names = {tool["name"] for tool in response.json()["result"]["tools"]}
        assert {
            "search_drops",
            "list_drops_between_dates",
            "get_drop",
            "search_games",
            "search_organizations",
            "get_stats",
        } <= names

    def test_search_drops_across_platforms(self) -> None:
        """Text search returns matching campaigns from every platform."""
        result = _call_tool(self.client, "search_drops", {"query": "Rust"})
        assert result["count"] == 3
        platforms = {item["platform"] for item in result["items"]}
        assert platforms == {"twitch", "kick", "chzzk"}

    def test_search_drops_filters_by_platform_and_status(self) -> None:
        """Platform and status filters narrow the results."""
        result = _call_tool(
            self.client,
            "search_drops",
            {"platform": "twitch", "status": "active"},
        )
        assert result["count"] == 1
        item = result["items"][0]
        assert item["platform"] == "twitch"
        assert item["status"] == "active"
        assert item["game"] == "Rust"
        assert item["organization"] == "Acme Games"
        assert item["reward_count"] == 1

    def test_list_drops_between_dates(self) -> None:
        """The date range tool includes campaigns overlapping the range."""
        start = (self.now - timedelta(days=1)).date().isoformat()
        end = (self.now + timedelta(days=1)).date().isoformat()
        result = _call_tool(
            self.client,
            "list_drops_between_dates",
            {"start_date": start, "end_date": end},
        )
        assert result["count"] == 1
        assert result["items"][0]["id"] == "twitch-campaign-1"

    def test_get_drop_includes_rewards(self) -> None:
        """Campaign lookup returns rewards and requirements."""
        result = _call_tool(
            self.client,
            "get_drop",
            {"platform": "twitch", "id": "twitch-campaign-1"},
        )
        assert result["name"] == "Rust Twitch Drop"
        assert result["game"] == "Rust"
        assert result["rewards"][0]["name"] == "Rust Skin"
        assert result["rewards"][0]["requirement"] == "Watch 30 minutes"

    def test_get_drop_unknown_campaign_is_in_band_error(self) -> None:
        """A missing campaign is reported as a tool error, not a crash."""
        response = _rpc(
            self.client,
            "tools/call",
            {
                "name": "get_drop",
                "arguments": {"platform": "twitch", "id": "missing"},
            },
        )
        payload = response.json()["result"]
        assert payload["isError"] is True
        assert "No Twitch drop campaign" in payload["content"][0]["text"]

    def test_get_kick_and_chzzk_drop(self) -> None:
        """Campaign lookup works for Kick and Chzzk too."""
        kick = _call_tool(
            self.client,
            "get_drop",
            {"platform": "kick", "id": "kick-campaign-1"},
        )
        assert kick["game"] == "Rust"
        assert kick["organization"] == "Acme Kick Games"

        chzzk = _call_tool(
            self.client,
            "get_drop",
            {"platform": "chzzk", "id": "9001"},
        )
        assert chzzk["name"] == "Rust Chzzk Drop"
        assert chzzk["game"] == "Rust"

    def test_invalid_date_is_in_band_error(self) -> None:
        """An unparseable date is reported in-band so the model can retry."""
        response = _rpc(
            self.client,
            "tools/call",
            {
                "name": "search_drops",
                "arguments": {"starts_after": "not-a-date"},
            },
        )
        payload = response.json()["result"]
        assert payload["isError"] is True
        assert "Invalid date" in payload["content"][0]["text"]

    def test_search_games_and_organizations(self) -> None:
        """Games and organizations can be searched across platforms."""
        games = _call_tool(self.client, "search_games", {"query": "Rust"})
        assert games["count"] == 2
        assert {item["platform"] for item in games["items"]} == {"twitch", "kick"}

        organizations = _call_tool(
            self.client,
            "search_organizations",
            {"query": "Acme"},
        )
        assert organizations["count"] == 2
        assert {item["platform"] for item in organizations["items"]} == {
            "twitch",
            "kick",
        }

    def test_get_stats(self) -> None:
        """Stats count campaigns on each platform."""
        result = _call_tool(self.client, "get_stats", {})
        assert result["total"] == 3
        assert result["twitch"]["active"] == 1
        assert result["kick"]["upcoming"] == 1
        assert result["chzzk"]["upcoming"] == 1

    def test_modern_protocol(self) -> None:
        """The server serves MCP 2026-07-28 with its required headers."""
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_stats",
                "arguments": {},
                "_meta": meta,
            },
        }
        response = self.client.post(
            "/mcp",
            data=json.dumps(body),
            content_type="application/json",
            headers={
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "get_stats",
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["total"] == 3

    def test_non_post_is_rejected(self) -> None:
        """The server only accepts POST requests."""
        response = self.client.get("/mcp")
        assert response.status_code == 405
