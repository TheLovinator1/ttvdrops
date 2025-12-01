from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.syndication.views import Feed
from django.db.models.query import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeText

from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    import datetime

    from django.db.models import Model
    from django.db.models import QuerySet


# MARK: /rss/organizations/
class OrganizationFeed(Feed):
    """RSS feed for latest organizations."""

    title: str = "TTVDrops Organizations"
    link: str = "/organizations/"
    description: str = "Latest organizations on TTVDrops"

    def items(self) -> list[Organization]:
        """Return the latest 100 organizations."""
        return list(Organization.objects.order_by("-id")[:100])

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

    def item_description(self, item: Model) -> SafeText:  # noqa: PLR0915
        """Return a description of the campaign."""
        description: str = ""
        image_url: str | None = getattr(item, "image_url", None)
        name: str = getattr(item, "name", str(item))
        if image_url:
            description += format_html(
                '<img src="{}" alt="{}"><br><br>',
                image_url,
                name,
            )
        desc_text: str | None = getattr(item, "description", None)
        if desc_text:
            description += format_html("<p>{}</p>", desc_text)
        start_at: datetime.datetime | None = getattr(item, "start_at", None)
        end_at: datetime.datetime | None = getattr(item, "end_at", None)
        if start_at:
            description += f"<p><strong>Starts:</strong> {start_at.strftime('%Y-%m-%d %H:%M %Z')}</p>"
        if end_at:
            description += f"<p><strong>Ends:</strong> {end_at.strftime('%Y-%m-%d %H:%M %Z')}</p>"
        drops: QuerySet[TimeBasedDrop] | None = getattr(item, "time_based_drops", None)
        if drops:
            drops_qs: QuerySet[TimeBasedDrop] = drops.select_related().prefetch_related("benefits").all()
            if drops_qs:
                description += "<h3>Drops in this campaign:</h3>"
                table_header = (
                    '<table style="border-collapse: collapse; width: 100%;">'
                    "<thead><tr>"
                    '<th style="border: 1px solid #ddd; padding: 8px;">Benefits</th>'
                    '<th style="border: 1px solid #ddd; padding: 8px;">Drop Name</th>'
                    '<th style="border: 1px solid #ddd; padding: 8px;">Requirements</th>'
                    '<th style="border: 1px solid #ddd; padding: 8px;">Period</th>'
                    "</tr></thead><tbody>"
                )
                description += table_header
                for drop in drops_qs:
                    description += "<tr>"
                    description += '<td style="border: 1px solid #ddd; padding: 8px;">'
                    for benefit in drop.benefits.all():
                        if getattr(benefit, "image_asset_url", None):
                            description += format_html(
                                '<img height="60" width="60" style="object-fit: cover; margin-right: 5px;" src="{}" alt="{}">',
                                benefit.image_asset_url,
                                benefit.name,
                            )
                        else:
                            placeholder_img = (
                                '<img height="60" width="60" style="object-fit: cover; margin-right: 5px;" '
                                'src="/static/images/placeholder.png" alt="No Image Available">'
                            )
                            description += placeholder_img
                    description += "</td>"
                    description += f'<td style="border: 1px solid #ddd; padding: 8px;">{getattr(drop, "name", str(drop))}</td>'
                    requirements: str = ""
                    if getattr(drop, "required_minutes_watched", None):
                        requirements = f"{drop.required_minutes_watched} minutes watched"
                    if getattr(drop, "required_subs", 0) > 0:
                        if requirements:
                            requirements += f" and {drop.required_subs} subscriptions required"
                        else:
                            requirements = f"{drop.required_subs} subscriptions required"
                    description += f'<td style="border: 1px solid #ddd; padding: 8px;">{requirements}</td>'
                    period: str = ""
                    start_at = getattr(drop, "start_at", None)
                    end_at = getattr(drop, "end_at", None)
                    if start_at is not None:
                        period += start_at.strftime("%Y-%m-%d %H:%M %Z")
                    if end_at is not None:
                        if period:
                            period += " - " + end_at.strftime("%Y-%m-%d %H:%M %Z")
                        else:
                            period = end_at.strftime("%Y-%m-%d %H:%M %Z")
                    description += f'<td style="border: 1px solid #ddd; padding: 8px;">{period}</td>'
                    description += "</tr>"
                description += "</tbody></table><br>"
        details_url: str | None = getattr(item, "details_url", None)
        if details_url:
            description += format_html('<p><a href="{}">About this drop</a></p>', details_url)
        return SafeText(description)

    def item_link(self, item: Model) -> str:
        """Return the link to the campaign detail."""
        return reverse("twitch:campaign_detail", args=[item.pk])

    def item_pubdate(self, item: Model) -> datetime.datetime:
        """Returns the publication date to the feed item. Fallback to updated_at or now if missing."""
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
        if item.game:
            return (
                "twitch",
                item.game.get_game_name,
            )
        return ()

    def item_guid(self, item: DropCampaign) -> str:
        """Return a unique identifier for each campaign."""
        return item.twitch_id + "@ttvdrops.com"

    def item_author_name(self, item: DropCampaign) -> str:
        """Return the author name for the campaign, typically the game name."""
        if item.game and item.game.display_name:
            return item.game.display_name
        return "Twitch"

    def item_enclosure_url(self, item: DropCampaign) -> str:
        """Returns the URL of the campaign image for enclosure."""
        return item.image_url

    def item_enclosure_length(self, item: DropCampaign) -> int:  # noqa: ARG002
        """Returns the length of the enclosure. Currently not tracked, so return 0."""
        return 0

    def item_enclosure_mime_type(self, item: DropCampaign) -> str:  # noqa: ARG002
        """Returns the MIME type of the enclosure."""
        return "image/jpeg"
