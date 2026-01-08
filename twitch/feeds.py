from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from typing import Literal

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.contrib.syndication.views import Feed
from django.db.models.query import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.html import format_html_join
from django.utils.safestring import SafeString
from django.utils.safestring import SafeText

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    import datetime

    from django.db.models import Model
    from django.db.models import QuerySet
    from django.http import HttpRequest

logger: logging.Logger = logging.getLogger("ttvdrops")


# MARK: /rss/organizations/
class OrganizationFeed(Feed):
    """RSS feed for latest organizations."""

    title: str = "TTVDrops Organizations"
    link: str = "/organizations/"
    description: str = "Latest organizations on TTVDrops"

    def items(self) -> list[Organization]:
        """Return the latest 100 organizations."""
        return list(Organization.objects.order_by("-updated_at")[:100])

    def item_title(self, item: Model) -> SafeText:
        """Return the organization name as the item title."""
        return SafeText(getattr(item, "name", str(item)))

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the organization."""
        return SafeText(f"Organization {getattr(item, 'name', str(item))}")

    def item_link(self, item: Model) -> str:
        """Return the link to the organization detail."""
        return reverse("twitch:organization_detail", args=[item.pk])


# MARK: /rss/games/
class GameFeed(Feed):
    """RSS feed for latest games."""

    title: str = "TTVDrops Games"
    link: str = "/games/"
    description: str = "Latest games on TTVDrops"

    def items(self) -> list[Game]:
        """Return the latest 100 games."""
        return list(Game.objects.order_by("-id")[:100])

    def item_title(self, item: Model) -> SafeText:
        """Return the game name as the item title (SafeText for RSS)."""
        return SafeText(str(item))

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the game."""
        return SafeText(f"Game {getattr(item, 'display_name', str(item))}")

    def item_link(self, item: Model) -> str:
        """Return the link to the game detail."""
        return reverse("twitch:game_detail", args=[item.pk])


# MARK: /rss/campaigns/
class DropCampaignFeed(Feed):
    """RSS feed for latest drop campaigns."""

    def _get_channel_name_from_drops(self, drops: QuerySet[TimeBasedDrop]) -> str | None:
        for d in drops:
            campaign: DropCampaign | None = getattr(d, "campaign", None)
            if campaign:
                allow_channels: QuerySet[Channel] | None = getattr(campaign, "allow_channels", None)
                if allow_channels:
                    channels: QuerySet[Channel, Channel] = allow_channels.all()
                    if channels:
                        return channels[0].name
        return None

    def get_channel_from_benefit(self, benefit: Model) -> str | None:
        """Get the Twitch channel name associated with a drop benefit.

        Args:
            benefit (Model): The drop benefit model instance.

        Returns:
            str | None: The Twitch channel name if found, else None.
        """
        drop_obj: QuerySet[TimeBasedDrop] | None = getattr(benefit, "drops", None)
        if drop_obj and hasattr(drop_obj, "all"):
            try:
                return self._get_channel_name_from_drops(drop_obj.all())
            except AttributeError:
                logger.exception("Exception occurred while resolving channel name for benefit")
        return None

    def _resolve_channel_name(self, drop: dict) -> str | None:
        """Try to resolve the Twitch channel name for a drop dict's benefits or fallback keys.

        Args:
            drop (dict): The drop data dictionary.

        Returns:
            str | None: The Twitch channel name if found, else None.
        """
        benefits: list[Model] = drop.get("benefits", [])
        benefit0: Model | None = benefits[0] if benefits else None
        if benefit0 and hasattr(benefit0, "drops"):
            channel_name: str | None = self.get_channel_from_benefit(benefit0)
            if channel_name:
                return channel_name
        return None

    def _build_channels_html(self, channels: QuerySet[Channel], game: Game | None) -> SafeText:
        """Render up to max_links channel links as <li>, then a count of additional channels, or fallback to game category link.

        If only one channel and drop_requirements is '1 subscriptions required',
        merge the Twitch link with the '1 subs' row.

        Args:
            channels (QuerySet[Channel]): The queryset of channels.
            game (Game | None): The game object for fallback link.

        Returns:
            SafeText: HTML <ul> with up to max_links channel links, count of more, or fallback link.
        """  # noqa: E501
        max_links = 5
        channels_all: list[Channel] = list(channels.all())
        total: int = len(channels_all)

        if channels_all:
            items: list[SafeString] = [
                format_html(
                    "<li>"
                    '<a href="https://twitch.tv/{}" target="_blank" rel="noopener noreferrer"'
                    ' title="Watch {} on Twitch">{}</a>'
                    "</li>",
                    ch.name,
                    ch.display_name,
                    ch.display_name,
                )
                for ch in channels_all[:max_links]
            ]
            if total > max_links:
                items.append(format_html("<li>... and {} more</li>", total - max_links))

            return format_html(
                "<ul>{}</ul>",
                format_html_join("", "{}", [(item,) for item in items]),
            )

        if not game:
            logger.warning("No game associated with drop campaign for channel fallback link")
            return format_html("{}", "<ul><li>Drop has no game and no channels connected to the drop.</li></ul>")

        if not game.twitch_directory_url:
            logger.warning("Game %s has no Twitch directory URL for channel fallback link", game)
            return format_html("{}", "<ul><li>Failed to get Twitch category URL :(</li></ul>")

        # If no channel is associated, the drop is category-wide; link to the game's Twitch directory
        display_name: str = getattr(game, "display_name", "this game")
        return format_html(
            "<ul><li>"
            '<a href="{}" target="_blank" rel="noopener noreferrer"'
            ' title="Browse {} category">Category-wide for {}</a>'
            "</li></ul>",
            game.twitch_directory_url,
            display_name,
            display_name,
        )

    """RSS feed for latest drop campaigns."""

    title: str = "Twitch Drop Campaigns"
    link: str = "/campaigns/"
    description: str = "Latest Twitch drop campaigns"
    feed_url: str = "/rss/campaigns/"
    feed_copyright: str = "Information wants to be free."

    def items(self) -> list[DropCampaign]:
        """Return the latest 100 drop campaigns."""
        return list(DropCampaign.objects.select_related("game").order_by("-added_at")[:100])

    def item_title(self, item: Model) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        game: Game | None = getattr(item, "game", None)
        game_name: str = getattr(game, "display_name", str(game)) if game else ""
        clean_name: str = getattr(item, "clean_name", str(item))
        return SafeText(f"{game_name}: {clean_name}")

    def _build_drops_data(self, drops_qs: QuerySet[TimeBasedDrop]) -> list[dict]:
        """Build a simplified data structure for rendering drops in a template.

        Returns:
            list[dict]: A list of dictionaries each containing `name`, `benefits`,
            `requirements`, and `period` for a drop, suitable for template rendering.
        """
        drops_data: list[dict] = []
        for drop in drops_qs:
            requirements: str = ""
            required_minutes: int | None = getattr(drop, "required_minutes_watched", None)
            required_subs: int = getattr(drop, "required_subs", 0) or 0
            if required_minutes:
                requirements = f"{required_minutes} minutes watched"
            if required_subs > 0:
                sub_word: Literal["subs", "sub"] = "subs" if required_subs > 1 else "sub"
                if requirements:
                    requirements += f" and {required_subs} {sub_word} required"
                else:
                    requirements = f"{required_subs} {sub_word} required"

            period: str = ""
            drop_start: datetime.datetime | None = getattr(drop, "start_at", None)
            drop_end: datetime.datetime | None = getattr(drop, "end_at", None)
            if drop_start is not None:
                period += drop_start.strftime("%Y-%m-%d %H:%M %Z")
            if drop_end is not None:
                if period:
                    period += " - " + drop_end.strftime("%Y-%m-%d %H:%M %Z")
                else:
                    period = drop_end.strftime("%Y-%m-%d %H:%M %Z")

            drops_data.append({
                "name": getattr(drop, "name", str(drop)),
                "benefits": list(drop.benefits.all()),
                "requirements": requirements,
                "period": period,
            })
        return drops_data

    def _construct_drops_summary(self, drops_data: list[dict]) -> SafeText:
        """Construct a safe HTML summary of drops and their benefits.

        If the requirements indicate a subscription is required, link the benefit names to the Twitch channel.

        Args:
            drops_data (list[dict]): List of drop data dicts.

        Returns:
            SafeText: A single safe HTML line summarizing all drops, or empty SafeText if none.
        """
        if not drops_data:
            return SafeText("")

        def sort_key(drop: dict) -> tuple[bool, int]:
            req: str = drop.get("requirements", "")
            m: re.Match[str] | None = re.search(r"(\d+) minutes watched", req)
            minutes: int | None = int(m.group(1)) if m else None
            is_sub: bool = "sub required" in req or "subs required" in req
            return (is_sub, minutes if minutes is not None else 99999)

        sorted_drops: list[dict] = sorted(drops_data, key=sort_key)
        items: list[SafeText] = []
        for drop in sorted_drops:
            requirements: str = drop.get("requirements", "")
            benefits: list[DropBenefit] = drop.get("benefits", [])
            channel_name: str | None = self._resolve_channel_name(drop)
            is_sub_required: bool = "sub required" in requirements or "subs required" in requirements
            benefit_names: list[tuple[str]] = []
            for b in benefits:
                benefit_name: str = getattr(b, "name", str(b))
                if is_sub_required and channel_name:
                    benefit_names.append((
                        format_html(
                            '<a href="https://twitch.tv/{}" target="_blank">{}</a>',
                            channel_name,
                            benefit_name,
                        ),
                    ))
                else:
                    benefit_names.append((benefit_name,))
            benefits_str: SafeString = format_html_join(", ", "{}", benefit_names) if benefit_names else SafeText("")
            if requirements:
                items.append(format_html("<li>{}: {}</li>", requirements, benefits_str))
            else:
                items.append(format_html("<li>{}</li>", benefits_str))
        return format_html("<ul>{}</ul>", format_html_join("", "{}", [(item,) for item in items]))

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the campaign."""
        drops_data: list[dict] = []

        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_data = self._build_drops_data(drops.select_related().prefetch_related("benefits").all())

        parts: list[SafeText] = []

        image_url: str | None = getattr(item, "image_url", None)
        if image_url:
            item_name: str = getattr(item, "name", str(object=item))
            parts.append(
                format_html('<img src="{}" alt="{}" width="160" height="160" />', image_url, item_name),
            )

        desc_text: str | None = getattr(item, "description", None)
        if desc_text:
            parts.append(format_html("<p>{}</p>", desc_text))

        # Insert start and end date info
        self.insert_date_info(item, parts)

        if drops_data:
            parts.append(format_html("<p>{}</p>", self._construct_drops_summary(drops_data)))

        # Only show channels if drop is not subscription only
        if not getattr(item, "is_subscription_only", False):
            channels: QuerySet[Channel] | None = getattr(item, "allow_channels", None)
            if channels is not None:
                game: Game | None = getattr(item, "game", None)
                parts.append(self._build_channels_html(channels, game=game))

        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            parts.append(format_html('<a href="{}">About</a>', details_url))

        return SafeText("".join(str(p) for p in parts))

    def insert_date_info(self, item: Model, parts: list[SafeText]) -> None:
        """Insert start and end date information into parts list.

        Args:
            item (Model): The campaign item containing start_at and end_at.
            parts (list[SafeText]): The list of HTML parts to append to.
        """
        end_at: datetime.datetime | None = getattr(item, "end_at", None)
        start_at: datetime.datetime | None = getattr(item, "start_at", None)

        if start_at or end_at:
            start_part: SafeString = (
                format_html("Starts: {} ({})", start_at.strftime("%Y-%m-%d %H:%M %Z"), naturaltime(start_at))
                if start_at
                else SafeText("")
            )
            end_part: SafeString = (
                format_html("Ends: {} ({})", end_at.strftime("%Y-%m-%d %H:%M %Z"), naturaltime(end_at))
                if end_at
                else SafeText("")
            )
            # Start date and end date separated by a line break if both present
            if start_part and end_part:
                parts.append(format_html("<p>{}<br />{}</p>", start_part, end_part))
            elif start_part:
                parts.append(format_html("<p>{}</p>", start_part))
            elif end_part:
                parts.append(format_html("<p>{}</p>", end_part))

    def item_link(self, item: Model) -> str:
        """Return the link to the campaign detail."""
        if not isinstance(item, DropCampaign):
            logger.error("item_link called with non-DropCampaign item: %s", type(item))
            return reverse("twitch:dashboard")

        return reverse("twitch:campaign_detail", args=[item.twitch_id])

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to updated_at or now if missing.
        """
        start_at: datetime.datetime | None = getattr(item, "start_at", None)
        if start_at:
            return start_at
        updated_at: datetime.datetime | None = getattr(item, "updated_at", None)
        if updated_at:
            return updated_at
        return timezone.now()

    def item_updateddate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: DropCampaign) -> tuple[str, ...]:
        """Returns the associated game's name as a category."""
        categories: list[str] = ["twitch", "drops"]

        item_game: Game | None = getattr(item, "game", None)
        if item_game:
            categories.append(item_game.get_game_name)
            item_game_owner: Organization | None = getattr(item_game, "owner", None)
            if item_game_owner:
                categories.extend((str(item_game_owner.name), str(item_game_owner.twitch_id)))

        return tuple(categories)

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign."""
        return item.twitch_id + "@ttvdrops.com"

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        item_game: Game | None = getattr(item, "game", None)
        if item_game and item_game.display_name:
            return item_game.display_name

        return "Twitch"

    def item_enclosure_url(self, item: DropCampaign) -> str:
        """Returns the URL of the campaign image for enclosure."""
        return item.image_url

    def item_enclosure_length(self, item: DropCampaign) -> int:  # noqa: ARG002
        """Returns the length of the enclosure."""
        # TODO(TheLovinator): Track image size for proper length  # noqa: TD003

        return 0

    def item_enclosure_mime_type(self, item: DropCampaign) -> str:  # noqa: ARG002
        """Returns the MIME type of the enclosure."""
        # TODO(TheLovinator): Determine actual MIME type if needed  # noqa: TD003
        return "image/jpeg"


# MARK: /rss/games/<twitch_id>/campaigns/
class GameCampaignFeed(DropCampaignFeed):
    """RSS feed for campaigns of a specific game."""

    def get_object(self, request: HttpRequest, twitch_id: str) -> Game:  # noqa: ARG002
        """Get the game object for this feed.

        Args:
            request: The HTTP request.
            twitch_id: The Twitch ID of the game.

        Returns:
            Game: The game object.
        """
        return Game.objects.get(twitch_id=twitch_id)

    def title(self, obj: Game) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the feed title."""
        return f"TTVDrops: {obj.display_name} Campaigns"

    def link(self, obj: Game) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the link to the game detail."""
        return reverse("twitch:game_detail", args=[obj.twitch_id])

    def description(self, obj: Game) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the feed description."""
        return f"Latest drop campaigns for {obj.display_name}"

    def items(self, obj: Game) -> list[DropCampaign]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Return the latest 100 campaigns for this game."""
        return list(DropCampaign.objects.filter(game=obj).select_related("game").order_by("-added_at")[:100])


# MARK: /rss/organizations/<twitch_id>/campaigns/
class OrganizationCampaignFeed(DropCampaignFeed):
    """RSS feed for campaigns of a specific organization."""

    def get_object(self, request: HttpRequest, twitch_id: str) -> Organization:  # noqa: ARG002
        """Get the organization object for this feed.

        Args:
            request: The HTTP request.
            twitch_id: The Twitch ID of the organization.

        Returns:
            Organization: The organization object.
        """
        return Organization.objects.get(twitch_id=twitch_id)

    def title(self, obj: Organization) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the feed title."""
        return f"TTVDrops: {obj.name} Campaigns"

    def link(self, obj: Organization) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the link to the organization detail."""
        return reverse("twitch:organization_detail", args=[obj.twitch_id])

    def description(self, obj: Organization) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the feed description."""
        return f"Latest drop campaigns for {obj.name}"

    def items(self, obj: Organization) -> list[DropCampaign]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Return the latest 100 campaigns for this organization."""
        return list(DropCampaign.objects.filter(game__owner=obj).select_related("game").order_by("-added_at")[:100])
