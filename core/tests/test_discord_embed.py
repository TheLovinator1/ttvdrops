"""Tests for Discord component-embed payload generation."""

import json
import re
from typing import TYPE_CHECKING
from typing import Any

import pytest
from django.urls import reverse

from core.discord_embed import MAX_EMBED_BYTES
from core.discord_embed import build_component_embed
from core.discord_embed import embed_buttons
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from django.http import HttpResponse
    from django.test import Client

EMBED_PATTERN = re.compile(
    r'<script id="discord:component-embed" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _parse_embed(content: str) -> dict[str, Any]:
    """Extract and parse the component embed payload from a response body.

    Args:
        content: Rendered response body.

    Returns:
        dict[str, Any]: Parsed component embed payload.
    """
    match = EMBED_PATTERN.search(content)
    assert match is not None
    return json.loads(match.group(1))


def _buttons(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return link buttons from a parsed component embed payload."""
    for component in payload["component"]["components"]:
        if component.get("type") == 1:
            return component["components"]
    return []


def _gallery(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return media gallery items from a parsed component embed payload."""
    for component in payload["component"]["components"]:
        if component.get("type") == 12:
            return component["items"]
    return []


def test_builds_container_with_title_description_and_buttons() -> None:
    """The payload should use a single top-level container component."""
    payload: dict[str, Any] = json.loads(
        build_component_embed(
            title="Test title",
            description="Test description",
            image_url="https://example.com/image.webp",
            buttons=embed_buttons(("Details", "https://example.com/details")),
        ),
    )

    component: dict[str, Any] = payload["component"]
    assert component["type"] == 17
    assert len(component["components"]) > 0
    assert all(item["type"] != 17 for item in component["components"])

    text_component: dict[str, Any] = component["components"][0]
    assert text_component["type"] == 9
    assert (
        text_component["components"][0]["content"] == "# Test title\nTest description"
    )
    assert (
        text_component["accessory"]["media"]["url"] == "https://example.com/image.webp"
    )

    buttons: list[dict[str, str]] = _buttons(payload)
    assert buttons == [
        {
            "type": 2,
            "style": 5,
            "label": "Details",
            "url": "https://example.com/details",
        },
    ]


def test_escapes_html_and_markdown_characters() -> None:
    """Titles must not be able to break out of the script element."""
    raw: str = build_component_embed(
        title="<script>alert(1)</script> *bold*",
        description="A & B",
    )

    assert "<" not in raw
    assert ">" not in raw
    assert "&" not in raw
    assert "\\u003cscript" in raw
    assert "\\u0026" in raw

    payload: dict[str, Any] = json.loads(raw)
    content: str = payload["component"]["components"][0]["content"]
    assert "<script" in content
    assert "\\*bold\\*" in content


def test_builds_media_gallery() -> None:
    """Gallery images should be emitted as a Media Gallery component."""
    payload: dict[str, Any] = json.loads(
        build_component_embed(
            title="Gallery",
            gallery=[
                {"url": "https://example.com/1.webp", "description": "First"},
                {"url": "https://example.com/2.webp"},
                {"url": "javascript:alert(1)", "description": "Invalid"},
            ],
        ),
    )

    gallery: dict[str, Any] = next(
        component
        for component in payload["component"]["components"]
        if component["type"] == 12
    )
    assert [item["media"]["url"] for item in gallery["items"]] == [
        "https://example.com/1.webp",
        "https://example.com/2.webp",
    ]
    assert gallery["items"][0]["description"] == "First"
    assert "description" not in gallery["items"][1]


def test_limits_buttons_and_skips_invalid_urls() -> None:
    """Only link buttons with valid absolute HTTP(S) URLs are kept."""
    buttons: list[dict[str, str]] = embed_buttons(
        ("One", "https://example.com/1"),
        ("Two", "https://example.com/2"),
        ("Three", "https://example.com/3"),
        ("Four", "https://example.com/4"),
        ("Five", "https://example.com/5"),
        ("Six", "https://example.com/6"),
        ("Invalid", "javascript:alert(1)"),
        ("Empty", ""),
    )
    payload: dict[str, Any] = json.loads(
        build_component_embed(title="Buttons", buttons=buttons),
    )

    assert [button["label"] for button in _buttons(payload)] == [
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
    ]


def test_payload_stays_under_size_limit() -> None:
    """Oversized optional content should be trimmed instead of exceeding the limit."""
    raw: str = build_component_embed(
        title="A" * 500,
        description="B" * 2000,
        image_url="https://example.com/" + "c" * 1500 + ".webp",
        gallery=[
            {"url": "https://example.com/" + "d" * 300 + ".webp"},
            {"url": "https://example.com/" + "e" * 300 + ".webp"},
            {"url": "https://example.com/" + "f" * 300 + ".webp"},
        ],
        buttons=embed_buttons(
            ("One", "https://example.com/" + "1" * 400),
            ("Two", "https://example.com/" + "2" * 400),
            ("Three", "https://example.com/" + "3" * 400),
            ("Four", "https://example.com/" + "4" * 400),
            ("Five", "https://example.com/" + "5" * 400),
        ),
    )

    assert raw
    assert len(raw.encode("utf-8")) <= MAX_EMBED_BYTES
    json.loads(raw)


@pytest.mark.django_db
def test_campaign_detail_includes_details_connect_and_streams_buttons(
    client: Client,
) -> None:
    """Campaign pages should link to details, account connect, and streams."""
    game: Game = Game.objects.create(
        twitch_id="g1",
        name="Game",
        display_name="Game",
        slug="game-slug",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="c1",
        name="Campaign",
        description="A test campaign",
        details_url="https://example.com/details",
        account_link_url="https://example.com/connect",
        game=game,
        operation_names=["DropCampaignDetails"],
    )
    drop: TimeBasedDrop = TimeBasedDrop.objects.create(
        twitch_id="d1",
        name="Drop",
        campaign=campaign,
    )
    for index, name in enumerate(("First reward", "Second reward"), start=1):
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id=f"b{index}",
            name=name,
            image_asset_url=f"https://example.com/reward-{index}.webp",
        )
        DropBenefitEdge.objects.create(drop=drop, benefit=benefit)

    response: HttpResponse = client.get(
        reverse("twitch:campaign_detail", args=[campaign.twitch_id]),
    )
    payload: dict[str, Any] = _parse_embed(response.content.decode())
    buttons: list[dict[str, str]] = _buttons(payload)

    labels: list[str] = [button["label"] for button in buttons]
    assert labels == ["Details", "Connect", "Twitch streams", "All Game campaigns"]
    assert buttons[0]["url"] == "https://example.com/details"
    assert buttons[1]["url"] == "https://example.com/connect"
    assert (
        buttons[2]["url"]
        == "https://www.twitch.tv/directory/category/game-slug?filter=drops"
    )

    gallery: list[dict[str, Any]] = _gallery(payload)
    assert [item["media"]["url"] for item in gallery] == [
        "https://example.com/reward-2.webp",
    ]
    assert gallery[0]["description"] == "Second reward"


@pytest.mark.django_db
def test_home_page_includes_embed(client: Client) -> None:
    """Every page rendered from base.html should include a valid embed."""
    response: HttpResponse = client.get(reverse("core:dashboard"))
    content: str = response.content.decode()
    payload: dict[str, Any] = _parse_embed(content)

    assert payload["component"]["type"] == 17
    assert len(_buttons(payload)) <= 5

    match = EMBED_PATTERN.search(content)
    assert match is not None
    assert len(match.group(1).encode("utf-8")) <= MAX_EMBED_BYTES


def test_api_docs_page_includes_embed(client: Client) -> None:
    """The API docs page bypasses base.html but should still have an embed."""
    response: HttpResponse = client.get(reverse("api-v1:openapi-view"))
    content: str = response.content.decode()
    payload: dict[str, Any] = _parse_embed(content)

    assert payload["component"]["type"] == 17
    assert [button["label"] for button in _buttons(payload)] == [
        "API docs",
        "OpenAPI schema",
        "Datasets",
        "Twitch Drops",
    ]
