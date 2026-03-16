from collections import defaultdict
from typing import TYPE_CHECKING

from django.shortcuts import render

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


def index(request: HttpRequest) -> HttpResponse:
    """Render a minimal list of YouTube channels with known drops-enabled partners.

    Returns:
            HttpResponse: Rendered index page for YouTube drops channels.
    """
    channels: list[dict[str, str]] = [
        {
            "partner": "Activision (Call of Duty)",
            "channel": "Call of Duty",
            "url": "https://www.youtube.com/channel/UCbLIqv9Puhyp9_ZjVtfOy7w",
        },
        {
            "partner": "Battle.net / Blizzard",
            "channel": "PlayOverwatch",
            "url": "https://www.youtube.com/c/playoverwatch/featured",
        },
        {
            "partner": "Battle.net / Blizzard",
            "channel": "Hearthstone",
            "url": "https://www.youtube.com/c/Hearthstone/featured",
        },
        {
            "partner": "Electronic Arts",
            "channel": "FIFA",
            "url": "https://www.youtube.com/channel/UCFA6YGp5lvgayO20lk7_Ung",
        },
        {
            "partner": "Electronic Arts",
            "channel": "EA Madden NFL",
            "url": "https://www.youtube.com/@EAMaddenNFL",
        },
        {
            "partner": "Epic Games",
            "channel": "Fortnite",
            "url": "https://www.youtube.com/user/epicfortnite",
        },
        {
            "partner": "Garena",
            "channel": "Free Fire",
            "url": "https://www.youtube.com/channel/UC_vVy4OI86F0amXqFN_zTMg",
        },
        {
            "partner": "Krafton (PUBG)",
            "channel": "PUBG: BATTLEGROUNDS",
            "url": "https://www.youtube.com/channel/UCTDO0RgowRyaAEUrPnBAg4g",
        },
        {
            "partner": "MLBB",
            "channel": "Mobile Legends: Bang Bang",
            "url": "https://www.youtube.com/channel/UCqmld-BIYME2i_ooRTo1EOg",
        },
        {
            "partner": "NBA",
            "channel": "NBA",
            "url": "https://www.youtube.com/user/NBA",
        },
        {
            "partner": "NFL",
            "channel": "NFL",
            "url": "https://www.youtube.com/@NFL",
        },
        {
            "partner": "PUBG Mobile",
            "channel": "PUBG MOBILE",
            "url": "https://www.youtube.com/channel/UCTDO0RgowRyaAEUrPnBAg4g",
        },
        {
            "partner": "Riot Games",
            "channel": "Riot Games",
            "url": "https://www.youtube.com/user/RiotGamesInc",
        },
        {
            "partner": "Riot Games",
            "channel": "LoL Esports",
            "url": "https://www.youtube.com/lolesports",
        },
        {
            "partner": "Supercell",
            "channel": "Clash Royale",
            "url": "https://www.youtube.com/channel/UC_F8DoJf9MZogEOU51TpTbQ",
        },
        {
            "partner": "Ubisoft",
            "channel": "Ubisoft",
            "url": "https://www.youtube.com/user/ubisoft",
        },
    ]

    grouped_channels: dict[str, list[dict[str, str]]] = defaultdict(list)
    for channel in channels:
        grouped_channels[channel["partner"]].append(channel)

    partner_groups: list[dict[str, str | list[dict[str, str]]]] = []
    for partner in sorted(grouped_channels.keys(), key=str.lower):
        sorted_items: list[dict[str, str]] = sorted(
            grouped_channels[partner],
            key=lambda item: item["channel"].lower(),
        )
        partner_groups.append({"partner": partner, "channels": sorted_items})

    context: dict[str, list[dict[str, str | list[dict[str, str]]]]] = {
        "partner_groups": partner_groups,
    }
    return render(request=request, template_name="youtube/index.html", context=context)
