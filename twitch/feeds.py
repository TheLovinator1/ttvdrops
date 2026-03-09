import logging
import re
from typing import TYPE_CHECKING
from typing import Literal

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.contrib.syndication.views import Feed
from django.db.models import Prefetch
from django.db.models.query import QuerySet
from django.urls import reverse
from django.utils import feedgenerator
from django.utils import timezone
from django.utils.html import format_html
from django.utils.html import format_html_join
from django.utils.safestring import SafeText

from twitch.models import Channel
from twitch.models import ChatBadge
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
    from django.http import HttpResponse
    from django.utils.safestring import SafeString

    from twitch.models import DropBenefit

logger: logging.Logger = logging.getLogger("ttvdrops")


def _with_campaign_related(queryset: QuerySet[DropCampaign]) -> QuerySet[DropCampaign]:
    """Apply related-selects/prefetches needed by feed rendering to avoid N+1 queries.

    Returns:
        QuerySet[DropCampaign]: Queryset with related data preloaded for feed rendering.
    """
    drops_prefetch: Prefetch = Prefetch(
        "time_based_drops",
        queryset=TimeBasedDrop.objects.prefetch_related("benefits"),
    )

    return queryset.select_related("game").prefetch_related(
        "game__owners",
        Prefetch(
            "allow_channels",
            queryset=Channel.objects.order_by("display_name"),
            to_attr="channels_ordered",
        ),
        drops_prefetch,
    )


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
            format_html(
                "Starts: {} ({})",
                start_at.strftime("%Y-%m-%d %H:%M %Z"),
                naturaltime(start_at),
            )
            if start_at
            else SafeText("")
        )
        end_part: SafeString = (
            format_html(
                "Ends: {} ({})",
                end_at.strftime("%Y-%m-%d %H:%M %Z"),
                naturaltime(end_at),
            )
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


def _build_channels_html(
    channels: list[Channel] | QuerySet[Channel],
    game: Game | None,
) -> SafeText:
    """Render up to max_links channel links as <li>, then a count of additional channels, or fallback to game category link.

    If only one channel and drop_requirements is '1 subscriptions required',
    merge the Twitch link with the '1 subs' row.

    Args:
        channels (list[Channel] | QuerySet[Channel]): The channels (already ordered).
        game (Game | None): The game object for fallback link.

    Returns:
        SafeText: HTML <ul> with up to max_links channel links, count of more, or fallback link.
    """
    max_links = 5
    channels_all: list[Channel] = (
        list(channels) if isinstance(channels, list) else list(channels.all())
    )
    total: int = len(channels_all)

    if channels_all:
        items: list[SafeString] = [
            format_html(
                '<li><a href="https://twitch.tv/{}" title="Watch {} on Twitch">{}</a></li>',
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
        logger.warning(
            "No game associated with drop campaign for channel fallback link",
        )
        return format_html(
            "{}",
            "<ul><li>Drop has no game and no channels connected to the drop.</li></ul>",
        )

    if not game.twitch_directory_url:
        logger.warning(
            "Game %s has no Twitch directory URL for channel fallback link",
            game,
        )
        if (
            getattr(game, "details_url", "")
            == "https://help.twitch.tv/s/article/twitch-chat-badges-guide "
        ):
            # TODO(TheLovinator): Improve detection of global emotes  # noqa: TD003
            return format_html("{}", "<ul><li>Global Twitch Emote?</li></ul>")

        return format_html(
            "{}",
            "<ul><li>Failed to get Twitch category URL :(</li></ul>",
        )

    display_name: str = getattr(game, "display_name", "this game")
    return format_html(
        '<ul><li><a href="{}" title="Browse {} category">Category-wide for {}</a></li></ul>',
        game.twitch_directory_url,
        display_name,
        display_name,
    )


def _construct_drops_summary(
    drops_data: list[dict],
    channel_name: str | None = None,
) -> SafeText:
    """Construct a safe HTML summary of drops and their benefits.

    If the requirements indicate a subscription is required, link the benefit
    names to the Twitch channel.

    Args:
        drops_data (list[dict]): List of drop data dicts.
        channel_name (str | None): Optional channel name to link benefit names to on Twitch.

    Returns:
        SafeText: A single safe HTML line summarizing all drops, or empty SafeText if none.
    """
    if not drops_data:
        return SafeText("")

    badge_titles: set[str] = set()
    for drop in drops_data:
        for b in drop.get("benefits", []):
            if getattr(b, "distribution_type", "") == "BADGE" and getattr(
                b,
                "name",
                "",
            ):
                badge_titles.add(b.name)

    badge_descriptions_by_title: dict[str, str] = {}
    if badge_titles:
        badge_descriptions_by_title = dict(
            ChatBadge.objects.filter(title__in=badge_titles).values_list(
                "title",
                "description",
            ),
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
        is_sub_required: bool = (
            "sub required" in requirements or "subs required" in requirements
        )
        benefit_names: list[tuple[str]] = []
        for b in benefits:
            benefit_name: str = getattr(b, "name", str(b))
            badge_desc: str | None = badge_descriptions_by_title.get(benefit_name)
            if is_sub_required and channel_name:
                linked_name: SafeString = format_html(
                    '<a href="https://twitch.tv/{}" >{}</a>',
                    channel_name,
                    benefit_name,
                )
                if badge_desc:
                    benefit_names.append((
                        format_html("{} (<em>{}</em>)", linked_name, badge_desc),
                    ))
                else:
                    benefit_names.append((linked_name,))
            elif badge_desc:
                benefit_names.append((
                    format_html("{} (<em>{}</em>)", benefit_name, badge_desc),
                ))
            else:
                benefit_names.append((benefit_name,))
        benefits_str: SafeString = (
            format_html_join(", ", "{}", benefit_names)
            if benefit_names
            else SafeText("")
        )
        if requirements:
            items.append(format_html("<li>{}: {}</li>", requirements, benefits_str))
        else:
            items.append(format_html("<li>{}</li>", benefits_str))
    return format_html(
        "<ul>{}</ul>",
        format_html_join("", "{}", [(item,) for item in items]),
    )


# MARK: /rss/organizations/
class OrganizationRSSFeed(Feed):
    """RSS feed for latest organizations."""

    feed_type = feedgenerator.Rss201rev2Feed
    title: str = "TTVDrops Twitch Organizations"
    link: str = "/organizations/"
    description: str = "Latest organizations on TTVDrops"
    feed_copyright: str = "Information wants to be free."
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        """Override to capture limit parameter from request.

        Args:
            request (HttpRequest): The incoming HTTP request, potentially containing a 'limit' query parameter.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: The HTTP response generated by the parent Feed class after processing the request.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except ValueError, TypeError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> QuerySet[Organization]:
        """Return the latest organizations (default 200, or limited by ?limit query param)."""
        limit: int = self._limit if self._limit is not None else 200
        return Organization.objects.order_by("-added_at")[:limit]

    def item_title(self, item: Organization) -> SafeText:
        """Return the organization name as the item title."""
        return SafeText(getattr(item, "name", str(item)))

    def item_description(self, item: Organization) -> SafeText:
        """Return a description of the organization."""
        return SafeText(item.feed_description)

    def item_link(self, item: Organization) -> str:
        """Return the link to the organization detail."""
        return reverse("twitch:organization_detail", args=[item.twitch_id])

    def item_pubdate(self, item: Organization) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to added_at or now if missing.
        """
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
        return timezone.now()

    def item_updateddate(self, item: Organization) -> datetime.datetime:
        """Returns the organization's last update time."""
        updated_at: datetime.datetime | None = getattr(item, "updated_at", None)
        if updated_at:
            return updated_at
        return timezone.now()

    def item_author_name(self, item: Organization) -> str:
        """Return the author name for the organization."""
        return getattr(item, "name", "Twitch")


# MARK: /rss/games/
class GameFeed(Feed):
    """RSS feed for newly added games."""

    title: str = "Games - TTVDrops"
    link: str = "/games/"
    description: str = "Newly added games on TTVDrops"
    feed_copyright: str = "Information wants to be free."
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        """Override to capture limit parameter from request.

        Args:
            request (HttpRequest): The incoming HTTP request, potentially containing a 'limit' query parameter.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: The HTTP response generated by the parent Feed class after processing the request.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except ValueError, TypeError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> list[Game]:
        """Return the latest games (default 20, or limited by ?limit query param)."""
        limit: int = self._limit if self._limit is not None else 20
        return list(
            Game.objects.prefetch_related("owners").order_by("-added_at")[:limit],
        )

    def item_title(self, item: Game) -> SafeText:
        """Return the game name as the item title."""
        return SafeText(item.get_game_name)

    def item_description(self, item: Game) -> SafeText:
        """Return a description of the game."""
        twitch_id: str = getattr(item, "twitch_id", "")
        slug: str = getattr(item, "slug", "")
        name: str = getattr(item, "name", "")
        display_name: str = getattr(item, "display_name", "")
        box_art: str = item.box_art_best_url
        owner: Organization | None = item.owners.first()

        description_parts: list[SafeText] = []

        game_name: str = display_name or name or slug or twitch_id
        game_owner: str = owner.name if owner else "Unknown Owner"

        if box_art:
            description_parts.append(
                SafeText(
                    f"<img src='{box_art}' alt='Box Art for {game_name}' width='600' height='800' />",
                ),
            )

        # Get the full URL for TTVDrops game detail page
        game_url: str = reverse("twitch:game_detail", args=[twitch_id])
        rss_feed_url: str = reverse("twitch:game_campaign_feed", args=[twitch_id])
        twitch_directory_url: str = getattr(item, "twitch_directory_url", "")

        description_parts.append(
            SafeText(
                f"<p>New game has been added to ttvdrops.lovinator.space: {game_name} by {game_owner}\n\n"
                f"<a href='{game_url}'>[Details]</a> "
                f"<a href='{twitch_directory_url}'>[Twitch]</a> "
                f"<a href='{rss_feed_url}'>[RSS feed]</a>\n</p>",
            ),
        )

        return SafeText("".join(str(part) for part in description_parts))

    def item_link(self, item: Game) -> str:
        """Return the link to the game detail."""
        return reverse("twitch:game_detail", args=[item.twitch_id])

    def item_pubdate(self, item: Game) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to added_at or now if missing.
        """
        added_at: datetime.datetime | None = getattr(item, "added_at", None)
        if added_at:
            return added_at
        return timezone.now()

    def item_updateddate(self, item: Game) -> datetime.datetime:
        """Returns the game's last update time."""
        updated_at: datetime.datetime | None = getattr(item, "updated_at", None)
        if updated_at:
            return updated_at
        return timezone.now()

    def item_guid(self, item: Game) -> str:
        """Return a unique identifier for each game."""
        twitch_id: str = getattr(item, "twitch_id", "unknown")
        return twitch_id + "@ttvdrops.com"

    def item_author_name(self, item: Game) -> str:
        """Return the author name for the game, typically the owner organization name."""
        owner: Organization | None = item.owners.first()
        if owner and owner.name:
            return owner.name

        return "Twitch"

    def item_enclosure_url(self, item: Game) -> str:
        """Returns the URL of the game's box art for enclosure."""
        box_art: str = item.box_art_best_url
        if box_art:
            return box_art
        return ""

    def item_enclosure_length(self, item: Game) -> int:  # noqa: ARG002
        """Returns the length of the enclosure."""
        # TODO(TheLovinator): Track image size for proper length  # noqa: TD003

        return 0

    def item_enclosure_mime_type(self, item: Game) -> str:  # noqa: ARG002
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
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        """Override to capture limit parameter from request.

        Args:
            request (HttpRequest): The incoming HTTP request, potentially containing a 'limit' query parameter.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: The HTTP response generated by the parent Feed class after processing the request.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except ValueError, TypeError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> list[DropCampaign]:
        """Return the latest drop campaigns ordered by most recent start date (default 200, or limited by ?limit query param)."""
        limit: int = self._limit if self._limit is not None else 200
        queryset: QuerySet[DropCampaign] = DropCampaign.objects.order_by("-start_at")
        return list(_with_campaign_related(queryset)[:limit])

    def item_title(self, item: DropCampaign) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        return SafeText(item.get_feed_title())

    def item_description(self, item: DropCampaign) -> SafeText:
        """Return a description of the campaign."""
        drops_data: list[dict] = []
        channels: list[Channel] | None = getattr(item, "channels_ordered", None)
        channel_name: str | None = channels[0].name if channels else None

        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_data = _build_drops_data(drops.all())

        parts: list[SafeText] = []

        image_url: str | None = getattr(item, "image_url", None)
        if image_url:
            item_name: str = getattr(item, "name", str(object=item))
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    image_url,
                    item_name,
                ),
            )

        desc_text: str | None = getattr(item, "description", None)
        if desc_text:
            parts.append(format_html("<p>{}</p>", desc_text))

        # Insert start and end date info
        insert_date_info(item, parts)

        if drops_data:
            parts.append(
                format_html(
                    "<p>{}</p>",
                    _construct_drops_summary(drops_data, channel_name=channel_name),
                ),
            )

        # Only show channels if drop is not subscription only
        if not getattr(item, "is_subscription_only", False) and channels is not None:
            game: Game | None = getattr(item, "game", None)
            parts.append(_build_channels_html(channels, game=game))

        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            parts.append(format_html('<a href="{}">About</a>', details_url))

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: DropCampaign) -> str:
        """Return the link to the campaign detail."""
        return item.get_feed_link()

    def item_pubdate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to updated_at or now if missing.
        """
        return item.get_feed_pubdate()

    def item_updateddate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: DropCampaign) -> tuple[str, ...]:
        """Returns the associated game's name as a category."""
        return item.get_feed_categories()

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign."""
        return item.get_feed_guid()

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        return item.get_feed_author_name()

    def item_enclosure_url(self, item: DropCampaign) -> str:
        """Returns the URL of the campaign image for enclosure."""
        return item.get_feed_enclosure_url()

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
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        """Override to capture limit parameter from request.

        Args:
            request (HttpRequest): The incoming HTTP request, potentially containing a 'limit' query parameter
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: The HTTP response generated by the parent Feed class after processing the request.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except ValueError, TypeError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

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
        """Return the latest drop campaigns for this game, ordered by most recent start date (default 200, or limited by ?limit query param)."""
        limit: int = self._limit if self._limit is not None else 200
        queryset: QuerySet[DropCampaign] = DropCampaign.objects.filter(
            game=obj,
        ).order_by("-start_at")
        return list(_with_campaign_related(queryset)[:limit])

    def item_title(self, item: DropCampaign) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        return SafeText(item.get_feed_title())

    def item_description(self, item: DropCampaign) -> SafeText:
        """Return a description of the campaign."""
        drops_data: list[dict] = []
        channels: list[Channel] | None = getattr(item, "channels_ordered", None)
        channel_name: str | None = channels[0].name if channels else None

        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_data = _build_drops_data(drops.all())

        parts: list[SafeText] = []

        image_url: str | None = getattr(item, "image_url", None)
        if image_url:
            item_name: str = getattr(item, "name", str(object=item))
            parts.append(
                format_html(
                    '<img src="{}" alt="{}" width="160" height="160" />',
                    image_url,
                    item_name,
                ),
            )

        desc_text: str | None = getattr(item, "description", None)
        if desc_text:
            parts.append(format_html("<p>{}</p>", desc_text))

        # Insert start and end date info
        insert_date_info(item, parts)

        if drops_data:
            parts.append(
                format_html(
                    "<p>{}</p>",
                    _construct_drops_summary(drops_data, channel_name=channel_name),
                ),
            )

        # Only show channels if drop is not subscription only
        if not getattr(item, "is_subscription_only", False) and channels is not None:
            game: Game | None = getattr(item, "game", None)
            parts.append(_build_channels_html(channels, game=game))

        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            parts.append(format_html('<a href="{}">About</a>', details_url))

        account_link_url: str | None = getattr(item, "account_link_url", None)
        if account_link_url:
            parts.append(
                format_html(' | <a href="{}">Link Account</a>', account_link_url),
            )

        return SafeText("".join(str(p) for p in parts))

    def item_pubdate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Uses start_at (when the drop starts). Fallback to added_at or now if missing.
        """
        if isinstance(item, DropCampaign):
            if item.start_at:
                return item.start_at
            if item.added_at:
                return item.added_at
        return timezone.now()

    def item_updateddate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: DropCampaign) -> tuple[str, ...]:
        """Returns the associated game's name as a category."""
        return item.get_feed_categories()

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign."""
        return item.get_feed_guid()

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        return item.get_feed_author_name()

    def item_enclosure_url(self, item: DropCampaign) -> str:
        """Returns the URL of the campaign image for enclosure."""
        return item.get_feed_enclosure_url()

    def item_enclosure_length(self, item: DropCampaign) -> int:  # noqa: ARG002
        """Returns the length of the enclosure."""
        # TODO(TheLovinator): Track image size for proper length  # noqa: TD003

        return 0

    def item_enclosure_mime_type(self, item: DropCampaign) -> str:  # noqa: ARG002
        """Returns the MIME type of the enclosure."""
        # TODO(TheLovinator): Determine actual MIME type if needed  # noqa: TD003
        return "image/jpeg"


# MARK: /rss/reward-campaigns/
class RewardCampaignFeed(Feed):
    """RSS feed for latest reward campaigns (Quest rewards)."""

    title: str = "Twitch Reward Campaigns (Quest Rewards)"
    link: str = "/campaigns/"
    description: str = "Latest Twitch reward campaigns (Quest rewards) on TTVDrops"
    feed_url: str = "/rss/reward-campaigns/"
    feed_copyright: str = "Information wants to be free."
    _limit: int | None = None

    def __call__(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        """Override to capture limit parameter from request.

        Args:
            request (HttpRequest): The incoming HTTP request, potentially containing a 'limit' query parameter.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            HttpResponse: The HTTP response generated by the parent Feed class after processing the request.
        """
        if request.GET.get("limit"):
            try:
                self._limit = int(request.GET.get("limit", 200))
            except ValueError, TypeError:
                self._limit = None
        return super().__call__(request, *args, **kwargs)

    def items(self) -> list[RewardCampaign]:
        """Return the latest reward campaigns (default 200, or limited by ?limit query param)."""
        limit: int = self._limit if self._limit is not None else 200
        return list(
            RewardCampaign.objects.select_related("game").order_by("-added_at")[:limit],
        )

    def item_title(self, item: RewardCampaign) -> SafeText:
        """Return the reward campaign name as the item title."""
        return SafeText(item.get_feed_title())

    def item_description(self, item: RewardCampaign) -> SafeText:
        """Return a description of the reward campaign."""
        return SafeText(item.get_feed_description())

    def item_link(self, item: RewardCampaign) -> str:
        """Return the link to the reward campaign (external URL or dashboard)."""
        return item.get_feed_link()

    def item_pubdate(self, item: RewardCampaign) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Uses starts_at (when the reward starts). Fallback to added_at or now if missing.
        """
        return item.get_feed_pubdate()

    def item_updateddate(self, item: RewardCampaign) -> datetime.datetime:
        """Returns the reward campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: RewardCampaign) -> tuple[str, ...]:
        """Returns the associated game's name and brand as categories."""
        return item.get_feed_categories()

    def item_guid(self, item: RewardCampaign) -> str:
        """Return a unique identifier for each reward campaign."""
        return item.get_feed_guid()

    def item_author_name(self, item: RewardCampaign) -> str:
        """Return the author name for the reward campaign."""
        return item.get_feed_author_name()
