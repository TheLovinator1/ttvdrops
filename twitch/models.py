from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, ClassVar

from django.db import models
from django.utils import timezone

from accounts.models import User

if TYPE_CHECKING:
    import datetime

logger: logging.Logger = logging.getLogger("ttvdrops")


class Game(models.Model):
    """Represents a game on Twitch."""

    id = models.TextField(primary_key=True)
    slug = models.TextField(blank=True, default="", db_index=True)
    name = models.TextField(blank=True, default="", db_index=True)
    display_name = models.TextField(blank=True, default="", db_index=True)
    box_art = models.URLField(max_length=500, blank=True, default="")

    class Meta:
        indexes: ClassVar[list] = [
            models.Index(fields=["slug"]),
            models.Index(fields=["display_name"]),
            models.Index(fields=["name"]),
            models.Index(fields=["box_art"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the game."""
        if (self.display_name and self.name) and (self.display_name != self.name):
            logger.warning(
                "Game display name '%s' does not match name '%s'.",
                self.display_name,
                self.name,
            )
            return f"{self.display_name} ({self.name})"
        return self.name or self.slug or self.id

    @property
    def organizations(self) -> models.QuerySet[Organization]:
        """Return all organizations that have drop campaigns for this game."""
        return Organization.objects.filter(drop_campaigns__game=self).distinct()

    @property
    def box_art_base_url(self) -> str:
        """Return the base box art URL without size suffix.

        Twitch box art URLs often include size suffixes like '-120x160.jpg'.
        This property returns the base URL without the size suffix.

        Examples:
            'https://static-cdn.jtvnw.net/ttv-boxart/512710-120x160.jpg'
            -> 'https://static-cdn.jtvnw.net/ttv-boxart/512710.jpg'
        """
        if not self.box_art:
            return ""

        # Remove size suffix pattern like '-120x160' from the filename
        return re.sub(r"-\d+x\d+(\.jpg|\.png|\.jpeg|\.gif|\.webp)$", r"\1", self.box_art)


class Organization(models.Model):
    """Represents an organization on Twitch that can own drop campaigns."""

    id = models.TextField(primary_key=True)
    name = models.TextField(db_index=True)

    class Meta:
        indexes: ClassVar[list] = [
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the organization."""
        return self.name


class DropCampaign(models.Model):
    """Represents a Twitch drop campaign."""

    id = models.TextField(primary_key=True)
    name = models.TextField(db_index=True)
    description = models.TextField(blank=True)
    details_url = models.URLField(max_length=500, blank=True, default="")
    account_link_url = models.URLField(max_length=500, blank=True, default="")
    image_url = models.URLField(max_length=500, blank=True, default="")
    start_at = models.DateTimeField(db_index=True, null=True)
    end_at = models.DateTimeField(db_index=True, null=True)
    is_account_connected = models.BooleanField(default=False)

    # Foreign keys
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="drop_campaigns", db_index=True)
    owner = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="drop_campaigns", db_index=True)

    # Tracking fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list] = [
            models.Index(fields=["name"]),
            models.Index(fields=["start_at", "end_at"]),
            models.Index(fields=["game"]),
            models.Index(fields=["owner"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop campaign."""
        return self.name

    @property
    def is_active(self) -> bool:
        """Check if the campaign is currently active."""
        now: datetime.datetime = timezone.now()
        if self.start_at is None or self.end_at is None:
            return False
        return self.start_at <= now <= self.end_at

    @property
    def clean_name(self) -> str:
        """Return the campaign name without the game name prefix.

        Examples:
            "Ravendawn - July 2" -> "July 2"
            "Party Animals Twitch Drop" -> "Twitch Drop"
            "Skull & Bones - Closed Beta" -> "Closed Beta" (& is replaced with "and")
        """
        if not self.game or not self.game.display_name:
            return self.name

        # Try different variations of the game name
        game_variations = [self.game.display_name]

        # Add & to "and" conversion
        if "&" in self.game.display_name:
            game_variations.append(self.game.display_name.replace("&", "and"))

        # Add "and" to & conversion
        if "and" in self.game.display_name:
            game_variations.append(self.game.display_name.replace("and", "&"))

        # Check each variation
        for game_name in game_variations:
            if not self.name.startswith(game_name):
                continue

            # Check if it's followed by a separator like " - "
            if self.name[len(game_name) :].startswith(" - "):
                return self.name[len(game_name) + 3 :].strip()

            # Or just remove the game name if it's followed by a space
            if len(self.name) > len(game_name) and self.name[len(game_name)] == " ":
                return self.name[len(game_name) + 1 :].strip()

        return self.name


class DropBenefit(models.Model):
    """Represents a benefit that can be earned from a drop."""

    id = models.TextField(primary_key=True)
    name = models.TextField(db_index=True, blank=True, default="N/A")
    image_asset_url = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(db_index=True, null=True)
    entitlement_limit = models.PositiveIntegerField(default=1)
    is_ios_available = models.BooleanField(default=False)
    distribution_type = models.TextField(db_index=True, blank=True, default="")

    # Foreign keys
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="drop_benefits", db_index=True)
    owner_organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="drop_benefits", db_index=True)

    class Meta:
        indexes: ClassVar[list] = [
            models.Index(fields=["name"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["distribution_type"]),
            models.Index(fields=["game"]),
            models.Index(fields=["owner_organization"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop benefit."""
        return self.name


class TimeBasedDrop(models.Model):
    """Represents a time-based drop in a drop campaign."""

    id = models.TextField(primary_key=True)
    name = models.TextField(db_index=True)
    required_minutes_watched = models.PositiveIntegerField(db_index=True, null=True)
    required_subs = models.PositiveIntegerField(default=0)
    start_at = models.DateTimeField(db_index=True, null=True)
    end_at = models.DateTimeField(db_index=True, null=True)

    # Foreign keys
    campaign = models.ForeignKey(DropCampaign, on_delete=models.CASCADE, related_name="time_based_drops", db_index=True)
    benefits = models.ManyToManyField(DropBenefit, through="DropBenefitEdge", related_name="drops")  # type: ignore[var-annotated]

    class Meta:
        indexes: ClassVar[list] = [
            models.Index(fields=["name"]),
            models.Index(fields=["start_at", "end_at"]),
            models.Index(fields=["campaign"]),
            models.Index(fields=["required_minutes_watched"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the time-based drop."""
        return self.name


class DropBenefitEdge(models.Model):
    """Represents the relationship between a TimeBasedDrop and a DropBenefit."""

    drop = models.ForeignKey(TimeBasedDrop, on_delete=models.CASCADE, db_index=True)
    benefit = models.ForeignKey(DropBenefit, on_delete=models.CASCADE, db_index=True)
    entitlement_limit = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("drop", "benefit")
        indexes: ClassVar[list] = [
            models.Index(fields=["drop", "benefit"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop benefit edge."""
        return f"{self.drop.name} - {self.benefit.name}"


class NotificationSubscription(models.Model):
    """Users can subscribe to games to get notified."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, null=True, blank=True, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, null=True, blank=True, on_delete=models.CASCADE)

    notify_found = models.BooleanField(default=False)
    notify_live = models.BooleanField(default=False)

    class Meta:
        unique_together: ClassVar[list[tuple[str, str]]] = [
            ("user", "game"),
            ("user", "organization"),
        ]

    def __str__(self) -> str:
        if self.game:
            return f"{self.user} subscription to game: {self.game.display_name}"
        if self.organization:
            return f"{self.user} subscription to organization: {self.organization.name}"
        return f"{self.user} subscription"
