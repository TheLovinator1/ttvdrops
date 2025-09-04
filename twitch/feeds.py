from __future__ import annotations

from django.contrib.syndication.views import Feed
from django.urls import reverse

from twitch.models import DropCampaign, Game, Organization


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

    title = "TTVDrops Drop Campaigns"
    link = "/campaigns/"
    description = "Latest drop campaigns on TTVDrops"

    def items(self) -> list[DropCampaign]:
        """Return the latest 100 drop campaigns."""
        return list(DropCampaign.objects.order_by("-added_at")[:100])

    def item_title(self, item: DropCampaign) -> str:
        """Return the campaign name as the item title."""
        return item.name

    def item_description(self, item: DropCampaign) -> str:
        """Return a description of the campaign."""
        return item.description or f"Campaign {item.name}"

    def item_link(self, item: DropCampaign) -> str:
        """Return the link to the campaign detail."""
        return reverse("twitch:campaign_detail", args=[item.pk])
