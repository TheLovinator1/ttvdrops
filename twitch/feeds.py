from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils.html import format_html

from twitch.models import DropCampaign, Game, Organization

if TYPE_CHECKING:
    import datetime


class OrganizationFeed(Feed):
    """RSS feed for latest organizations."""

    title = "TTVDrops Organizations"
    link = "/organizations/"
    description = "Latest organizations on TTVDrops"

    def items(self) -> list[Organization]:
        """Return the latest 100 organizations."""
        return list(Organization.objects.order_by("-id")[:100])

    def item_title(self, item: Organization) -> str:
        """Return the organization name as the item title."""
        return item.name

    def item_description(self, item: Organization) -> str:
        """Return a description of the organization."""
        return f"Organization {item.name}"

    def item_link(self, item: Organization) -> str:
        """Return the link to the organization detail."""
        return reverse("twitch:organization_detail", args=[item.pk])


class GameFeed(Feed):
    """RSS feed for latest games."""

    title = "TTVDrops Games"
    link = "/games/"
    description = "Latest games on TTVDrops"

    def items(self) -> list[Game]:
        """Return the latest 100 games."""
        return list(Game.objects.order_by("-id")[:100])

    def item_title(self, item: Game) -> str:
        """Return the game name as the item title."""
        return str(item)

    def item_description(self, item: Game) -> str:
        """Return a description of the game."""
        return f"Game {item.display_name}"

    def item_link(self, item: Game) -> str:
        """Return the link to the game detail."""
        return reverse("twitch:game_detail", args=[item.pk])


class DropCampaignFeed(Feed):
    """RSS feed for latest drop campaigns."""

    title = "Twitch Drop Campaigns"
    link = "/campaigns/"
    description = "Latest Twitch drop campaigns"
    feed_url = "/rss/campaigns/"
    feed_copyright = "Information wants to be free."

    def items(self) -> list[DropCampaign]:
        """Return the latest 100 drop campaigns."""
        return list(DropCampaign.objects.select_related("game").order_by("-added_at")[:100])

    def item_title(self, item: DropCampaign) -> str:
        """Return the campaign name as the item title."""
        return f"{item.game.display_name}: {item.clean_name}"

    def item_description(self, item: DropCampaign) -> str:
        """Return a description of the campaign."""
        description = ""

        # Include the campaign image if available
        if item.image_url:
            description += format_html(
                '<img src="{}" alt="{}"><br><br>',
                item.image_url,
                item.name,
            )

        # Add the campaign description text
        description += format_html("<p>{}</p>", item.description) if item.description else ""

        # Add start and end dates for clarity
        if item.start_at:
            description += f"<p><strong>Starts:</strong> {item.start_at.strftime('%Y-%m-%d %H:%M %Z')}</p>"
        if item.end_at:
            description += f"<p><strong>Ends:</strong> {item.end_at.strftime('%Y-%m-%d %H:%M %Z')}</p>"

        # Add information about the drops in this campaign
        drops = item.time_based_drops.select_related().prefetch_related("benefits").all()  # type: ignore[attr-defined]
        if drops:
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

            for drop in drops:
                description += "<tr>"
                # Benefits column with images
                description += '<td style="border: 1px solid #ddd; padding: 8px;">'
                for benefit in drop.benefits.all():
                    if benefit.image_asset_url:
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

                # Drop name
                description += f'<td style="border: 1px solid #ddd; padding: 8px;">{drop.name}</td>'

                # Requirements
                requirements = ""
                if drop.required_minutes_watched:
                    requirements = f"{drop.required_minutes_watched} minutes watched"
                if drop.required_subs > 0:
                    if requirements:
                        requirements += f" and {drop.required_subs} subscriptions required"
                    else:
                        requirements = f"{drop.required_subs} subscriptions required"
                description += f'<td style="border: 1px solid #ddd; padding: 8px;">{requirements}</td>'

                # Period
                period = ""
                if drop.start_at:
                    period += drop.start_at.strftime("%Y-%m-%d %H:%M %Z")
                if drop.end_at:
                    if period:
                        period += " - " + drop.end_at.strftime("%Y-%m-%d %H:%M %Z")
                    else:
                        period = drop.end_at.strftime("%Y-%m-%d %H:%M %Z")
                description += f'<td style="border: 1px solid #ddd; padding: 8px;">{period}</td>'

                description += "</tr>"

            description += "</tbody></table><br>"

        # Add a clear link to the campaign details page
        if item.details_url:
            description += format_html('<p><a href="{}">About this drop</a></p>', item.details_url)

        return f"{description}"

    def item_link(self, item: DropCampaign) -> str:
        """Return the link to the campaign detail."""
        return reverse("twitch:campaign_detail", args=[item.pk])

    def item_pubdate(self, item: DropCampaign) -> datetime.datetime:
        """Returns the publication date to the feed item."""
        return item.start_at

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
        return item.id + "@ttvdrops.com"

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
