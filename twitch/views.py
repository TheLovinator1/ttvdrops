from __future__ import annotations

import csv
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
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign

if TYPE_CHECKING:
    import datetime

    from django.db.models import QuerySet
    from django.http import HttpRequest

    from core.seo import SeoMeta

logger: logging.Logger = logging.getLogger("ttvdrops.views")

MIN_QUERY_LENGTH_FOR_FTS = 3
MIN_SEARCH_RANK = 0.05
DEFAULT_SITE_DESCRIPTION = "Archive of Twitch drops, campaigns, rewards, and more."


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


def _build_seo_context(
    page_title: str = "ttvdrops",
    page_description: str | None = None,
    seo_meta: SeoMeta | None = None,
) -> dict[str, Any]:
    """Build SEO context for template rendering.

    Args:
        page_title: Page title (shown in browser tab, og:title).
        page_description: Page description (meta description, og:description).
        seo_meta: Optional typed SEO metadata with image, schema, breadcrumb,
            pagination, OpenGraph, and date fields.

    Returns:
        Dict with SEO context variables to pass to render().
    """
    context: dict[str, Any] = {
        "page_title": page_title,
        "page_description": page_description or DEFAULT_SITE_DESCRIPTION,
        "og_type": "website",
        "robots_directive": "index, follow",
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

        published_date = seo_meta.get("published_date")
        if published_date:
            context["published_date"] = published_date

        modified_date = seo_meta.get("modified_date")
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


def emote_gallery_view(request: HttpRequest) -> HttpResponse:
    """View to display all emote images.

    Emotes are associated with DropBenefits of type "EMOTE".

    Args:
        request: The HTTP request.

    Returns:
        HttpResponse: The rendered emote gallery page.
    """
    emotes: list[dict[str, str | DropCampaign]] = DropBenefit.emotes_for_gallery()

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Emotes",
        page_description="List of all Twitch emotes available as rewards.",
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

    # CollectionPage schema for organizations list
    collection_schema: dict[str, str] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Twitch Organizations",
        "description": "List of Twitch organizations.",
        "url": build_absolute_uri("/organizations/"),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Organizations",
        page_description="List of Twitch organizations.",
        seo_meta={"schema_data": collection_schema},
    )
    context: dict[str, Any] = {"orgs": orgs, **seo_context}

    return render(request, "twitch/org_list.html", context)


# MARK: /organizations/<twitch_id>/
def organization_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:
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
    org_description: str = f"{org_name} has {games_count} {noun}."

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
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": build_absolute_uri("/")},
        {"name": "Organizations", "url": build_absolute_uri("/organizations/")},
        {
            "name": org_name,
            "url": build_absolute_uri(
                reverse("twitch:organization_detail", args=[organization.twitch_id]),
            ),
        },
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=org_name,
        page_description=org_description,
        seo_meta={
            "schema_data": org_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "published_date": organization.added_at.isoformat(),
            "modified_date": organization.updated_at.isoformat(),
        },
    )
    context: dict[str, Any] = {
        "organization": organization,
        "games": games,
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
    per_page: int = 100
    now: datetime.datetime = timezone.now()

    queryset: QuerySet[DropCampaign] = DropCampaign.for_campaign_list(
        now,
        game_twitch_id=game_filter,
        status=status_filter,
    )

    paginator: Paginator[DropCampaign] = Paginator(queryset, per_page)
    page: str | Literal[1] = request.GET.get("page") or 1
    try:
        campaigns: Page[DropCampaign] = paginator.page(page)
    except PageNotAnInteger:
        campaigns = paginator.page(1)
    except EmptyPage:
        campaigns = paginator.page(paginator.num_pages)

    status_descriptions: dict[str, str] = {
        "active": "Browse active Twitch drops.",
        "upcoming": "View upcoming Twitch drops starting soon.",
        "expired": "Browse expired Twitch drops.",
    }
    title = "Twitch Drops"
    description = "Browse Twitch drops"
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
    if status_filter and game_filter:
        base_url += f"?status={status_filter}&game={game_filter}"
    elif status_filter:
        base_url += f"?status={status_filter}"
    elif game_filter:
        base_url += f"?game={game_filter}"

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
        "selected_status": status_filter or "",
        **seo_context,
    }
    return render(request, "twitch/campaign_list.html", context)


# MARK: /campaigns/<twitch_id>/
def drop_campaign_detail_view(request: HttpRequest, twitch_id: str) -> HttpResponse:  # ruff:ignore[too-many-locals]
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

    context: dict[str, Any] = {
        "campaign": campaign,
        "now": now,
        "drops": enhanced_drops,
        "owners": owners,
        "allowed_channels": getattr(campaign, "channels_ordered", []),
    }

    campaign_name: str = campaign.name or campaign.clean_name or campaign.twitch_id
    campaign_description: str = (
        _truncate_description(campaign.description)
        if campaign.description
        else f"Twitch drop campaign: {campaign_name}"
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
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": build_absolute_uri("/")},
        {"name": "Games", "url": build_absolute_uri("/games/")},
        {
            "name": game_name,
            "url": build_absolute_uri(
                reverse("twitch:game_detail", args=[campaign.game.twitch_id]),
            ),
        },
        {
            "name": campaign_name,
            "url": build_absolute_uri(
                reverse("twitch:campaign_detail", args=[campaign.twitch_id]),
            ),
        },
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=campaign_name,
        page_description=campaign_description,
        seo_meta={
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

        # CollectionPage schema for games list
        collection_schema: dict[str, str] = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Twitch Games",
            "description": "Twitch games that had or have Twitch drops.",
            "url": build_absolute_uri("/games/"),
        }

        seo_context: dict[str, Any] = _build_seo_context(
            page_title="Twitch Games",
            page_description="Twitch games that had or have Twitch drops.",
            seo_meta={"schema_data": collection_schema},
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

    def get_context_data(self, **kwargs) -> dict[str, Any]:  # ruff:ignore[too-many-locals]
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

        game_name: str = game.get_game_name
        game_description: str = f"Twitch drops for {game_name}."
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
        breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
            {"name": "Home", "url": build_absolute_uri("/")},
            {"name": "Games", "url": build_absolute_uri("/games/")},
            {
                "name": game_name,
                "url": build_absolute_uri(
                    reverse("twitch:game_detail", args=[game.twitch_id]),
                ),
            },
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title=game_name,
            page_description=game_description,
            seo_meta={
                "page_image": game_image,
                "page_image_width": game_image_width,
                "page_image_height": game_image_height,
                "schema_data": game_schema,
                "breadcrumb_schema": breadcrumb_schema,
                "published_date": game.added_at.isoformat() if game.added_at else None,
                "modified_date": game.updated_at.isoformat()
                if game.updated_at
                else None,
            },
        )
        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "active_rewards": active_rewards,
            "upcoming_rewards": upcoming_rewards,
            "expired_rewards": expired_rewards,
            "owner": owners[0] if owners else None,
            "owners": owners,
            "now": now,
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
    dashboard_data: dict[str, Any] = DropCampaign.dashboard_context(now)

    # WebSite schema with SearchAction for sitelinks search box
    # TODO(TheLovinator): Should this be on all pages instead of just the dashboard?  # ruff:ignore[missing-todo-link]
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
        page_title="Twitch Drops",
        page_description="Overview of active Twitch drops and rewards.",
        seo_meta={
            "og_type": "website",
            "schema_data": website_schema,
        },
    )
    return render(
        request,
        "twitch/dashboard.html",
        {
            "now": now,
            **dashboard_data,
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

    title = "Twitch Rewards"
    if status_filter:
        title += f" ({status_filter.capitalize()})"

    description = "Twitch rewards."
    if status_filter == "active":
        description = "Browse active Twitch rewards."
    elif status_filter == "upcoming":
        description = "Browse upcoming Twitch rewards."
    elif status_filter == "expired":
        description = "Browse expired Twitch rewards."

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
        "url": build_absolute_uri(base_url),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": build_absolute_uri(base_url),
            "pagination_info": pagination_info,
            "schema_data": collection_schema,
        },
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

    now: datetime.datetime = timezone.now()

    campaign_name: str = reward_campaign.name or reward_campaign.twitch_id
    campaign_description: str = (
        _truncate_description(reward_campaign.summary)
        if reward_campaign.summary
        else f"{campaign_name}"
    )

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
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": build_absolute_uri("/")},
        {
            "name": "Reward Campaigns",
            "url": build_absolute_uri("/reward-campaigns/"),
        },
        {
            "name": campaign_name,
            "url": build_absolute_uri(
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
        seo_meta={
            "schema_data": campaign_schema,
            "breadcrumb_schema": breadcrumb_schema,
            "published_date": reward_campaign.added_at.isoformat(),
            "modified_date": reward_campaign.updated_at.isoformat(),
        },
    )
    context: dict[str, Any] = {
        "reward_campaign": reward_campaign,
        "now": now,
        "is_active": reward_campaign.is_active,
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
        base_url: str = f"/channels/?{query_string}" if query_string else "/channels/"

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
            "url": build_absolute_uri("/channels/"),
        }

        seo_context: dict[str, Any] = _build_seo_context(
            page_title="Twitch Channels",
            page_description="List of Twitch channels participating in drop campaigns.",
            seo_meta={
                "page_url": build_absolute_uri(base_url),
                "pagination_info": pagination_info,
                "schema_data": collection_schema,
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

        name: str = channel.preferred_name
        total_campaigns: int = len(campaigns_list)
        description: str = channel.detail_description(total_campaigns)

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
        breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
            {"name": "Home", "url": build_absolute_uri("/")},
            {"name": "Channels", "url": build_absolute_uri("/channels/")},
            {
                "name": name,
                "url": build_absolute_uri(
                    reverse("twitch:channel_detail", args=[channel.twitch_id]),
                ),
            },
        ])

        seo_context: dict[str, Any] = _build_seo_context(
            page_title=name,
            page_description=description,
            seo_meta={
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
        context.update({
            "active_campaigns": active_campaigns,
            "upcoming_campaigns": upcoming_campaigns,
            "expired_campaigns": expired_campaigns,
            "now": now,
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
        "url": build_absolute_uri("/badges/"),
    }

    seo_context: dict[str, Any] = _build_seo_context(
        page_title="Twitch Chat Badges",
        page_description="List of Twitch chat badges awarded through drop campaigns.",
        seo_meta={"schema_data": collection_schema},
    )
    context: dict[str, Any] = {
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
        f"badge{'s' if badge_count != 1 else ''} awarded through drop campaigns."
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
        page_title=f"Badge Set: {badge_set_name}",
        page_description=badge_set_description,
        seo_meta={"schema_data": badge_schema},
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

    title: str = "Site-wide Rewards"
    description: str = "Twitch reward campaigns available site-wide."

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
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": build_absolute_uri("/")},
        {"name": "Reward Campaigns", "url": build_absolute_uri("/reward-campaigns/")},
        {"name": title, "url": url},
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": url,
            "schema_data": collection_schema,
            "breadcrumb_schema": breadcrumb_schema,
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

    title: str = "Brand & Site-wide Rewards"
    description: str = (
        "Twitch reward campaigns without a linked game (brand-specific and site-wide)."
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
    breadcrumb_schema: dict[str, Any] = _build_breadcrumb_schema([
        {"name": "Home", "url": build_absolute_uri("/")},
        {"name": "Reward Campaigns", "url": build_absolute_uri("/reward-campaigns/")},
        {"name": title, "url": url},
    ])

    seo_context: dict[str, Any] = _build_seo_context(
        page_title=title,
        page_description=description,
        seo_meta={
            "page_url": url,
            "schema_data": collection_schema,
            "breadcrumb_schema": breadcrumb_schema,
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
