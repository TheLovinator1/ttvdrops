import csv
import datetime
import json
import logging
import operator
from collections import OrderedDict
from collections import defaultdict
from copy import copy
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from django.conf import settings
from django.core.paginator import EmptyPage
from django.core.paginator import Page
from django.core.paginator import PageNotAnInteger
from django.core.paginator import Paginator
from django.core.serializers import serialize
from django.db import connection
from django.db.models import Case
from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import When
from django.db.models.functions import Trim
from django.db.models.query import QuerySet
from django.http import FileResponse
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import render
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView
from django.views.generic import ListView
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers.data import JsonLexer

from twitch.feeds import DropCampaignAtomFeed
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameAtomFeed
from twitch.feeds import GameCampaignAtomFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameFeed
from twitch.feeds import OrganizationAtomFeed
from twitch.feeds import OrganizationRSSFeed
from twitch.feeds import RewardCampaignAtomFeed
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

    from debug_toolbar.utils import QueryDict
    from django.db.models import QuerySet
    from django.http import HttpRequest

logger: logging.Logger = logging.getLogger("ttvdrops.views")

MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05
DEFAULT_SITE_DESCRIPTION = "Archive of Twitch drops, campaigns, rewards, and more."


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


def _build_breadcrumb_schema(items: list[dict[str, str | int]]) -> dict[str, Any]:
    """Build a BreadcrumbList schema for structured data.

    Args:
        items: List of dicts with "name" and "url" keys.
               First item should be homepage.

    Returns:
        BreadcrumbList schema dict.
    """
    # TODO(TheLovinator): Replace dict with something more structured, like a dataclass or namedtuple, for better type safety and readability.  # noqa: TD003

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
            "url": request.build_absolute_uri(prev_url),
        })

    if page_obj.has_next():
        next_url: str = f"{base_url}?page={page_obj.next_page_number()}"
        if "?" in base_url:
            # Preserve existing query params
            next_url = f"{base_url}&page={page_obj.next_page_number()}"
        pagination_links.append({
            "rel": "next",
            "url": request.build_absolute_uri(next_url),
        })

    return pagination_links or None


def emote_gallery_view(request: HttpRequest) -> HttpResponse:
    """View to display all emote images.

    Emotes are associated with DropBenefits of type "EMOTE".

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered emote gallery page.
    """
    emote_benefits: QuerySet[DropBenefit, DropBenefit] = (
        DropBenefit.objects
        .filter(distribution_type="EMOTE")
        .select_related()
        .prefetch_related(
            Prefetch(
                "drops",
                queryset=TimeBasedDrop.objects.select_related("campaign"),
                to_attr="_emote_drops",
            ),
        )
    )

    emotes: list[dict[str, str | DropCampaign]] = []
    for benefit in emote_benefits:
        # Find the first drop with a campaign for this benefit
        drop: TimeBasedDrop | None = next(
            (d for d in getattr(benefit, "_emote_drops", []) if d.campaign),
            None,
        )
        if drop and drop.campaign:
            emotes.append({
                "image_url": benefit.image_best_url,
                "campaign": drop.campaign,
            })

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Emotes",
        page_description="List of all Twitch emotes available as rewards.",
    )
    context: dict[str, Any] = {"emotes": emotes, **seo_context}
    return render(request, "twitch/emote_gallery.html", context)


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


# MARK: /organizations/
def org_list_view(request: HttpRequest) -> HttpResponse:
    """Function-based view for organization list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered organization list page.
    """
    orgs: QuerySet[Organization] = Organization.objects.all().order_by("name")

    # Serialize all organizations
    serialized_orgs: str = serialize(
        "json",
        orgs,
        fields=("twitch_id", "name", "added_at", "updated_at"),
    )
    orgs_data: list[dict] = json.loads(serialized_orgs)

    # CollectionPage schema for organizations list
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Twitch Organizations",
        "description": "List of Twitch organizations.",
        "url": request.build_absolute_uri("/organizations/"),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Organizations",
        page_description="List of Twitch organizations.",
        schema_data=collection_schema,
    )
    context: dict[str, Any] = {
        "orgs": orgs,
        "orgs_data": format_and_color_json(orgs_data),
        **seo_context,
    }

    return render(request, "twitch/org_list.html", context)


# MARK: /organizations/<twitch_id>/
def organization_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # noqa: PLR0914
    """Function-based view for organization detail.

    Args:
        request: The HTTP request.
        twitch_id: The Twitch ID of the organization.

    Returns:
        HttpResponse: The rendered organization detail page.

    Raises:
        Http404: If the organization is not found.
    """
    try:
        organization: Organization = Organization.objects.get(twitch_id=twitch_id)
    except Organization.DoesNotExist as exc:
        msg = "No organization found matching the query"
        raise Http404(msg) from exc

    games: QuerySet[Game] = organization.games.all()  # pyright: ignore[reportAttributeAccessIssue]

    serialized_org: str = serialize(
        "json",
        [organization],
        fields=("twitch_id", "name", "added_at", "updated_at"),
    )
    org_data: list[dict] = json.loads(serialized_org)

    if games.exists():
        serialized_games: str = serialize(
            "json",
            games,
            fields=(
                "twitch_id",
                "slug",
                "name",
                "display_name",
                "box_art",
                "added_at",
                "updated_at",
            ),
        )
        games_data: list[dict] = json.loads(serialized_games)
        org_data[0]["fields"]["games"] = games_data

    org_name: str = organization.name or organization.twitch_id
    games_count: int = games.count()
    s: Literal["", "s"] = "" if games_count == 1 else "s"
    org_description: str = f"{org_name} has {games_count} game{s}."

    url: str = request.build_absolute_uri(
        reverse("twitch:organization_detail", args=[organization.twitch_id]),
    )
    org_schema: dict[str, str | dict[str, str]] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": org_name,
        "url": url,
        "description": org_description,
    }

    # Breadcrumb schema
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": request.build_absolute_uri("/")},
        {"name": "Organizations", "url": request.build_absolute_uri("/organizations/")},
        {
            "name": org_name,
            "url": request.build_absolute_uri(
                reverse("twitch:organization_detail", args=[organization.twitch_id]),
            ),
        },
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=org_name,
        page_description=org_description,
        schema_data=org_schema,
        breadcrumb_schema=breadcrumb_schema,
        modified_date=organization.updated_at.isoformat(),
    )
    context: dict[str, Any] = {
        "organization": organization,
        "games": games,
        "org_data": format_and_color_json(org_data[0]),
        **seo_context,
    }

    return render(request, "twitch/organization_detail.html", context)


# MARK: /campaigns/
def drop_campaign_list_view(request: HttpRequest) -> HttpResponse:  # noqa: PLR0914, PLR0915
    """Function-based view for drop campaigns list.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered campaign list page.
    """
    game_filter: str | None = request.GET.get("game")
    status_filter: str | None = request.GET.get("status")
    per_page: int = 100
    queryset: QuerySet[DropCampaign] = DropCampaign.objects.all()

    if game_filter:
        queryset = queryset.filter(game__twitch_id=game_filter)

    queryset = queryset.prefetch_related("game__owners").order_by("-start_at")

    # Optionally filter by status (active, upcoming, expired)
    now: datetime.datetime = timezone.now()
    if status_filter == "active":
        queryset = queryset.filter(start_at__lte=now, end_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(start_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(end_at__lt=now)

    paginator: Paginator[DropCampaign] = Paginator(queryset, per_page)
    page: str | Literal[1] = request.GET.get("page") or 1
    try:
        campaigns: Page[DropCampaign] = paginator.page(page)
    except PageNotAnInteger:
        campaigns = paginator.page(1)
    except EmptyPage:
        campaigns = paginator.page(paginator.num_pages)

    title = "Twitch Drop Campaigns"
    if status_filter:
        title += f" ({status_filter.capitalize()})"
    if game_filter:
        try:
            game: Game = Game.objects.get(twitch_id=game_filter)
            title += f" - {game.display_name}"
        except Game.DoesNotExist:
            pass

    description = "Browse Twitch drop campaigns"
    if status_filter == "active":
        description = "Browse active Twitch drop campaigns."
    elif status_filter == "upcoming":
        description = "View upcoming Twitch drop campaigns starting soon."
    elif status_filter == "expired":
        description = "Browse expired Twitch drop campaigns."

    # Build base URL for pagination
    base_url = "/campaigns/"
    if status_filter:
        base_url += f"?status={status_filter}"
        if game_filter:
            base_url += f"&game={game_filter}"
    elif game_filter:
        base_url += f"?game={game_filter}"

    pagination_info: list[dict[str, str]] | None = _build_pagination_info(
        request,
        campaigns,
        base_url,
    )

    # CollectionPage schema for campaign list
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": request.build_absolute_uri(base_url),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        pagination_info=pagination_info,
        schema_data=collection_schema,
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
        "selected_status": status_filter or "",
        **seo_context,
    }
    return render(request, "twitch/campaign_list.html", context)


def format_and_color_json(data: dict[str, Any] | list[dict] | str) -> str:
    """Format and color a JSON string for HTML display.

    Args:
        data: Either a dictionary, list of dictionaries, or a JSON string to format.

    Returns:
        str: The formatted code with HTML styles.
    """
    if isinstance(data, (dict, list)):
        formatted_code: str = json.dumps(data, indent=4)
    else:
        formatted_code = data
    return highlight(formatted_code, JsonLexer(), HtmlFormatter())


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

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Dataset",
        page_description="Database backups and datasets available for download.",
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


def _enhance_drops_with_context(
    drops: QuerySet[TimeBasedDrop],
    now: datetime.datetime,
) -> list[dict[str, Any]]:
    """Helper to enhance drops with countdown and context.

    Args:
        drops: QuerySet of TimeBasedDrop objects.
        now: Current datetime.

    Returns:
        List of dicts with drop and additional context for display.
    """
    enhanced: list[dict[str, Any]] = []
    for drop in drops:
        if drop.end_at and drop.end_at > now:
            time_diff: datetime.timedelta = drop.end_at - now
            days: int = time_diff.days
            hours, remainder = divmod(time_diff.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if days > 0:
                countdown_text: str = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                countdown_text = f"{hours}h {minutes}m"
            elif minutes > 0:
                countdown_text = f"{minutes}m {seconds}s"
            else:
                countdown_text = f"{seconds}s"
        elif drop.start_at and drop.start_at > now:
            countdown_text = "Not started"
        else:
            countdown_text = "Expired"
        enhanced.append({
            "drop": drop,
            "local_start": drop.start_at,
            "local_end": drop.end_at,
            "timezone_name": "UTC",
            "countdown_text": countdown_text,
        })
    return enhanced


# MARK: /campaigns/<twitch_id>/
def drop_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # noqa: PLR0914, PLR0915
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
        campaign: DropCampaign = DropCampaign.objects.prefetch_related(
            "game__owners",
            Prefetch(
                "allow_channels",
                queryset=Channel.objects.order_by("display_name"),
                to_attr="channels_ordered",
            ),
        ).get(twitch_id=twitch_id)
    except DropCampaign.DoesNotExist as exc:
        msg = "No campaign found matching the query"
        raise Http404(msg) from exc

    drops: QuerySet[TimeBasedDrop] = (
        TimeBasedDrop.objects
        .filter(campaign=campaign)
        .select_related("campaign")
        .prefetch_related("benefits")
        .order_by("required_minutes_watched")
    )

    serialized_campaign: str = serialize(
        "json",
        [campaign],
        fields=(
            "twitch_id",
            "name",
            "description",
            "details_url",
            "account_link_url",
            "image_url",
            "start_at",
            "end_at",
            "allow_is_enabled",
            "operation_names",
            "game",
            "created_at",
            "updated_at",
        ),
    )
    campaign_data: list[dict[str, Any]] = json.loads(serialized_campaign)

    if drops.exists():
        badge_benefit_names: set[str] = {
            benefit.name
            for drop in drops
            for benefit in drop.benefits.all()
            if benefit.distribution_type == "BADGE" and benefit.name
        }
        badge_descriptions_by_title: dict[str, str] = dict(
            ChatBadge.objects.filter(title__in=badge_benefit_names).values_list(
                "title",
                "description",
            ),
        )

        serialized_drops = serialize(
            "json",
            drops,
            fields=(
                "twitch_id",
                "name",
                "required_minutes_watched",
                "required_subs",
                "start_at",
                "end_at",
                "added_at",
                "updated_at",
            ),
        )
        drops_data: list[dict[str, Any]] = json.loads(serialized_drops)

        for i, drop in enumerate(drops):
            drop_benefits: list[DropBenefit] = list(drop.benefits.all())
            if drop_benefits:
                serialized_benefits: str = serialize(
                    "json",
                    drop_benefits,
                    fields=(
                        "twitch_id",
                        "name",
                        "image_asset_url",
                        "added_at",
                        "updated_at",
                        "created_at",
                        "entitlement_limit",
                        "is_ios_available",
                        "distribution_type",
                    ),
                )
                benefits_data: list[dict[str, Any]] = json.loads(serialized_benefits)

                for benefit_data in benefits_data:
                    fields: dict[str, Any] = benefit_data.get("fields", {})
                    if fields.get("distribution_type") != "BADGE":
                        continue

                    # DropBenefit doesn't have a description field; fetch it from ChatBadge when possible.
                    if fields.get("description"):
                        continue

                    badge_description: str | None = badge_descriptions_by_title.get(
                        fields.get("name", ""),
                    )
                    if badge_description:
                        fields["description"] = badge_description

                drops_data[i]["fields"]["benefits"] = benefits_data

        campaign_data[0]["fields"]["drops"] = drops_data

    now: datetime.datetime = timezone.now()
    enhanced_drops: list[dict[str, Any]] = _enhance_drops_with_context(drops, now)
    # Attach awarded_badge to each drop in enhanced_drops
    for enhanced_drop in enhanced_drops:
        drop = enhanced_drop["drop"]
        awarded_badge = None
        for benefit in drop.benefits.all():
            if benefit.distribution_type == "BADGE":
                awarded_badge: ChatBadge | None = ChatBadge.objects.filter(
                    title=benefit.name,
                ).first()
                break
        enhanced_drop["awarded_badge"] = awarded_badge

    context: dict[str, Any] = {
        "campaign": campaign,
        "now": now,
        "drops": enhanced_drops,
        "campaign_data": format_and_color_json(campaign_data[0]),
        "owners": list(campaign.game.owners.all()),
        "allowed_channels": getattr(campaign, "channels_ordered", []),
    }

    campaign_name: str = campaign.name or campaign.clean_name or campaign.twitch_id
    campaign_description: str = (
        _truncate_description(campaign.description)
        if campaign.description
        else f"Twitch drop campaign: {campaign_name}"
    )
    campaign_image: str | None = campaign.image_best_url
    campaign_image_width: int | None = (
        campaign.image_width if campaign.image_file else None
    )
    campaign_image_height: int | None = (
        campaign.image_height if campaign.image_file else None
    )

    url: str = request.build_absolute_uri(
        reverse("twitch:campaign_detail", args=[campaign.twitch_id]),
    )

    # TODO(TheLovinator): If the campaign has specific allowed channels, we could list those as potential locations instead of just linking to Twitch homepage.  # noqa: TD003
    campaign_schema: dict[str, str | dict[str, str]] = {
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
        campaign_schema["startDate"] = campaign.start_at.isoformat()
    if campaign.end_at:
        campaign_schema["endDate"] = campaign.end_at.isoformat()
    if campaign_image:
        campaign_schema["image"] = campaign_image
    if campaign.game and campaign.game.owners.exists():
        owner: Organization | None = campaign.game.owners.first()
        if owner:
            campaign_schema["organizer"] = {
                "@type": "Organization",
                "name": owner.name or owner.twitch_id,
            }

    # Breadcrumb schema for navigation
    # TODO(TheLovinator): We should have a game.get_display_name() method that encapsulates the logic of choosing between display_name, name, and twitch_id.  # noqa: TD003
    game_name: str = (
        campaign.game.display_name or campaign.game.name or campaign.game.twitch_id
    )
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": request.build_absolute_uri("/")},
        {"name": "Games", "url": request.build_absolute_uri("/games/")},
        {
            "name": game_name,
            "url": request.build_absolute_uri(
                reverse("twitch:game_detail", args=[campaign.game.twitch_id]),
            ),
        },
        {
            "name": campaign_name,
            "url": request.build_absolute_uri(
                reverse("twitch:campaign_detail", args=[campaign.twitch_id]),
            ),
        },
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=campaign_name,
        page_description=campaign_description,
        page_image=campaign_image,
        page_image_width=campaign_image_width,
        page_image_height=campaign_image_height,
        schema_data=campaign_schema,
        breadcrumb_schema=breadcrumb_schema,
        modified_date=campaign.updated_at.isoformat() if campaign.updated_at else None,
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
        now: datetime.datetime = timezone.now()
        return (
            super()
            .get_queryset()
            .prefetch_related("owners")
            .annotate(
                campaign_count=Count("drop_campaigns", distinct=True),
                active_count=Count(
                    "drop_campaigns",
                    filter=Q(
                        drop_campaigns__start_at__lte=now,
                        drop_campaigns__end_at__gte=now,
                    ),
                    distinct=True,
                ),
            )
            .order_by("display_name")
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
        now: datetime.datetime = timezone.now()

        games_with_campaigns: QuerySet[Game] = (
            Game.objects
            .filter(drop_campaigns__isnull=False)
            .prefetch_related("owners")
            .annotate(
                campaign_count=Count("drop_campaigns", distinct=True),
                active_count=Count(
                    "drop_campaigns",
                    filter=Q(
                        drop_campaigns__start_at__lte=now,
                        drop_campaigns__end_at__gte=now,
                    ),
                    distinct=True,
                ),
            )
            .order_by("display_name")
        )

        games_by_org: defaultdict[Organization, list[dict[str, Game]]] = defaultdict(
            list,
        )
        for game in games_with_campaigns:
            for org in game.owners.all():
                games_by_org[org].append({"game": game})

        context["games_by_org"] = OrderedDict(
            sorted(games_by_org.items(), key=lambda item: item[0].name),
        )

        # CollectionPage schema for games list
        collection_schema: dict[str, str] = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Twitch Games",
            "description": "Twitch games that had or have Twitch drops.",
            "url": self.request.build_absolute_uri("/games/"),
        }

        seo_context: dict[str, Any] = _build_seo_context(
            page_title="Twitch Games",
            page_description="Twitch games that had or have Twitch drops.",
            schema_data=collection_schema,
        )
        context.update(seo_context)

        return context


# MARK: /games/<twitch_id>/
class GameDetailView(DetailView):
    """Detail view for a game."""

    model = Game
    template_name = "twitch/game_detail.html"
    context_object_name = "game"
    lookup_field = "twitch_id"

    def get_object(self, queryset: QuerySet[Game] | None = None) -> Game:
        """Get the game object using twitch_id as the primary key lookup.

        Args:
            queryset: Optional queryset to use.

        Returns:
            Game: The game object.

        Raises:
            Http404: If the game is not found.
        """
        if queryset is None:
            queryset = self.get_queryset()

        # Use twitch_id as the lookup field since it's the primary key
        twitch_id: str | None = self.kwargs.get("twitch_id")
        try:
            game: Game = queryset.get(twitch_id=twitch_id)
        except Game.DoesNotExist as exc:
            msg = "No game found matching the query"
            raise Http404(msg) from exc

        return game

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:  # noqa: PLR0914
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
        all_campaigns: QuerySet[DropCampaign] = (
            DropCampaign.objects
            .filter(game=game)
            .select_related("game")
            .prefetch_related(
                Prefetch(
                    "time_based_drops",
                    queryset=TimeBasedDrop.objects.prefetch_related(
                        Prefetch(
                            "benefits",
                            queryset=DropBenefit.objects.order_by("name"),
                        ),
                    ),
                ),
            )
            .order_by("-end_at")
        )

        campaigns_list: list[DropCampaign] = list(all_campaigns)

        # For each drop, find awarded badge (distribution_type BADGE)
        drop_awarded_badges: dict[str, ChatBadge] = {}
        benefit_badge_titles: set[str] = set()
        for campaign in campaigns_list:
            for drop in campaign.time_based_drops.all():  # pyright: ignore[reportAttributeAccessIssue]
                for benefit in drop.benefits.all():
                    if benefit.distribution_type == "BADGE" and benefit.name:
                        benefit_badge_titles.add(benefit.name)

        # Bulk-load all matching ChatBadge instances to avoid N+1 queries
        badges_by_title: dict[str, ChatBadge] = {
            badge.title: badge
            for badge in ChatBadge.objects.filter(title__in=benefit_badge_titles)
        }

        for campaign in campaigns_list:
            for drop in campaign.time_based_drops.all():  # pyright: ignore[reportAttributeAccessIssue]
                for benefit in drop.benefits.all():
                    if benefit.distribution_type == "BADGE":
                        badge: ChatBadge | None = badges_by_title.get(benefit.name)
                        if badge:
                            drop_awarded_badges[drop.twitch_id] = badge

        active_campaigns: list[DropCampaign] = [
            campaign
            for campaign in campaigns_list
            if campaign.start_at is not None
            and campaign.start_at <= now
            and campaign.end_at is not None
            and campaign.end_at >= now
        ]
        active_campaigns.sort(
            key=lambda c: (
                c.end_at
                if c.end_at is not None
                else datetime.datetime.max.replace(tzinfo=datetime.UTC)
            ),
        )

        upcoming_campaigns: list[DropCampaign] = [
            campaign
            for campaign in campaigns_list
            if campaign.start_at is not None and campaign.start_at > now
        ]

        upcoming_campaigns.sort(
            key=lambda c: (
                c.start_at
                if c.start_at is not None
                else datetime.datetime.max.replace(tzinfo=datetime.UTC)
            ),
        )

        expired_campaigns: list[DropCampaign] = [
            campaign
            for campaign in campaigns_list
            if campaign.end_at is not None and campaign.end_at < now
        ]

        serialized_game: str = serialize(
            "json",
            [game],
            fields=(
                "twitch_id",
                "slug",
                "name",
                "display_name",
                "box_art",
                "owners",
                "added_at",
                "updated_at",
            ),
        )
        game_data: list[dict[str, Any]] = json.loads(serialized_game)

        if campaigns_list:
            serialized_campaigns = serialize(
                "json",
                campaigns_list,
                fields=(
                    "twitch_id",
                    "name",
                    "description",
                    "details_url",
                    "account_link_url",
                    "image_url",
                    "start_at",
                    "end_at",
                    "allow_is_enabled",
                    "game",
                    "operation_names",
                    "added_at",
                    "updated_at",
                ),
            )
            campaigns_data: list[dict[str, Any]] = json.loads(serialized_campaigns)
            game_data[0]["fields"]["campaigns"] = campaigns_data

        owners: list[Organization] = list(game.owners.all())

        game_name: str = game.display_name or game.name or game.twitch_id
        game_description: str = f"Twitch drop campaigns for {game_name}."
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
            "url": self.request.build_absolute_uri(
                reverse("twitch:game_detail", args=[game.twitch_id]),
            ),
        }
        if game.box_art_best_url:
            game_schema["image"] = game.box_art_best_url
        if owners:
            game_schema["publisher"] = {
                "@type": "Organization",
                "name": owners[0].name or owners[0].twitch_id,
            }

        # Breadcrumb schema
        breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
            {"name": "Home", "url": self.request.build_absolute_uri("/")},
            {"name": "Games", "url": self.request.build_absolute_uri("/games/")},
            {
                "name": game_name,
                "url": self.request.build_absolute_uri(
                    reverse("twitch:game_detail", args=[game.twitch_id]),
                ),
            },
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title=game_name,
            page_description=game_description,
            page_image=game_image,
            page_image_width=game_image_width,
            page_image_height=game_image_height,
            schema_data=game_schema,
            breadcrumb_schema=breadcrumb_schema,
            modified_date=game.updated_at.isoformat() if game.updated_at else None,
        )
        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "owner": owners[0] if owners else None,
            "owners": owners,
            "drop_awarded_badges": drop_awarded_badges,
            "now": now,
            "game_data": format_and_color_json(game_data[0]),
            **seo_context,
        })

        return context


# MARK: /
def dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard view showing active campaigns and progress.

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered dashboard template.
    """
    now: datetime.datetime = timezone.now()
    active_campaigns: QuerySet[DropCampaign] = (
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

    # Preserve insertion order (newest campaigns first).
    # Group by game so games with multiple owners don't render duplicate campaign cards.
    campaigns_by_game: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for campaign in active_campaigns:
        game: Game = campaign.game
        game_id: str = game.twitch_id

        if game_id not in campaigns_by_game:
            campaigns_by_game[game_id] = {
                "name": game.display_name,
                "box_art": game.box_art_best_url,
                "owners": list(game.owners.all()),
                "campaigns": [],
            }

        campaigns_by_game[game_id]["campaigns"].append({
            "campaign": campaign,
            "allowed_channels": getattr(campaign, "channels_ordered", []),
        })

    # Get active reward campaigns (Quest rewards)
    active_reward_campaigns: QuerySet[RewardCampaign] = (
        RewardCampaign.objects
        .filter(starts_at__lte=now, ends_at__gte=now)
        .select_related("game")
        .order_by("-starts_at")
    )

    # WebSite schema with SearchAction for sitelinks search box
    # TODO(TheLovinator): Should this be on all pages instead of just the dashboard?  # noqa: TD003
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
        page_title="Twitch Drops",
        page_description="Overview of active Twitch drop campaigns and rewards.",
        og_type="website",
        schema_data=website_schema,
    )
    return render(
        request,
        "twitch/dashboard.html",
        {
            "active_campaigns": active_campaigns,
            "campaigns_by_game": campaigns_by_game,
            "active_reward_campaigns": active_reward_campaigns,
            "now": now,
            **seo_context,
        },
    )


# MARK: /reward-campaigns/
def reward_campaign_list_view(request: HttpRequest) -> HttpResponse:
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

    queryset = queryset.select_related("game").order_by("-starts_at")

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

    title = "Twitch Reward Campaigns"
    if status_filter:
        title += f" ({status_filter.capitalize()})"

    description = "Twitch rewards."
    if status_filter == "active":
        description = "Browse active Twitch reward campaigns."
    elif status_filter == "upcoming":
        description = "Browse upcoming Twitch reward campaigns."
    elif status_filter == "expired":
        description = "Browse expired Twitch reward campaigns."

    # Build base URL for pagination
    base_url = "/reward-campaigns/"
    if status_filter:
        base_url += f"?status={status_filter}"
        if game_filter:
            base_url += f"&game={game_filter}"
    elif game_filter:
        base_url += f"?game={game_filter}"

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
        "url": request.build_absolute_uri(base_url),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        pagination_info=pagination_info,
        schema_data=collection_schema,
    )
    context: dict[str, Any] = {
        "reward_campaigns": reward_campaigns,
        "games": Game.objects.all().order_by("display_name"),
        "status_options": ["active", "upcoming", "expired"],
        "now": now,
        "selected_game": game_filter or "",
        "selected_per_page": per_page,
        "selected_status": status_filter or "",
        **seo_context,
    }
    return render(request, "twitch/reward_campaign_list.html", context)


# MARK: /reward-campaigns/<twitch_id>/
def reward_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
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
        reward_campaign: RewardCampaign = RewardCampaign.objects.select_related(
            "game",
        ).get(twitch_id=twitch_id)
    except RewardCampaign.DoesNotExist as exc:
        msg = "No reward campaign found matching the query"
        raise Http404(msg) from exc

    serialized_campaign: str = serialize(
        "json",
        [reward_campaign],
        fields=(
            "twitch_id",
            "name",
            "brand",
            "summary",
            "instructions",
            "external_url",
            "about_url",
            "reward_value_url_param",
            "starts_at",
            "ends_at",
            "is_sitewide",
            "game",
            "added_at",
            "updated_at",
        ),
    )
    campaign_data: list[dict[str, Any]] = json.loads(serialized_campaign)

    now: datetime.datetime = timezone.now()

    campaign_name: str = reward_campaign.name or reward_campaign.twitch_id
    campaign_description: str = (
        _truncate_description(reward_campaign.summary)
        if reward_campaign.summary
        else f"{campaign_name}"
    )

    campaign_schema: dict[str, str | dict[str, str]] = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": campaign_name,
        "description": campaign_description,
        "url": request.build_absolute_uri(
            reverse("twitch:reward_campaign_detail", args=[reward_campaign.twitch_id]),
        ),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "location": {"@type": "VirtualLocation", "url": "https://www.twitch.tv"},
    }
    if reward_campaign.starts_at:
        campaign_schema["startDate"] = reward_campaign.starts_at.isoformat()
    if reward_campaign.ends_at:
        campaign_schema["endDate"] = reward_campaign.ends_at.isoformat()
    if reward_campaign.game and reward_campaign.game.owners.exists():
        owner = reward_campaign.game.owners.first()
        campaign_schema["organizer"] = {
            "@type": "Organization",
            "name": owner.name or owner.twitch_id,
        }

    # Breadcrumb schema
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": request.build_absolute_uri("/")},
        {
            "name": "Reward Campaigns",
            "url": request.build_absolute_uri("/reward-campaigns/"),
        },
        {
            "name": campaign_name,
            "url": request.build_absolute_uri(
                reverse(
                    "twitch:reward_campaign_detail",
                    args=[reward_campaign.twitch_id],
                ),
            ),
        },
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=campaign_name,
        page_description=campaign_description,
        schema_data=campaign_schema,
        breadcrumb_schema=breadcrumb_schema,
        modified_date=reward_campaign.updated_at.isoformat(),
    )
    context: dict[str, Any] = {
        "reward_campaign": reward_campaign,
        "now": now,
        "campaign_data": format_and_color_json(campaign_data[0]),
        "is_active": reward_campaign.is_active,
        **seo_context,
    }

    return render(request, "twitch/reward_campaign_detail.html", context)


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


# MARK: /games/list/
class GamesListView(GamesGridView):
    """List view for games in simple list format."""

    template_name: str = "twitch/games_list.html"


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
            limited_request.GET = get_data  # pyright: ignore[reportAttributeAccessIssue]

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
            "url": absolute(reverse("twitch:organization_feed")),
            "atom_url": absolute(reverse("twitch:organization_feed_atom")),
            "example_xml": render_feed(OrganizationRSSFeed()),
            "example_xml_atom": render_feed(OrganizationAtomFeed())
            if show_atom
            else "",
        },
        {
            "title": "All Games",
            "description": "Latest games added to TTVDrops",
            "url": absolute(reverse("twitch:game_feed")),
            "atom_url": absolute(reverse("twitch:game_feed_atom")),
            "example_xml": render_feed(GameFeed()),
            "example_xml_atom": render_feed(GameAtomFeed()) if show_atom else "",
        },
        {
            "title": "All Drop Campaigns",
            "description": "Latest drop campaigns across all games",
            "url": absolute(reverse("twitch:campaign_feed")),
            "atom_url": absolute(reverse("twitch:campaign_feed_atom")),
            "example_xml": render_feed(DropCampaignFeed()),
            "example_xml_atom": render_feed(DropCampaignAtomFeed())
            if show_atom
            else "",
        },
        {
            "title": "All Reward Campaigns",
            "description": "Latest reward campaigns (Quest rewards) on Twitch",
            "url": absolute(reverse("twitch:reward_campaign_feed")),
            "atom_url": absolute(reverse("twitch:reward_campaign_feed_atom")),
            "example_xml": render_feed(RewardCampaignFeed()),
            "example_xml_atom": render_feed(RewardCampaignAtomFeed())
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
                    reverse("twitch:game_campaign_feed", args=[sample_game.twitch_id]),
                )
                if sample_game
                else absolute("/rss/games/<game_id>/campaigns/")
            ),
            "atom_url": (
                absolute(
                    reverse(
                        "twitch:game_campaign_feed_atom",
                        args=[sample_game.twitch_id],
                    ),
                )
                if sample_game
                else absolute("/atom/games/<game_id>/campaigns/")
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
        queryset: QuerySet[Channel] = super().get_queryset()
        search_query: str | None = self.request.GET.get("search")

        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query)
                | Q(display_name__icontains=search_query),
            )

        return queryset.annotate(campaign_count=Count("allowed_campaigns")).order_by(
            "-campaign_count",
            "name",
        )

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        search_query: str = self.request.GET.get("search", "")

        # Build pagination info
        base_url = "/channels/"
        if search_query:
            base_url += f"?search={search_query}"

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
            "url": self.request.build_absolute_uri("/channels/"),
        }

        seo_context: dict[str, Any] = _build_seo_context(
            page_title="Twitch Channels",
            page_description="List of Twitch channels participating in drop campaigns.",
            pagination_info=pagination_info,
            schema_data=collection_schema,
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

        Raises:
            Http404: If the channel is not found.
        """
        if queryset is None:
            queryset = self.get_queryset()

        twitch_id: str | None = self.kwargs.get("twitch_id")
        try:
            channel: Channel = queryset.get(twitch_id=twitch_id)
        except Channel.DoesNotExist as exc:
            msg = "No channel found matching the query"
            raise Http404(msg) from exc

        return channel

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:  # noqa: PLR0914
        """Add additional context data.

        Args:
            **kwargs: Additional arguments.

        Returns:
            dict: Context data with active, upcoming, and expired campaigns.
        """
        context: dict[str, Any] = super().get_context_data(**kwargs)
        channel: Channel = self.object  # pyright: ignore[reportAssignmentType]

        now: datetime.datetime = timezone.now()
        all_campaigns: QuerySet[DropCampaign] = (
            DropCampaign.objects
            .filter(allow_channels=channel)
            .select_related("game")
            .prefetch_related(
                Prefetch(
                    "time_based_drops",
                    queryset=TimeBasedDrop.objects.prefetch_related(
                        Prefetch(
                            "benefits",
                            queryset=DropBenefit.objects.order_by("name"),
                        ),
                    ),
                ),
            )
            .order_by("-start_at")
        )

        campaigns_list: list[DropCampaign] = list(all_campaigns)

        active_campaigns: list[DropCampaign] = [
            campaign
            for campaign in campaigns_list
            if campaign.start_at is not None
            and campaign.start_at <= now
            and campaign.end_at is not None
            and campaign.end_at >= now
        ]
        active_campaigns.sort(
            key=lambda c: (
                c.end_at
                if c.end_at is not None
                else datetime.datetime.max.replace(tzinfo=datetime.UTC)
            ),
        )

        upcoming_campaigns: list[DropCampaign] = [
            campaign
            for campaign in campaigns_list
            if campaign.start_at is not None and campaign.start_at > now
        ]
        upcoming_campaigns.sort(
            key=lambda c: (
                c.start_at
                if c.start_at is not None
                else datetime.datetime.max.replace(tzinfo=datetime.UTC)
            ),
        )

        expired_campaigns: list[DropCampaign] = [
            campaign
            for campaign in campaigns_list
            if campaign.end_at is not None and campaign.end_at < now
        ]

        serialized_channel: str = serialize(
            "json",
            [channel],
            fields=("twitch_id", "name", "display_name", "added_at", "updated_at"),
        )
        channel_data: list[dict[str, Any]] = json.loads(serialized_channel)

        if campaigns_list:
            serialized_campaigns: str = serialize(
                "json",
                campaigns_list,
                fields=(
                    "twitch_id",
                    "name",
                    "description",
                    "details_url",
                    "account_link_url",
                    "image_url",
                    "start_at",
                    "end_at",
                    "added_at",
                    "updated_at",
                ),
            )
            campaigns_data: list[dict[str, Any]] = json.loads(serialized_campaigns)
            channel_data[0]["fields"]["campaigns"] = campaigns_data

        name: str = channel.display_name or channel.name or channel.twitch_id
        total_campaigns: int = len(campaigns_list)
        description: str = f"{name} participates in {total_campaigns} drop campaign"
        if total_campaigns > 1:
            description += "s"

        channel_schema: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "BroadcastChannel",
            "name": name,
            "description": description,
            "url": self.request.build_absolute_uri(
                reverse("twitch:channel_detail", args=[channel.twitch_id]),
            ),
            "broadcastChannelId": channel.twitch_id,
            "providerName": "Twitch",
        }

        # Breadcrumb schema
        breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
            {"name": "Home", "url": self.request.build_absolute_uri("/")},
            {"name": "Channels", "url": self.request.build_absolute_uri("/channels/")},
            {
                "name": name,
                "url": self.request.build_absolute_uri(
                    reverse("twitch:channel_detail", args=[channel.twitch_id]),
                ),
            },
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title=name,
            page_description=description,
            schema_data=channel_schema,
            breadcrumb_schema=breadcrumb_schema,
            modified_date=channel.updated_at.isoformat()
            if channel.updated_at
            else None,
        )
        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "now": now,
            "channel_data": format_and_color_json(channel_data[0]),
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
    badge_sets: QuerySet[ChatBadgeSet] = (
        ChatBadgeSet.objects
        .all()
        .prefetch_related(
            Prefetch("badges", queryset=ChatBadge.objects.order_by("badge_id")),
        )
        .order_by("set_id")
    )

    # Group badges by set for easier display
    badge_data: list[dict[str, Any]] = [
        {
            "set": badge_set,
            "badges": list(badge_set.badges.all()),  # pyright: ignore[reportAttributeAccessIssue]
        }
        for badge_set in badge_sets
    ]

    # CollectionPage schema for badges list
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Twitch chat badges",
        "description": "List of Twitch chat badges awarded through drop campaigns.",
        "url": request.build_absolute_uri("/badges/"),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Chat Badges",
        page_description="List of Twitch chat badges awarded through drop campaigns.",
        schema_data=collection_schema,
    )
    context: dict[str, Any] = {
        "badge_sets": badge_sets,
        "badge_data": badge_data,
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
        badge_set: ChatBadgeSet = ChatBadgeSet.objects.prefetch_related(
            Prefetch("badges", queryset=ChatBadge.objects.order_by("badge_id")),
        ).get(set_id=set_id)
    except ChatBadgeSet.DoesNotExist as exc:
        msg = "No badge set found matching the query"
        raise Http404(msg) from exc

    def get_sorted_badges(badge_set: ChatBadgeSet) -> QuerySet[ChatBadge]:
        badges = badge_set.badges.all()  # pyright: ignore[reportAttributeAccessIssue]

        def sort_badges(badge: ChatBadge) -> tuple:
            """Sort badges by badge_id, treating numeric IDs as integers.

            Args:
                badge: The ChatBadge to sort.

            Returns:
                A tuple used for sorting, where numeric badge_ids are sorted as integers.
            """
            try:
                return (int(badge.badge_id),)
            except ValueError:
                return (badge.badge_id,)

        sorted_badges: list[ChatBadge] = sorted(badges, key=sort_badges)
        badge_ids: list[int] = [badge.pk for badge in sorted_badges]
        preserved_order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(badge_ids)],
        )
        return ChatBadge.objects.filter(pk__in=badge_ids).order_by(preserved_order)

    badges = get_sorted_badges(badge_set)

    # Attach award_campaigns attribute to each badge for template use
    for badge in badges:
        benefits: QuerySet[DropBenefit, DropBenefit] = DropBenefit.objects.filter(
            distribution_type="BADGE",
            name=badge.title,
        )
        campaigns: QuerySet[DropCampaign, DropCampaign] = DropCampaign.objects.filter(
            time_based_drops__benefits__in=benefits,
        ).distinct()
        badge.award_campaigns = list(campaigns)  # pyright: ignore[reportAttributeAccessIssue]

    # Serialize for JSON display
    serialized_set: str = serialize(
        "json",
        [badge_set],
        fields=("set_id", "added_at", "updated_at"),
    )
    set_data: list[dict[str, Any]] = json.loads(serialized_set)

    if badges:
        serialized_badges: str = serialize(
            "json",
            badges,
            fields=(
                "badge_id",
                "image_url_1x",
                "image_url_2x",
                "image_url_4x",
                "title",
                "description",
                "click_action",
                "click_url",
                "added_at",
                "updated_at",
            ),
        )
        badges_data: list[dict[str, Any]] = json.loads(serialized_badges)
        set_data[0]["fields"]["badges"] = badges_data

    badge_set_name: str = badge_set.set_id
    badge_set_description: str = f"Twitch chat badge set {badge_set_name} with {len(badges)} badge{'s' if len(badges) != 1 else ''} awarded through drop campaigns."

    badge_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": badge_set_name,
        "description": badge_set_description,
        "url": request.build_absolute_uri(
            reverse("twitch:badge_set_detail", args=[badge_set.set_id]),
        ),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=f"Badge Set: {badge_set_name}",
        page_description=badge_set_description,
        schema_data=badge_schema,
    )
    context: dict[str, Any] = {
        "badge_set": badge_set,
        "badges": badges,
        "set_data": format_and_color_json(set_data[0]),
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


def export_games_csv(request: HttpRequest) -> HttpResponse:  # noqa: ARG001  # noqa: ARG001
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


def export_games_json(request: HttpRequest) -> HttpResponse:  # noqa: ARG001  # noqa: ARG001
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


def export_organizations_csv(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
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


def export_organizations_json(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
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
