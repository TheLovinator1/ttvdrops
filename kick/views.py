from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from django.core.paginator import EmptyPage
from django.core.paginator import PageNotAnInteger
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from kick.models import KickCategory
from kick.models import KickDropCampaign
from kick.models import KickOrganization

if TYPE_CHECKING:
    import datetime

    from django.core.paginator import Page
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django.http import HttpResponse

    from core.seo import SeoMeta
    from kick.models import KickChannel
    from kick.models import KickReward

logger: logging.Logger = logging.getLogger("ttvdrops.kick.views")


def _build_seo_context(
    page_title: str = "Kick Drops",
    page_description: str | None = None,
    seo_meta: SeoMeta | None = None,
) -> dict[str, Any]:
    """Build SEO context for template rendering.

    Args:
        page_title: The title of the page for <title> and OG tags.
        page_description: Optional description for meta and OG tags.
        seo_meta: Optional typed SEO metadata.

    Returns:
        A dictionary with SEO-related context variables.
    """
    context: dict[str, Any] = {
        "page_title": page_title,
        "page_description": page_description or "Archive of Kick drops.",
        "og_type": "website",
        "robots_directive": "index, follow",
    }
    if seo_meta:
        if seo_meta.get("og_type"):
            context["og_type"] = seo_meta["og_type"]
        if seo_meta.get("robots_directive"):
            context["robots_directive"] = seo_meta["robots_directive"]
        if seo_meta.get("schema_data"):
            context["schema_data"] = json.dumps(seo_meta["schema_data"])
        if seo_meta.get("published_date"):
            context["published_date"] = seo_meta["published_date"]
        if seo_meta.get("modified_date"):
            context["modified_date"] = seo_meta["modified_date"]
    return context


def _build_breadcrumb_schema(items: list[dict[str, str]]) -> str:
    """Build a serialised BreadcrumbList JSON-LD string.

    Args:
        items: A list of breadcrumb items, each with "name" and "url" keys

    Returns:
        A JSON string representing the BreadcrumbList schema.
    """
    breadcrumb_items: list[dict[str, str | int]] = [
        {
            "@type": "ListItem",
            "position": i + 1,
            "name": item["name"],
            "item": item["url"],
        }
        for i, item in enumerate(items)
    ]
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": breadcrumb_items,
    })


def _build_pagination_info(
    request: HttpRequest,
    page_obj: Page,
    base_url: str,
) -> list[dict[str, str]] | None:
    """Build rel="prev"/"next" pagination link info.

    Args:
        request: The current HTTP request (used to build absolute URLs).
        page_obj: The current Page object from the paginator.
        base_url: The base URL for pagination links (without ?page=).

    Returns:
        A list of pagination link dictionaries or None if no links.
    """
    sep: Literal["&", "?"] = "&" if "?" in base_url else "?"
    links: list[dict[str, str]] = []
    if page_obj.has_previous():
        links.append({
            "rel": "prev",
            "url": request.build_absolute_uri(
                f"{base_url}{sep}page={page_obj.previous_page_number()}",
            ),
        })
    if page_obj.has_next():
        links.append({
            "rel": "next",
            "url": request.build_absolute_uri(
                f"{base_url}{sep}page={page_obj.next_page_number()}",
            ),
        })
    return links or None


# MARK: /kick/
def dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard showing currently active Kick drop campaigns.

    This view focuses on active campaigns, showing them in order of start date.
    For a complete list of campaigns with filtering and pagination, see the
    campaign_list_view.

    Returns:
        An HttpResponse rendering the dashboard template with active campaigns.
    """
    now: datetime.datetime = timezone.now()
    active_campaigns: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .filter(starts_at__lte=now, ends_at__gte=now)
        .select_related("organization", "category")
        .prefetch_related("channels__user", "rewards")
        .order_by("-starts_at")
    )

    seo_context: dict[str, str] = _build_seo_context(
        page_title="Kick Drops",
        page_description="Overview of active Kick drop campaigns.",
    )
    return render(
        request,
        "kick/dashboard.html",
        {
            "active_campaigns": active_campaigns,
            "now": now,
            **seo_context,
        },
    )


# MARK: /kick/campaigns/
def campaign_list_view(request: HttpRequest) -> HttpResponse:
    """Paginated list of all Kick campaigns with optional filtering.

    Supports filtering by game and status (active/upcoming/expired) via query parameters.
    Pagination is implemented with 100 campaigns per page.

    Returns:
        An HttpResponse rendering the campaign list template with the filtered and paginated campaigns.
    """
    game_filter: str | None = request.GET.get("game") or request.GET.get("category")
    status_filter: str | None = request.GET.get("status")
    per_page = 100
    now: datetime.datetime = timezone.now()

    queryset: QuerySet[KickDropCampaign] = (
        KickDropCampaign.objects
        .select_related("organization", "category")
        .prefetch_related("rewards")
        .order_by("-starts_at")
    )

    if game_filter:
        queryset = queryset.filter(category__kick_id=game_filter)

    if status_filter == "active":
        queryset = queryset.filter(starts_at__lte=now, ends_at__gte=now)
    elif status_filter == "upcoming":
        queryset = queryset.filter(starts_at__gt=now)
    elif status_filter == "expired":
        queryset = queryset.filter(ends_at__lt=now)

    paginator: Paginator[KickDropCampaign] = Paginator(queryset, per_page)
    page: str | Literal[1] = request.GET.get("page") or 1
    try:
        campaigns: Page[KickDropCampaign] = paginator.page(page)
    except PageNotAnInteger:
        campaigns = paginator.page(1)
    except EmptyPage:
        campaigns = paginator.page(paginator.num_pages)

    title = "Kick Drop Campaigns"
    if status_filter:
        title += f" ({status_filter.capitalize()})"

    base_url = "/kick/campaigns/"
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

    seo_context: dict[str, str] = _build_seo_context(
        page_title=title,
        page_description="Browse Kick drop campaigns.",
    )
    return render(
        request,
        "kick/campaign_list.html",
        {
            "campaigns": campaigns,
            "page_obj": campaigns,
            "is_paginated": campaigns.has_other_pages(),
            "games": KickCategory.objects.order_by("name"),
            "status_options": ["active", "upcoming", "expired"],
            "now": now,
            "selected_game": game_filter or "",
            "selected_status": status_filter or "",
            "pagination_info": pagination_info,
            **seo_context,
        },
    )


# MARK: /kick/campaigns/<kick_id>/
def campaign_detail_view(request: HttpRequest, kick_id: str) -> HttpResponse:
    """Detail view for a single Kick drop campaign.

    Args:
        request: The HTTP request object.
        kick_id: The unique identifier for the Kick campaign.

    Returns:
        An HttpResponse rendering the campaign detail template with the campaign information.

    Raises:
        Http404: If no campaign is found matching the kick_id.
    """
    try:
        campaign: KickDropCampaign = (
            KickDropCampaign.objects
            .select_related("organization", "category")
            .prefetch_related("channels__user", "rewards__category")
            .get(kick_id=kick_id)
        )
    except KickDropCampaign.DoesNotExist as exc:
        msg = "No campaign found matching the query"
        raise Http404(msg) from exc

    now: datetime.datetime = timezone.now()
    rewards: list[KickReward] = list(
        campaign.rewards.order_by("required_units").select_related("category"),  # type: ignore[union-attr]
    )
    channels: list[KickChannel] = list(campaign.channels.select_related("user"))
    reward_count: int = len(rewards)
    channels_count: int = len(channels)
    total_watch_minutes: int = sum(reward.required_units for reward in rewards)

    breadcrumb_schema: str = _build_breadcrumb_schema([
        {"name": "Home", "url": request.build_absolute_uri("/")},
        {
            "name": "Kick Campaigns",
            "url": request.build_absolute_uri(reverse("kick:campaign_list")),
        },
        {
            "name": campaign.name,
            "url": request.build_absolute_uri(
                reverse("kick:campaign_detail", args=[campaign.kick_id]),
            ),
        },
    ])

    campaign_url: str = request.build_absolute_uri(
        reverse("kick:campaign_detail", args=[campaign.kick_id]),
    )
    campaign_event: dict[str, Any] = {
        "@type": "Event",
        "name": campaign.name,
        "description": f"Kick drop campaign: {campaign.name}.",
        "url": campaign_url,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "location": {"@type": "VirtualLocation", "url": "https://kick.com"},
    }
    if campaign.starts_at:
        campaign_event["startDate"] = campaign.starts_at.isoformat()
    if campaign.ends_at:
        campaign_event["endDate"] = campaign.ends_at.isoformat()
    if campaign.organization:
        campaign_event["organizer"] = {
            "@type": "Organization",
            "name": campaign.organization.name,
        }

    webpage_node: dict[str, Any] = {
        "@type": "WebPage",
        "url": campaign_url,
        "datePublished": campaign.added_at.isoformat(),
        "dateModified": campaign.updated_at.isoformat(),
    }

    campaign_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@graph": [
            campaign_event,
            webpage_node,
        ],
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=campaign.name,
        page_description=f"Kick drop campaign: {campaign.name}.",
        seo_meta={
            "schema_data": campaign_schema,
            "published_date": campaign.added_at.isoformat(),
            "modified_date": campaign.updated_at.isoformat(),
        },
    )
    return render(
        request,
        "kick/campaign_detail.html",
        {
            "campaign": campaign,
            "rewards": rewards,
            "channels": channels,
            "reward_count": reward_count,
            "channels_count": channels_count,
            "total_watch_minutes": total_watch_minutes,
            "now": now,
            "breadcrumb_schema": breadcrumb_schema,
            **seo_context,
        },
    )


# MARK: /kick/games/
def category_list_view(request: HttpRequest) -> HttpResponse:
    """List of all Kick games with their campaign counts.

    Returns:
        An HttpResponse rendering the category list template with all games and their campaign counts.
    """
    categories: QuerySet[KickCategory] = KickCategory.objects.annotate(
        campaign_count=Count("campaigns", distinct=True),
    ).order_by("name")

    seo_context: dict[str, str] = _build_seo_context(
        page_title="Kick Games",
        page_description="Games on Kick that have drop campaigns.",
    )
    return render(
        request,
        "kick/category_list.html",
        {
            "categories": categories,
            **seo_context,
        },
    )


# MARK: /kick/games/<kick_id>/
def category_detail_view(request: HttpRequest, kick_id: int) -> HttpResponse:
    """Detail view for a Kick game with its drop campaigns.

    Args:
        request: The HTTP request object.
        kick_id: The unique identifier for the Kick game.

    Returns:
        An HttpResponse rendering the category detail template with the game information and its campaigns.

    Raises:
        Http404: If no game is found matching the kick_id.
    """
    try:
        category: KickCategory = KickCategory.objects.get(kick_id=kick_id)
    except KickCategory.DoesNotExist as exc:
        msg = "No game found matching the query"
        raise Http404(msg) from exc

    now: datetime.datetime = timezone.now()
    all_campaigns: list[KickDropCampaign] = list(
        KickDropCampaign.objects
        .filter(category=category)
        .select_related("organization")
        .prefetch_related("channels__user", "rewards")
        .order_by("-starts_at"),
    )

    active_campaigns: list[KickDropCampaign] = [
        c
        for c in all_campaigns
        if c.starts_at and c.ends_at and c.starts_at <= now <= c.ends_at
    ]
    upcoming_campaigns: list[KickDropCampaign] = [
        c for c in all_campaigns if c.starts_at and c.starts_at > now
    ]
    expired_campaigns: list[KickDropCampaign] = [
        c for c in all_campaigns if c.ends_at and c.ends_at < now
    ]

    breadcrumb_schema: str = _build_breadcrumb_schema([
        {"name": "Home", "url": request.build_absolute_uri("/")},
        {
            "name": "Kick Games",
            "url": request.build_absolute_uri(reverse("kick:game_list")),
        },
        {
            "name": category.name,
            "url": request.build_absolute_uri(
                reverse("kick:game_detail", args=[category.kick_id]),
            ),
        },
    ])

    category_url: str = request.build_absolute_uri(
        reverse("kick:game_detail", args=[category.kick_id]),
    )
    category_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": category.name,
        "description": f"Kick drop campaigns for {category.name}.",
        "url": category_url,
        "datePublished": category.added_at.isoformat(),
        "dateModified": category.updated_at.isoformat(),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=category.name,
        page_description=f"Kick drop campaigns for {category.name}.",
        seo_meta={
            "schema_data": category_schema,
            "published_date": category.added_at.isoformat(),
            "modified_date": category.updated_at.isoformat(),
        },
    )
    return render(
        request,
        "kick/category_detail.html",
        {
            "category": category,
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "now": now,
            "breadcrumb_schema": breadcrumb_schema,
            **seo_context,
        },
    )


# MARK: /kick/organizations/
def organization_list_view(request: HttpRequest) -> HttpResponse:
    """List of all Kick organizations.

    Returns:
        An HttpResponse rendering the organization list template with all organizations and their campaign counts.
    """
    orgs: QuerySet[KickOrganization] = KickOrganization.objects.annotate(
        campaign_count=Count("campaigns", distinct=True),
    ).order_by("name")

    seo_context: dict[str, str] = _build_seo_context(
        page_title="Kick Organizations",
        page_description="Organizations that run Kick drop campaigns.",
    )
    return render(
        request,
        "kick/org_list.html",
        {
            "orgs": orgs,
            **seo_context,
        },
    )


# MARK: /kick/organizations/<kick_id>/
def organization_detail_view(request: HttpRequest, kick_id: str) -> HttpResponse:
    """Detail view for a Kick organization with its campaigns.

    Args:
        request: The HTTP request object.
        kick_id: The unique identifier for the Kick organization.

    Returns:
        An HttpResponse rendering the organization detail template with the organization information and its campaigns.

    Raises:
        Http404: If no organization is found matching the kick_id.
    """
    try:
        org: KickOrganization = KickOrganization.objects.get(kick_id=kick_id)
    except KickOrganization.DoesNotExist as exc:
        msg = "No organization found matching the query"
        raise Http404(msg) from exc

    now: datetime.datetime = timezone.now()
    campaigns: list[KickDropCampaign] = list(
        KickDropCampaign.objects
        .filter(organization=org)
        .select_related("category")
        .prefetch_related("rewards")
        .order_by("-starts_at"),
    )

    breadcrumb_schema: str = _build_breadcrumb_schema([
        {"name": "Home", "url": request.build_absolute_uri("/")},
        {
            "name": "Kick Organizations",
            "url": request.build_absolute_uri(reverse("kick:organization_list")),
        },
        {
            "name": org.name,
            "url": request.build_absolute_uri(
                reverse("kick:organization_detail", args=[org.kick_id]),
            ),
        },
    ])

    org_url: str = request.build_absolute_uri(
        reverse("kick:organization_detail", args=[org.kick_id]),
    )
    organization_node: dict[str, Any] = {
        "@type": "Organization",
        "name": org.name,
        "url": org_url,
        "description": f"Kick drop campaigns by {org.name}.",
    }
    webpage_node: dict[str, Any] = {
        "@type": "WebPage",
        "url": org_url,
        "datePublished": org.added_at.isoformat(),
        "dateModified": org.updated_at.isoformat(),
    }
    org_schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@graph": [
            organization_node,
            webpage_node,
        ],
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=org.name,
        page_description=f"Kick drop campaigns by {org.name}.",
        seo_meta={
            "schema_data": org_schema,
            "published_date": org.added_at.isoformat(),
            "modified_date": org.updated_at.isoformat(),
        },
    )
    return render(
        request,
        "kick/organization_detail.html",
        {
            "org": org,
            "campaigns": campaigns,
            "now": now,
            "breadcrumb_schema": breadcrumb_schema,
            **seo_context,
        },
    )
