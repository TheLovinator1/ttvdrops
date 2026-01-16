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
from twitch.models import ChatBadge
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    import datetime

    from django.db.models import Model
    from django.db.models import QuerySet
    from django.http import HttpRequest

logger: logging.Logger = logging.getLogger("ttvdrops")


def insert_date_info(item: Model, parts: list[SafeText]) -> None:
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


def _build_drops_data(drops_qs: QuerySet[TimeBasedDrop]) -> list[dict]:
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


def _build_channels_html(channels: QuerySet[Channel], game: Game | None) -> SafeText:
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
        if getattr(game, "details_url", "") == "https://help.twitch.tv/s/article/twitch-chat-badges-guide ":
            # TODO(TheLovinator): Improve detection of global emotes  # noqa: TD003
            return format_html("{}", "<ul><li>Global Twitch Emote?</li></ul>")

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


def _get_channel_name_from_drops(drops: QuerySet[TimeBasedDrop]) -> str | None:
    for d in drops:
        campaign: DropCampaign | None = getattr(d, "campaign", None)
        if campaign:
            allow_channels: QuerySet[Channel] | None = getattr(campaign, "allow_channels", None)
            if allow_channels:
                channels: QuerySet[Channel, Channel] = allow_channels.all()
                if channels:
                    return channels[0].name
    return None


def get_channel_from_benefit(benefit: Model) -> str | None:
    """Get the Twitch channel name associated with a drop benefit.

    Args:
        benefit (Model): The drop benefit model instance.

    Returns:
        str | None: The Twitch channel name if found, else None.
    """
    drop_obj: QuerySet[TimeBasedDrop] | None = getattr(benefit, "drops", None)
    if drop_obj and hasattr(drop_obj, "all"):
        try:
            return _get_channel_name_from_drops(drop_obj.all())
        except AttributeError:
            logger.exception("Exception occurred while resolving channel name for benefit")
    return None


def _resolve_channel_name(drop: dict) -> str | None:
    """Try to resolve the Twitch channel name for a drop dict's benefits or fallback keys.

    Args:
        drop (dict): The drop data dictionary.

    Returns:
        str | None: The Twitch channel name if found, else None.
    """
    benefits: list[Model] = drop.get("benefits", [])
    benefit0: Model | None = benefits[0] if benefits else None
    if benefit0 and hasattr(benefit0, "drops"):
        channel_name: str | None = get_channel_from_benefit(benefit0)
        if channel_name:
            return channel_name
    return None


def _construct_drops_summary(drops_data: list[dict]) -> SafeText:
    """Construct a safe HTML summary of drops and their benefits.

    If the requirements indicate a subscription is required, link the benefit names to the Twitch channel.

    Args:
        drops_data (list[dict]): List of drop data dicts.

    Returns:
        SafeText: A single safe HTML line summarizing all drops, or empty SafeText if none.
    """
    if not drops_data:
        return SafeText("")

    badge_titles: set[str] = set()
    for drop in drops_data:
        for b in drop.get("benefits", []):
            if getattr(b, "distribution_type", "") == "BADGE" and getattr(b, "name", ""):
                badge_titles.add(b.name)

    badge_descriptions_by_title: dict[str, str] = {}
    if badge_titles:
        badge_descriptions_by_title = dict(
            ChatBadge.objects.filter(title__in=badge_titles).values_list("title", "description"),
        )

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
        channel_name: str | None = _resolve_channel_name(drop)
        is_sub_required: bool = "sub required" in requirements or "subs required" in requirements
        benefit_names: list[tuple[str]] = []
        for b in benefits:
            benefit_name: str = getattr(b, "name", str(b))
            badge_desc: str | None = badge_descriptions_by_title.get(benefit_name)
            if is_sub_required and channel_name:
                linked_name: SafeString = format_html(
                    '<a href="https://twitch.tv/{}" target="_blank">{}</a>',
                    channel_name,
                    benefit_name,
                )
                if badge_desc:
                    benefit_names.append((format_html("{} (<em>{}</em>)", linked_name, badge_desc),))
                else:
                    benefit_names.append((linked_name,))
            elif badge_desc:
                benefit_names.append((format_html("{} (<em>{}</em>)", benefit_name, badge_desc),))
            else:
                benefit_names.append((benefit_name,))
        benefits_str: SafeString = format_html_join(", ", "{}", benefit_names) if benefit_names else SafeText("")
        if requirements:
            items.append(format_html("<li>{}: {}</li>", requirements, benefits_str))
        else:
            items.append(format_html("<li>{}</li>", benefits_str))
    return format_html("<ul>{}</ul>", format_html_join("", "{}", [(item,) for item in items]))


# MARK: /rss/organizations/
class OrganizationFeed(Feed):
    """RSS feed for latest organizations."""

    title: str = "TTVDrops Organizations"
    link: str = "/organizations/"
    description: str = "Latest organizations on TTVDrops"
    feed_copyright: str = "Information wants to be free."

    def items(self) -> list[Organization]:
        """Return the latest 200 organizations."""
        return list(Organization.objects.order_by("-added_at")[:200])

    def item_title(self, item: Model) -> SafeText:
        """Return the organization name as the item title."""
        if not isinstance(item, Organization):
            logger.error("item_title called with non-Organization item: %s", type(item))
            return SafeText("New Twitch organization added")

        return SafeText(getattr(item, "name", str(item)))

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the organization."""
        if not isinstance(item, Organization):
            logger.error("item_description called with non-Organization item: %s", type(item))
            return SafeText("No description available.")

        description_parts: list[SafeText] = []

        name: str = getattr(item, "name", "")
        twitch_id: str = getattr(item, "twitch_id", "")

        # Link to ttvdrops organization page
        description_parts.extend((
            SafeText("<p>New Twitch organization added to TTVDrops:</p>"),
            SafeText(
                f"<p><a href='{reverse('twitch:organization_detail', args=[twitch_id])}' target='_blank' rel='noopener noreferrer'>{name}</a></p>",  # noqa: E501
            ),
        ))
        return SafeText("".join(str(part) for part in description_parts))

    def item_link(self, item: Model) -> str:
        """Return the link to the organization detail."""
        if not isinstance(item, Organization):
            logger.error("item_link called with non-Organization item: %s", type(item))
            return reverse("twitch:dashboard")

        return reverse("twitch:organization_detail", args=[item.twitch_id])

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to added_at or now if missing.
        """
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
        return timezone.now()

    def item_updateddate(self, item: Model) -> datetime.datetime:
        """Returns the organization's last update time."""
        updated_at: datetime.datetime | None = getattr(item, "updated_at", None)
        if updated_at:
            return updated_at
        return timezone.now()

    def item_guid(self, item: Model) -> str:
        """Return a unique identifier for each organization."""
        twitch_id: str = getattr(item, "twitch_id", "unknown")
        return twitch_id + "@ttvdrops.com"

    def item_author_name(self, item: Model) -> str:
        """Return the author name for the organization."""
        if not isinstance(item, Organization):
            logger.error("item_author_name called with non-Organization item: %s", type(item))
            return "Twitch"

        return getattr(item, "name", "Twitch")


# MARK: /rss/games/
class GameFeed(Feed):
    """RSS feed for latest games."""

    title: str = "Games - TTVDrops"
    link: str = "/games/"
    description: str = "Latest games on TTVDrops"
    feed_copyright: str = "Information wants to be free."

    def items(self) -> list[Game]:
        """Return the latest 200 games."""
        return list(Game.objects.order_by("-added_at")[:200])

    def item_title(self, item: Model) -> SafeText:
        """Return the game name as the item title (SafeText for RSS)."""
        if not isinstance(item, Game):
            logger.error("item_title called with non-Game item: %s", type(item))
            return SafeText("New Twitch game added to TTVDrops")

        return SafeText(item.get_game_name)

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the game."""
        # Return all the information we have about the game
        if not isinstance(item, Game):
            logger.error("item_description called with non-Game item: %s", type(item))
            return SafeText("No description available.")

        twitch_id: str = getattr(item, "twitch_id", "")
        slug: str = getattr(item, "slug", "")
        name: str = getattr(item, "name", "")
        display_name: str = getattr(item, "display_name", "")
        box_art: str | None = getattr(item, "box_art", None)
        owner: Organization | None = getattr(item, "owner", None)

        description_parts: list[SafeText] = []

        game_name: str = display_name or name or slug or twitch_id
        game_owner: str = owner.name if owner else "Unknown Owner"

        if box_art:
            description_parts.append(
                SafeText(f"<img src='{box_art}' alt='Box Art for {game_name}' width='600' height='800' />"),
            )

        if slug:
            description_parts.append(
                SafeText(
                    f"<p><a href='https://www.twitch.tv/directory/game/{slug}' target='_blank' rel='noopener noreferrer'>{game_name} by {game_owner}</a></p>",  # noqa: E501
                ),
            )
        else:
            description_parts.append(SafeText(f"<p>{game_name} by {game_owner}</p>"))

        if twitch_id:
            description_parts.append(SafeText(f"<small>Twitch ID: {twitch_id}</small>"))

        return SafeText("".join(str(part) for part in description_parts))

    def item_link(self, item: Model) -> str:
        """Return the link to the game detail."""
        if not isinstance(item, Game):
            logger.error("item_link called with non-Game item: %s", type(item))
            return reverse("twitch:dashboard")

        return reverse("twitch:game_detail", args=[item.twitch_id])

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to added_at or now if missing.
        """
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
        return timezone.now()

    def item_updateddate(self, item: Model) -> datetime.datetime:
        """Returns the game's last update time."""
        updated_at: datetime.datetime | None = getattr(item, "updated_at", None)
        if updated_at:
            return updated_at
        return timezone.now()

    def item_guid(self, item: Model) -> str:
        """Return a unique identifier for each game."""
        twitch_id: str = getattr(item, "twitch_id", "unknown")
        return twitch_id + "@ttvdrops.com"

    def item_author_name(self, item: Model) -> str:
        """Return the author name for the game, typically the owner organization name."""
        owner: Organization | None = getattr(item, "owner", None)
        if owner and owner.name:
            return owner.name

        return "Twitch"

    def item_enclosure_url(self, item: Model) -> str:
        """Returns the URL of the game's box art for enclosure."""
        box_art: str | None = getattr(item, "box_art", None)
        if box_art:
            return box_art
        return ""

    def item_enclosure_length(self, item: Model) -> int:  # noqa: ARG002
        """Returns the length of the enclosure."""
        # TODO(TheLovinator): Track image size for proper length  # noqa: TD003

        return 0

    def item_enclosure_mime_type(self, item: Model) -> str:  # noqa: ARG002
        """Returns the MIME type of the enclosure."""
        # TODO(TheLovinator): Determine actual MIME type if needed  # noqa: TD003
        return "image/jpeg"


# MARK: /rss/campaigns/
class DropCampaignFeed(Feed):
    """RSS feed for latest drop campaigns."""

    title: str = "Twitch Drop Campaigns"
    link: str = "/campaigns/"
    description: str = "Latest Twitch drop campaigns on TTVDrops"
    feed_url: str = "/rss/campaigns/"
    feed_copyright: str = "Information wants to be free."

    def items(self) -> list[DropCampaign]:
        """Return the latest 200 drop campaigns ordered by most recent start date."""
        return list(
            DropCampaign.objects.select_related("game").order_by("-start_at")[:200],
        )

    def item_title(self, item: Model) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        game: Game | None = getattr(item, "game", None)
        game_name: str = getattr(game, "display_name", str(game)) if game else ""
        clean_name: str = getattr(item, "clean_name", str(item))
        return SafeText(f"{game_name}: {clean_name}")

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the campaign."""
        drops_data: list[dict] = []

        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_data = _build_drops_data(drops.select_related().prefetch_related("benefits").all())

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
        insert_date_info(item, parts)

        if drops_data:
            parts.append(format_html("<p>{}</p>", _construct_drops_summary(drops_data)))

        # Only show channels if drop is not subscription only
        if not getattr(item, "is_subscription_only", False):
            channels: QuerySet[Channel] | None = getattr(item, "allow_channels", None)
            if channels is not None:
                game: Game | None = getattr(item, "game", None)
                parts.append(_build_channels_html(channels, game=game))

        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            parts.append(format_html('<a href="{}">About</a>', details_url))

        return SafeText("".join(str(p) for p in parts))

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
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
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
class GameCampaignFeed(Feed):
    """RSS feed for the latest drop campaigns of a specific game."""

    feed_copyright: str = "Information wants to be free."

    def get_object(self, request: HttpRequest, twitch_id: str) -> Game:  # noqa: ARG002
        """Retrieve the Game instance for the given Twitch ID.

        Returns:
            Game: The corresponding Game object.
        """
        return Game.objects.get(twitch_id=twitch_id)

    def item_link(self, item: DropCampaign) -> str:
        """Return the link to the campaign detail."""
        return reverse("twitch:campaign_detail", args=[item.twitch_id])

    def title(self, obj: Game) -> str:
        """Return the feed title for the game campaigns."""
        return f"TTVDrops: {obj.display_name} Campaigns"

    def link(self, obj: Game) -> str:
        """Return the absolute URL to the game detail page."""
        return reverse("twitch:game_detail", args=[obj.twitch_id])

    def description(self, obj: Game) -> str:
        """Return a description for the feed."""
        return f"Latest drop campaigns for {obj.display_name}"

    def feed_url(self, obj: Game) -> str:
        """Return the URL to the RSS feed itself."""
        return reverse("twitch:game_campaign_feed", args=[obj.twitch_id])

    def items(self, obj: Game) -> list[DropCampaign]:
        """Return the latest 200 drop campaigns for this game, ordered by most recent start date."""
        return list(
            DropCampaign.objects.filter(game=obj).select_related("game").order_by("-start_at")[:200],
        )

    def item_title(self, item: Model) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        clean_name: str = getattr(item, "clean_name", str(item))
        return SafeText(clean_name)

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the campaign."""
        drops_data: list[dict] = []

        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_data = _build_drops_data(drops.select_related().prefetch_related("benefits").all())

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
        insert_date_info(item, parts)

        if drops_data:
            parts.append(format_html("<p>{}</p>", _construct_drops_summary(drops_data)))

        # Only show channels if drop is not subscription only
        if not getattr(item, "is_subscription_only", False):
            channels: QuerySet[Channel] | None = getattr(item, "allow_channels", None)
            if channels is not None:
                game: Game | None = getattr(item, "game", None)
                parts.append(_build_channels_html(channels, game=game))

        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            parts.append(format_html('<a href="{}">About</a>', details_url))

        account_link_url: str | None = getattr(item, "account_link_url", None)
        if account_link_url:
            parts.append(format_html(' | <a href="{}">Link Account</a>', account_link_url))

        return SafeText("".join(str(p) for p in parts))

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Uses start_at (when the drop starts). Fallback to added_at or now if missing.
        """
        start_at: datetime.datetime | None = getattr(item, "start_at", None)
        if start_at:
            return start_at
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
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


# MARK: /rss/organizations/<twitch_id>/campaigns/
class OrganizationCampaignFeed(Feed):
    """RSS feed for campaigns of a specific organization."""

    def get_object(self, request: HttpRequest, twitch_id: str) -> Organization:  # noqa: ARG002
        """Retrieve the Organization instance for the given Twitch ID.

        Returns:
            Organization: The corresponding Organization object.
        """
        return Organization.objects.get(twitch_id=twitch_id)

    def item_link(self, item: DropCampaign) -> str:
        """Return the link to the campaign detail."""
        return reverse("twitch:campaign_detail", args=[item.twitch_id])

    def title(self, obj: Organization) -> str:
        """Return the feed title for the organization's campaigns."""
        return f"TTVDrops: {obj.name} Campaigns"

    def link(self, obj: Organization) -> str:
        """Return the absolute URL to the organization detail page."""
        return reverse("twitch:organization_detail", args=[obj.twitch_id])

    def description(self, obj: Organization) -> str:
        """Return a description for the feed."""
        return f"Latest drop campaigns for organization {obj.name}"

    def items(self, obj: Organization) -> list[DropCampaign]:
        """Return the latest 200 drop campaigns for this organization, ordered by most recent start date."""
        return list(
            DropCampaign.objects.filter(game__owners=obj).select_related("game").order_by("-start_at")[:200],
        )

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        item_game: Game | None = getattr(item, "game", None)
        if item_game and item_game.display_name:
            return item_game.display_name

        return "Twitch"

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign."""
        return item.twitch_id + "@ttvdrops.com"

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

    def item_updateddate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the campaign's last update time."""
        return item.updated_at

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Uses start_at (when the drop starts). Fallback to added_at or now if missing.
        """
        start_at: datetime.datetime | None = getattr(item, "start_at", None)
        if start_at:
            return start_at
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
        return timezone.now()

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the campaign."""
        drops_data: list[dict] = []

        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_data = _build_drops_data(drops.select_related().prefetch_related("benefits").all())

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
        insert_date_info(item, parts)

        if drops_data:
            parts.append(format_html("<p>{}</p>", _construct_drops_summary(drops_data)))

        # Only show channels if drop is not subscription only
        if not getattr(item, "is_subscription_only", False):
            channels: QuerySet[Channel] | None = getattr(item, "allow_channels", None)
            if channels is not None:
                game: Game | None = getattr(item, "game", None)
                parts.append(_build_channels_html(channels, game=game))

        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            parts.append(format_html('<a href="{}">About</a>', details_url))

        return SafeText("".join(str(p) for p in parts))


# MARK: /rss/reward-campaigns/
class RewardCampaignFeed(Feed):
    """RSS feed for latest reward campaigns (Quest rewards)."""

    title: str = "Twitch Reward Campaigns (Quest Rewards)"
    link: str = "/campaigns/"
    description: str = "Latest Twitch reward campaigns (Quest rewards) on TTVDrops"
    feed_url: str = "/rss/reward-campaigns/"
    feed_copyright: str = "Information wants to be free."

    def items(self) -> list[RewardCampaign]:
        """Return the latest 200 reward campaigns."""
        return list(
            RewardCampaign.objects.select_related("game").order_by("-added_at")[:200],
        )

    def item_title(self, item: Model) -> SafeText:
        """Return the reward campaign name as the item title."""
        brand: str = getattr(item, "brand", "")
        name: str = getattr(item, "name", str(item))
        if brand:
            return SafeText(f"{brand}: {name}")
        return SafeText(name)

    def item_description(self, item: Model) -> SafeText:
        """Return a description of the reward campaign."""
        parts: list[SafeText] = []

        summary: str | None = getattr(item, "summary", None)
        if summary:
            parts.append(format_html("<p>{}</p>", summary))

        # Insert start and end date info (uses starts_at/ends_at instead of start_at/end_at)
        ends_at: datetime.datetime | None = getattr(item, "ends_at", None)
        starts_at: datetime.datetime | None = getattr(item, "starts_at", None)

        if starts_at or ends_at:
            start_part: SafeString = (
                format_html("Starts: {} ({})", starts_at.strftime("%Y-%m-%d %H:%M %Z"), naturaltime(starts_at))
                if starts_at
                else SafeText("")
            )
            end_part: SafeString = (
                format_html("Ends: {} ({})", ends_at.strftime("%Y-%m-%d %H:%M %Z"), naturaltime(ends_at))
                if ends_at
                else SafeText("")
            )
            if start_part and end_part:
                parts.append(format_html("<p>{}<br />{}</p>", start_part, end_part))
            elif start_part:
                parts.append(format_html("<p>{}</p>", start_part))
            elif end_part:
                parts.append(format_html("<p>{}</p>", end_part))

        is_sitewide: bool = getattr(item, "is_sitewide", False)
        if is_sitewide:
            parts.append(SafeText("<p><strong>This is a sitewide reward campaign</strong></p>"))
        else:
            game: Game | None = getattr(item, "game", None)
            if game:
                parts.append(format_html("<p>Game: {}</p>", game.display_name or game.name))

        about_url: str | None = getattr(item, "about_url", None)
        if about_url:
            parts.append(format_html('<p><a href="{}">Learn more</a></p>', about_url))

        external_url: str | None = getattr(item, "external_url", None)
        if external_url:
            parts.append(format_html('<p><a href="{}">Redeem reward</a></p>', external_url))

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: Model) -> str:
        """Return the link to the reward campaign (external URL or dashboard)."""
        external_url: str | None = getattr(item, "external_url", None)
        if external_url:
            return external_url
        return reverse("twitch:dashboard")

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Uses starts_at (when the reward starts). Fallback to added_at or now if missing.
        """
        starts_at: datetime.datetime | None = getattr(item, "starts_at", None)
        if starts_at:
            return starts_at
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
        return timezone.now()

    def item_updateddate(self, item: RewardCampaign) -> datetime.datetime:
        """Returns the reward campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: RewardCampaign) -> tuple[str, ...]:
        """Returns the associated game's name and brand as categories."""
        categories: list[str] = ["twitch", "rewards", "quests"]

        brand: str | None = getattr(item, "brand", None)
        if brand:
            categories.append(brand)

        item_game: Game | None = getattr(item, "game", None)
        if item_game:
            categories.append(item_game.get_game_name)

        return tuple(categories)

    def item_guid(self, item: RewardCampaign) -> str:
        """Return a unique identifier for each reward campaign."""
        return item.twitch_id + "@ttvdrops.com"

    def item_author_name(self, item: RewardCampaign) -> str:
        """Return the author name for the reward campaign."""
        brand: str | None = getattr(item, "brand", None)
        if brand:
            return brand

        item_game: Game | None = getattr(item, "game", None)
        if item_game and item_game.display_name:
            return item_game.display_name

        return "Twitch"
