import xml.etree.ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]
from typing import TYPE_CHECKING

from django.urls import reverse

if TYPE_CHECKING:
    from django.test.client import Client
    from pytest_django.fixtures import SettingsWrapper


def _extract_locs(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)  # ruff:ignore[suspicious-xml-element-tree-usage]
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [el.text for el in root.findall(".//s:loc", ns) if el.text]


def test_sitemap_static_contains_expected_links(
    client: Client,
    settings: SettingsWrapper,
) -> None:
    """Ensure the static sitemap contains the main site links across apps.

    This test checks a representative set of URLs from core, twitch, kick, and
    youtube apps as well as some misc static files like /robots.txt.
    """
    # Ensure deterministic BASE_URL
    settings.BASE_URL = "https://ttvdrops.lovinator.space"  # pyright: ignore[reportAttributeAccessIssue]

    response = client.get(reverse("sitemap-static"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/xml"

    locs = _extract_locs(response.content)

    base = settings.BASE_URL.rstrip("/")  # pyright: ignore[reportAttributeAccessIssue]

    expected_paths = [
        reverse("core:dashboard"),
        reverse("core:search"),
        reverse("core:debug"),
        reverse("core:dataset_backups"),
        reverse("core:docs_rss"),
        reverse("core:campaign_feed"),
        reverse("core:game_feed"),
        reverse("core:organization_feed"),
        reverse("core:reward_campaign_feed"),
        reverse("core:campaign_feed_atom"),
        reverse("core:campaign_feed_discord"),
        reverse("twitch:dashboard"),
        reverse("twitch:campaign_list"),
        reverse("twitch:games_grid"),
        reverse("twitch:games_list"),
        reverse("twitch:channel_list"),
        reverse("twitch:badge_list"),
        reverse("twitch:emote_gallery"),
        reverse("twitch:org_list"),
        reverse("twitch:reward_campaign_list"),
        reverse("twitch:export_campaigns_csv"),
        reverse("kick:dashboard"),
        reverse("kick:campaign_list"),
        reverse("kick:game_list"),
        reverse("kick:organization_list"),
        reverse("kick:campaign_feed"),
        reverse("kick:game_feed"),
        reverse("kick:organization_feed"),
        reverse("youtube:index"),
        "/about/",
        "/robots.txt",
    ]

    expected: set[str] = {base + p for p in expected_paths}

    for url in expected:
        assert url in locs, f"Expected {url} in sitemap, found {len(locs)} entries"
