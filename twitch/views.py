from __future__ import annotations

import csv
import datetime
import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from urllib.parse import urlencode

from django.core.paginator import EmptyPage
from django.core.paginator import Page
from django.core.paginator import PageNotAnInteger
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.db.models.query import QuerySet
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView
from django.views.generic import ListView

from core.base_url import build_absolute_uri
from core.campaign_timing import campaign_timing_stats
from core.discord_embed import TWITCH_ACCENT_COLOR
from core.discord_embed import embed_buttons
from core.discord_embed import escape_markdown
from twitch.history import MODEL_FILTER_LABELS
from twitch.history import HistoryPage
from twitch.history import build_event_detail
from twitch.history import build_event_rows
from twitch.history import campaign_history_querysets
from twitch.history import channel_history_querysets
from twitch.history import event_detail_by_slug
from twitch.history import game_history_querysets
from twitch.history import history_page
from twitch.history import history_page_for_querysets
from twitch.history import organization_history_querysets
from twitch.history import reward_campaign_history_querysets
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet
    from django.http import HttpRequest

    from core.seo import SeoMeta

logger: logging.Logger = logging.getLogger("ttvdrops.views")

type HistoryEntity = DropCampaign | Game | Organization | Channel | RewardCampaign

MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05
MAX_EMBED_CHANNELS = 8
DEFAULT_SITE_DESCRIPTION = "Archive of Twitch drops, campaigns, rewards, and more."
MIN_META_DESCRIPTION_LENGTH = 120
MAX_META_DESCRIPTION_LENGTH = 160
META_DESCRIPTION_PADDING = (
    "Explore the Twitch drops archive, follow new campaigns via RSS feeds, "
    "and query rewards with the free JSON API on ttvdrops."
)


def _gallery_items(
    items: Iterable[tuple[str, str]],
    *,
    exclude_url: str = "",
    limit: int = 6,
) -> list[dict[str, str]]:
    """Build deduplicated media gallery items for Discord component embeds.

    Args:
        items: ``(description, image_url)`` pairs.
        exclude_url: Image URL to skip, typically the embed thumbnail.
        limit: Maximum number of gallery items.

    Returns:
        list[dict[str, str]]: Gallery items with ``url`` and ``description``.
    """
    gallery: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for description, image_url in items:
        if not image_url or image_url == exclude_url or image_url in seen_urls:
            continue
        seen_urls.add(image_url)
        gallery.append({"url": image_url, "description": description})
        if len(gallery) >= limit:
            break
    return gallery


def _pick_owner(owners: list[Organization]) -> Organization | None:
    """Return the most relevant owner, skipping generic Twitch org names when possible.

    Args:
        owners: List of Organization objects associated with a game.

    Returns:
        The first non-generic owner, or the first owner if all are generic, or None.
    """
    if not owners:
        return None

    # Twitch Gaming is Twitch's own generic publishing label; when a game has multiple
    # owners we prefer the actual game publisher over it for attribution.
    generic_orgs: frozenset[str] = frozenset({"Twitch Gaming", "Twitch"})
    preferred: list[Organization] = [o for o in owners if o.name not in generic_orgs]

    return preferred[0] if preferred else owners[0]


def _build_image_object(
    request: HttpRequest,
    image_url: str,
    creator_name: str,
    creator_url: str,
    *,
    copyright_notice: str | None = None,
) -> dict[str, Any]:
    """Build a Schema.org ImageObject with attribution metadata.

    Args:
        request: The HTTP request used for absolute URL building.
        image_url: Relative or absolute image URL.
        creator_name: Human-readable creator/owner name.
        creator_url: URL for the creator organization or fallback owner page.
        copyright_notice: Optional copyright text.

    Returns:
        Dict with ImageObject fields used in structured data.
    """
    creator: dict[str, str] = {
        "@type": "Organization",
        "name": creator_name,
        "url": creator_url,
    }

    return {
        "@type": "ImageObject",
        "contentUrl": build_absolute_uri(image_url),
        "creditText": creator_name,
        "copyrightNotice": copyright_notice or creator_name,
        "creator": creator,
    }


def _truncate_description(text: str, max_length: int = 160) -> str:
    """Truncate text to a reasonable description length (for meta tags).

    Args:
        text: The text to truncate.
        max_length: Maximum length for the description.

    Returns:
        Truncated text with ellipsis if needed.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "…"


def _meta_description(text: str | None) -> str:
    """Return a meta description long enough to be useful in search results.

    Bing flags descriptions shorter than roughly 50 characters, so every Twitch
    page gets a description of at least ``MIN_META_DESCRIPTION_LENGTH``
    characters. Short descriptions are extended with a site-wide explanation,
    while longer ones are capped so search engines can show the full snippet.

    Args:
        text: Page-specific description, or None to use the site default.

    Returns:
        str: Description between the minimum and maximum supported lengths.
    """
    description: str = (text or DEFAULT_SITE_DESCRIPTION).strip()
    if description and description[-1] not in ".!?…":
        description += "."

    while len(description) < MIN_META_DESCRIPTION_LENGTH:
        separator: str = " " if description else ""
        description = f"{description}{separator}{META_DESCRIPTION_PADDING}"

    return _truncate_description(description, MAX_META_DESCRIPTION_LENGTH)


def _discord_timestamp(value: datetime.datetime | None, style: str = "F") -> str:
    """Return a Discord timestamp token for a datetime.

    Args:
        value: Datetime to format, or None when the date is unknown.
        style: Discord timestamp style flag, such as ``F`` or ``R``.

    Returns:
        str: Timestamp token like ``<t:1794427200:F>``, or an empty string.
    """
    if value is None:
        return ""
    return f"<t:{int(value.timestamp())}:{style}>"


def _drop_requirement_label(drop: TimeBasedDrop) -> str:
    """Return a compact requirement label for a drop.

    Args:
        drop: Time-based drop.

    Returns:
        str: Label such as ``"30 min watched + 1 sub"``.
    """
    parts: list[str] = []
    if drop.required_minutes_watched:
        parts.append(f"{drop.required_minutes_watched} min watched")
    if drop.required_subs:
        suffix: str = "" if drop.required_subs == 1 else "s"
        parts.append(f"{drop.required_subs} sub{suffix}")
    return " + ".join(parts)


def _drop_reward_names(drop: TimeBasedDrop) -> list[str]:
    """Return the reward names unlocked by a drop.

    Args:
        drop: Time-based drop.

    Returns:
        list[str]: Unique benefit names, falling back to the drop name.
    """
    names: list[str] = []
    for benefit in drop.benefits.all():
        name: str = (benefit.name or "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        name = (drop.name or "").strip()
        if name:
            names.append(name)
    return names


def _drop_availability_label(
    drop: TimeBasedDrop,
    campaign: DropCampaign,
) -> str:
    """Return a timestamp range when a drop's availability differs.

    Args:
        drop: Time-based drop.
        campaign: Campaign that owns the drop.

    Returns:
        str: Timestamp range, or an empty string when the drop uses the
        campaign's full window.
    """
    drop_start = drop.start_at
    drop_end = drop.end_at
    start_differs: bool = drop_start is not None and drop_start != campaign.start_at
    end_differs: bool = drop_end is not None and drop_end != campaign.end_at
    if not (start_differs or end_differs):
        return ""

    start: datetime.datetime | None = drop_start or campaign.start_at
    end: datetime.datetime | None = drop_end or campaign.end_at
    start_token: str = _discord_timestamp(start, "f")
    end_token: str = _discord_timestamp(end, "f")
    if start_token and end_token:
        return f"{start_token} - {end_token}"
    return start_token or end_token


def _campaign_embed_description(
    campaign: DropCampaign,
    drops: list[dict[str, Any]],
    channels: list[Channel],
) -> str:
    """Build a Discord markdown body with campaign dates, drops, and channels.

    Args:
        campaign: Campaign being rendered.
        drops: Enhanced drop entries from ``DropCampaign.enhanced_drops_for_detail``.
        channels: Channels allowed to award the campaign's drops.

    Returns:
        str: Markdown suitable for a Discord Text Display component.
    """
    blocks: list[str] = []

    start_absolute: str = _discord_timestamp(campaign.start_at, "F")
    end_absolute: str = _discord_timestamp(campaign.end_at, "F")
    date_lines: list[str] = []
    if start_absolute:
        date_lines.append(
            f"**Starts:** {start_absolute} ({_discord_timestamp(campaign.start_at, 'R')})",
        )
    if end_absolute:
        date_lines.append(
            f"**Ends:** {end_absolute} ({_discord_timestamp(campaign.end_at, 'R')})",
        )
    if date_lines:
        blocks.append("\n".join(date_lines))

    drop_lines: list[str] = []
    for entry in drops:
        drop: TimeBasedDrop = entry["drop"]
        requirement: str = _drop_requirement_label(drop)
        reward_names: list[str] = _drop_reward_names(drop)
        reward_text: str = ", ".join(
            escape_markdown(name) for name in reward_names
        ) or escape_markdown(drop.name or "Drop")
        drop_line: str = (
            f"- {requirement}: {reward_text}" if requirement else f"- {reward_text}"
        )
        availability: str = _drop_availability_label(drop, campaign)
        if availability:
            drop_line += f" ({availability})"
        drop_lines.append(drop_line)
    if drop_lines:
        blocks.append("**Drops**\n" + "\n".join(drop_lines))

    if channels:
        shown_channels: list[str] = [
            escape_markdown(channel.preferred_name)
            for channel in channels[:MAX_EMBED_CHANNELS]
        ]
        channel_text: str = ", ".join(shown_channels)
        hidden_count: int = len(channels) - len(shown_channels)
        if hidden_count > 0:
            channel_text += f" +{hidden_count} more"
        blocks.append("**Channels**\n" + channel_text)

    return "\n\n".join(blocks)


def _campaign_gallery_items(
    drops: list[dict[str, Any]],
    *,
    exclude_url: str = "",
) -> list[dict[str, str]]:
    """Build reward gallery items for a campaign's Discord embed.

    Args:
        drops: Enhanced drop entries from ``DropCampaign.enhanced_drops_for_detail``.
        exclude_url: Image URL to skip, normally the embed thumbnail.

    Returns:
        list[dict[str, str]]: Deduplicated gallery items.
    """
    items: list[tuple[str, str]] = []
    for entry in drops:
        drop: TimeBasedDrop = entry["drop"]
        requirement: str = _drop_requirement_label(drop)
        for benefit in drop.benefits.all():
            description: str = (
                f"{requirement}: {benefit.name}" if requirement else benefit.name
            )
            items.append((description, benefit.image_best_url))
    return _gallery_items(items, exclude_url=exclude_url)


def _build_seo_context(
    page_title: str = "ttvdrops",
    page_description: str | None = None,
    seo_meta: SeoMeta | None = None,
) -> dict[str, Any]:
    """Build SEO context for template rendering.

    Args:
        page_title: Page title (shown in browser tab, og:title).
        page_description: Page description (meta description, og:description).
            Short values are automatically extended to the minimum length.
        seo_meta: Optional typed SEO metadata with image, schema, breadcrumb,
            pagination, OpenGraph, and date fields.

    Returns:
        Dict with SEO context variables to pass to render().
    """
    context: dict[str, Any] = {
        "page_title": page_title,
        "page_description": _meta_description(page_description),
        "og_type": "website",
        "robots_directive": "index, follow",
        "discord_embed_accent_color": TWITCH_ACCENT_COLOR,
        "discord_embed_buttons": embed_buttons(
            ("Twitch", "https://www.twitch.tv/drops/campaigns"),
            ("Campaigns", "/twitch/campaigns/"),
            ("Games", "/twitch/games/"),
            ("Reward Campaigns", "/twitch/reward-campaigns/"),
        ),
    }
    if seo_meta:
        page_url = seo_meta.get("page_url")
        if page_url:
            context["page_url"] = page_url

        og_type = seo_meta.get("og_type")
        if og_type:
            context["og_type"] = og_type

        robots_directive = seo_meta.get("robots_directive")
        if robots_directive:
            context["robots_directive"] = robots_directive

        page_image = seo_meta.get("page_image")
        if page_image:
            context["page_image"] = page_image
            page_image_width = seo_meta.get("page_image_width")
            page_image_height = seo_meta.get("page_image_height")
            if page_image_width and page_image_height:
                context["page_image_width"] = page_image_width
                context["page_image_height"] = page_image_height

        schema_data = seo_meta.get("schema_data")
        if schema_data:
            context["schema_data"] = json.dumps(schema_data)

        breadcrumb_schema = seo_meta.get("breadcrumb_schema")
        if breadcrumb_schema:
            context["breadcrumb_schema"] = json.dumps(breadcrumb_schema)

        pagination_info = seo_meta.get("pagination_info")
        if pagination_info:
            context["pagination_info"] = pagination_info

        feed_links = seo_meta.get("feed_links")
        if feed_links:
            context["feed_links"] = feed_links

        published_date = seo_meta.get("published_date")
        if published_date:
            context["published_date"] = published_date

        modified_date = seo_meta.get("modified_date")
        if modified_date:
            context["modified_date"] = modified_date
    return context


def _webpage_schema(name: str, description: str, url: str) -> dict[str, str]:
    """Build a minimal WebPage schema for pages without a richer entity type.

    Args:
        name: Page name, usually the page title without the site suffix.
        description: Meta description used for the page.
        url: Absolute canonical URL of the page.

    Returns:
        dict: JSON-LD WebPage schema.
    """
    return {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": name,
        "description": description,
        "url": url,
    }


def _build_breadcrumb_schema(items: list[dict[str, str | int]]) -> dict[str, Any]:
    """Build a BreadcrumbList schema for structured data.

    Args:
        items: List of dicts with "name" and "url" keys.
               First item should be homepage.

    Returns:
        BreadcrumbList schema dict.
    """
    # TODO(TheLovinator): Replace dict with something more structured, like a dataclass or namedtuple, for better type safety and readability.  # ruff:ignore[missing-todo-link]

    breadcrumb_items: list[dict[str, str | int]] = []
    for position, item in enumerate(items, start=1):
        breadcrumb_items.append({
            "@type": "ListItem",
            "position": position,
            "name": item["name"],
            "item": item["url"],
        })

    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": breadcrumb_items,
    }


def _twitch_breadcrumb(
    items: list[tuple[str, str, list[str] | None]],
) -> dict[str, Any]:
    """Build a Twitch breadcrumb from human-readable names and route names.

    Args:
        items: Ordered ``(name, route_name, args)`` tuples. The last item is
            the current page.

    Returns:
        BreadcrumbList schema with absolute URLs.
    """
    return _build_breadcrumb_schema([
        {
            "name": name,
            "url": build_absolute_uri(reverse(route_name, args=args or None)),
        }
        for name, route_name, args in items
    ])


def _build_pagination_info(
    request: HttpRequest,
    page_obj: Page,
    base_url: str,
) -> list[dict[str, str]] | None:
    """Build pagination link info for rel="next"/"prev" tags.

    Args:
        request: HTTP request to build absolute URLs.
        page_obj: Django Page object from paginator.
        base_url: Base URL for pagination (e.g., "/campaigns/?status=active").

    Returns:
        List of dicts with rel and url, or None if no prev/next.
    """
    pagination_links: list[dict[str, str]] = []

    if page_obj.has_previous():
        prev_url: str = f"{base_url}?page={page_obj.previous_page_number()}"
        if "?" in base_url:
            prev_url = f"{base_url}&page={page_obj.previous_page_number()}"
        pagination_links.append({
            "rel": "prev",
            "url": build_absolute_uri(prev_url),
        })

    if page_obj.has_next():
        next_url: str = f"{base_url}?page={page_obj.next_page_number()}"
        if "?" in base_url:
            # Preserve existing query params
            next_url = f"{base_url}&page={page_obj.next_page_number()}"
        pagination_links.append({
            "rel": "next",
            "url": build_absolute_uri(next_url),
        })

    return pagination_links or None


def _feed_link(name: str, title: str, *args: str) -> dict[str, str]:
    """Build a rel="alternate" link for an RSS or Atom feed.

    Args:
        name: Name of the feed URL under the ``core`` namespace.
        title: Human-readable feed title.
        *args: Optional arguments, such as a game Twitch ID.

    Returns:
        dict: Feed link attributes for the SEO context.
    """
    feed_type: str = "application/atom+xml" if "atom" in name else "application/rss+xml"
    return {
        "rel": "alternate",
        "type": feed_type,
        "title": title,
        "href": build_absolute_uri(reverse(f"core:{name}", args=args or None)),
    }


# MARK: /twitch/
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """Render the Twitch landing page.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered Twitch landing page.
    """
    now: datetime.datetime = timezone.now()

    campaign_stats: dict[str, Any] = DropCampaign.objects.aggregate(
        total=Count("pk"),
        active=Count("pk", filter=Q(start_at__lte=now, end_at__gte=now)),
        latest_added=Max("added_at"),
    )
    reward_stats: dict[str, Any] = RewardCampaign.objects.aggregate(
        active=Count("pk", filter=Q(starts_at__lte=now, ends_at__gte=now)),
        latest_added=Max("added_at"),
    )

    latest_campaign_added_at: datetime.datetime | None = max(
        (
            added_at
            for added_at in (
                campaign_stats["latest_added"],
                reward_stats["latest_added"],
            )
            if added_at is not None
        ),
        default=None,
    )

    # Show campaigns that are ending soonest first, so the landing page stays
    # useful for visitors looking for drops they can still earn.
    featured_campaigns: list[DropCampaign] = list(
        DropCampaign.active_for_dashboard(now).order_by("end_at")[:6],
    )
    featured_reward_campaigns: list[RewardCampaign] = list(
        RewardCampaign.active_for_dashboard(now).order_by("ends_at")[:3],
    )

    counts: dict[str, int] = {
        "active_campaigns": campaign_stats["active"],
        "total_campaigns": campaign_stats["total"],
        "active_reward_campaigns": reward_stats["active"],
        "games": Game.objects.count(),
        "channels": Channel.objects.count(),
        "organizations": Organization.objects.count(),
    }

    description: str = (
        "Track active Twitch drop campaigns, open reward campaigns, games, "
        "channels, and organizations. Browse the archive, subscribe via RSS, "
        "or use the free JSON API."
    )
    page_url: str = build_absolute_uri(reverse("twitch:dashboard"))
    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Drops, Campaigns & Rewards",
        page_description=description,
        seo_meta={
            "page_url": page_url,
            "schema_data": _webpage_schema(
                "Twitch Drops, Campaigns & Rewards",
                description,
                page_url,
            ),
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
            ]),
        },
    )

    context: dict[str, Any] = {
        "now": now,
        "counts": counts,
        "featured_campaigns": featured_campaigns,
        "featured_reward_campaigns": featured_reward_campaigns,
        "latest_campaign_added_at": latest_campaign_added_at,
        **seo_context,
    }
    return render(request, "twitch/dashboard.html", context)


def emote_gallery_view(request: HttpRequest) -> HttpResponse:
    """View to display all emote images.

    Emotes are associated with DropBenefits of type "EMOTE".

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered emote gallery page.
    """
    emotes: list[dict[str, str | DropCampaign]] = DropBenefit.emotes_for_gallery()

    description: str = (
        "Browse Twitch emotes earned as drop rewards in our archive. See which "
        "campaigns granted each emote, follow new rewards via RSS, or query "
        "the JSON API."
    )
    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Emotes - Drop Rewards Archive",
        page_description=description,
        seo_meta={
            "page_url": build_absolute_uri(reverse("twitch:emote_gallery")),
            "schema_data": _webpage_schema(
                "Twitch Emotes",
                description,
                build_absolute_uri(reverse("twitch:emote_gallery")),
            ),
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Emotes", "twitch:emote_gallery", None),
            ]),
        },
    )
    context: dict[str, Any] = {"emotes": emotes, **seo_context}
    return render(request, "twitch/emote_gallery.html", context)


# MARK: /organizations/
def org_list_view(request: HttpRequest) -> HttpResponse:
    """Function-based view for organization list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered organization list page.
    """
    orgs: QuerySet[Organization] = Organization.for_list_view()
    description: str = (
        "Browse the Twitch organizations behind drop campaigns in our archive. "
        "Follow new publishers via RSS feeds or query games and rewards with "
        "the JSON API."
    )

    # CollectionPage schema for organizations list
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Twitch Organizations",
        "description": description,
        "url": build_absolute_uri(reverse("twitch:org_list")),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Organizations - Drop Campaign Archive",
        page_description=description,
        seo_meta={
            "page_url": build_absolute_uri(reverse("twitch:org_list")),
            "schema_data": collection_schema,
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Organizations", "twitch:org_list", None),
            ]),
        },
    )
    context: dict[str, Any] = {"orgs": orgs, **seo_context}

    return render(request, "twitch/org_list.html", context)


# MARK: /organizations/<twitch_id>/
def organization_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # ruff:ignore[too-many-locals]
    """Function-based view for organization detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the organization.

    Returns:
        HttpResponse: The rendered organization detail page.

    """
    organization: Organization = get_object_or_404(
        Organization.for_detail_view(),
        twitch_id=twitch_id,
    )

    games: list[Game] = list(getattr(organization, "games_for_detail", []))

    org_name: str = organization.name or organization.twitch_id
    games_count: int = len(games)
    noun: str = "game" if games_count == 1 else "games"
    org_description: str = (
        f"{org_name} is a Twitch publisher in our drop archive with "
        f"{games_count} {noun}. Follow new drops via RSS feeds or query rewards "
        "with the JSON API."
    )

    url: str = build_absolute_uri(
        reverse("twitch:organization_detail", args=[organization.twitch_id]),
    )
    organization_node: dict[str, Any] = {
        "@type": "Organization",
        "name": org_name,
        "url": url,
        "description": org_description,
    }
    webpage_node: dict[str, Any] = {
        "@type": "WebPage",
        "url": url,
        "datePublished": organization.added_at.isoformat(),
        "dateModified": organization.updated_at.isoformat(),
    }
    org_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@graph": [
            organization_node,
            webpage_node,
        ],
    }

    # Breadcrumb schema
    breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
        ("Home", "core:dashboard", None),
        ("Twitch", "twitch:dashboard", None),
        ("Organizations", "twitch:org_list", None),
        (
            org_name,
            "twitch:organization_detail",
            [organization.twitch_id],
        ),
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=f"{org_name} - Twitch Drops and Games",
        page_description=org_description,
        seo_meta={
            "page_url": url,
            "schema_data": org_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "published_date": organization.added_at.isoformat(),
            "modified_date": organization.updated_at.isoformat(),
        },
    )
    seo_context["discord_embed_gallery"] = _gallery_items(
        ((org_game.display_name, org_game.box_art_best_url) for org_game in games),
    )
    total_campaigns: int = sum(getattr(g, "campaign_count", 0) or 0 for g in games)

    history_querysets: list[QuerySet] = organization_history_querysets(organization)
    history_events: list[dict[str, Any]] = build_event_rows(
        history_page_for_querysets(history_querysets, per_page=20).rows,
    )
    history_event_count: int = sum(queryset.count() for queryset in history_querysets)

    now: datetime.datetime = timezone.now()
    campaign_stats: dict[str, Any] = DropCampaign.objects.filter(
        game__owners=organization,
    ).aggregate(
        first_campaign=Min("start_at"),
        last_campaign=Max("start_at"),
        active_campaigns=Count("pk", filter=Q(start_at__lte=now, end_at__gte=now)),
        upcoming_campaigns=Count("pk", filter=Q(start_at__gt=now)),
        expired_campaigns=Count("pk", filter=Q(end_at__lt=now)),
    )

    context: dict[str, Any] = {
        "organization": organization,
        "games": games,
        "games_count": games_count,
        "total_campaigns": total_campaigns,
        "campaign_stats": campaign_stats,
        "history_events": history_events,
        "history_event_count": history_event_count,
        **seo_context,
    }

    return render(request, "twitch/organization_detail.html", context)


# MARK: /campaigns/
def drop_campaign_list_view(request: HttpRequest) -> HttpResponse:  # ruff:ignore[too-many-locals]
    """Function-based view for drop campaigns list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered campaign list page.
    """
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")
    search_query: str = request.GET.get("q", "").strip()
    per_page: int = 100
    now: datetime.datetime = timezone.now()

    queryset: QuerySet[DropCampaign] = DropCampaign.for_campaign_list(
        now,
        game_twitch_id=game_filter,
        status=status_filter,
    )
    if search_query:
        queryset = queryset.filter(
            Q(name__icontains=search_query)
            | Q(game__display_name__icontains=search_query)
            | Q(game__name__icontains=search_query)
            | Q(game__owners__name__icontains=search_query),
        ).distinct()
    if status_filter == "ios":
        queryset = queryset.filter(
            time_based_drops__benefits__is_ios_available=True,
        ).distinct()

    paginator: Paginator[DropCampaign] = Paginator(queryset, per_page)
    page: str | Literal[1] = request.GET.get("page") or 1
    try:
        campaigns: Page[DropCampaign] = paginator.page(page)
    except PageNotAnInteger:
        campaigns = paginator.page(1)
    except EmptyPage:
        campaigns = paginator.page(paginator.num_pages)

    status_descriptions: dict[str, str] = {
        "active": (
            "Browse active Twitch drops in our archive with rewards, watch "
            "requirements, and channels. Follow new campaigns via RSS feeds or "
            "query the free JSON API."
        ),
        "upcoming": (
            "See upcoming Twitch drop campaigns, start dates, rewards, and watch "
            "requirements. Track updates via RSS feeds or query our archive "
            "through the free JSON API."
        ),
        "expired": (
            "Explore expired Twitch drop campaigns in our archive with past "
            "rewards and requirements. Subscribe to RSS feeds or query historical "
            "drops via the JSON API."
        ),
    }
    title = "Twitch Drop Campaigns - Active, Upcoming and Archive"
    description = (
        "Browse active, upcoming, and expired Twitch drop campaigns in our "
        "archive. Check rewards and watch requirements, subscribe via RSS, or "
        "use the JSON API."
    )
    if status_filter:
        title += f" ({status_filter.capitalize()})"
        description = status_descriptions.get(status_filter, description)
    if game_filter:
        try:
            game_name: str = (
                Game.objects
                .only("display_name")
                .values_list("display_name", flat=True)
                .get(twitch_id=game_filter)
            )
            title += f" - {game_name}"
        except Game.DoesNotExist:
            pass

    # Build base URL for pagination
    base_url = "/campaigns/"
    query_params: dict[str, str] = {
        key: value
        for key, value in (
            ("q", search_query),
            ("status", status_filter),
            ("game", game_filter),
        )
        if value
    }
    if query_params:
        base_url += "?" + urlencode(query_params)

    pagination_info: list[dict[str, str]] | None = _build_pagination_info(
        request,
        campaigns,
        base_url,
    )

    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": build_absolute_uri(base_url),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": build_absolute_uri(base_url),
            "pagination_info": pagination_info,
            "schema_data": collection_schema,
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Campaigns", "twitch:campaign_list", None),
            ]),
        },
    )
    context: dict[str, Any] = {
        "campaigns": campaigns,
        "page_obj": campaigns,
        "is_paginated": campaigns.has_other_pages(),
        "games": Game.objects.all().order_by("display_name"),
        "status_options": ["active", "upcoming", "expired"],
        "now": now,
        "selected_game": game_filter or "",
        "selected_per_page": per_page,
        "selected_query": search_query,
        "selected_status": status_filter or "",
        **seo_context,
    }
    return render(request, "twitch/campaign_list.html", context)


# MARK: /campaigns/<twitch_id>/
def drop_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # ruff:ignore[too-many-locals, too-many-statements]
    """Function-based view for a drop campaign detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the campaign.

    Returns:
        HttpResponse: The rendered campaign detail page.

    Raises:
        Http404: If the campaign is not found.
    """
    try:
        campaign: DropCampaign = DropCampaign.for_detail_view(twitch_id)
    except DropCampaign.DoesNotExist as exc:
        msg = "No campaign found matching the query"
        raise Http404(msg) from exc

    now: datetime.datetime = timezone.now()
    owners: list[Organization] = list(getattr(campaign.game, "owners_for_detail", []))
    enhanced_drops: list[dict[str, Any]] = campaign.enhanced_drops_for_detail(now)
    if campaign.start_at and campaign.end_at:
        if campaign.start_at <= now <= campaign.end_at:
            campaign_status: str = "active"
        elif campaign.start_at > now:
            campaign_status = "upcoming"
        else:
            campaign_status = "expired"
    else:
        campaign_status = "unknown"

    game_campaign_count: int = DropCampaign.objects.filter(game=campaign.game).count()
    game_active_campaign_count: int = DropCampaign.objects.filter(
        game=campaign.game,
        start_at__lte=now,
        end_at__gte=now,
    ).count()
    campaign_progress_percent: int = 0
    if campaign.start_at and campaign.end_at and campaign.end_at > campaign.start_at:
        if now >= campaign.end_at:
            campaign_progress_percent = 100
        elif now > campaign.start_at:
            elapsed: float = (now - campaign.start_at).total_seconds()
            total: float = (campaign.end_at - campaign.start_at).total_seconds()
            campaign_progress_percent = round(elapsed / total * 100)

    history_querysets: list[QuerySet] = campaign_history_querysets(campaign)
    history_events: list[dict[str, Any]] = build_event_rows(
        history_page_for_querysets(history_querysets, per_page=20).rows,
    )
    history_event_count: int = sum(queryset.count() for queryset in history_querysets)

    context: dict[str, Any] = {
        "campaign": campaign,
        "now": now,
        "drops": enhanced_drops,
        "owners": owners,
        "allowed_channels": getattr(campaign, "channels_ordered", []),
        "campaign_status": campaign_status,
        "game_campaign_count": game_campaign_count,
        "game_active_campaign_count": game_active_campaign_count,
        "campaign_progress_percent": campaign_progress_percent,
        "history_events": history_events,
        "history_event_count": history_event_count,
    }

    campaign_name: str = campaign.name or campaign.clean_name or campaign.twitch_id
    campaign_description: str = (
        _truncate_description(campaign.description)
        if campaign.description
        else (
            f"Twitch drop campaign {campaign_name} with rewards, watch "
            "requirements, and drop dates in our archive. Follow new drops via "
            "RSS or query the JSON API."
        )
    )
    campaign_embed_description: str = _campaign_embed_description(
        campaign,
        enhanced_drops,
        list(getattr(campaign, "channels_ordered", [])),
    )
    single_reward: DropBenefit | None = campaign.single_reward_benefit
    single_reward_image: str = single_reward.image_best_url if single_reward else ""
    campaign_image: str | None = single_reward_image or campaign.image_best_url
    campaign_image_width: int | None = None
    campaign_image_height: int | None = None
    if single_reward_image and single_reward and single_reward.image_file:
        campaign_image_width = single_reward.image_width
        campaign_image_height = single_reward.image_height
    elif campaign.image_file:
        campaign_image_width = campaign.image_width
        campaign_image_height = campaign.image_height

    url: str = build_absolute_uri(
        reverse("twitch:campaign_detail", args=[campaign.twitch_id]),
    )

    # TODO(TheLovinator): If the campaign has specific allowed channels, we could list those as potential locations instead of just linking to Twitch homepage.  # ruff:ignore[missing-todo-link]
    campaign_event: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": campaign_name,
        "description": campaign_description,
        "url": url,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "location": {
            "@type": "VirtualLocation",
            "url": "https://www.twitch.tv/",
        },
    }
    if campaign.start_at:
        campaign_event["startDate"] = campaign.start_at.isoformat()
    if campaign.end_at:
        campaign_event["endDate"] = campaign.end_at.isoformat()
    campaign_owner: Organization | None = _pick_owner(owners) if owners else None
    campaign_owner_name: str = (
        (campaign_owner.name or campaign_owner.twitch_id)
        if campaign_owner
        else "Twitch"
    )
    campaign_owner_url: str = (
        build_absolute_uri(
            reverse("twitch:organization_detail", args=[campaign_owner.twitch_id]),
        )
        if campaign_owner
        else "https://www.twitch.tv/"
    )
    if campaign_image:
        campaign_event["image"] = _build_image_object(
            request,
            campaign_image,
            campaign_owner_name,
            campaign_owner_url,
            copyright_notice=campaign_owner_name,
        )
    if campaign_owner:
        campaign_event["organizer"] = {
            "@type": "Organization",
            "name": campaign_owner_name,
        }
    webpage_node: dict[str, Any] = {
        "@type": "WebPage",
        "url": url,
        "datePublished": campaign.added_at.isoformat(),
        "dateModified": campaign.updated_at.isoformat(),
    }
    campaign_event["mainEntityOfPage"] = webpage_node
    campaign_schema: dict[str, Any] = campaign_event

    # Breadcrumb schema for navigation
    # TODO(TheLovinator): We should have a game.get_display_name() method that encapsulates the logic of choosing between display_name, name, and twitch_id.  # ruff:ignore[missing-todo-link]
    game_name: str = (
        campaign.game.display_name or campaign.game.name or campaign.game.twitch_id
    )
    breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
        ("Home", "core:dashboard", None),
        ("Twitch", "twitch:dashboard", None),
        ("Campaigns", "twitch:campaign_list", None),
        (game_name, "twitch:game_detail", [campaign.game.twitch_id]),
        (campaign_name, "twitch:campaign_detail", [campaign.twitch_id]),
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=f"{campaign_name} - {game_name} Twitch Drops",
        page_description=campaign_description,
        seo_meta={
            "page_url": url,
            "page_image": campaign_image,
            "page_image_width": campaign_image_width,
            "page_image_height": campaign_image_height,
            "schema_data": campaign_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "published_date": campaign.added_at.isoformat()
            if campaign.added_at
            else None,
            "modified_date": campaign.updated_at.isoformat()
            if campaign.updated_at
            else None,
        },
    )
    seo_context["discord_embed_buttons"] = embed_buttons(
        ("Details", campaign.details_url),
        ("Connect", campaign.account_link_url),
        (
            "Twitch streams",
            campaign.game.twitch_directory_url if campaign.game else "",
        ),
        (
            f"All {game_name} campaigns",
            reverse("twitch:game_detail", args=[campaign.game.twitch_id])
            if campaign.game
            else "",
        ),
    )
    if campaign_embed_description:
        seo_context["discord_embed_description"] = campaign_embed_description
        seo_context["discord_embed_description_is_markdown"] = True
    seo_context["discord_embed_gallery"] = _campaign_gallery_items(
        enhanced_drops,
        exclude_url=campaign_image or "",
    )
    context.update(seo_context)

    return render(request, "twitch/campaign_detail.html", context)


# MARK: /games/
class GamesGridView(ListView):
    """List view for games grouped by organization."""

    model = Game
    template_name = "twitch/games_grid.html"
    context_object_name = "games"

    def get_queryset(self) -> QuerySet[Game]:
        """Get queryset of all games, annotated with campaign counts.

        Returns:
            QuerySet: Annotated games queryset.
        """
        return Game.with_campaign_counts(
            timezone.now(),
            with_campaigns_only=True,
        )

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Games are grouped by their owning organization.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with games grouped by organization.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        games: QuerySet[Game] = context["games"]
        context["games_by_org"] = Game.grouped_by_owner_for_grid(
            games,
        )

        description: str = (
            "Explore Twitch games with drops in our archive, including active and "
            "expired campaigns. Follow new games via RSS feeds or browse all data "
            "with the JSON API."
        )

        # CollectionPage schema for games list
        collection_schema: dict[str, str] = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Twitch Games",
            "description": description,
            "url": build_absolute_uri(self.request.path),
        }
        breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
            {
                "name": "Home",
                "url": build_absolute_uri(reverse("core:dashboard")),
            },
            {
                "name": "Twitch",
                "url": build_absolute_uri(reverse("twitch:dashboard")),
            },
            {"name": "Games", "url": build_absolute_uri(self.request.path)},
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title="Twitch Games with Drops - Rewards Archive",
            page_description=description,
            seo_meta={
                "page_url": build_absolute_uri(self.request.path),
                "schema_data": collection_schema,
                "breadcrumb_schema": breadcrumb_schema,
            },
        )
        context.update(seo_context)

        return context


# MARK: /games/<twitch_id>/
class GameDetailView(DetailView):
    """Detail view for a game."""

    model = Game
    template_name = "twitch/game_detail.html"
    context_object_name = "game"
    slug_field = "twitch_id"
    slug_url_kwarg = "twitch_id"

    def get_queryset(self) -> QuerySet[Game]:
        """Return game queryset optimized for the game detail page."""
        return Game.for_detail_view()

    def get_context_data(self, **kwargs) -> dict[str, Any]:  # ruff:ignore[too-many-locals, too-many-statements]
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired
                campaigns. Expired campaigns are filtered based on
                either end date or status.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        game: Game = self.object  # pyright: ignore[reportAssignmentType]

        now: datetime.datetime = timezone.now()
        campaigns_list: list[DropCampaign] = list(DropCampaign.for_game_detail(game))
        active_campaigns, upcoming_campaigns, expired_campaigns = (
            DropCampaign.split_for_channel_detail(campaigns_list, now)
        )

        # Fetch reward campaigns for this game
        all_reward_campaigns: list[RewardCampaign] = list(
            RewardCampaign.objects
            .filter(game=game)
            .select_related("game")
            .order_by("-starts_at"),
        )
        active_rewards: list[RewardCampaign] = []
        upcoming_rewards: list[RewardCampaign] = []
        expired_rewards: list[RewardCampaign] = []
        for rc in all_reward_campaigns:
            if rc.starts_at is None or rc.ends_at is None:
                continue
            if rc.starts_at <= now <= rc.ends_at:
                active_rewards.append(rc)
            elif rc.starts_at > now:
                upcoming_rewards.append(rc)
            elif rc.ends_at < now:
                expired_rewards.append(rc)

        owners: list[Organization] = list(getattr(game, "owners_for_detail", []))

        history_querysets: list[QuerySet] = game_history_querysets(game)
        history_events: list[dict[str, Any]] = build_event_rows(
            history_page_for_querysets(history_querysets, per_page=20).rows,
        )
        history_event_count: int = sum(
            queryset.count() for queryset in history_querysets
        )

        game_name: str = game.get_game_name
        active_count: int = len(active_campaigns)
        upcoming_count: int = len(upcoming_campaigns)
        campaign_count: int = len(campaigns_list)
        reward_count: int = len(all_reward_campaigns)
        game_description: str = (
            f"{game_name} Twitch drops: {active_count} active · "
            f"{upcoming_count} upcoming · "
            f"{campaign_count} campaign{'' if campaign_count == 1 else 's'} · "
            f"{reward_count} reward{'' if reward_count == 1 else 's'}. "
            "Explore our archive, follow game drops via RSS, or query the JSON API."
        )
        game_image: str | None = game.box_art_best_url
        game_image_width: int | None = game.box_art_width if game.box_art_file else None
        game_image_height: int | None = (
            game.box_art_height if game.box_art_file else None
        )

        game_schema: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "VideoGame",
            "name": game_name,
            "description": game_description,
            "url": build_absolute_uri(
                reverse("twitch:game_detail", args=[game.twitch_id]),
            ),
        }
        if game.added_at:
            game_schema["datePublished"] = game.added_at.isoformat()
        if game.updated_at:
            game_schema["dateModified"] = game.updated_at.isoformat()
        preferred_owner: Organization | None = _pick_owner(owners)
        owner_name: str = (
            (preferred_owner.name or preferred_owner.twitch_id)
            if preferred_owner
            else "Twitch"
        )
        owner_url: str = (
            build_absolute_uri(
                reverse("twitch:organization_detail", args=[preferred_owner.twitch_id]),
            )
            if preferred_owner
            else "https://www.twitch.tv/"
        )
        if game.box_art_best_url:
            game_schema["image"] = _build_image_object(
                self.request,
                game.box_art_best_url,
                owner_name,
                owner_url,
                copyright_notice=owner_name,
            )
        if owners:
            game_schema["publisher"] = {
                "@type": "Organization",
                "name": owner_name,
            }

        # Breadcrumb schema
        breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
            ("Home", "core:dashboard", None),
            ("Twitch", "twitch:dashboard", None),
            ("Games", "twitch:games_grid", None),
            (game_name, "twitch:game_detail", [game.twitch_id]),
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title=f"{game_name} - Twitch Drops and Rewards",
            page_description=game_description,
            seo_meta={
                "page_url": build_absolute_uri(
                    reverse("twitch:game_detail", args=[game.twitch_id]),
                ),
                "page_image": game_image,
                "page_image_width": game_image_width,
                "page_image_height": game_image_height,
                "schema_data": game_schema,
                "breadcrumb_schema": breadcrumb_schema,
                "feed_links": [
                    _feed_link(
                        "game_campaign_feed",
                        f"{game_name} drops (RSS)",
                        game.twitch_id,
                    ),
                    _feed_link(
                        "game_reward_feed",
                        f"{game_name} rewards (RSS)",
                        game.twitch_id,
                    ),
                ],
                "published_date": game.added_at.isoformat() if game.added_at else None,
                "modified_date": game.updated_at.isoformat()
                if game.updated_at
                else None,
            },
        )
        seo_context["discord_embed_buttons"] = embed_buttons(
            ("Twitch streams", game.twitch_directory_url),
            ("Twitch", "https://www.twitch.tv/drops/campaigns"),
        )
        # Compute campaign date range from all campaigns
        campaign_dates: list[datetime.datetime] = [
            c.start_at for c in campaigns_list if c.start_at is not None
        ]
        first_campaign_date: datetime.datetime | None = (
            min(campaign_dates) if campaign_dates else None
        )
        last_campaign_date: datetime.datetime | None = (
            max(campaign_dates) if campaign_dates else None
        )
        last_campaign_end_at, average_gap_days = campaign_timing_stats(
            (campaign.start_at, campaign.end_at) for campaign in campaigns_list
        )

        context.update({
            "selected_status": self.request.GET.get("status", ""),
            "last_campaign_end_at": last_campaign_end_at,
            "average_gap_days": average_gap_days,
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "active_rewards": active_rewards,
            "upcoming_rewards": upcoming_rewards,
            "expired_rewards": expired_rewards,
            "total_campaigns": campaign_count,
            "total_rewards": reward_count,
            "first_campaign_date": first_campaign_date,
            "last_campaign_date": last_campaign_date,
            "owner": owners[0] if owners else None,
            "owners": owners,
            "now": now,
            "history_events": history_events,
            "history_event_count": history_event_count,
            **seo_context,
        })

        return context


# MARK: /stats/
def stats_view(request: HttpRequest) -> HttpResponse:
    """View showing fun statistics about the Twitch drop archive.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered stats page.
    """
    now: datetime.datetime = timezone.now()
    stats_data: dict[str, Any] = DropCampaign.stats(now)

    counters: dict[str, int] = stats_data["counters"]
    totals: list[tuple[str, int]] = [
        ("Campaigns", counters["campaigns"]),
        ("Drops", counters["drops"]),
        ("Rewards", counters["benefits"]),
        ("Games", counters["games"]),
        ("Organizations", counters["organizations"]),
        ("Channels", counters["channels"]),
        ("Reward campaigns", counters["reward_campaigns"]),
        ("Active", counters["active_campaigns"]),
        ("Upcoming", counters["upcoming_campaigns"]),
        ("Expired", counters["expired_campaigns"]),
    ]

    leaderboards_data: dict[str, list[dict[str, Any]]] = stats_data["leaderboards"]
    leaderboards: list[dict[str, Any]] = [
        {
            "title": "Top games by drops",
            "rows": leaderboards_data["games_by_drops"],
        },
        {
            "title": "Top games by campaigns",
            "rows": leaderboards_data["games_by_campaigns"],
        },
        {
            "title": "Top campaigns by rewards",
            "rows": leaderboards_data["campaigns_by_rewards"],
        },
        {
            "title": "Top campaigns by drops",
            "rows": leaderboards_data["campaigns_by_drops"],
        },
        {
            "title": "Top organizations by campaigns",
            "rows": leaderboards_data["organizations_by_campaigns"],
        },
        {
            "title": "Top channels by campaigns",
            "rows": leaderboards_data["channels_by_campaigns"],
        },
    ]

    description: str = (
        "Explore Twitch drops statistics in our archive: top games, longest "
        "campaigns, and reward totals. Query the data with our free JSON API."
    )
    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Drops Stats and Leaderboards",
        page_description=description,
        seo_meta={
            "page_url": build_absolute_uri(reverse("twitch:stats")),
            "schema_data": _webpage_schema(
                "Twitch Drops Statistics",
                description,
                build_absolute_uri(reverse("twitch:stats")),
            ),
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Stats", "twitch:stats", None),
            ]),
        },
    )
    context: dict[str, Any] = {
        "now": now,
        "stats": stats_data,
        "totals": totals,
        "leaderboards": leaderboards,
        **seo_context,
    }
    return render(request, "twitch/stats.html", context)


# MARK: /reward-campaigns/
def reward_campaign_list_view(request: HttpRequest) -> HttpResponse:  # ruff:ignore[too-many-locals]
    """Function-based view for reward campaigns list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered reward campaigns list page.
    """
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")
    per_page: int = 100
    queryset: QuerySet[RewardCampaign] = RewardCampaign.objects.all()

    if game_filter:
        queryset = queryset.filter(game__twitch_id=game_filter)

    queryset = (
        queryset
        .select_related("game")
        .prefetch_related("rewards")
        .order_by("-starts_at")
    )

    # Optionally filter by status (active, upcoming, expired)
    now: datetime.datetime = timezone.now()
    if status_filter == "active":
        queryset = queryset.filter(starts_at__lte=now, ends_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(starts_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(ends_at__lt=now)

    paginator: Paginator[RewardCampaign] = Paginator(queryset, per_page)
    page: str | Literal[1] = request.GET.get("page") or 1
    try:
        reward_campaigns: Page[RewardCampaign] = paginator.page(page)
    except PageNotAnInteger:
        reward_campaigns = paginator.page(1)
    except EmptyPage:
        reward_campaigns = paginator.page(paginator.num_pages)

    page_campaigns: list[RewardCampaign] = list(reward_campaigns)
    active_campaigns: list[RewardCampaign] = sorted(
        (
            campaign
            for campaign in page_campaigns
            if campaign.starts_at
            and campaign.ends_at
            and campaign.starts_at <= now <= campaign.ends_at
        ),
        key=lambda campaign: campaign.ends_at or now,
    )
    upcoming_campaigns: list[RewardCampaign] = sorted(
        (
            campaign
            for campaign in page_campaigns
            if campaign.starts_at and campaign.starts_at > now
        ),
        key=lambda campaign: campaign.starts_at or now,
    )
    expired_campaigns: list[RewardCampaign] = sorted(
        (
            campaign
            for campaign in page_campaigns
            if campaign.ends_at and campaign.ends_at < now
        ),
        key=lambda campaign: campaign.ends_at or now,
        reverse=True,
    )

    title = "Twitch Reward Campaigns - Free Rewards Archive"
    if status_filter:
        title += f" ({status_filter.capitalize()})"

    description = (
        "Browse Twitch reward campaigns in our archive: free rewards, bonuses, "
        "and site-wide drops. Follow new offers via RSS feeds or query them with "
        "the JSON API."
    )
    if status_filter == "active":
        description = (
            "Browse active Twitch reward campaigns in our archive with free "
            "rewards and bonuses. Follow new offers via RSS feeds or query "
            "current drops with the JSON API."
        )
    elif status_filter == "upcoming":
        description = (
            "See upcoming Twitch reward campaigns, free rewards, and start dates "
            "in our archive. Follow new bonuses via RSS feeds or query offers "
            "with the JSON API."
        )
    elif status_filter == "expired":
        description = (
            "Explore expired Twitch reward campaigns in our archive and the free "
            "rewards they offered. Follow new offers via RSS feeds or query past "
            "data with the JSON API."
        )

    # Build base URL for pagination
    base_url: str = reverse("twitch:reward_campaign_list")
    query_params: dict[str, str] = {
        key: value
        for key, value in (
            ("status", status_filter),
            ("game", game_filter),
        )
        if value
    }
    if query_params:
        base_url += "?" + urlencode(query_params)

    pagination_info: list[dict[str, str]] | None = _build_pagination_info(
        request,
        reward_campaigns,
        base_url,
    )

    # CollectionPage schema for reward campaigns list
    collection_schema: dict[str, str | dict[str, str | dict[str, str]]] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": build_absolute_uri(base_url),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": build_absolute_uri(base_url),
            "pagination_info": pagination_info,
            "schema_data": collection_schema,
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Reward Campaigns", "twitch:reward_campaign_list", None),
            ]),
        },
    )
    context: dict[str, Any] = {
        "reward_campaigns": reward_campaigns,
        "active_campaigns": active_campaigns,
        "upcoming_campaigns": upcoming_campaigns,
        "expired_campaigns": expired_campaigns,
        "games": (
            Game.objects
            .filter(reward_campaigns__isnull=False)
            .distinct()
            .order_by("display_name")
        ),
        "status_options": ["active", "upcoming", "expired"],
        "now": now,
        "selected_game": game_filter or "",
        "selected_per_page": per_page,
        "selected_status": status_filter or "",
        **seo_context,
    }
    return render(request, "twitch/reward_campaign_list.html", context)


# MARK: /reward-campaigns/<twitch_id>/
def reward_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # ruff:ignore[too-many-locals]
    """Function-based view for a reward campaign detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the reward campaign.

    Returns:
        HttpResponse: The rendered reward campaign detail page.

    Raises:
        Http404: If the reward campaign is not found.
    """
    try:
        reward_campaign: RewardCampaign = (
            RewardCampaign.objects
            .select_related("game")
            .prefetch_related("rewards")
            .get(twitch_id=twitch_id)
        )
    except RewardCampaign.DoesNotExist as exc:
        msg = "No reward campaign found matching the query"
        raise Http404(msg) from exc

    now: datetime.datetime = timezone.now()
    rewards: list[Reward] = list(reward_campaign.rewards.all())  # pyright: ignore[reportAttributeAccessIssue]
    redemption_urls: set[str] = {
        reward.redemption_url for reward in rewards if reward.redemption_url
    }
    common_redemption_url: str = (
        next(iter(redemption_urls))
        if rewards
        and len(redemption_urls) == 1
        and all(reward.redemption_url for reward in rewards)
        else ""
    )

    history_querysets: list[QuerySet] = reward_campaign_history_querysets(
        reward_campaign,
    )
    history_events: list[dict[str, Any]] = build_event_rows(
        history_page_for_querysets(history_querysets, per_page=20).rows,
    )
    history_event_count: int = sum(queryset.count() for queryset in history_querysets)

    campaign_name: str = reward_campaign.name or reward_campaign.twitch_id
    reward_game_name: str = (
        reward_campaign.game.get_game_name if reward_campaign.game else ""
    )
    title_suffix: str = (
        f"{reward_game_name} Twitch Rewards"
        if reward_game_name
        else "Twitch Reward Campaign"
    )
    campaign_description: str = (
        _truncate_description(reward_campaign.summary)
        if reward_campaign.summary
        else (
            f"{campaign_name} is a Twitch reward campaign with free in-game "
            "rewards and bonuses in our archive. Follow new offers via RSS or "
            "query the JSON API."
        )
    )

    reward_progress_percent: int = 0
    if (
        reward_campaign.starts_at
        and reward_campaign.ends_at
        and reward_campaign.ends_at > reward_campaign.starts_at
    ):
        if now >= reward_campaign.ends_at:
            reward_progress_percent = 100
        elif now > reward_campaign.starts_at:
            elapsed: float = (now - reward_campaign.starts_at).total_seconds()
            total: float = (
                reward_campaign.ends_at - reward_campaign.starts_at
            ).total_seconds()
            reward_progress_percent = round(elapsed / total * 100)

    reward_url: str = build_absolute_uri(
        reverse("twitch:reward_campaign_detail", args=[reward_campaign.twitch_id]),
    )

    campaign_event: dict[str, Any] = {
        "@type": "Event",
        "name": campaign_name,
        "description": campaign_description,
        "url": reward_url,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "location": {"@type": "VirtualLocation", "url": "https://www.twitch.tv"},
    }
    if reward_campaign.starts_at:
        campaign_event["startDate"] = reward_campaign.starts_at.isoformat()
    if reward_campaign.ends_at:
        campaign_event["endDate"] = reward_campaign.ends_at.isoformat()
    if reward_campaign.game and reward_campaign.game.owners.exists():
        owner = reward_campaign.game.owners.first()
        campaign_event["organizer"] = {
            "@type": "Organization",
            "name": owner.name or owner.twitch_id,
        }

    webpage_node: dict[str, Any] = {
        "@type": "WebPage",
        "url": reward_url,
        "datePublished": reward_campaign.added_at.isoformat(),
        "dateModified": reward_campaign.updated_at.isoformat(),
    }

    campaign_schema = {
        "@context": "https://schema.org",
        "@graph": [
            campaign_event,
            webpage_node,
        ],
    }

    # Breadcrumb schema
    breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
        ("Home", "core:dashboard", None),
        ("Twitch", "twitch:dashboard", None),
        ("Reward Campaigns", "twitch:reward_campaign_list", None),
        (
            campaign_name,
            "twitch:reward_campaign_detail",
            [reward_campaign.twitch_id],
        ),
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=f"{campaign_name} - {title_suffix}",
        page_description=campaign_description,
        seo_meta={
            "page_url": reward_url,
            "schema_data": campaign_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "published_date": reward_campaign.added_at.isoformat(),
            "modified_date": reward_campaign.updated_at.isoformat(),
        },
    )
    seo_context["discord_embed_gallery"] = _gallery_items(
        ((reward.name, reward.image_best_url) for reward in rewards[:6]),
    )
    context: dict[str, Any] = {
        "reward_campaign": reward_campaign,
        "rewards": rewards,
        "common_redemption_url": common_redemption_url,
        "now": now,
        "is_active": reward_campaign.is_active,
        "reward_progress_percent": reward_progress_percent,
        "history_events": history_events,
        "history_event_count": history_event_count,
        **seo_context,
    }

    return render(request, "twitch/reward_campaign_detail.html", context)


# MARK: /games/list/
class GamesListView(GamesGridView):
    """List view for games in simple list format."""

    template_name: str | None = "twitch/games_list.html"


# MARK: /channels/
class ChannelListView(ListView):
    """List view for channels."""

    model = Channel
    template_name = "twitch/channel_list.html"
    context_object_name = "channels"
    paginate_by = 200

    def get_queryset(self) -> QuerySet[Channel]:
        """Get queryset of channels.

        Returns:
            QuerySet: Filtered channels.
        """
        search_query: str | None = self.request.GET.get("search")
        return Channel.for_list_view(search_query)

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        search_query: str = self.request.GET.get("search", "").strip()

        # Build pagination info
        query_string: str = urlencode({"search": search_query}) if search_query else ""
        base_url: str = reverse("twitch:channel_list")
        if query_string:
            base_url = f"{base_url}?{query_string}"

        page_obj: Page | None = context.get("page_obj")
        pagination_info: list[dict[str, str]] | None = (
            _build_pagination_info(self.request, page_obj, base_url)
            if isinstance(page_obj, Page)
            else None
        )

        # CollectionPage schema for channels list
        collection_schema: dict[str, str | dict[str, str | dict[str, str]]] = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Twitch Channels",
            "description": "List of Twitch channels participating in drop campaigns.",
            "url": build_absolute_uri(reverse("twitch:channel_list")),
        }

        seo_context: dict[str, Any] = _build_seo_context(
            page_title="Twitch Channels - Drop Campaigns and Rewards",
            page_description=(
                "Browse Twitch channels in our drop archive with campaign history "
                "and rewards. Follow channel activity via RSS feeds or query the "
                "JSON API."
            ),
            seo_meta={
                "page_url": build_absolute_uri(base_url),
                "pagination_info": pagination_info,
                "schema_data": collection_schema,
                "breadcrumb_schema": _twitch_breadcrumb([
                    ("Home", "core:dashboard", None),
                    ("Twitch", "twitch:dashboard", None),
                    ("Channels", "twitch:channel_list", None),
                ]),
            },
        )
        context.update(seo_context)
        context["search_query"] = search_query
        return context


# MARK: /channels/<twitch_id>/
class ChannelDetailView(DetailView):
    """Detail view for a channel."""

    model = Channel
    template_name = "twitch/channel_detail.html"
    context_object_name = "channel"
    lookup_field = "twitch_id"

    def get_object(self, queryset: QuerySet[Channel] | None = None) -> Channel:
        """Get the channel object using twitch_id as the primary key lookup.

        Args:
            queryset: Optional queryset to use.

        Returns:
            Channel: The channel object.
        """
        queryset = queryset or Channel.for_detail_view()
        twitch_id: str = str(self.kwargs.get("twitch_id", ""))
        channel: Channel = get_object_or_404(queryset, twitch_id=twitch_id)
        return channel

    def get_context_data(self, **kwargs) -> dict[str, Any]:  # ruff:ignore[too-many-locals]
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired campaigns.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        channel: Channel = self.object  # pyright: ignore[reportAssignmentType]

        now: datetime.datetime = timezone.now()
        campaigns_list: list[DropCampaign] = list(
            DropCampaign.for_channel_detail(channel),
        )
        active_campaigns, upcoming_campaigns, expired_campaigns = (
            DropCampaign.split_for_channel_detail(campaigns_list, now)
        )

        history_querysets: list[QuerySet] = channel_history_querysets(channel)
        history_events: list[dict[str, Any]] = build_event_rows(
            history_page_for_querysets(history_querysets, per_page=20).rows,
        )
        history_event_count: int = sum(
            queryset.count() for queryset in history_querysets
        )

        name: str = channel.preferred_name
        total_campaigns: int = len(campaigns_list)
        description: str = (
            f"{channel.detail_description(total_campaigns)} in our drops archive. "
            "Follow channel drops via RSS or query the free JSON API."
        )

        channel_url: str = build_absolute_uri(
            reverse("twitch:channel_detail", args=[channel.twitch_id]),
        )
        channel_node: dict[str, Any] = {
            "@type": "BroadcastChannel",
            "name": name,
            "description": description,
            "url": channel_url,
            "broadcastChannelId": channel.twitch_id,
            "providerName": "Twitch",
        }
        webpage_node: dict[str, Any] = {
            "@type": "WebPage",
            "url": channel_url,
            "datePublished": channel.added_at.isoformat(),
            "dateModified": channel.updated_at.isoformat(),
        }
        channel_schema: dict[str, Any] = {
            "@context": "https://schema.org",
            "@graph": [
                channel_node,
                webpage_node,
            ],
        }

        # Breadcrumb schema
        breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
            ("Home", "core:dashboard", None),
            ("Twitch", "twitch:dashboard", None),
            ("Channels", "twitch:channel_list", None),
            (name, "twitch:channel_detail", [channel.twitch_id]),
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title=f"{name} - Twitch Drops and Rewards",
            page_description=description,
            seo_meta={
                "page_url": channel_url,
                "schema_data": channel_schema,
                "breadcrumb_schema": breadcrumb_schema,
                "published_date": channel.added_at.isoformat()
                if channel.added_at
                else None,
                "modified_date": channel.updated_at.isoformat()
                if channel.updated_at
                else None,
            },
        )
        seo_context["discord_embed_buttons"] = embed_buttons(
            ("Watch on Twitch", f"https://www.twitch.tv/{channel.name}"),
            ("All channels", "/twitch/channels/"),
            ("Twitch", "https://www.twitch.tv/drops/campaigns"),
        )
        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "now": now,
            "history_events": history_events,
            "history_event_count": history_event_count,
            **seo_context,
        })

        return context


# MARK: /badges/
def badge_list_view(request: HttpRequest) -> HttpResponse:
    """List view for chat badge sets.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered badge list page.
    """
    badge_data: list[dict[str, Any]] = [
        {
            "set": badge_set,
            "badges": list(badge_set.badges.all()),  # pyright: ignore[reportAttributeAccessIssue]
        }
        for badge_set in ChatBadgeSet.for_list_view()
    ]

    # CollectionPage schema for badges list
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Twitch chat badges",
        "description": "List of Twitch chat badges awarded through drop campaigns.",
        "url": build_absolute_uri(reverse("twitch:badge_list")),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Chat Badges - Drop Rewards Archive",
        page_description=(
            "Browse Twitch chat badges awarded through drop campaigns in our "
            "archive. Follow new badge rewards via RSS or query the JSON API."
        ),
        seo_meta={
            "page_url": build_absolute_uri(reverse("twitch:badge_list")),
            "schema_data": collection_schema,
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Badges", "twitch:badge_list", None),
            ]),
        },
    )
    context: dict[str, Any] = {
        "badge_data": badge_data,
        "total_badges": sum(len(item["badges"]) for item in badge_data),
        **seo_context,
    }

    return render(request, "twitch/badge_list.html", context)


# MARK: /badges/<set_id>/
def badge_set_detail_view(request: HttpRequest, set_id: str) -> HttpResponse:
    """Detail view for a specific badge set.

    Args:
        request: The HTTP request.
        set_id: The ID of the badge set.

    Returns:
        HttpResponse: The rendered badge set detail page.

    Raises:
        Http404: If the badge set is not found.
    """
    try:
        badge_set: ChatBadgeSet = ChatBadgeSet.for_detail_view(set_id)
    except ChatBadgeSet.DoesNotExist as exc:
        msg = "No badge set found matching the query"
        raise Http404(msg) from exc

    # Sort badges treating pure-numeric badge_ids as integers, strings alphabetically after
    badges: list[ChatBadge] = sorted(
        badge_set.badges.all(),  # pyright: ignore[reportAttributeAccessIssue]
        key=lambda b: (0, int(b.badge_id)) if b.badge_id.isdigit() else (1, b.badge_id),
    )

    # Batch-fetch award campaigns for all badge titles (2 queries regardless of badge count)
    award_map: dict[str, list[DropCampaign]] = ChatBadge.award_campaigns_by_title(
        [b.title for b in badges],
    )
    for badge in badges:
        badge.award_campaigns = award_map.get(badge.title, [])  # pyright: ignore[reportAttributeAccessIssue]

    badge_set_name: str = badge_set.set_id
    badge_count: int = len(badges)
    badge_set_description: str = (
        f"Twitch chat badge set {badge_set_name} with {badge_count} "
        f"badge{'s' if badge_count != 1 else ''} awarded through drops in our "
        "archive. Follow new badges via RSS or query the JSON API."
    )

    badge_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": badge_set_name,
        "description": badge_set_description,
        "url": build_absolute_uri(
            reverse("twitch:badge_set_detail", args=[badge_set.set_id]),
        ),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=f"{badge_set_name} - Twitch Chat Badge Set",
        page_description=badge_set_description,
        seo_meta={
            "page_url": build_absolute_uri(
                reverse("twitch:badge_set_detail", args=[badge_set.set_id]),
            ),
            "schema_data": badge_schema,
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("Badges", "twitch:badge_list", None),
                (
                    badge_set_name,
                    "twitch:badge_set_detail",
                    [badge_set.set_id],
                ),
            ]),
        },
    )
    context: dict[str, Any] = {
        "badge_set": badge_set,
        "badges": badges,
        **seo_context,
    }

    return render(request, "twitch/badge_set_detail.html", context)


# MARK: Export Views
def export_campaigns_csv(request: HttpRequest) -> HttpResponse:
    """Export drop campaigns to CSV format.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: CSV file response.
    """
    # Get filters from query parameters
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")

    queryset: QuerySet[DropCampaign] = DropCampaign.objects.all()

    if game_filter:
        queryset = queryset.filter(game__twitch_id=game_filter)

    queryset = queryset.prefetch_related("game__owners").order_by("-start_at")

    now: datetime.datetime = timezone.now()
    if status_filter == "active":
        queryset = queryset.filter(start_at__lte=now, end_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(start_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(end_at__lt=now)

    # Create CSV response
    response: HttpResponse = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = "attachment; filename=campaigns.csv"

    writer = csv.writer(response)
    writer.writerow([
        "Twitch ID",
        "Name",
        "Description",
        "Game",
        "Status",
        "Start Date",
        "End Date",
        "Details URL",
        "Created At",
        "Updated At",
    ])

    for campaign in queryset:
        # Determine campaign status
        if campaign.start_at and campaign.end_at:
            if campaign.start_at <= now <= campaign.end_at:
                status: str = "Active"
            elif campaign.start_at > now:
                status: str = "Upcoming"
            else:
                status: str = "Expired"
        else:
            status: str = "Unknown"

        writer.writerow([
            campaign.twitch_id,
            campaign.name,
            campaign.description or "",
            campaign.game.name if campaign.game else "",
            status,
            campaign.start_at.isoformat() if campaign.start_at else "",
            campaign.end_at.isoformat() if campaign.end_at else "",
            campaign.details_url,
            campaign.added_at.isoformat() if campaign.added_at else "",
            campaign.updated_at.isoformat() if campaign.updated_at else "",
        ])

    return response


def export_campaigns_json(request: HttpRequest) -> HttpResponse:
    """Export drop campaigns to JSON format.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: JSON file response.
    """
    # Get filters from query parameters
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")

    queryset: QuerySet[DropCampaign] = DropCampaign.objects.all()

    if game_filter:
        queryset = queryset.filter(game__twitch_id=game_filter)

    queryset = queryset.prefetch_related("game__owners").order_by("-start_at")

    now: datetime.datetime = timezone.now()
    if status_filter == "active":
        queryset = queryset.filter(start_at__lte=now, end_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(start_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(end_at__lt=now)

    # Build data list
    campaigns_data: list[dict[str, Any]] = []
    for campaign in queryset:
        # Determine campaign status
        if campaign.start_at and campaign.end_at:
            if campaign.start_at <= now <= campaign.end_at:
                status: str = "Active"
            elif campaign.start_at > now:
                status: str = "Upcoming"
            else:
                status: str = "Expired"
        else:
            status: str = "Unknown"

        campaigns_data.append({
            "twitch_id": campaign.twitch_id,
            "name": campaign.name,
            "description": campaign.description,
            "game": campaign.game.name if campaign.game else None,
            "game_twitch_id": campaign.game.twitch_id if campaign.game else None,
            "status": status,
            "start_at": campaign.start_at.isoformat() if campaign.start_at else None,
            "end_at": campaign.end_at.isoformat() if campaign.end_at else None,
            "details_url": campaign.details_url,
            "account_link_url": campaign.account_link_url,
            "added_at": campaign.added_at.isoformat() if campaign.added_at else None,
            "updated_at": campaign.updated_at.isoformat(),
        })

    # Create JSON response
    response = HttpResponse(
        json.dumps(campaigns_data, indent=2),
        content_type="application/json",
    )
    response["Content-Disposition"] = "attachment; filename=campaigns.json"

    return response


def export_games_csv(request: HttpRequest) -> HttpResponse:
    """Export games to CSV format.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: CSV file response.
    """
    queryset: QuerySet[Game] = Game.objects.all().order_by("display_name")

    # Create CSV response
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = "attachment; filename=games.csv"

    writer = csv.writer(response)
    writer.writerow([
        "Twitch ID",
        "Name",
        "Display Name",
        "Slug",
        "Box Art URL",
        "Added At",
        "Updated At",
    ])

    for game in queryset:
        writer.writerow([
            game.twitch_id,
            game.name,
            game.display_name,
            game.slug,
            game.box_art_best_url,
            game.added_at.isoformat() if game.added_at else "",
            game.updated_at.isoformat() if game.updated_at else "",
        ])

    return response


def export_games_json(request: HttpRequest) -> HttpResponse:
    """Export games to JSON format.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: JSON file response.
    """
    queryset: QuerySet[Game] = Game.objects.all().order_by("display_name")

    # Build data list
    games_data: list[dict[str, Any]] = [
        {
            "twitch_id": game.twitch_id,
            "name": game.name,
            "display_name": game.display_name,
            "slug": game.slug,
            "box_art_url": game.box_art_best_url,
            "added_at": game.added_at.isoformat() if game.added_at else None,
            "updated_at": game.updated_at.isoformat() if game.updated_at else None,
        }
        for game in queryset
    ]

    # Create JSON response
    response = HttpResponse(
        json.dumps(games_data, indent=2),
        content_type="application/json",
    )
    response["Content-Disposition"] = "attachment; filename=games.json"

    return response


def export_organizations_csv(request: HttpRequest) -> HttpResponse:
    """Export organizations to CSV format.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: CSV file response.
    """
    queryset: QuerySet[Organization] = Organization.objects.all().order_by("name")

    # Create CSV response
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = "attachment; filename=organizations.csv"

    writer = csv.writer(response)
    writer.writerow(["Twitch ID", "Name", "Added At", "Updated At"])

    for org in queryset:
        writer.writerow([
            org.twitch_id,
            org.name,
            org.added_at.isoformat() if org.added_at else "",
            org.updated_at.isoformat() if org.updated_at else "",
        ])

    return response


def export_organizations_json(request: HttpRequest) -> HttpResponse:
    """Export organizations to JSON format.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: JSON file response.
    """
    queryset: QuerySet[Organization] = Organization.objects.all().order_by("name")

    # Build data list
    orgs_data: list[dict[str, Any]] = [
        {
            "twitch_id": org.twitch_id,
            "name": org.name,
            "added_at": org.added_at.isoformat() if org.added_at else None,
            "updated_at": org.updated_at.isoformat() if org.updated_at else None,
        }
        for org in queryset
    ]

    # Create JSON response
    response = HttpResponse(
        json.dumps(orgs_data, indent=2),
        content_type="application/json",
    )
    response["Content-Disposition"] = "attachment; filename=organizations.json"

    return response


# MARK: /sitewide/
def sitewide_rewards_view(request: HttpRequest) -> HttpResponse:
    """View to display all site-wide reward campaigns.

    Site-wide reward campaigns have is_sitewide=True and no specific game.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered site-wide rewards page.
    """
    now: datetime.datetime = timezone.now()

    # Fetch all reward campaigns that are site-wide
    sitewide_campaigns: QuerySet[RewardCampaign] = (
        RewardCampaign.objects
        .filter(is_sitewide=True)
        .select_related("game")
        .prefetch_related("rewards")
        .order_by("-starts_at")
    )

    # Split into active, upcoming, expired
    active_campaigns: list[RewardCampaign] = []
    upcoming_campaigns: list[RewardCampaign] = []
    expired_campaigns: list[RewardCampaign] = []

    for campaign in sitewide_campaigns:
        if campaign.starts_at is None or campaign.ends_at is None:
            continue
        if campaign.starts_at <= now <= campaign.ends_at:
            active_campaigns.append(campaign)
        elif campaign.starts_at > now:
            upcoming_campaigns.append(campaign)
        elif campaign.ends_at < now:
            expired_campaigns.append(campaign)

    title: str = "Twitch Site-wide Rewards and Bonuses"
    description: str = (
        "Explore Twitch site-wide reward campaigns in our archive, active and "
        "expired. Follow new bonuses via RSS feeds or query every reward with "
        "the JSON API."
    )

    url: str = build_absolute_uri(reverse("twitch:sitewide_rewards"))

    # CollectionPage schema
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": url,
    }

    # Breadcrumb schema
    breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
        ("Home", "core:dashboard", None),
        ("Twitch", "twitch:dashboard", None),
        ("Reward Campaigns", "twitch:reward_campaign_list", None),
        (title, "twitch:sitewide_rewards", None),
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": url,
            "schema_data": collection_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "feed_links": [
                _feed_link("sitewide_reward_feed", "Site-wide rewards (RSS)"),
                _feed_link("sitewide_reward_feed_atom", "Site-wide rewards (Atom)"),
            ],
        },
    )
    context: dict[str, Any] = {
        "active_campaigns": active_campaigns,
        "upcoming_campaigns": upcoming_campaigns,
        "expired_campaigns": expired_campaigns,
        "now": now,
        **seo_context,
    }

    return render(request, "twitch/sitewide_rewards.html", context)


# MARK: /rewards/no-game/
def game_less_rewards_view(request: HttpRequest) -> HttpResponse:
    """View to display all reward campaigns with no game linked.

    Includes both site-wide (is_sitewide=True) and brand-specific campaigns
    that don't have a Twitch game associated with them.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered game-less rewards page.
    """
    now: datetime.datetime = timezone.now()

    # Fetch all reward campaigns without a game
    campaigns: QuerySet[RewardCampaign] = (
        RewardCampaign.objects
        .filter(game__isnull=True)
        .select_related("game")
        .prefetch_related("rewards")
        .order_by("-starts_at")
    )

    # Split into active, upcoming, expired
    active_campaigns: list[RewardCampaign] = []
    upcoming_campaigns: list[RewardCampaign] = []
    expired_campaigns: list[RewardCampaign] = []

    for campaign in campaigns:
        if campaign.starts_at is None or campaign.ends_at is None:
            continue
        if campaign.starts_at <= now <= campaign.ends_at:
            active_campaigns.append(campaign)
        elif campaign.starts_at > now:
            upcoming_campaigns.append(campaign)
        elif campaign.ends_at < now:
            expired_campaigns.append(campaign)

    title: str = "Twitch Brand Rewards and Site-wide Drops"
    description: str = (
        "Browse Twitch brand and site-wide reward campaigns in our archive, with no "
        "linked game. Follow new offers via RSS feeds or query the free JSON API."
    )

    url: str = build_absolute_uri(reverse("twitch:game_less_rewards"))

    # CollectionPage schema
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": url,
    }

    # Breadcrumb schema
    breadcrumb_schema: dict[str, Any] = _twitch_breadcrumb([
        ("Home", "core:dashboard", None),
        ("Twitch", "twitch:dashboard", None),
        ("Reward Campaigns", "twitch:reward_campaign_list", None),
        (title, "twitch:game_less_rewards", None),
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": url,
            "schema_data": collection_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "feed_links": [
                _feed_link("game_less_reward_feed", "Brand rewards (RSS)"),
                _feed_link("game_less_reward_feed_atom", "Brand rewards (Atom)"),
            ],
        },
    )
    context: dict[str, Any] = {
        "active_campaigns": active_campaigns,
        "upcoming_campaigns": upcoming_campaigns,
        "expired_campaigns": expired_campaigns,
        "now": now,
        **seo_context,
    }

    return render(request, "twitch/game_less_rewards.html", context)


# MARK: History
HISTORY_PAGE_SIZE = 100

_HISTORY_PERIOD_DAYS: dict[str, int] = {
    "24h": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


def _history_cursor(request: HttpRequest) -> tuple[str | None, str]:
    """Return the requested cursor and direction.

    Args:
        request: The HTTP request.

    Returns:
        Tuple of cursor string (or None) and direction.
    """
    older: str | None = request.GET.get("older")
    newer: str | None = request.GET.get("newer")
    if older:
        return older, "older"
    if newer:
        return newer, "newer"
    return None, "older"


def _paginate_history(
    request: HttpRequest,
    *,
    model_filter: str = "",
    action_filter: str = "",
    q: str = "",
    since: datetime.datetime | None = None,
    querysets: list[QuerySet] | None = None,
) -> tuple[HistoryPage, list[dict[str, Any]]]:
    """Build a materialized history page and presentation rows.

    Args:
        request: The HTTP request.
        model_filter: Optional model filter for global pages.
        action_filter: Optional action filter for global pages.
        q: Optional search string for global pages.
        since: Optional lower time bound for global pages.
        querysets: Per-model querysets for entity pages.

    Returns:
        Tuple of the history page and rendered event rows.
    """
    page: HistoryPage
    cursor: str | None
    direction: str
    cursor, direction = _history_cursor(request)
    if querysets is not None:
        page = history_page_for_querysets(
            querysets,
            cursor=cursor,
            direction=direction,
            per_page=HISTORY_PAGE_SIZE,
        )
    else:
        page = history_page(
            model_filter=model_filter,
            action_filter=action_filter,
            q=q,
            since=since,
            cursor=cursor,
            direction=direction,
            per_page=HISTORY_PAGE_SIZE,
        )
    return page, build_event_rows(page.rows)


def _history_since(period: str) -> datetime.datetime | None:
    days: int | None = _HISTORY_PERIOD_DAYS.get(period)
    if days is None:
        return None
    return timezone.now() - datetime.timedelta(days=days)


def _history_pagination_context(
    request: HttpRequest,
    page: HistoryPage,
    *,
    base_url: str,
) -> dict[str, Any]:
    """Build cursor pagination context for a history page.

    Args:
        request: The HTTP request.
        page: Materialized history page.
        base_url: Base URL preserving current filters.

    Returns:
        Template pagination context.
    """
    return {
        "history_base_url": base_url,
        "page_join": "&" if "?" in base_url else "?",
        "older_cursor": page.older_cursor,
        "newer_cursor": page.newer_cursor,
    }


def history_view(request: HttpRequest) -> HttpResponse:
    """Display the global SteamDB-style history browser.

    Args:
        request: The HTTP request.

    Returns:
        Rendered global history page.
    """
    model_filter: str = request.GET.get("model", "")
    action_filter: str = request.GET.get("action", "")
    period: str = request.GET.get("period", "7d")
    search: str = request.GET.get("q", "").strip()

    page, rows = _paginate_history(
        request,
        model_filter=model_filter,
        action_filter=action_filter,
        q=search,
        since=_history_since(period),
    )

    base_url: str = "/twitch/history/"
    query_params: dict[str, str] = {
        key: value
        for key, value in (
            ("model", model_filter),
            ("action", action_filter),
            ("period", period),
            ("q", search),
        )
        if value
    }
    if query_params:
        base_url += "?" + urlencode(query_params)

    model_options: list[dict[str, str]] = [
        {"value": value, "label": label} for value, label in MODEL_FILTER_LABELS.items()
    ]
    action_options: list[dict[str, str]] = [
        {"value": "", "label": "All actions"},
        {"value": "created", "label": "Created"},
        {"value": "updated", "label": "Updated"},
        {"value": "deleted", "label": "Deleted"},
        {"value": "membership_added", "label": "Membership added"},
        {"value": "membership_removed", "label": "Membership removed"},
    ]

    url: str = build_absolute_uri(base_url)
    description: str = (
        "Browse a timestamped history of every Twitch drop change in our "
        "archive. Follow updates via RSS feeds or query the data with the "
        "JSON API."
    )
    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Drop Archive - Change History",
        page_description=description,
        seo_meta={
            "page_url": url,
            "schema_data": _webpage_schema(
                "Twitch Drop Archive History",
                description,
                url,
            ),
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("History", "twitch:history", None),
            ]),
        },
    )
    context: dict[str, Any] = {
        "events": rows,
        "history_title": "Twitch archive history",
        "history_description": (
            "Every tracked change to campaigns, drops, rewards, games, "
            "organizations and channels."
        ),
        "show_filters": True,
        "model_options": model_options,
        "action_options": action_options,
        "period_options": [
            {"value": "24h", "label": "Last 24 hours"},
            {"value": "7d", "label": "Last 7 days"},
            {"value": "30d", "label": "Last 30 days"},
            {"value": "90d", "label": "Last 90 days"},
            {"value": "all", "label": "All time"},
        ],
        "selected_model": model_filter,
        "selected_action": action_filter,
        "selected_period": period,
        "search_query": search,
        **_history_pagination_context(request, page, base_url=base_url),
        **seo_context,
    }
    return render(request, "twitch/history.html", context)


def _render_entity_history(
    request: HttpRequest,
    *,
    entity: HistoryEntity,
    entity_name: str,
    entity_url: str,
    title: str,
    description: str,
    querysets: list[QuerySet],
) -> HttpResponse:
    """Render a per-object history page.

    Args:
        request: The HTTP request.
        entity: The campaign, game, organization, channel, or reward campaign.
        entity_name: Human-readable object name.
        entity_url: Canonical object URL.
        title: Page title.
        description: Page description.
        querysets: Per-event-model querysets.

    Returns:
        Rendered history page.
    """
    page, rows = _paginate_history(request, querysets=querysets)
    base_url: str = request.path
    url: str = build_absolute_uri(base_url)
    breadcrumb_items: list[dict[str, str | int]] = [
        {"name": "Home", "url": build_absolute_uri(reverse("core:dashboard"))},
        {"name": "Twitch", "url": build_absolute_uri(reverse("twitch:dashboard"))},
        {"name": "History", "url": build_absolute_uri(reverse("twitch:history"))},
    ]
    if entity_name and entity_url:
        breadcrumb_items.append({
            "name": entity_name,
            "url": build_absolute_uri(entity_url),
        })
    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": url,
            "schema_data": _webpage_schema(title, description, url),
            "breadcrumb_schema": _build_breadcrumb_schema(breadcrumb_items),
        },
    )
    context: dict[str, Any] = {
        "events": rows,
        "history_title": title,
        "history_description": description,
        "show_filters": False,
        "entity": entity,
        "entity_name": entity_name,
        "entity_url": entity_url,
        **_history_pagination_context(request, page, base_url=base_url),
        **seo_context,
    }
    return render(request, "twitch/history.html", context)


def drop_campaign_history_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Display the history of a drop campaign and its children.

    Args:
        request: The HTTP request.
        twitch_id: The campaign Twitch ID.

    Returns:
        Rendered campaign history page.
    """
    campaign: DropCampaign = get_object_or_404(DropCampaign, twitch_id=twitch_id)
    querysets: list[QuerySet] = campaign_history_querysets(campaign)
    entity_url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
    return _render_entity_history(
        request,
        entity=campaign,
        entity_name=campaign.name,
        entity_url=entity_url,
        title=f"{campaign.name} - Twitch Drop History",
        description=(
            f"Timestamped history of {campaign.name} in the Twitch drops archive. "
            "Follow changes via RSS feeds or query the full dataset with the "
            "JSON API."
        ),
        querysets=querysets,
    )


def game_history_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Display the history of a game and its campaigns/rewards.

    Args:
        request: The HTTP request.
        twitch_id: The game Twitch ID.

    Returns:
        Rendered game history page.
    """
    game: Game = get_object_or_404(Game, twitch_id=twitch_id)
    querysets: list[QuerySet] = game_history_querysets(game)
    entity_url: str = reverse("twitch:game_detail", args=[game.twitch_id])
    return _render_entity_history(
        request,
        entity=game,
        entity_name=game.get_game_name,
        entity_url=entity_url,
        title=f"{game.get_game_name} - Twitch Drop History",
        description=(
            f"Timestamped history of {game.get_game_name} in the Twitch drops "
            "archive. Follow changes via RSS feeds or query the full dataset "
            "with the JSON API."
        ),
        querysets=querysets,
    )


def organization_history_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Display the history of an organization and its games.

    Args:
        request: The HTTP request.
        twitch_id: The organization Twitch ID.

    Returns:
        Rendered organization history page.
    """
    organization: Organization = get_object_or_404(Organization, twitch_id=twitch_id)
    querysets: list[QuerySet] = organization_history_querysets(organization)
    entity_url: str = reverse(
        "twitch:organization_detail",
        args=[organization.twitch_id],
    )
    return _render_entity_history(
        request,
        entity=organization,
        entity_name=organization.name,
        entity_url=entity_url,
        title=f"{organization.name} - Twitch Drop History",
        description=(
            f"Timestamped history of {organization.name} in the Twitch drops "
            "archive. Follow changes via RSS feeds or query the full dataset "
            "with the JSON API."
        ),
        querysets=querysets,
    )


def channel_history_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
    """Display the history of a channel and its campaign memberships.

    Args:
        request: The HTTP request.
        twitch_id: The channel Twitch ID.

    Returns:
        Rendered channel history page.
    """
    channel: Channel = get_object_or_404(Channel, twitch_id=twitch_id)
    querysets: list[QuerySet] = channel_history_querysets(channel)
    entity_url: str = reverse("twitch:channel_detail", args=[channel.twitch_id])
    display_name: str = channel.display_name or channel.name
    return _render_entity_history(
        request,
        entity=channel,
        entity_name=display_name,
        entity_url=entity_url,
        title=f"{display_name} - Twitch Drop History",
        description=(
            f"Timestamped history of {display_name} in the Twitch drops archive. "
            "Follow changes via RSS feeds or query the full dataset with the "
            "JSON API."
        ),
        querysets=querysets,
    )


def reward_campaign_history_view(
    request: HttpRequest,
    twitch_id: str,
) -> HttpResponse:
    """Display the history of a reward campaign and its rewards.

    Args:
        request: The HTTP request.
        twitch_id: The reward campaign Twitch ID.

    Returns:
        Rendered reward campaign history page.
    """
    reward_campaign: RewardCampaign = get_object_or_404(
        RewardCampaign,
        twitch_id=twitch_id,
    )
    querysets: list[QuerySet] = reward_campaign_history_querysets(reward_campaign)
    entity_url: str = reverse(
        "twitch:reward_campaign_detail",
        args=[reward_campaign.twitch_id],
    )
    return _render_entity_history(
        request,
        entity=reward_campaign,
        entity_name=reward_campaign.name,
        entity_url=entity_url,
        title=f"{reward_campaign.name} - Twitch Drop History",
        description=(
            f"Timestamped history of {reward_campaign.name} in the Twitch drops "
            "archive. Follow changes via RSS feeds or query the full dataset "
            "with the JSON API."
        ),
        querysets=querysets,
    )


def history_event_detail_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Display a single history event with before/after values.

    Args:
        request: The HTTP request.
        slug: Aggregate event slug.

    Returns:
        Rendered event detail page.

    Raises:
        Http404: If no event matches the slug.
    """
    event: dict[str, Any] | None = event_detail_by_slug(slug)
    if event is None:
        msg = "No history event found matching the query"
        raise Http404(msg)

    detail: dict[str, Any] = build_event_detail(event)
    title: str = f"{detail['object_name']} - {detail['label']}"
    description: str = (
        "A single timestamped change from the Twitch drops archive, with "
        "before and after values, source details, and a link to the full "
        "history."
    )
    url: str = build_absolute_uri(request.path)
    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": url,
            "robots_directive": "noindex, follow",
            "schema_data": _webpage_schema(title, description, url),
            "breadcrumb_schema": _twitch_breadcrumb([
                ("Home", "core:dashboard", None),
                ("Twitch", "twitch:dashboard", None),
                ("History", "twitch:history", None),
                (
                    detail["object_name"],
                    "twitch:history_event_detail",
                    [slug],
                ),
            ]),
        },
    )
    context: dict[str, Any] = {
        "event": detail,
        **seo_context,
    }
    return render(request, "twitch/history_event.html", context)
