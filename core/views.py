import datetime
import json
import logging
import operator
from collections import OrderedDict
from copy import copy
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models.functions import Trim
from django.db.models.query import QuerySet
from django.http import FileResponse
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import render
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone

from kick.models import KickChannel
from kick.models import KickDropCampaign
from twitch.feeds import DropCampaignAtomFeed
from twitch.feeds import DropCampaignDiscordFeed
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameAtomFeed
from twitch.feeds import GameCampaignAtomFeed
from twitch.feeds import GameCampaignDiscordFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameDiscordFeed
from twitch.feeds import GameFeed
from twitch.feeds import OrganizationAtomFeed
from twitch.feeds import OrganizationDiscordFeed
from twitch.feeds import OrganizationRSSFeed
from twitch.feeds import RewardCampaignAtomFeed
from twitch.feeds import RewardCampaignDiscordFeed
from twitch.feeds import RewardCampaignFeed
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
    from collections.abc import Callable
    from os import stat_result
    from pathlib import Path

    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django.http.request import QueryDict


logger: logging.Logger = logging.getLogger("ttvdrops.views")


MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05
DEFAULT_SITE_DESCRIPTION = "Archive of Twitch drops, campaigns, rewards, and more."


def _build_seo_context(  # noqa: PLR0913, PLR0917
    page_title: str = "ttvdrops",
    page_description: str | None = None,
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
    # TODO(TheLovinator): Instead of having so many parameters,  # noqa: TD003
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


# MARK: /sitemap.xml
def sitemap_view(request: HttpRequest) -> HttpResponse:  # noqa: PLR0915
    """Generate a dynamic XML sitemap for search engines.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: XML sitemap.
    """
    base_url: str = f"{request.scheme}://{request.get_host()}"

    # Start building sitemap XML
    sitemap_urls: list[dict[str, str | dict[str, str]]] = []

    # Static pages
    sitemap_urls.extend([
        {"url": f"{base_url}/", "priority": "1.0", "changefreq": "daily"},
        {"url": f"{base_url}/campaigns/", "priority": "0.9", "changefreq": "daily"},
        {
            "url": f"{base_url}/reward-campaigns/",
            "priority": "0.9",
            "changefreq": "daily",
        },
        {"url": f"{base_url}/games/", "priority": "0.9", "changefreq": "weekly"},
        {
            "url": f"{base_url}/organizations/",
            "priority": "0.8",
            "changefreq": "weekly",
        },
        {"url": f"{base_url}/channels/", "priority": "0.8", "changefreq": "weekly"},
        {"url": f"{base_url}/badges/", "priority": "0.7", "changefreq": "monthly"},
        {"url": f"{base_url}/emotes/", "priority": "0.7", "changefreq": "monthly"},
        {"url": f"{base_url}/search/", "priority": "0.6", "changefreq": "monthly"},
    ])

    # Dynamic detail pages - Games
    games: QuerySet[Game] = Game.objects.all()
    for game in games:
        entry: dict[str, str | dict[str, str]] = {
            "url": f"{base_url}{reverse('twitch:game_detail', args=[game.twitch_id])}",
            "priority": "0.8",
            "changefreq": "weekly",
        }
        if game.updated_at:
            entry["lastmod"] = game.updated_at.isoformat()
        sitemap_urls.append(entry)

    # Dynamic detail pages - Campaigns
    campaigns: QuerySet[DropCampaign] = DropCampaign.objects.all()
    for campaign in campaigns:
        resource_url: str = reverse("twitch:campaign_detail", args=[campaign.twitch_id])
        full_url: str = f"{base_url}{resource_url}"
        entry: dict[str, str | dict[str, str]] = {
            "url": full_url,
            "priority": "0.7",
            "changefreq": "weekly",
        }
        if campaign.updated_at:
            entry["lastmod"] = campaign.updated_at.isoformat()
        sitemap_urls.append(entry)

    # Dynamic detail pages - Organizations
    orgs: QuerySet[Organization] = Organization.objects.all()
    for org in orgs:
        resource_url = reverse("twitch:organization_detail", args=[org.twitch_id])
        full_url: str = f"{base_url}{resource_url}"
        entry: dict[str, str | dict[str, str]] = {
            "url": full_url,
            "priority": "0.7",
            "changefreq": "weekly",
        }
        if org.updated_at:
            entry["lastmod"] = org.updated_at.isoformat()
        sitemap_urls.append(entry)

    # Dynamic detail pages - Channels
    channels: QuerySet[Channel] = Channel.objects.all()
    for channel in channels:
        resource_url = reverse("twitch:channel_detail", args=[channel.twitch_id])
        full_url: str = f"{base_url}{resource_url}"
        entry: dict[str, str | dict[str, str]] = {
            "url": full_url,
            "priority": "0.6",
            "changefreq": "weekly",
        }
        if channel.updated_at:
            entry["lastmod"] = channel.updated_at.isoformat()
        sitemap_urls.append(entry)

    # Dynamic detail pages - Badges
    badge_sets: QuerySet[ChatBadgeSet] = ChatBadgeSet.objects.all()
    for badge_set in badge_sets:
        resource_url = reverse("twitch:badge_set_detail", args=[badge_set.set_id])
        full_url: str = f"{base_url}{resource_url}"
        sitemap_urls.append({
            "url": full_url,
            "priority": "0.5",
            "changefreq": "monthly",
        })

    # Dynamic detail pages - Reward Campaigns
    reward_campaigns: QuerySet[RewardCampaign] = RewardCampaign.objects.all()
    for reward_campaign in reward_campaigns:
        resource_url = reverse(
            "twitch:reward_campaign_detail",
            args=[
                reward_campaign.twitch_id,
            ],
        )
        full_url: str = f"{base_url}{resource_url}"
        entry: dict[str, str | dict[str, str]] = {
            "url": full_url,
            "priority": "0.6",
            "changefreq": "weekly",
        }
        if reward_campaign.updated_at:
            entry["lastmod"] = reward_campaign.updated_at.isoformat()
        sitemap_urls.append(entry)

    # Build XML
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    for url_entry in sitemap_urls:
        xml_content += "  <url>\n"
        xml_content += f"    <loc>{url_entry['url']}</loc>\n"
        if url_entry.get("lastmod"):
            xml_content += f"    <lastmod>{url_entry['lastmod']}</lastmod>\n"
        xml_content += (
            f"    <changefreq>{url_entry.get('changefreq', 'monthly')}</changefreq>\n"
        )
        xml_content += f"    <priority>{url_entry.get('priority', '0.5')}</priority>\n"
        xml_content += "  </url>\n"

    xml_content += "</urlset>"

    return HttpResponse(xml_content, content_type="application/xml")


# MARK: /docs/rss/
def docs_rss_view(request: HttpRequest) -> HttpResponse:
    """View for /docs/rss that lists all available RSS feeds.

    Args:
        request: The HTTP request object.

    Returns:
        Rendered HTML response with list of RSS feeds.
    """

    def absolute(path: str) -> str:
        try:
            return request.build_absolute_uri(path)
        except Exception:
            logger.exception("Failed to build absolute URL for %s", path)
        return path

    def _pretty_example(xml_str: str, max_items: int = 1) -> str:
        try:
            trimmed: str = xml_str.strip()
            first_item: int = trimmed.find("<item")
            if first_item != -1 and max_items == 1:
                second_item: int = trimmed.find("<item", first_item + 5)
                if second_item != -1:
                    end_channel: int = trimmed.find("</channel>", second_item)
                    if end_channel != -1:
                        trimmed = trimmed[:second_item] + trimmed[end_channel:]
            formatted: str = trimmed.replace("><", ">\n<")
            return "\n".join(line for line in formatted.splitlines() if line.strip())
        except Exception:
            logger.exception("Failed to pretty-print RSS example")
            return xml_str

    def render_feed(feed_view: Callable[..., HttpResponse], *args: object) -> str:
        try:
            limited_request: HttpRequest = copy(request)
            # Add limit=1 to GET parameters
            get_data: QueryDict = request.GET.copy()
            get_data["limit"] = "1"
            limited_request.GET = get_data

            response: HttpResponse = feed_view(limited_request, *args)
            return _pretty_example(response.content.decode("utf-8"))
        except Exception:
            logger.exception(
                "Failed to render %s for RSS docs",
                feed_view.__class__.__name__,
            )
        return ""

    show_atom: bool = bool(request.GET.get("show_atom"))

    feeds: list[dict[str, str]] = [
        {
            "title": "All Organizations",
            "description": "Latest organizations added to TTVDrops",
            "url": absolute(reverse("core:organization_feed")),
            "atom_url": absolute(reverse("core:organization_feed_atom")),
            "discord_url": absolute(reverse("core:organization_feed_discord")),
            "example_xml": render_feed(OrganizationRSSFeed()),
            "example_xml_atom": render_feed(OrganizationAtomFeed())
            if show_atom
            else "",
            "example_xml_discord": render_feed(OrganizationDiscordFeed())
            if show_atom
            else "",
        },
        {
            "title": "All Games",
            "description": "Latest games added to TTVDrops",
            "url": absolute(reverse("core:game_feed")),
            "atom_url": absolute(reverse("core:game_feed_atom")),
            "discord_url": absolute(reverse("core:game_feed_discord")),
            "example_xml": render_feed(GameFeed()),
            "example_xml_atom": render_feed(GameAtomFeed()) if show_atom else "",
            "example_xml_discord": render_feed(GameDiscordFeed()) if show_atom else "",
        },
        {
            "title": "All Drop Campaigns",
            "description": "Latest drop campaigns across all games",
            "url": absolute(reverse("core:campaign_feed")),
            "atom_url": absolute(reverse("core:campaign_feed_atom")),
            "discord_url": absolute(reverse("core:campaign_feed_discord")),
            "example_xml": render_feed(DropCampaignFeed()),
            "example_xml_atom": render_feed(DropCampaignAtomFeed())
            if show_atom
            else "",
            "example_xml_discord": render_feed(DropCampaignDiscordFeed())
            if show_atom
            else "",
        },
        {
            "title": "All Reward Campaigns",
            "description": "Latest reward campaigns (Quest rewards) on Twitch",
            "url": absolute(reverse("core:reward_campaign_feed")),
            "atom_url": absolute(reverse("core:reward_campaign_feed_atom")),
            "discord_url": absolute(reverse("core:reward_campaign_feed_discord")),
            "example_xml": render_feed(RewardCampaignFeed()),
            "example_xml_atom": render_feed(RewardCampaignAtomFeed())
            if show_atom
            else "",
            "example_xml_discord": render_feed(RewardCampaignDiscordFeed())
            if show_atom
            else "",
        },
    ]

    sample_game: Game | None = Game.objects.order_by("-added_at").first()
    sample_org: Organization | None = Organization.objects.order_by("-added_at").first()
    if sample_org is None and sample_game is not None:
        sample_org = sample_game.owners.order_by("-pk").first()

    filtered_feeds: list[dict[str, str | bool]] = [
        {
            "title": "Campaigns for a Single Game",
            "description": "Latest drop campaigns for one game.",
            "url": (
                absolute(
                    reverse("core:game_campaign_feed", args=[sample_game.twitch_id]),
                )
                if sample_game
                else absolute("/rss/games/<game_id>/campaigns/")
            ),
            "atom_url": (
                absolute(
                    reverse(
                        "core:game_campaign_feed_atom",
                        args=[sample_game.twitch_id],
                    ),
                )
                if sample_game
                else absolute("/atom/games/<game_id>/campaigns/")
            ),
            "discord_url": (
                absolute(
                    reverse(
                        "core:game_campaign_feed_discord",
                        args=[sample_game.twitch_id],
                    ),
                )
                if sample_game
                else absolute("/discord/games/<game_id>/campaigns/")
            ),
            "has_sample": bool(sample_game),
            "example_xml": render_feed(GameCampaignFeed(), sample_game.twitch_id)
            if sample_game
            else "",
            "example_xml_atom": (
                render_feed(GameCampaignAtomFeed(), sample_game.twitch_id)
                if sample_game and show_atom
                else ""
            ),
            "example_xml_discord": (
                render_feed(GameCampaignDiscordFeed(), sample_game.twitch_id)
                if sample_game and show_atom
                else ""
            ),
        },
    ]

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch RSS Feeds",
        page_description="RSS feeds for Twitch drops.",
    )
    return render(
        request,
        "twitch/docs_rss.html",
        {
            "feeds": feeds,
            "filtered_feeds": filtered_feeds,
            "sample_game": sample_game,
            "sample_org": sample_org,
            **seo_context,
        },
    )


# MARK: /debug/
def debug_view(request: HttpRequest) -> HttpResponse:
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
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Debug",
        page_description="Debug view showing potentially broken or inconsistent data.",
        robots_directive="noindex, nofollow",
    )
    context.update(seo_context)

    return render(request, "twitch/debug.html", context)


# MARK: /datasets/
def dataset_backups_view(request: HttpRequest) -> HttpResponse:
    """View to list database backup datasets on disk.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered dataset backups page.
    """
    # TODO(TheLovinator): Instead of only using sql we should also support other formats like parquet, csv, or json.  # noqa: TD003
    # TODO(TheLovinator): Upload to s3 instead.  # noqa: TD003
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
            "contentUrl": request.build_absolute_uri(
                reverse("core:dataset_backup_download", args=[download_path]),
            ),
            "encodingFormat": "application/zstd",
        })

    dataset_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Historical archive of Twitch and Kick drop data",
        "identifier": request.build_absolute_uri(reverse("core:dataset_backups")),
        "temporalCoverage": "2024-07-17/..",
        "url": request.build_absolute_uri(reverse("core:dataset_backups")),
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
            "url": request.build_absolute_uri(reverse("core:dataset_backups")),
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
    return render(request, "twitch/dataset_backups.html", context)


def dataset_backup_download_view(
    request: HttpRequest,  # noqa: ARG001
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
    # TODO(TheLovinator): Use s3 instead of local disk.  # noqa: TD003

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

    # TODO(TheLovinator): Make the description more informative by including counts of each result type, e.g. "Found 5 games, 3 campaigns, and 10 drops for 'rust'."   # noqa: TD003
    if query:
        page_title: str = f"Search Results for '{query}'"[:60]
        page_description: str = f"Found {total_results_count} results for '{query}'."
    else:
        page_title = "Search"
        page_description = "Search for drops, games, channels, and organizations."

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=page_title,
        page_description=page_description,
    )
    return render(
        request,
        "twitch/search_results.html",
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
            }

        kick_campaigns_by_game[game_key]["campaigns"].append({
            "campaign": campaign,
            "channels": list(campaign.channels.all()),
            "rewards": list(campaign.rewards.all()),
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
        "url": request.build_absolute_uri("/"),
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": request.build_absolute_uri(
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
