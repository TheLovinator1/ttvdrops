import logging
import re
from typing import TYPE_CHECKING
from typing import Literal

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.contrib.syndication.views import Feed
from django.db.models import Prefetch
from django.db.models.query import QuerySet
from django.http.request import HttpRequest
from django.templatetags.static import static
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
RSS_STYLESHEETS: list[str] = [static("rss_styles.xslt")]


class BrowserFriendlyRss201rev2Feed(feedgenerator.Rss201rev2Feed):
    """RSS 2.0 feed generator with a browser-renderable XML content type."""

    content_type = "application/xml; charset=utf-8"


class BrowserFriendlyAtom1Feed(feedgenerator.Atom1Feed):
    """Atom 1.0 feed generator with an explicit browser-friendly content type."""

    content_type = "application/xml; charset=utf-8"


class TTVDropsBaseFeed(Feed):
    """Base feed class that keeps XML feeds browser-friendly.

    By default, Django's syndication feed framework serves feeds with a
    content type of "application/rss+xml", which causes browsers to
    download the feed as a file instead of displaying it. By overriding
    the __call__ method to set the Content-Disposition header to "inline",
    we can make browsers display the feed content directly, improving the
    user experience when visiting feed URLs in a browser.
    """

    feed_type = BrowserFriendlyRss201rev2Feed
    feed_copyright: str = "CC0; Information wants to be free."
    stylesheets: list[str] = RSS_STYLESHEETS
    ttl: int = 1
    _request: HttpRequest | None = None

    def _absolute_url(self, url: str) -> str:
        """Build an absolute URL for feed identifiers when request context exists.

        Args:
            url (str): Relative or absolute URL to normalize for feed metadata.

        Returns:
            str: Absolute URL when request context exists, otherwise the original URL.
        """
        if self._request is None:
            return url
        return self._request.build_absolute_uri(url)

    def _absolute_stylesheet_urls(self, request: HttpRequest) -> list[str]:
        """Return stylesheet URLs as absolute HTTP URLs for browser/file compatibility."""
        return [
            href
            if href.startswith(("http://", "https://"))
            else request.build_absolute_uri(href)
            for href in self.stylesheets
        ]

    def _inject_atom_stylesheets(self, response: HttpResponse) -> None:
        """Inject xml-stylesheet processing instructions for Atom feeds.

        Django emits stylesheet processing instructions for RSS feeds, but not for
        Atom feeds. Browsers then show Atom summaries as escaped HTML text. By
        injecting stylesheet PIs into Atom XML responses, we can transform Atom in
        the browser with the same XSLT used for RSS.
        """
        if not self.stylesheets:
            return

        encoding: str = response.charset or "utf-8"
        content: str = response.content.decode(encoding)

        # Detect Atom payload by XML structure/namespace so this still works even
        # when served as application/xml for browser-friendliness.
        if "<feed" not in content or "http://www.w3.org/2005/Atom" not in content:
            return

        if "<?xml-stylesheet" in content:
            return

        stylesheet_pis: str = "".join(
            f'<?xml-stylesheet href="{href}" type="text/xsl" media="screen"?>'
            for href in self.stylesheets
        )

        if content.startswith("<?xml"):
            xml_decl_end: int = content.find("?>")
            if xml_decl_end != -1:
                content = (
                    f"{content[: xml_decl_end + 2]}{stylesheet_pis}"
                    f"{content[xml_decl_end + 2 :]}"
                )
            else:
                content = f"{stylesheet_pis}{content}"
        else:
            content = f"{stylesheet_pis}{content}"

        response.content = content.encode(encoding)

    def __call__(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        """Return feed response with inline content disposition for browser display."""
        original_stylesheets: list[str] = self.stylesheets
        self.stylesheets = self._absolute_stylesheet_urls(request)
        self._request = request
        try:
            response: HttpResponse = super().__call__(request, *args, **kwargs)
            self._inject_atom_stylesheets(response)
        finally:
            self.stylesheets = original_stylesheets
            self._request = None
        response["Content-Disposition"] = "inline"
        return response


class TTVDropsAtomBaseFeed(TTVDropsBaseFeed):
    """Base class for Atom feeds with shared browser-friendly behavior."""

    feed_type = BrowserFriendlyAtom1Feed


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


def _active_drop_campaigns(queryset: QuerySet[DropCampaign]) -> QuerySet[DropCampaign]:
    """Filter a campaign queryset down to campaigns active right now.

    Returns:
        QuerySet[DropCampaign]: Queryset with only active drop campaigns.
    """
    now: datetime.datetime = timezone.now()
    return queryset.filter(start_at__lte=now, end_at__gte=now)


def _active_reward_campaigns(
    queryset: QuerySet[RewardCampaign],
) -> QuerySet[RewardCampaign]:
    """Filter a reward campaign queryset down to campaigns active right now.

    Returns:
        QuerySet[RewardCampaign]: Queryset with only active reward campaigns.
    """
    now: datetime.datetime = timezone.now()
    return queryset.filter(starts_at__lte=now, ends_at__gte=now)


def genereate_details_link_html(item: DropCampaign) -> list[SafeText]:
    """Helper method to append a details link to the description if available.

    Args:
        item (DropCampaign): The drop campaign item to check for a details URL.

    Returns:
        list[SafeText]: A list containing the formatted details link if a details URL is present, otherwise an empty list.
    """
    parts: list[SafeText] = []
    details_url: str | None = getattr(item, "details_url", None)
    if details_url:
        parts.append(format_html('<a href="{}">About</a>', details_url))
    return parts


def generate_item_image(item: DropCampaign) -> list[SafeText]:
    """Helper method to generate an image tag for the campaign image if available.

    Args:
        item (DropCampaign): The drop campaign item to check for an image URL.

    Returns:
        list[SafeText]: A list containing the formatted image tag if an image URL is present, otherwise an empty list.
    """
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

    return parts


def generate_item_image_tag(item: DropCampaign) -> list[SafeString]:
    """Helper method to append an image tag for the campaign image if available.

    Args:
        item (DropCampaign): The drop campaign item to check for an image URL.

    Returns:
        list[SafeString]: A list containing the formatted image tag if an image URL is present, otherwise an empty list.
    """
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

    return parts


def generate_details_link(item: DropCampaign) -> list[SafeString]:
    """Helper method to append a details link to the description if available.

    Args:
        item (DropCampaign): The drop campaign item to check for a details URL.

    Returns:
        list[SafeString]: A list containing the formatted details link if a details URL is present, otherwise an empty list.
    """
    parts: list[SafeText] = []
    details_url: str | None = getattr(item, "details_url", None)
    if details_url:
        parts.append(format_html('<a href="{}">Details</a>', details_url))
    return parts


def generate_description_html(item: DropCampaign) -> list[SafeText]:
    """Generate additional description HTML for a drop campaign item, such as the description text and details link.

    Args:
        item (DropCampaign): The drop campaign item to generate description HTML for.

    Returns:
            list[SafeText]: A list of SafeText elements containing the generated description HTML.
    """
    parts: list[SafeText] = []
    desc_text: str | None = getattr(item, "description", None)
    if desc_text:
        parts.append(format_html("<p>{}</p>", desc_text))
    return parts


def generate_date_html(item: Model) -> list[SafeText]:
    """Generate HTML snippets for the start and end dates of a campaign item, formatted with both absolute and relative times.

    Args:
        item (Model): The campaign item containing start_at and end_at.

    Returns:
        list[SafeText]: A list of SafeText elements with formatted start and end date info.
    """
    parts: list[SafeText] = []
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

    return parts


def generate_drops_summary_html(item: DropCampaign) -> list[SafeString]:
    """Generate HTML summary for drops and append to parts list.

    Args:
        item (DropCampaign): The drop campaign item containing the drops to summarize.

    Returns:
        list[SafeString]: A list of SafeText elements summarizing the drops, or empty if no drops.
    """
    parts: list[SafeText] = []
    drops_data: list[dict] = []

    channels: list[Channel] | None = getattr(item, "channels_ordered", None)
    channel_name: str | None = channels[0].name if channels else None

    drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
    if drops:
        drops_data = _build_drops_data(drops.all())

    if drops_data:
        parts.append(
            format_html(
                "{}",
                _construct_drops_summary(drops_data, channel_name=channel_name),
            ),
        )

    return parts


def generate_channels_html(item: Model) -> list[SafeText]:
    """Generate HTML for the list of channels associated with a drop campaign, if applicable.

    Only generates channel links if the drop is not subscription-only. If it is subscription-only, it will skip channel links to avoid confusion.

    Args:
        item (Model): The campaign item which may have an 'is_subscription_only' attribute.

    Returns:
        list[SafeText]: A list containing the HTML for the channels section, or empty if subscription-only or no channels.
    """
    max_links = 5
    parts: list[SafeText] = []

    channels: list[Channel] | None = getattr(item, "channels_ordered", None)
    if not channels:
        return parts

    if getattr(item, "is_subscription_only", False):
        return parts

    game: Game | None = getattr(item, "game", None)

    channels_all: list[Channel] = (
        list(channels)
        if isinstance(channels, list)
        else list(channels.all())
        if channels is not None
        else []
    )
    total: int = len(channels_all)

    if channels_all:
        create_channel_list_html(max_links, parts, channels_all, total)
        return parts

    if not game:
        logger.warning(
            "No game associated with drop campaign for channel fallback link",
        )
        parts.append(
            format_html(
                "{}",
                "<ul><li>Drop has no game and no channels connected to the drop.</li></ul>",
            ),
        )

        return parts

    if not game.twitch_directory_url:
        logger.warning(
            "Game %s has no Twitch directory URL for channel fallback link",
            game,
        )

        if "twitch-chat-badges-guide" in getattr(game, "details_url", ""):
            # TODO(TheLovinator): Improve detection of global emotes  # noqa: TD003
            parts.append(
                format_html(
                    "{}",
                    "<ul><li>Game is Twitch chat badges guide, likely meaning these are global Twitch emotes?</li></ul>",
                ),
            )
            return parts

        parts.append(
            format_html(
                "{}",
                "<ul><li>Game has no Twitch directory URL for channel fallback link.</li></ul>",
            ),
        )
        return parts

    display_name: str = getattr(game, "display_name", "this game")

    parts.append(
        format_html(
            '<ul><li><a href="{}" title="Browse {} category">Category-wide for {}</a></li></ul>',
            game.twitch_directory_url,
            display_name,
            display_name,
        ),
    )

    return parts


def create_channel_list_html(
    max_links: int,
    parts: list[SafeText],
    channels_all: list[Channel],
    total: int,
) -> None:
    """Helper function to create HTML for a list of channels, limited to max_links with a note if there are more.

    Args:
        max_links (int): The maximum number of channel links to display.
        parts (list[SafeText]): The list to append the generated HTML to.
        channels_all (list[Channel]): The full list of channels to generate links for.
        total (int): The total number of channels, used for the "... and X more" message if there are more than max_links.
    """
    items: list[SafeString] = [
        format_html(
            '<li><a href="https://twitch.tv/{}">{}</a></li>',
            channel.name,
            channel.display_name,
        )
        for channel in channels_all[:max_links]
    ]
    if total > max_links:
        items.append(format_html("<li>... and {} more</li>", total - max_links))

    html: SafeText = format_html(
        "<ul>{}</ul>",
        format_html_join("", "{}", [(item,) for item in items]),
    )
    parts.append(format_html("<p>Channels with this drop:</p>{}", html))


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
                    channel_name,
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
class OrganizationRSSFeed(TTVDropsBaseFeed):
    """RSS feed for latest organizations."""

    feed_type = BrowserFriendlyRss201rev2Feed

    title: str = "TTVDrops Twitch Organizations"
    link: str = "/organizations/"
    description: str = "Latest organizations on TTVDrops"

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
        if self._limit is None:
            self._limit = 200  # Default limit
        return Organization.objects.order_by("-added_at")[: self._limit]

    def item_title(self, item: Organization) -> SafeText:
        """Return the organization name as the item title."""
        return SafeText(getattr(item, "name", str(item)))

    def item_description(self, item: Organization) -> SafeText:
        """Return a description of the organization."""
        return SafeText(item.feed_description())

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

    def feed_url(self) -> str:
        """Return the absolute URL for this feed."""
        return reverse("twitch:organization_feed")


# MARK: /rss/games/
class GameFeed(TTVDropsBaseFeed):
    """RSS feed for newly added games."""

    title: str = "TTVDrops Twitch Games"
    link: str = "/games/"
    description: str = "Newly added games on TTVDrops"

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
                f"<p>{game_name} has been added to ttvdrops.lovinator.space!\nOwned by {game_owner}.\n\n"
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
        """Return a unique identifier for each game. Use the URL to the game detail page as the GUID."""
        twitch_id: str = getattr(item, "twitch_id", "unknown")
        return self._absolute_url(reverse("twitch:game_detail", args=[twitch_id]))

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

    def item_enclosures(self, item: Game) -> list[feedgenerator.Enclosure]:
        """Return a list of enclosures for the game, including the box art if available."""
        image_url: str = getattr(item, "box_art_best_url", "")
        if image_url:
            try:
                size: int | None = getattr(item, "box_art_size_bytes", None)
                length: int = int(size) if size is not None else 0
            except TypeError, ValueError:
                length = 0

            if not length:
                return []

            mime: str = getattr(item, "box_art_mime_type", "")
            mime_type: str = mime or "image/jpeg"

            return [
                feedgenerator.Enclosure(
                    self._absolute_url(image_url),
                    str(length),
                    mime_type,
                ),
            ]
        return []

    def feed_url(self) -> str:
        """Return the URL to the RSS feed itself."""
        return reverse("twitch:game_feed")


# MARK: /rss/campaigns/
class DropCampaignFeed(TTVDropsBaseFeed):
    """RSS feed for latest drop campaigns."""

    title: str = "Twitch Drop Campaigns"
    link: str = "/campaigns/"
    description: str = "Latest Twitch drop campaigns on TTVDrops"
    item_guid_is_permalink = True

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
        queryset: QuerySet[DropCampaign] = _active_drop_campaigns(
            DropCampaign.objects.order_by("-start_at"),
        )
        return list(_with_campaign_related(queryset)[:limit])

    def item_title(self, item: DropCampaign) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        game_name: str = item.game.display_name if item.game else ""
        return SafeText(f"{game_name}: {item.clean_name}")

    def item_description(self, item: DropCampaign) -> SafeText:
        """Return a description of the campaign."""
        parts: list[SafeText] = []

        parts.extend(generate_item_image(item))
        parts.extend(generate_description_html(item=item))
        parts.extend(generate_date_html(item=item))
        parts.extend(generate_drops_summary_html(item=item))
        parts.extend(generate_channels_html(item))
        parts.extend(genereate_details_link_html(item))

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: DropCampaign) -> str:
        """Return the link to the campaign detail."""
        return reverse("twitch:campaign_detail", args=[item.twitch_id])

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign. Use the URL to the campaign detail page as the GUID."""
        return self._absolute_url(
            reverse("twitch:campaign_detail", args=[item.twitch_id]),
        )

    def item_pubdate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Fallback to updated_at or now if missing.
        """
        if item.added_at:
            return item.added_at
        return timezone.now()

    def item_updateddate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: DropCampaign) -> tuple[str, ...]:
        """Returns the associated game's name as a category."""
        categories: list[str] = ["twitch", "drops"]

        game: Game | None = item.game
        if game:
            categories.append(game.get_game_name)

            # Prefer direct game owners, which can be prefetched in feed querysets.
            categories.extend(org.name for org in game.owners.all() if org.name)

        return tuple(categories)

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        game: Game | None = item.game
        if game and game.display_name:
            return game.display_name

        return "Twitch"

    # Enclose
    def item_enclosures(self, item: DropCampaign) -> list[feedgenerator.Enclosure]:
        """Return a list of enclosures for the drop campaign, if available.

        Args:
            item (DropCampaign): The drop campaign item.

        Returns:
            list[feedgenerator.Enclosure]: A list of Enclosure objects if an image URL is
            available, otherwise an empty list.
        """
        image_url: str = getattr(item, "image_best_url", "")
        if image_url:
            try:
                size: int | None = getattr(item, "image_size_bytes", None)
                length: int = int(size) if size is not None else 0
            except TypeError, ValueError:
                length = 0

            if not length:
                return []

            mime: str = getattr(item, "image_mime_type", "")
            mime_type: str = mime or "image/jpeg"

            return [
                feedgenerator.Enclosure(
                    self._absolute_url(image_url),
                    str(length),
                    mime_type,
                ),
            ]
        return []

    def feed_url(self) -> str:
        """Return the URL to the RSS feed itself."""
        return reverse("twitch:campaign_feed")


# MARK: /rss/games/<twitch_id>/campaigns/
class GameCampaignFeed(TTVDropsBaseFeed):
    """RSS feed for the latest drop campaigns of a specific game."""

    item_guid_is_permalink = True
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

    def items(self, obj: Game) -> list[DropCampaign]:
        """Return the latest drop campaigns for this game, ordered by most recent start date (default 200, or limited by ?limit query param)."""
        limit: int = self._limit if self._limit is not None else 200
        queryset: QuerySet[DropCampaign] = _active_drop_campaigns(
            DropCampaign.objects.filter(
                game=obj,
            ).order_by("-start_at"),
        )
        return list(_with_campaign_related(queryset)[:limit])

    def item_title(self, item: DropCampaign) -> SafeText:
        """Return the campaign name as the item title (SafeText for RSS)."""
        game_name: str = item.game.display_name if item.game else ""
        return SafeText(f"{game_name}: {item.clean_name}")

    def item_description(self, item: DropCampaign) -> SafeText:
        """Return a description of the campaign."""
        parts: list[SafeText] = []

        parts.extend(generate_item_image_tag(item))
        parts.extend(generate_details_link(item))
        parts.extend(generate_date_html(item))
        parts.extend(generate_drops_summary_html(item))
        parts.extend(generate_channels_html(item))

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
        categories: list[str] = ["twitch", "drops"]

        game: Game | None = item.game
        if game:
            categories.append(game.get_game_name)

            # Prefer direct game owners, which can be prefetched in feed querysets.
            categories.extend(org.name for org in game.owners.all() if org.name)

        return tuple(categories)

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign."""
        return self._absolute_url(
            reverse("twitch:campaign_detail", args=[item.twitch_id]),
        )

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        game: Game | None = item.game
        if game and game.display_name:
            return game.display_name

        return "Twitch"

    def author_name(self, obj: Game) -> str:
        """Return the author name for the game, typically the owner organization name."""
        owners_cache: list[Organization] | None = getattr(
            obj,
            "_prefetched_objects_cache",
            {},
        ).get("owners")
        if owners_cache:
            owner: Organization = owners_cache[0]
            if owner.name:
                return owner.name

        return "Twitch"

    def item_enclosures(self, item: DropCampaign) -> list[feedgenerator.Enclosure]:
        """Return a list of enclosures for the drop campaign, if available.

        Args:
            item (DropCampaign): The drop campaign item.

        Returns:
            list[feedgenerator.Enclosure]: A list of Enclosure objects if an image URL is available, otherwise an empty list.
        """
        # Use image_best_url as enclosure if available
        image_url: str = getattr(item, "image_best_url", "")
        if image_url:
            try:
                size: int | None = getattr(item, "image_size_bytes", None)
                length: int = int(size) if size is not None else 0
            except TypeError, ValueError:
                length = 0

            if not length:
                return []

            mime: str = getattr(item, "image_mime_type", "")
            mime_type: str = mime or "image/jpeg"

            return [
                feedgenerator.Enclosure(
                    self._absolute_url(image_url),
                    str(length),
                    mime_type,
                ),
            ]
        return []

    def feed_url(self, obj: Game) -> str:
        """Return the URL to the RSS feed itself."""
        return reverse("twitch:game_campaign_feed", args=[obj.twitch_id])


# MARK: /rss/reward-campaigns/
class RewardCampaignFeed(TTVDropsBaseFeed):
    """RSS feed for latest reward campaigns."""

    title: str = "Twitch Reward Campaigns"
    link: str = "/campaigns/"
    description: str = "Latest Twitch reward campaigns on TTVDrops"

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
        queryset: QuerySet[RewardCampaign] = _active_reward_campaigns(
            RewardCampaign.objects.select_related("game").order_by("-added_at"),
        )
        return list(
            queryset[:limit],
        )

    def item_title(self, item: RewardCampaign) -> SafeText:
        """Return the reward campaign name as the item title."""
        if item.brand:
            return SafeText(f"{item.brand}: {item.name}")
        return SafeText(item.name)

    def item_description(self, item: RewardCampaign) -> SafeText:
        """Return a description of the reward campaign."""
        parts: list = []

        if item.summary:
            parts.append(format_html("<p>{}</p>", item.summary))

        if item.starts_at or item.ends_at:
            start_part = (
                format_html(
                    "Starts: {} ({})",
                    item.starts_at.strftime("%Y-%m-%d %H:%M %Z"),
                    naturaltime(item.starts_at),
                )
                if item.starts_at
                else ""
            )
            end_part = (
                format_html(
                    "Ends: {} ({})",
                    item.ends_at.strftime("%Y-%m-%d %H:%M %Z"),
                    naturaltime(item.ends_at),
                )
                if item.ends_at
                else ""
            )
            if start_part and end_part:
                parts.append(format_html("<p>{}<br />{}</p>", start_part, end_part))
            elif start_part:
                parts.append(format_html("<p>{}</p>", start_part))
            elif end_part:
                parts.append(format_html("<p>{}</p>", end_part))

        if item.is_sitewide:
            parts.append(
                SafeText("<p><strong>This is a sitewide reward campaign</strong></p>"),
            )
        elif item.game:
            parts.append(
                format_html(
                    "<p>Game: {}</p>",
                    item.game.display_name or item.game.name,
                ),
            )

        if item.about_url:
            parts.append(
                format_html('<p><a href="{}">Learn more</a></p>', item.about_url),
            )

        if item.external_url:
            parts.append(
                format_html('<p><a href="{}">Redeem reward</a></p>', item.external_url),
            )

        return SafeText("".join(str(p) for p in parts))

    def item_link(self, item: RewardCampaign) -> str:
        """Return the link to the reward campaign (external URL or dashboard)."""
        if item.external_url:
            return item.external_url
        return reverse("twitch:dashboard")

    def item_pubdate(self, item: RewardCampaign) -> datetime.datetime:
        """Returns the publication date to the feed item.

        Uses starts_at (when the reward starts). Fallback to added_at or now if missing.
        """
        if item.starts_at:
            return item.starts_at
        if item.added_at:
            return item.added_at
        return timezone.now()

    def item_updateddate(self, item: RewardCampaign) -> datetime.datetime:
        """Returns the reward campaign's last update time."""
        return item.updated_at

    def item_categories(self, item: RewardCampaign) -> tuple[str, ...]:
        """Returns the associated game's name and brand as categories."""
        categories: list[str] = ["twitch", "rewards", "quests"]

        if item.brand:
            categories.append(item.brand)

        if item.game:
            categories.append(item.game.get_game_name)

        return tuple(categories)

    def item_guid(self, item: RewardCampaign) -> str:
        """Return a unique identifier for each reward campaign."""
        return self._absolute_url(
            reverse("twitch:reward_campaign_detail", args=[item.twitch_id]),
        )

    def item_author_name(self, item: RewardCampaign) -> str:
        """Return the author name for the reward campaign."""
        if item.brand:
            return item.brand

        if item.game and item.game.display_name:
            return item.game.display_name

        return "Twitch"

    def item_enclosures(self, item: RewardCampaign) -> list[feedgenerator.Enclosure]:
        """Return a list of enclosures for the reward campaign, if available.

        Args:
            item (RewardCampaign): The reward campaign item.

        Returns:
            list[feedgenerator.Enclosure]: A list of Enclosure objects if an image URL is available, otherwise an empty list.
        """
        image_url: str = getattr(item, "image_best_url", "")
        if image_url:
            try:
                size: int | None = getattr(item, "image_size_bytes", None)
                length: int = int(size) if size is not None else 0
            except TypeError, ValueError:
                length = 0

            if not length:
                return []

            mime: str = getattr(item, "image_mime_type", "")
            mime_type: str = mime or "image/jpeg"

            return [
                feedgenerator.Enclosure(
                    self._absolute_url(image_url),
                    str(length),
                    mime_type,
                ),
            ]
        return []

    def feed_url(self) -> str:
        """Return the URL to the RSS feed itself."""
        return reverse("twitch:reward_campaign_feed")


# Atom feed variants: reuse existing logic but switch the feed generator to Atom
class OrganizationAtomFeed(TTVDropsAtomBaseFeed, OrganizationRSSFeed):
    """Atom feed for latest organizations (reuses OrganizationRSSFeed)."""

    subtitle: str = OrganizationRSSFeed.description

    def feed_url(self) -> str:
        """Return the URL to the Atom feed itself."""
        return reverse("twitch:organization_feed_atom")


class GameAtomFeed(TTVDropsAtomBaseFeed, GameFeed):
    """Atom feed for newly added games (reuses GameFeed)."""

    subtitle: str = GameFeed.description

    def feed_url(self) -> str:
        """Return the URL to the Atom feed itself."""
        return reverse("twitch:game_feed_atom")


class DropCampaignAtomFeed(TTVDropsAtomBaseFeed, DropCampaignFeed):
    """Atom feed for latest drop campaigns (reuses DropCampaignFeed)."""

    subtitle: str = DropCampaignFeed.description

    def feed_url(self) -> str:
        """Return the URL to the Atom feed itself."""
        return reverse("twitch:campaign_feed_atom")


class GameCampaignAtomFeed(TTVDropsAtomBaseFeed, GameCampaignFeed):
    """Atom feed for latest drop campaigns for a specific game (reuses GameCampaignFeed)."""

    def feed_url(self, obj: Game) -> str:
        """Return the URL to the Atom feed itself."""
        return reverse("twitch:game_campaign_feed_atom", args=[obj.twitch_id])


class RewardCampaignAtomFeed(TTVDropsAtomBaseFeed, RewardCampaignFeed):
    """Atom feed for latest reward campaigns (reuses RewardCampaignFeed)."""

    subtitle: str = RewardCampaignFeed.description

    def feed_url(self) -> str:
        """Return the URL to the Atom feed itself."""
        return reverse("twitch:reward_campaign_feed_atom")
