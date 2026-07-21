import datetime
import json
import logging
import operator
from collections import OrderedDict
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models.functions import Trim
from django.http import FileResponse
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import render
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone

from core.base_url import build_absolute_uri
from kick.models import KickChannel
from kick.models import KickDropCampaign
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from os import stat_result
    from pathlib import Path

    from django.db.models import QuerySet
    from django.http import HttpRequest


logger: logging.Logger = logging.getLogger("ttvdrops.views")


MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05
DEFAULT_SITE_DESCRIPTION = "Archive of Twitch drops, campaigns, rewards, and more."


def _build_seo_context(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]
    page_title: str = "ttvdrops",
    page_description: str | None = None,
    page_url: str | None = None,
    page_image: str | None = None,
    page_image_width: int | None = None,
    page_image_height: int | None = None,
    og_type: str = "website",
    schema_data: dict[str, Any] | None = None,
    breadcrumb_schema: dict[str, Any] | None = None,
    pagination_info: list[dict[str, str]] | None = None,
    published_date: str | None = None,
    modified_date: str | None = None,
    robots_directive: str = "index, follow",
) -> dict[str, Any]:
    """Build SEO context for template rendering.

    Args:
        page_title: Page title (shown in browser tab, og:title).
        page_description: Page description (meta description, og:description).
        page_url: Canonical absolute URL for the current page.
        page_image: Image URL for og:image meta tag.
        page_image_width: Width of the image in pixels.
        page_image_height: Height of the image in pixels.
        og_type: OpenGraph type (e.g., "website", "article").
        schema_data: Dict representation of Schema.org JSON-LD data.
        breadcrumb_schema: Breadcrumb schema dict for navigation hierarchy.
        pagination_info: List of dicts with "rel" (prev|next|first|last) and "url".
        published_date: ISO 8601 published date (e.g., "2025-01-01T00:00:00Z").
        modified_date: ISO 8601 modified date.
        robots_directive: Robots meta content (e.g., "index, follow" or "noindex").

    Returns:
        Dict with SEO context variables to pass to render().
    """
    if page_url and not page_url.startswith("http"):
        page_url = f"{settings.BASE_URL}{page_url}"

    # TODO(TheLovinator): Instead of having so many parameters,  # ruff:ignore[missing-todo-link]
    # consider having a single "seo_info" parameter that
    # can contain all of these optional fields. This would make
    # it easier to extend in the future without changing the
    # function signature.

    context: dict[str, Any] = {
        "page_title": page_title,
        "page_description": page_description or DEFAULT_SITE_DESCRIPTION,
        "og_type": og_type,
        "robots_directive": robots_directive,
    }
    if page_url:
        context["page_url"] = page_url
    if page_image:
        context["page_image"] = page_image
        if page_image_width and page_image_height:
            context["page_image_width"] = page_image_width
            context["page_image_height"] = page_image_height
    if schema_data:
        context["schema_data"] = json.dumps(schema_data)
    if breadcrumb_schema:
        context["breadcrumb_schema"] = json.dumps(breadcrumb_schema)
    if pagination_info:
        context["pagination_info"] = pagination_info
    if published_date:
        context["published_date"] = published_date
    if modified_date:
        context["modified_date"] = modified_date
    return context


def _render_urlset_xml(
    url_entries: list[dict[str, str]],
) -> str:
    """Render a <urlset> sitemap XML string from URL entries.

    Args:
        url_entries: List of dictionaries containing URL entry data.

    Returns:
        A string containing the rendered XML.

    """
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url_entry in url_entries:
        xml += "  <url>\n"
        loc: str | None = url_entry.get("loc") or url_entry.get(
            "url",
        )  # Handle both keys
        if loc:
            xml += f"    <loc>{loc}</loc>\n"
        if "lastmod" in url_entry:
            xml += f"    <lastmod>{url_entry['lastmod']}</lastmod>\n"
        xml += "  </url>\n"
    xml += "</urlset>\n"
    return xml


def _render_sitemap_index_xml(sitemap_entries: list[dict[str, str]]) -> str:
    """Render a <sitemapindex> XML string listing sitemap URLs.

    Args:
        sitemap_entries: List of dictionaries with "loc" and optional "lastmod".

    Returns:
        A string containing the rendered XML.
    """
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for entry in sitemap_entries:
        xml += "  <sitemap>\n"
        xml += f"    <loc>{entry['loc']}</loc>\n"
        if entry.get("lastmod"):
            xml += f"    <lastmod>{entry['lastmod']}</lastmod>\n"
        xml += "  </sitemap>\n"
    xml += "</sitemapindex>"
    return xml


def _build_base_url() -> str:
    """Return the base URL for the site using settings.BASE_URL."""
    return getattr(settings, "BASE_URL", "https://ttvdrops.lovinator.space")


# MARK: /sitemap.xml
def sitemap_view(request: HttpRequest) -> HttpResponse:
    """Sitemap index pointing to per-section sitemap files.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap index XML.
    """
    base_url: str = _build_base_url()

    # Compute last modified per-section so search engines can more intelligently crawl.
    # Do not fabricate a lastmod date if the section has no data.
    twitch_channels_lastmod: datetime.datetime | None = Channel.objects.aggregate(
        max=Max("updated_at"),
    )["max"]

    twitch_drops_lastmod: datetime.datetime | None = max(
        (
            dt
            for dt in [
                DropCampaign.objects.aggregate(max=Max("updated_at"))["max"],
                RewardCampaign.objects.aggregate(max=Max("updated_at"))["max"],
            ]
            if dt is not None
        ),
        default=None,
    )

    twitch_others_lastmod: datetime.datetime | None = max(
        (
            dt
            for dt in [
                Game.objects.aggregate(max=Max("updated_at"))["max"],
                Organization.objects.aggregate(max=Max("updated_at"))["max"],
                ChatBadgeSet.objects.aggregate(max=Max("updated_at"))["max"],
            ]
            if dt is not None
        ),
        default=None,
    )

    kick_lastmod: datetime.datetime | None = KickDropCampaign.objects.aggregate(
        max=Max("updated_at"),
    )["max"]

    sitemap_entries: list[dict[str, str]] = [
        {"loc": f"{base_url}/sitemap-static.xml"},
        {"loc": f"{base_url}/sitemap-twitch-channels.xml"},
        {"loc": f"{base_url}/sitemap-twitch-drops.xml"},
        {"loc": f"{base_url}/sitemap-twitch-others.xml"},
        {"loc": f"{base_url}/sitemap-kick.xml"},
        {"loc": f"{base_url}/sitemap-youtube.xml"},
    ]

    if twitch_channels_lastmod is not None:
        sitemap_entries[1]["lastmod"] = twitch_channels_lastmod.isoformat()
    if twitch_drops_lastmod is not None:
        sitemap_entries[2]["lastmod"] = twitch_drops_lastmod.isoformat()
    if twitch_others_lastmod is not None:
        sitemap_entries[3]["lastmod"] = twitch_others_lastmod.isoformat()
    if kick_lastmod is not None:
        sitemap_entries[4]["lastmod"] = kick_lastmod.isoformat()

    xml_content: str = _render_sitemap_index_xml(sitemap_entries)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /sitemap-static.xml
def sitemap_static_view(request: HttpRequest) -> HttpResponse:
    """Sitemap containing the main static pages.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap XML.
    """
    base_url: str = _build_base_url().rstrip("/")
    sitemap_urls: list[dict[str, str]] = [
        {"loc": f"{base_url}{reverse('core:dashboard')}"},
        {"loc": f"{base_url}{reverse('core:search')}"},
        {"loc": f"{base_url}{reverse('core:debug')}"},
        {"loc": f"{base_url}{reverse('core:dataset_backups')}"},
        {"loc": f"{base_url}{reverse('core:docs_rss')}"},
        # Core RSS/Atom/Discord feeds
        {"loc": f"{base_url}{reverse('core:campaign_feed')}"},
        {"loc": f"{base_url}{reverse('core:game_feed')}"},
        {"loc": f"{base_url}{reverse('core:organization_feed')}"},
        {"loc": f"{base_url}{reverse('core:reward_campaign_feed')}"},
        {"loc": f"{base_url}{reverse('core:campaign_feed_atom')}"},
        {"loc": f"{base_url}{reverse('core:game_feed_atom')}"},
        {"loc": f"{base_url}{reverse('core:organization_feed_atom')}"},
        {"loc": f"{base_url}{reverse('core:reward_campaign_feed_atom')}"},
        {"loc": f"{base_url}{reverse('core:campaign_feed_discord')}"},
        {"loc": f"{base_url}{reverse('core:game_feed_discord')}"},
        {"loc": f"{base_url}{reverse('core:organization_feed_discord')}"},
        {"loc": f"{base_url}{reverse('core:reward_campaign_feed_discord')}"},
        # Twitch app pages
        {"loc": f"{base_url}{reverse('twitch:dashboard')}"},
        {"loc": f"{base_url}{reverse('twitch:campaign_list')}"},
        {"loc": f"{base_url}{reverse('twitch:games_grid')}"},
        {"loc": f"{base_url}{reverse('twitch:games_list')}"},
        {"loc": f"{base_url}{reverse('twitch:channel_list')}"},
        {"loc": f"{base_url}{reverse('twitch:badge_list')}"},
        {"loc": f"{base_url}{reverse('twitch:emote_gallery')}"},
        {"loc": f"{base_url}{reverse('twitch:org_list')}"},
        {"loc": f"{base_url}{reverse('twitch:reward_campaign_list')}"},
        {"loc": f"{base_url}{reverse('twitch:export_campaigns_csv')}"},
        {"loc": f"{base_url}{reverse('twitch:export_games_csv')}"},
        {"loc": f"{base_url}{reverse('twitch:export_organizations_csv')}"},
        {"loc": f"{base_url}{reverse('twitch:export_campaigns_json')}"},
        {"loc": f"{base_url}{reverse('twitch:export_games_json')}"},
        {"loc": f"{base_url}{reverse('twitch:export_organizations_json')}"},
        # Kick app pages and feeds
        {"loc": f"{base_url}{reverse('kick:dashboard')}"},
        {"loc": f"{base_url}{reverse('kick:campaign_list')}"},
        {"loc": f"{base_url}{reverse('kick:game_list')}"},
        {"loc": f"{base_url}{reverse('kick:organization_list')}"},
        {"loc": f"{base_url}{reverse('kick:campaign_feed')}"},
        {"loc": f"{base_url}{reverse('kick:game_feed')}"},
        {"loc": f"{base_url}{reverse('kick:organization_feed')}"},
        {"loc": f"{base_url}{reverse('kick:campaign_feed_atom')}"},
        {"loc": f"{base_url}{reverse('kick:game_feed_atom')}"},
        {"loc": f"{base_url}{reverse('kick:organization_feed_atom')}"},
        {"loc": f"{base_url}{reverse('kick:campaign_feed_discord')}"},
        {"loc": f"{base_url}{reverse('kick:game_feed_discord')}"},
        {"loc": f"{base_url}{reverse('kick:organization_feed_discord')}"},
        # YouTube
        {"loc": f"{base_url}{reverse('youtube:index')}"},
        # Misc/static
        {"loc": f"{base_url}/about/"},
        {"loc": f"{base_url}/robots.txt"},
    ]
    xml_content: str = _render_urlset_xml(sitemap_urls)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /sitemap-twitch-channels.xml
def sitemap_twitch_channels_view(request: HttpRequest) -> HttpResponse:
    """Sitemap containing Twitch channel pages.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap XML.
    """
    base_url: str = _build_base_url().rstrip("/")
    channel_detail_prefix: str = reverse("twitch:channel_list")
    sitemap_urls: list[dict[str, str]] = []

    channels: QuerySet[Channel] = Channel.objects.only(
        "twitch_id",
        "updated_at",
    ).order_by()
    for channel in channels:
        full_url: str = f"{base_url}{channel_detail_prefix}{channel.twitch_id}/"
        entry: dict[str, str] = {"loc": full_url}
        if channel.updated_at:
            entry["lastmod"] = channel.updated_at.isoformat()
        sitemap_urls.append(entry)

    xml_content: str = _render_urlset_xml(sitemap_urls)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /sitemap-twitch-drops.xml
def sitemap_twitch_drops_view(request: HttpRequest) -> HttpResponse:
    """Sitemap containing Twitch drop campaign pages.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap XML.
    """
    base_url: str = _build_base_url().rstrip("/")
    campaign_detail_prefix: str = reverse("twitch:campaign_list")
    reward_campaign_detail_prefix: str = reverse("twitch:reward_campaign_list")
    sitemap_urls: list[dict[str, str]] = []

    campaigns: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(
            is_fully_imported=True,
        )
        .only("twitch_id", "updated_at")
        .order_by()
    )
    for campaign in campaigns:
        full_url: str = f"{base_url}{campaign_detail_prefix}{campaign.twitch_id}/"
        campaign_url_entry: dict[str, str] = {"loc": full_url}
        if campaign.updated_at:
            campaign_url_entry["lastmod"] = campaign.updated_at.isoformat()
        sitemap_urls.append(campaign_url_entry)

    reward_campaigns: QuerySet[RewardCampaign] = RewardCampaign.objects.only(
        "twitch_id",
        "updated_at",
    ).order_by()
    for reward_campaign in reward_campaigns:
        full_url = (
            f"{base_url}{reward_campaign_detail_prefix}{reward_campaign.twitch_id}/"
        )
        reward_campaign_url_entry: dict[str, str] = {"loc": full_url}
        if reward_campaign.updated_at:
            reward_campaign_url_entry["lastmod"] = (
                reward_campaign.updated_at.isoformat()
            )

        sitemap_urls.append(reward_campaign_url_entry)

    xml_content: str = _render_urlset_xml(sitemap_urls)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /sitemap-twitch-others.xml
def sitemap_twitch_others_view(request: HttpRequest) -> HttpResponse:
    """Sitemap containing other Twitch pages (games, organizations, badges, emotes).

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap XML.
    """
    base_url: str = _build_base_url().rstrip("/")
    game_detail_prefix: str = reverse("twitch:games_grid")
    organization_detail_prefix: str = reverse("twitch:org_list")
    badge_set_detail_prefix: str = reverse("twitch:badge_list")
    sitemap_urls: list[dict[str, str]] = []

    games: QuerySet[Game] = Game.objects.only("twitch_id", "updated_at").order_by()
    for game in games:
        full_url: str = f"{base_url}{game_detail_prefix}{game.twitch_id}/"
        entry: dict[str, str] = {"loc": full_url}

        if game.updated_at:
            entry["lastmod"] = game.updated_at.isoformat()

        sitemap_urls.append(entry)

    orgs: QuerySet[Organization] = Organization.objects.only(
        "twitch_id",
        "updated_at",
    ).order_by()
    for org in orgs:
        full_url: str = f"{base_url}{organization_detail_prefix}{org.twitch_id}/"
        entry: dict[str, str] = {"loc": full_url}

        if org.updated_at:
            entry["lastmod"] = org.updated_at.isoformat()

        sitemap_urls.append(entry)

    badge_sets: QuerySet[ChatBadgeSet] = ChatBadgeSet.objects.only("set_id").order_by()
    for badge_set in badge_sets:
        full_url = f"{base_url}{badge_set_detail_prefix}{badge_set.set_id}/"
        sitemap_urls.append({"loc": full_url})

    # Emotes currently don't have individual detail pages, but keep a listing here.
    sitemap_urls.append({"loc": f"{base_url}/emotes/"})

    xml_content: str = _render_urlset_xml(sitemap_urls)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /sitemap-kick.xml
def sitemap_kick_view(request: HttpRequest) -> HttpResponse:
    """Sitemap containing Kick drops and related pages.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap XML.
    """
    base_url: str = _build_base_url().rstrip("/")
    kick_campaign_detail_prefix: str = reverse("kick:campaign_list")
    sitemap_urls: list[dict[str, str]] = []

    kick_campaigns: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .filter(
            is_fully_imported=True,
        )
        .only("kick_id", "updated_at")
        .order_by()
    )
    for campaign in kick_campaigns:
        full_url: str = f"{base_url}{kick_campaign_detail_prefix}{campaign.kick_id}/"
        entry: dict[str, str] = {"loc": full_url}
        if campaign.updated_at:
            entry["lastmod"] = campaign.updated_at.isoformat()
        sitemap_urls.append(entry)

    xml_content: str = _render_urlset_xml(sitemap_urls)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /sitemap-youtube.xml
def sitemap_youtube_view(request: HttpRequest) -> HttpResponse:
    """Sitemap containing the YouTube page(s).

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered sitemap XML.
    """
    base_url: str = _build_base_url()
    sitemap_urls: list[dict[str, str]] = [
        {"loc": f"{base_url}{reverse('youtube:index')}"},
    ]

    xml_content: str = _render_urlset_xml(sitemap_urls)
    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /docs/rss/
def docs_rss_view(request: HttpRequest) -> HttpResponse:
    """View for /docs/rss that lists all available feeds and explains how to use them.

    Args:
        request: The HTTP request object.

    Returns:
        HttpResponse: The rendered documentation page.
    """
    now: datetime.datetime = timezone.now()
    sample_game: Game | None = (
        Game.objects
        .filter(drop_campaigns__start_at__lte=now, drop_campaigns__end_at__gte=now)
        .distinct()
        .first()
    )

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Feed Documentation",
        page_description="Documentation for the RSS feeds available on ttvdrops.lovinator.space, including how to use them and what data they contain.",
        page_url=build_absolute_uri(reverse("core:docs_rss")),
    )

    return render(
        request,
        "core/docs_rss.html",
        {
            "game": sample_game,
            **seo_context,
        },
    )


# MARK: /debug/
def debug_view(request: HttpRequest) -> HttpResponse:  # ruff:ignore[too-many-locals]
    """Debug view showing potentially broken or inconsistent data.

    Returns:
        HttpResponse: Rendered debug template or redirect if unauthorized.
    """
    now: datetime.datetime = timezone.now()

    # Games with no assigned owner organization
    games_without_owner: QuerySet[Game] = Game.objects.filter(
        owners__isnull=True,
    ).order_by("display_name")

    # Campaigns with no images at all (no direct URL and no benefit image fallbacks)
    broken_image_campaigns: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(
            Q(image_url__isnull=True)
            | Q(image_url__exact="")
            | ~Q(image_url__startswith="http"),
        )
        .exclude(
            Exists(
                TimeBasedDrop.objects.filter(campaign=OuterRef("pk")).filter(
                    benefits__image_asset_url__startswith="http",
                ),
            ),
        )
        .select_related("game")
    )

    # Benefits with missing images
    broken_benefit_images: QuerySet[DropBenefit] = DropBenefit.objects.annotate(
        trimmed_url=Trim("image_asset_url"),
    ).filter(
        Q(image_asset_url__isnull=True)
        | Q(trimmed_url__exact="")
        | ~Q(image_asset_url__startswith="http"),
    )

    # Time-based drops without any benefits
    drops_without_benefits: QuerySet[TimeBasedDrop] = TimeBasedDrop.objects.filter(
        benefits__isnull=True,
    ).select_related("campaign__game")

    # Campaigns with invalid dates (start after end or missing either)
    invalid_date_campaigns: QuerySet[DropCampaign] = DropCampaign.objects.filter(
        Q(start_at__gt=F("end_at")) | Q(start_at__isnull=True) | Q(end_at__isnull=True),
    ).select_related("game")

    # Duplicate campaign names per game.
    # We retrieve the game's name for user-friendly display.
    duplicate_name_campaigns: QuerySet[DropCampaign, dict[str, Any]] = (
        DropCampaign.objects
        .values("game__display_name", "name", "game__twitch_id")
        .annotate(name_count=Count("twitch_id"))
        .filter(name_count__gt=1)
        .order_by("game__display_name", "name")
    )

    # Active campaigns with no images at all
    active_missing_image: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(start_at__lte=now, end_at__gte=now)
        .filter(
            Q(image_url__isnull=True)
            | Q(image_url__exact="")
            | ~Q(image_url__startswith="http"),
        )
        .exclude(
            Exists(
                TimeBasedDrop.objects.filter(campaign=OuterRef("pk")).filter(
                    benefits__image_asset_url__startswith="http",
                ),
            ),
        )
        .select_related("game")
    )

    # Distinct GraphQL operation names used to fetch campaigns with counts
    # Since operation_names is now a JSON list field, we need to flatten and count
    operation_names_counter: dict[str, int] = {}
    for campaign in DropCampaign.objects.only("operation_names"):
        for op_name in campaign.operation_names:
            if op_name and op_name.strip():
                operation_names_counter[op_name.strip()] = (
                    operation_names_counter.get(op_name.strip(), 0) + 1
                )

    operation_names_with_counts: list[dict[str, Any]] = [
        {"trimmed_op": op_name, "count": count}
        for op_name, count in sorted(operation_names_counter.items())
    ]

    # Campaigns missing DropCampaignDetails operation name
    # Need to handle SQLite separately since it doesn't support JSONField lookups
    # Sqlite is used when testing
    if connection.vendor == "sqlite":
        all_campaigns: QuerySet[DropCampaign] = DropCampaign.objects.select_related(
            "game",
        ).order_by("game__display_name", "name")
        campaigns_missing_dropcampaigndetails: list[DropCampaign] = [
            c
            for c in all_campaigns
            if c.operation_names is None
            or "DropCampaignDetails" not in c.operation_names
        ]
    else:
        campaigns_missing_dropcampaigndetails: list[DropCampaign] = list(
            DropCampaign.objects
            .filter(
                Q(operation_names__isnull=True)
                | ~Q(operation_names__contains=["DropCampaignDetails"]),
            )
            .select_related("game")
            .order_by("game__display_name", "name"),
        )

    # ── Data source diff ────────────────────────────────────────────────
    # Compare campaigns imported from different data sources
    sunkibot_drop_ids: set[str] = set(
        DropCampaign.objects.filter(
            data_source="SunkwiBOT/twitch-drops-api",
        ).values_list("twitch_id", flat=True),
    )
    miner_drop_ids: set[str] = set(
        DropCampaign.objects.filter(data_source="TwitchDropsMiner").values_list(
            "twitch_id",
            flat=True,
        ),
    )

    # Drops only in SunkwiBOT (not in TwitchDropsMiner)
    sunkibot_only_drop_ids: set[str] = sunkibot_drop_ids - miner_drop_ids
    # Drops only in TwitchDropsMiner (not in SunkwiBOT)
    miner_only_drop_ids: set[str] = miner_drop_ids - sunkibot_drop_ids

    sunkibot_only_drops: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(twitch_id__in=sunkibot_only_drop_ids)
        .select_related("game")
        .order_by("name")[:200]
    )
    miner_only_drops: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(twitch_id__in=miner_only_drop_ids)
        .select_related("game")
        .order_by("name")[:200]
    )

    # Same for reward campaigns
    sunkibot_reward_ids: set[str] = set(
        RewardCampaign.objects.filter(
            data_source="SunkwiBOT/twitch-drops-api",
        ).values_list("twitch_id", flat=True),
    )
    miner_reward_ids: set[str] = set(
        RewardCampaign.objects.filter(data_source="TwitchDropsMiner").values_list(
            "twitch_id",
            flat=True,
        ),
    )

    sunkibot_only_reward_ids: set[str] = sunkibot_reward_ids - miner_reward_ids
    miner_only_reward_ids: set[str] = miner_reward_ids - sunkibot_reward_ids

    sunkibot_only_rewards: QuerySet[RewardCampaign] = RewardCampaign.objects.filter(
        twitch_id__in=sunkibot_only_reward_ids,
    ).order_by("name")[:200]
    miner_only_rewards: QuerySet[RewardCampaign] = RewardCampaign.objects.filter(
        twitch_id__in=miner_only_reward_ids,
    ).order_by("name")[:200]

    # ── Unknown-status campaigns ────────────────────────────────────────
    # Kick campaigns with incomplete dates
    kick_unknown_campaigns: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .filter(
            Q(starts_at__isnull=True) | Q(ends_at__isnull=True),
            is_fully_imported=True,
        )
        .select_related("category", "organization")
        .order_by("name")
    )
    # Twitch reward campaigns with incomplete dates
    twitch_reward_unknown_campaigns: QuerySet[RewardCampaign] = (
        RewardCampaign.objects
        .filter(
            Q(starts_at__isnull=True) | Q(ends_at__isnull=True),
        )
        .select_related("game")
        .order_by("name")
    )

    context: dict[str, Any] = {
        "now": now,
        "games_without_owner": games_without_owner,
        "broken_image_campaigns": broken_image_campaigns,
        "broken_benefit_images": broken_benefit_images,
        "drops_without_benefits": drops_without_benefits,
        "invalid_date_campaigns": invalid_date_campaigns,
        "duplicate_name_campaigns": duplicate_name_campaigns,
        "active_missing_image": active_missing_image,
        "operation_names_with_counts": operation_names_with_counts,
        "campaigns_missing_dropcampaigndetails": campaigns_missing_dropcampaigndetails,
        # Data source diff
        "sunkibot_only_drops_count": len(sunkibot_only_drop_ids),
        "miner_only_drops_count": len(miner_only_drop_ids),
        "sunkibot_only_drops": sunkibot_only_drops,
        "miner_only_drops": miner_only_drops,
        "sunkibot_only_rewards_count": len(sunkibot_only_reward_ids),
        "miner_only_rewards_count": len(miner_only_reward_ids),
        "sunkibot_only_rewards": sunkibot_only_rewards,
        "miner_only_rewards": miner_only_rewards,
        # Unknown-status campaigns
        "kick_unknown_campaigns": kick_unknown_campaigns,
        "twitch_reward_unknown_campaigns": twitch_reward_unknown_campaigns,
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Debug",
        page_description="Debug view showing potentially broken or inconsistent data.",
        robots_directive="noindex, nofollow",
    )
    context.update(seo_context)

    return render(request, "core/debug.html", context)


# MARK: /datasets/
def dataset_backups_view(request: HttpRequest) -> HttpResponse:
    """View to list database backup datasets on disk.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered dataset backups page.
    """
    # TODO(TheLovinator): Instead of only using sql we should also support other formats like parquet, csv, or json.  # ruff:ignore[missing-todo-link]
    # TODO(TheLovinator): Upload to s3 instead.  # ruff:ignore[missing-todo-link]
    # TODO(TheLovinator): https://developers.google.com/search/docs/appearance/structured-data/dataset#json-ld
    datasets_root: Path = settings.DATA_DIR / "datasets"
    search_dirs: list[Path] = [datasets_root]
    seen_paths: set[str] = set()
    datasets: list[dict[str, Any]] = []

    for folder in search_dirs:
        if not folder.exists() or not folder.is_dir():
            continue

        # Only include .zst files
        for path in folder.glob("*.zst"):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen_paths:
                continue
            seen_paths.add(key)
            stat: stat_result = path.stat()
            updated_at: datetime.datetime = datetime.datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.get_current_timezone(),
            )
            try:
                display_path = str(path.relative_to(datasets_root))
                download_path: str | None = display_path
            except ValueError:
                display_path: str = path.name
                download_path: str | None = None
            datasets.append({
                "name": path.name,
                "display_path": display_path,
                "download_path": download_path,
                "size": filesizeformat(stat.st_size),
                "updated_at": updated_at,
            })

    datasets.sort(key=operator.itemgetter("updated_at"), reverse=True)

    dataset_distributions: list[dict[str, str]] = []
    for dataset in datasets:
        download_path: str | None = dataset.get("download_path")
        if not download_path:
            continue
        dataset_distributions.append({
            "@type": "DataDownload",
            "name": dataset["name"],
            "contentUrl": build_absolute_uri(
                reverse("core:dataset_backup_download", args=[download_path]),
            ),
            "encodingFormat": "application/zstd",
        })

    dataset_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Historical archive of Twitch and Kick drop data",
        "identifier": build_absolute_uri(reverse("core:dataset_backups")),
        "temporalCoverage": "2024-07-17/..",
        "url": build_absolute_uri(reverse("core:dataset_backups")),
        "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "isAccessibleForFree": True,
        "description": (
            "Historical data on Twitch and Kick drops, campaigns, rewards, and more, available for download as compressed SQL files or JSON."
        ),
        "keywords": [
            "Twitch drops",
            "Kick drops",
        ],
        "creator": {
            "@type": "Person",
            "givenName": "Joakim",
            "familyName": "Hellsén",
            "name": "Joakim Hellsén",
            "sameAs": "https://orcid.org/0009-0006-7305-524X",
        },
        "includedInDataCatalog": {
            "@type": "DataCatalog",
            "name": "ttvdrops.lovinator.space",
            "url": build_absolute_uri(reverse("core:dataset_backups")),
        },
    }
    if dataset_distributions:
        dataset_schema["distribution"] = dataset_distributions

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch/Kick drop data",
        page_description="Twitch/Kick datasets available for download, including historical drop campaign data and more.",
        schema_data=dataset_schema,
    )
    context: dict[str, Any] = {
        "datasets": datasets,
        "data_dir": str(datasets_root),
        "dataset_count": len(datasets),
        **seo_context,
    }
    return render(request, "core/dataset_backups.html", context)


def dataset_backup_download_view(
    request: HttpRequest,
    relative_path: str,
) -> FileResponse:
    """Download a dataset backup from the data directory.

    Args:
        request: The HTTP request.
        relative_path: The path relative to the data directory.

    Returns:
        FileResponse: The file response for the requested dataset.

    Raises:
        Http404: When the file is not found or is outside the data directory.
    """
    # TODO(TheLovinator): Use s3 instead of local disk.  # ruff:ignore[missing-todo-link]

    datasets_root: Path = settings.DATA_DIR / "datasets"
    requested_path: Path = (datasets_root / relative_path).resolve()
    data_root: Path = datasets_root.resolve()

    try:
        requested_path.relative_to(data_root)
    except ValueError as exc:
        msg = "File not found"
        raise Http404(msg) from exc
    if not requested_path.exists() or not requested_path.is_file():
        msg = "File not found"
        raise Http404(msg)
    if not requested_path.name.endswith(".zst"):
        msg = "File not found"
        raise Http404(msg)

    return FileResponse(
        requested_path.open("rb"),
        as_attachment=True,
        filename=requested_path.name,
    )


# MARK: /search/
def search_view(request: HttpRequest) -> HttpResponse:
    """Search view for all models.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered search results.
    """
    query: str = request.GET.get("q", "")
    results: dict[str, QuerySet] = {}

    if query:
        if len(query) < MIN_QUERY_LENGTH_FOR_FTS:
            results["organizations"] = Organization.objects.filter(
                name__istartswith=query,
            )
            results["games"] = Game.objects.filter(
                Q(name__istartswith=query) | Q(display_name__istartswith=query),
            )

            results["campaigns"] = DropCampaign.objects.filter(
                Q(name__istartswith=query) | Q(description__icontains=query),
            ).select_related("game")

            results["drops"] = TimeBasedDrop.objects.filter(
                name__istartswith=query,
            ).select_related("campaign")

            results["benefits"] = DropBenefit.objects.filter(
                name__istartswith=query,
            ).prefetch_related("drops__campaign")

            results["reward_campaigns"] = RewardCampaign.objects.filter(
                Q(name__istartswith=query)
                | Q(brand__istartswith=query)
                | Q(summary__icontains=query),
            ).select_related("game")

            results["badge_sets"] = ChatBadgeSet.objects.filter(
                set_id__istartswith=query,
            )

            results["badges"] = ChatBadge.objects.filter(
                Q(title__istartswith=query) | Q(description__icontains=query),
            ).select_related("badge_set")
        else:
            results["organizations"] = Organization.objects.filter(
                name__icontains=query,
            )
            results["games"] = Game.objects.filter(
                Q(name__icontains=query) | Q(display_name__icontains=query),
            )

            results["campaigns"] = DropCampaign.objects.filter(
                Q(name__icontains=query) | Q(description__icontains=query),
            ).select_related("game")

            results["drops"] = TimeBasedDrop.objects.filter(
                name__icontains=query,
            ).select_related("campaign")

            results["benefits"] = DropBenefit.objects.filter(
                name__icontains=query,
            ).prefetch_related("drops__campaign")

            results["reward_campaigns"] = RewardCampaign.objects.filter(
                Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(summary__icontains=query),
            ).select_related("game")

            results["badge_sets"] = ChatBadgeSet.objects.filter(set_id__icontains=query)
            results["badges"] = ChatBadge.objects.filter(
                Q(title__icontains=query) | Q(description__icontains=query),
            ).select_related("badge_set")

    total_results_count: int = sum(len(qs) for qs in results.values())

    # TODO(TheLovinator): Make the description more informative by including counts of each result type, e.g. "Found 5 games, 3 campaigns, and 10 drops for 'rust'."   # ruff:ignore[missing-todo-link]
    if query:
        page_title: str = f"Search Results for '{query}'"[:60]
        page_description: str = f"Found {total_results_count} results for '{query}'."
    else:
        page_title = "Search"
        page_description = "Search for drops, games, channels, and organizations."

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=page_title,
        page_description=page_description,
        page_url=build_absolute_uri(reverse("core:search")),
    )
    return render(
        request,
        "core/search_results.html",
        {"query": query, "results": results, **seo_context},
    )


# MARK: /
def dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard view showing summary stats and latest campaigns.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered dashboard page.
    """
    now: datetime.datetime = timezone.now()

    active_twitch_campaigns: QuerySet[DropCampaign] = (
        DropCampaign.objects
        .filter(start_at__lte=now, end_at__gte=now)
        .select_related("game")
        .prefetch_related("game__owners")
        .prefetch_related(
            Prefetch(
                "allow_channels",
                queryset=Channel.objects.order_by("display_name"),
                to_attr="channels_ordered",
            ),
        )
        .order_by("-start_at")
    )

    twitch_campaigns_by_game: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for campaign in active_twitch_campaigns:
        game: Game = campaign.game
        game_id: str = game.twitch_id
        if game_id not in twitch_campaigns_by_game:
            twitch_campaigns_by_game[game_id] = {
                "name": game.display_name,
                "box_art": game.box_art_best_url,
                "owners": list(game.owners.all()),
                "campaigns": [],
            }
        twitch_campaigns_by_game[game_id]["campaigns"].append({
            "campaign": campaign,
            "allowed_channels": getattr(campaign, "channels_ordered", []),
        })

    active_kick_campaigns: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .filter(starts_at__lte=now, ends_at__gte=now)
        .select_related("organization", "category")
        .prefetch_related(
            Prefetch("channels", queryset=KickChannel.objects.select_related("user")),
            "rewards",
        )
        .order_by("-starts_at")
    )

    kick_campaigns_by_game: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for campaign in active_kick_campaigns:
        if campaign.category is None:
            game_key: str = "unknown"
            game_name: str = "Unknown Category"
            game_image: str = ""
            game_kick_id: int | None = None
        else:
            game_key = str(campaign.category.kick_id)
            game_name = campaign.category.name
            game_image = campaign.category.image_url
            game_kick_id = campaign.category.kick_id

        if game_key not in kick_campaigns_by_game:
            kick_campaigns_by_game[game_key] = {
                "name": game_name,
                "image": game_image,
                "kick_id": game_kick_id,
                "campaigns": [],
                "organizations": [],
            }

        if campaign.organization:
            org = campaign.organization
            if org not in kick_campaigns_by_game[game_key]["organizations"]:
                kick_campaigns_by_game[game_key]["organizations"].append(org)

        kick_campaigns_by_game[game_key]["campaigns"].append({
            "campaign": campaign,
            "channels": list(campaign.channels.all()),
            "rewards": list(campaign.rewards.all()),  # pyright: ignore[reportAttributeAccessIssue]
        })

    active_reward_campaigns: QuerySet[RewardCampaign] = (
        RewardCampaign.objects
        .filter(starts_at__lte=now, ends_at__gte=now)
        .select_related("game")
        .order_by("-starts_at")
    )

    website_schema: dict[str, str | dict[str, str | dict[str, str]]] = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "ttvdrops",
        "url": build_absolute_uri("/"),
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": build_absolute_uri(
                    "/search/?q={search_term_string}",
                ),
            },
            "query-input": "required name=search_term_string",
        },
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch/Kick Drops",
        page_description=(
            "RSS feeds, historical data, and information about Twitch and Kick drops, campaigns, rewards, and more."
        ),
        og_type="website",
        schema_data=website_schema,
    )

    return render(
        request,
        "core/dashboard.html",
        {
            "campaigns_by_game": twitch_campaigns_by_game,
            "kick_campaigns_by_game": kick_campaigns_by_game,
            "active_reward_campaigns": active_reward_campaigns,
            "now": now,
            **seo_context,
        },
    )
