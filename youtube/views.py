import json
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.shortcuts import render

from core.base_url import build_absolute_uri

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


PAGE_TITLE = "YouTube channels with rewards"
PAGE_DESCRIPTION = "Browse YouTube channels listed as reward-enabled, including Call of Duty, Blizzard, Fortnite, Riot Games, and more."


def index(request: HttpRequest) -> HttpResponse:
    """Render a minimal list of YouTube channels with known drops-enabled organizations.

    Returns:
            HttpResponse: Rendered index page for YouTube drops channels.
    """
    channels: list[dict[str, str]] = [
        {
            "organization": "Activision (Call of Duty)",
            "channel": "Call of Duty",
            "url": "https://www.youtube.com/channel/UCbLIqv9Puhyp9_ZjVtfOy7w",
        },
        {
            "organization": "Battle.net / Blizzard",
            "channel": "PlayOverwatch",
            "url": "https://www.youtube.com/c/playoverwatch/featured",
        },
        {
            "organization": "Battle.net / Blizzard",
            "channel": "Hearthstone",
            "url": "https://www.youtube.com/c/Hearthstone/featured",
        },
        {
            "organization": "Electronic Arts",
            "channel": "FIFA",
            "url": "https://www.youtube.com/channel/UCFA6YGp5lvgayO20lk7_Ung",
        },
        {
            "organization": "Electronic Arts",
            "channel": "EA Madden NFL",
            "url": "https://www.youtube.com/@EAMaddenNFL",
        },
        {
            "organization": "Epic Games",
            "channel": "Fortnite",
            "url": "https://www.youtube.com/user/epicfortnite",
        },
        {
            "organization": "Garena",
            "channel": "Free Fire",
            "url": "https://www.youtube.com/channel/UC_vVy4OI86F0amXqFN_zTMg",
        },
        {
            "organization": "Krafton (PUBG)",
            "channel": "PUBG: BATTLEGROUNDS",
            "url": "https://www.youtube.com/channel/UCTDO0RgowRyaAEUrPnBAg4g",
        },
        {
            "organization": "MLBB",
            "channel": "Mobile Legends: Bang Bang",
            "url": "https://www.youtube.com/channel/UCqmld-BIYME2i_ooRTo1EOg",
        },
        {
            "organization": "NBA",
            "channel": "NBA",
            "url": "https://www.youtube.com/user/NBA",
        },
        {
            "organization": "NFL",
            "channel": "NFL",
            "url": "https://www.youtube.com/@NFL",
        },
        {
            "organization": "PUBG Mobile",
            "channel": "PUBG MOBILE",
            "url": "https://www.youtube.com/channel/UCTDO0RgowRyaAEUrPnBAg4g",
        },
        {
            "organization": "Riot Games",
            "channel": "Riot Games",
            "url": "https://www.youtube.com/user/RiotGamesInc",
        },
        {
            "organization": "Riot Games",
            "channel": "LoL Esports",
            "url": "https://www.youtube.com/lolesports",
        },
        {
            "organization": "Supercell",
            "channel": "Clash Royale",
            "url": "https://www.youtube.com/channel/UC_F8DoJf9MZogEOU51TpTbQ",
        },
        {
            "organization": "Ubisoft",
            "channel": "Ubisoft",
            "url": "https://www.youtube.com/user/ubisoft",
        },
    ]

    grouped_channels: dict[str, list[dict[str, str]]] = defaultdict(list)
    for channel in channels:
        grouped_channels[channel["organization"]].append(channel)

    organization_groups: list[dict[str, str | list[dict[str, str]]]] = []
    for organization in sorted(grouped_channels.keys(), key=str.lower):
        sorted_items: list[dict[str, str]] = sorted(
            grouped_channels[organization],
            key=lambda item: item["channel"].lower(),
        )
        organization_groups.append({
            "organization": organization,
            "channels": sorted_items,
        })

    list_items: list[dict[str, Any]] = [
        {
            "@type": "ListItem",
            "position": position,
            "item": {
                "@type": "Organization",
                "name": group["organization"],
                "sameAs": [ch["url"] for ch in group["channels"]],  # type: ignore[index]
            },
        }
        for position, group in enumerate(organization_groups, start=1)
    ]

    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": PAGE_TITLE,
        "description": PAGE_DESCRIPTION,
        "url": build_absolute_uri(request=request),
        "itemListElement": list_items,
    }

    context: dict[str, Any] = {
        "page_title": PAGE_TITLE,
        "page_description": PAGE_DESCRIPTION,
        "organization_groups": organization_groups,
        "schema_data": json.dumps(schema),
    }
    return render(request=request, template_name="youtube/index.html", context=context)
