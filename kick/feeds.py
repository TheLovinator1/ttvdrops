from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.html import format_html_join
from django.utils.safestring import SafeText

from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from twitch.feeds import TTVDropsAtomBaseFeed
from twitch.feeds import TTVDropsBaseFeed
from twitch.feeds import discord_timestamp

if TYPE_CHECKING:
    import datetime

    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django.http import HttpResponse


def _with_campaign_related(
    queryset: QuerySet[KickDropCampaign],
) -> QuerySet[KickDropCampaign]:
    """Apply related-select/prefetches needed by feed rendering.

    Returns:
        QuerySet[KickDropCampaign]: QuerySet with related objects optimized for feed rendering.
    """
    return queryset.select_related("organization", "category").prefetch_related(
        Prefetch(
            "channels",
            queryset=KickChannel.objects.select_related("user").order_by("slug"),
            to_attr="channels_ordered",
        ),
        Prefetch(
            "rewards",
            queryset=KickReward.objects.order_by("required_units", "name"),
            to_attr="rewards_ordered",
        ),
    )


def _active_campaigns(
    queryset: QuerySet[KickDropCampaign],
) -> QuerySet[KickDropCampaign]:
    """Filter campaign queryset down to currently active campaigns.

    Returns:
        QuerySet[KickDropCampaign]: Filtered queryset with active campaigns.
    """
    now: datetime.datetime = timezone.now()
    return queryset.filter(starts_at__lte=now, ends_at__gte=now)


def _campaign_dates_html(
    campaign: KickDropCampaign,
    *,
    use_discord_relative: bool = False,
) -> SafeText:
    """Render campaign date rows in standard or Discord relative format.

    Returns:
        SafeText: HTML with campaign dates or empty if no dates are available.
    """
    starts_at: datetime.datetime | None = campaign.starts_at
    ends_at: datetime.datetime | None = campaign.ends_at

    if not starts_at and not ends_at:
        return SafeText("")

    formatter = discord_timestamp if use_discord_relative else naturaltime

    start_part: SafeText = (
        format_html(
            "Starts: {} ({})",
            starts_at.strftime("%Y-%m-%d %H:%M %Z"),
            formatter(starts_at),
        )
        if starts_at
        else SafeText("")
    )
    end_part: SafeText = (
        format_html(
            "Ends: {} ({})",
            ends_at.strftime("%Y-%m-%d %H:%M %Z"),
            formatter(ends_at),
        )
        if ends_at
        else SafeText("")
    )

    if start_part and end_part:
        return format_html("<p>{}<br />{}</p>", start_part, end_part)
    if start_part:
        return format_html("<p>{}</p>", start_part)
    return format_html("<p>{}</p>", end_part)


def _campaign_rewards_html(campaign: KickDropCampaign) -> SafeText:
    """Render a compact rewards summary for a campaign.

    Rewards are rendered as a simple list of "X minutes: Reward Name" entries.

    Returns:
        SafeText: HTML with rewards summary or empty if no rewards are available.
    """
    rewards: list[KickReward] = getattr(campaign, "rewards_ordered", [])
    if not rewards:
        rewards = list(
            campaign.rewards.order_by("required_units", "name"),  # type: ignore[reportAttributeAccessIssue]
        )

    if not rewards:
        return SafeText("")

    items: list[SafeText] = [
        format_html("<li>{} minutes: {}</li>", reward.required_units, reward.name)
        for reward in rewards
    ]

    html: SafeText = format_html(
        "<ul>{}</ul>",
        format_html_join("", "{}", [(item,) for item in items]),
    )
    return format_html("<p>Rewards:</p>{}", html)


def _campaign_channels_html(campaign: KickDropCampaign) -> SafeText:
    """Render up to 5 linked channels for a campaign.

    If there are more than 5 channels, a note about additional channels will be added.

    Returns:
        SafeText: HTML with linked channels or empty if no channels are available.
    """
    channels: list[KickChannel] = getattr(campaign, "channels_ordered", [])
    if not channels:
        channels = list(campaign.channels.select_related("user").order_by("slug"))

    if not channels:
        return SafeText("")

    max_links: int = 5
    items: list[SafeText] = []
    for channel in channels[:max_links]:
        label: str = channel.user.username if channel.user else channel.slug
        if channel.channel_url:
            items.append(
                format_html('<li><a href="{}">{}</a></li>', channel.channel_url, label),
            )
        else:
            items.append(format_html("<li>{}</li>", label))

    if len(channels) > max_links:
        items.append(format_html("<li>... and {} more</li>", len(channels) - max_links))

    html: SafeText = format_html(
        "<ul>{}</ul>",
        format_html_join("", "{}", [(item,) for item in items]),
    )
    return format_html("<p>Channels with this drop:</p>{}", html)


def _campaign_links_html(campaign: KickDropCampaign) -> SafeText:
    """Render campaign external links if available.

    Links can include the main campaign URL and a separate connect URL for claiming rewards.

    Returns:
        SafeText: HTML with campaign links or empty if no links are available.
    """
    links: list[SafeText] = []

    if campaign.url:
        links.append(format_html('<a href="{}">About</a>', campaign.url))
    if campaign.connect_url:
        links.append(format_html('<a href="{}">Connect</a>', campaign.connect_url))

    if not links:
        return SafeText("")

    return format_html(
        "<p>{}</p>",
        format_html_join(" | ", "{}", [(link,) for link in links]),
    )


class KickOrganizationFeed(TTVDropsBaseFeed):
    """RSS feed for latest Kick organizations."""

    title: str = "TTVDrops Kick Organizations"
    link: str = "/kick/organizations/"
    description: str = "Latest organizations on Kick in TTVDrops"

    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: str | int,
        **kwargs: str | int,
    ) -> HttpResponse:
        """Capture optional ?limit query parameter.

        A ?limit parameter can be used to limit the number of organizations returned by the feed.

        Returns:
            HttpResponse: An HTTP response containing the feed XML.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except TypeError, ValueError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> QuerySet[KickOrganization]:
        """Return latest organizations with configurable limit."""
        limit: int = self._limit if self._limit is not None else 200
        return KickOrganization.objects.order_by("-added_at")[:limit]

    def item_title(self, item: KickOrganization) -> SafeText:
        """Return organization name as title."""
        return SafeText(item.name)

    def item_description(self, item: KickOrganization) -> SafeText:
        """Return organization summary HTML."""
        parts: list[SafeText] = []

        if item.logo_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    item.logo_url,
                    item.name,
                ),
            )

        if item.url:
            parts.append(format_html('<p><a href="{}">Website</a></p>', item.url))

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: KickOrganization) -> str:
        """Return the link to organization detail."""
        return reverse("kick:organization_detail", args=[item.kick_id])

    def item_pubdate(self, item: KickOrganization) -> datetime.datetime:
        """Return publication date with fallback."""
        return item.added_at or timezone.now()

    def item_updateddate(self, item: KickOrganization) -> datetime.datetime:
        """Return updated date with fallback."""
        return item.updated_at or timezone.now()

    def item_author_name(self, item: KickOrganization) -> str:
        """Return author name for feed item."""
        return item.name or "Kick"

    def feed_url(self) -> str:
        """Return feed URL."""
        return reverse("kick:organization_feed")


class KickCategoryFeed(TTVDropsBaseFeed):
    """RSS feed for latest Kick games."""

    title: str = "TTVDrops Kick Games"
    link: str = "/kick/games/"
    description: str = "Latest games on Kick in TTVDrops"

    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: str | int,
        **kwargs: str | int,
    ) -> HttpResponse:
        """Capture optional ?limit query parameter.

        Returns:
            HttpResponse: The response from the parent __call__ after capturing the limit.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except TypeError, ValueError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> QuerySet[KickCategory]:
        """Return latest games with configurable limit."""
        limit: int = self._limit if self._limit is not None else 200
        return KickCategory.objects.order_by("-added_at")[:limit]

    def item_title(self, item: KickCategory) -> SafeText:
        """Return game name as title."""
        return SafeText(item.name)

    def item_description(self, item: KickCategory) -> SafeText:
        """Return game summary HTML."""
        parts: list[SafeText] = []

        if item.image_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="320" height="180" />',
                    item.image_url,
                    item.name,
                ),
            )

        if item.kick_url:
            parts.append(
                format_html('<p><a href="{}">Kick game</a></p>', item.kick_url),
            )

        category_campaign_feed: str = reverse(
            "kick:game_campaign_feed",
            args=[item.kick_id],
        )
        parts.append(
            format_html(
                '<p><a href="{}">Campaign feed</a></p>',
                category_campaign_feed,
            ),
        )

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: KickCategory) -> str:
        """Return the link to game detail."""
        return reverse("kick:game_detail", args=[item.kick_id])

    def item_pubdate(self, item: KickCategory) -> datetime.datetime:
        """Return publication date with fallback."""
        return item.added_at or timezone.now()

    def item_updateddate(self, item: KickCategory) -> datetime.datetime:
        """Return updated date with fallback."""
        return item.updated_at or timezone.now()

    def item_author_name(self, item: KickCategory) -> str:
        """Return author name for feed item."""
        return item.name or "Kick"

    def feed_url(self) -> str:
        """Return feed URL."""
        return reverse("kick:game_feed")


class KickCampaignFeed(TTVDropsBaseFeed):
    """RSS feed for currently active Kick drop campaigns."""

    title: str = "Kick Drop Campaigns"
    link: str = "/kick/campaigns/"
    description: str = "Active Kick drop campaigns on TTVDrops"

    item_guid_is_permalink = True
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: str | int,
        **kwargs: str | int,
    ) -> HttpResponse:
        """Capture optional ?limit query parameter.

        A ?limit parameter can be used to limit the number of campaigns returned by the feed.

        Returns:
            HttpResponse: An HTTP response containing the feed XML.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except TypeError, ValueError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> list[KickDropCampaign]:
        """Return active campaigns ordered by newest starts_at."""
        limit: int = self._limit if self._limit is not None else 200
        queryset: QuerySet[KickDropCampaign] = _active_campaigns(
            KickDropCampaign.objects.order_by("-starts_at"),
        )
        return list(_with_campaign_related(queryset)[:limit])

    def item_title(self, item: KickDropCampaign) -> SafeText:
        """Return campaign title with optional game prefix."""
        game_name: str = item.category.name if item.category else "Kick"
        return SafeText(f"{game_name}: {item.name}")

    def item_description(self, item: KickDropCampaign) -> SafeText:
        """Return campaign summary HTML."""
        parts: list[SafeText] = []

        if item.image_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    item.image_url,
                    item.name,
                ),
            )

        parts.extend(
            (
                _campaign_dates_html(item),
                _campaign_rewards_html(item),
                _campaign_channels_html(item),
                _campaign_links_html(item),
            ),
        )

        return SafeText("".join(str(p) for p in parts if p))

    def item_link(self, item: KickDropCampaign) -> str:
        """Return campaign detail URL."""
        return reverse("kick:campaign_detail", args=[item.kick_id])

    def item_guid(self, item: KickDropCampaign) -> str:
        """Return unique item GUID as absolute URL."""
        return self._absolute_url(reverse("kick:campaign_detail", args=[item.kick_id]))

    def item_pubdate(self, item: KickDropCampaign) -> datetime.datetime:
        """Return publication date with fallback chain."""
        if item.starts_at:
            return item.starts_at
        if item.added_at:
            return item.added_at
        return timezone.now()

    def item_updateddate(self, item: KickDropCampaign) -> datetime.datetime:
        """Return update date with fallback chain."""
        if item.api_updated_at:
            return item.api_updated_at
        if item.updated_at:
            return item.updated_at
        return timezone.now()

    def item_categories(self, item: KickDropCampaign) -> tuple[str, ...]:
        """Return category labels for this campaign."""
        categories: list[str] = ["kick", "drops"]
        if item.category and item.category.name:
            categories.append(item.category.name)
        if item.organization and item.organization.name:
            categories.append(item.organization.name)
        return tuple(categories)

    def item_author_name(self, item: KickDropCampaign) -> str:
        """Return author name from organization/category fallback."""
        if item.organization and item.organization.name:
            return item.organization.name
        if item.category and item.category.name:
            return item.category.name
        return "Kick"

    def feed_url(self) -> str:
        """Return feed URL."""
        return reverse("kick:campaign_feed")


class KickCategoryCampaignFeed(TTVDropsBaseFeed):
    """RSS feed for active campaigns under a specific Kick game."""

    item_guid_is_permalink = True
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: str | int,
        **kwargs: str | int,
    ) -> HttpResponse:
        """Capture optional ?limit query parameter.

        Returns:
            HttpResponse: The response from the parent __call__ after capturing the limit.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except TypeError, ValueError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def get_object(self, request: HttpRequest, kick_id: int) -> KickCategory:  # ruff:ignore[unused-method-argument]
        """Return game object for this feed URL."""
        return KickCategory.objects.get(kick_id=kick_id)

    def title(self, obj: KickCategory) -> str:
        """Return dynamic feed title for game campaigns."""
        return f"TTVDrops: {obj.name} Kick Campaigns"

    def link(self, obj: KickCategory) -> str:
        """Return detail URL for game."""
        return reverse("kick:game_detail", args=[obj.kick_id])

    def description(self, obj: KickCategory) -> str:
        """Return dynamic feed description for game campaigns."""
        return f"Active Kick drop campaigns for {obj.name}"

    def items(self, obj: KickCategory) -> list[KickDropCampaign]:
        """Return active campaigns for this game."""
        limit: int = self._limit if self._limit is not None else 200
        queryset: QuerySet[KickDropCampaign] = _active_campaigns(
            KickDropCampaign.objects.filter(category=obj).order_by("-starts_at"),
        )
        return list(_with_campaign_related(queryset)[:limit])

    def item_title(self, item: KickDropCampaign) -> SafeText:
        """Return campaign title."""
        game_name: str = item.category.name if item.category else "Kick"
        return SafeText(f"{game_name}: {item.name}")

    def item_description(self, item: KickDropCampaign) -> SafeText:
        """Return campaign summary HTML."""
        parts: list[SafeText] = []

        if item.image_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    item.image_url,
                    item.name,
                ),
            )

        parts.extend(
            (
                _campaign_dates_html(item),
                _campaign_rewards_html(item),
                _campaign_channels_html(item),
                _campaign_links_html(item),
            ),
        )

        return SafeText("".join(str(p) for p in parts if p))

    def item_link(self, item: KickDropCampaign) -> str:
        """Return campaign detail URL."""
        return reverse("kick:campaign_detail", args=[item.kick_id])

    def item_guid(self, item: KickDropCampaign) -> str:
        """Return absolute GUID URL."""
        return self._absolute_url(reverse("kick:campaign_detail", args=[item.kick_id]))

    def item_pubdate(self, item: KickDropCampaign) -> datetime.datetime:
        """Return publication date with fallback chain."""
        if item.starts_at:
            return item.starts_at
        if item.added_at:
            return item.added_at
        return timezone.now()

    def item_updateddate(self, item: KickDropCampaign) -> datetime.datetime:
        """Return update date with fallback chain."""
        if item.api_updated_at:
            return item.api_updated_at
        if item.updated_at:
            return item.updated_at
        return timezone.now()

    def item_author_name(self, item: KickDropCampaign) -> str:
        """Return author for campaign item."""
        if item.organization and item.organization.name:
            return item.organization.name
        return "Kick"

    def feed_url(self, obj: KickCategory) -> str:
        """Return feed URL."""
        return reverse("kick:game_campaign_feed", args=[obj.kick_id])


class KickOrganizationAtomFeed(TTVDropsAtomBaseFeed, KickOrganizationFeed):
    """Atom feed variant for organizations."""

    subtitle: str = KickOrganizationFeed.description

    def feed_url(self) -> str:
        """Return Atom feed URL."""
        return reverse("kick:organization_feed_atom")


class KickCategoryAtomFeed(TTVDropsAtomBaseFeed, KickCategoryFeed):
    """Atom feed variant for games."""

    subtitle: str = KickCategoryFeed.description

    def feed_url(self) -> str:
        """Return Atom feed URL."""
        return reverse("kick:game_feed_atom")


class KickCampaignAtomFeed(TTVDropsAtomBaseFeed, KickCampaignFeed):
    """Atom feed variant for campaigns."""

    subtitle: str = KickCampaignFeed.description

    def feed_url(self) -> str:
        """Return Atom feed URL."""
        return reverse("kick:campaign_feed_atom")


class KickCategoryCampaignAtomFeed(TTVDropsAtomBaseFeed, KickCategoryCampaignFeed):
    """Atom feed variant for game-specific campaigns."""

    def feed_url(self, obj: KickCategory) -> str:
        """Return Atom feed URL."""
        return reverse("kick:game_campaign_feed_atom", args=[obj.kick_id])


class KickOrganizationDiscordFeed(TTVDropsAtomBaseFeed, KickOrganizationFeed):
    """Discord (Atom) feed variant for organizations."""

    subtitle: str = KickOrganizationFeed.description

    def feed_url(self) -> str:
        """Return Discord feed URL."""
        return reverse("kick:organization_feed_discord")


class KickCategoryDiscordFeed(TTVDropsAtomBaseFeed, KickCategoryFeed):
    """Discord (Atom) feed variant for games."""

    subtitle: str = KickCategoryFeed.description

    def feed_url(self) -> str:
        """Return Discord feed URL."""
        return reverse("kick:game_feed_discord")


class KickCampaignDiscordFeed(TTVDropsAtomBaseFeed, KickCampaignFeed):
    """Discord (Atom) feed variant for campaigns with relative timestamps."""

    subtitle: str = KickCampaignFeed.description

    def item_description(self, item: KickDropCampaign) -> SafeText:
        """Return campaign summary HTML with Discord relative timestamps."""
        parts: list[SafeText] = []

        if item.image_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    item.image_url,
                    item.name,
                ),
            )

        parts.extend(
            (
                _campaign_dates_html(item, use_discord_relative=True),
                _campaign_rewards_html(item),
                _campaign_channels_html(item),
                _campaign_links_html(item),
            ),
        )

        return SafeText("".join(str(p) for p in parts if p))

    def feed_url(self) -> str:
        """Return Discord feed URL."""
        return reverse("kick:campaign_feed_discord")


class KickCategoryCampaignDiscordFeed(TTVDropsAtomBaseFeed, KickCategoryCampaignFeed):
    """Discord (Atom) feed variant for game-specific campaigns with relative timestamps."""

    def item_description(self, item: KickDropCampaign) -> SafeText:
        """Return campaign summary HTML with Discord relative timestamps."""
        parts: list[SafeText] = []

        if item.image_url:
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    item.image_url,
                    item.name,
                ),
            )

        parts.extend(
            (
                _campaign_dates_html(item, use_discord_relative=True),
                _campaign_rewards_html(item),
                _campaign_channels_html(item),
                _campaign_links_html(item),
            ),
        )

        return SafeText("".join(str(p) for p in parts if p))

    def feed_url(self, obj: KickCategory) -> str:
        """Return Discord feed URL."""
        return reverse("kick:game_campaign_feed_discord", args=[obj.kick_id])
