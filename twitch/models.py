from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone


class Game(models.Model):
    """Represents a game on Twitch."""

    id = models.TextField(primary_key=True)
    slug = models.TextField(blank=True, default="")
    display_name = models.TextField()

    def __str__(self) -> str:
        """Return a string representation of the game."""
        return self.display_name


class Organization(models.Model):
    """Represents an organization on Twitch that can own drop campaigns."""

    id = models.TextField(primary_key=True)
    name = models.TextField()

    def __str__(self) -> str:
        """Return a string representation of the organization."""
        return self.name


class DropCampaign(models.Model):
    """Represents a Twitch drop campaign."""

    STATUS_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("ACTIVE", "Active"),
        ("UPCOMING", "Upcoming"),
        ("EXPIRED", "Expired"),
    ]

    id = models.TextField(primary_key=True)
    name = models.TextField()
    description = models.TextField(blank=True)
    details_url = models.URLField(max_length=500, blank=True, default="")
    account_link_url = models.URLField(max_length=500, blank=True, default="")
    image_url = models.URLField(max_length=500, blank=True, default="")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.TextField(choices=STATUS_CHOICES)
    is_account_connected = models.BooleanField(default=False)

    # Foreign keys
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="drop_campaigns")
    owner = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="drop_campaigns")

    # Tracking fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        """Return a string representation of the drop campaign."""
        return self.name

    @property
    def is_active(self) -> bool:
        """Check if the campaign is currently active."""
        now = timezone.now()
        return self.start_at <= now <= self.end_at and self.status == "ACTIVE"

    @property
    def clean_name(self) -> str:
        """Return the campaign name without the game name prefix.

        Examples:
            "Ravendawn - July 2" -> "July 2"
            "Party Animals Twitch Drop" -> "Twitch Drop"
        """
        if not self.game or not self.game.display_name:
            return self.name

        game_name = self.game.display_name

        # Remove game name if it's at the beginning of the campaign name
        if self.name.startswith(game_name):
            # Check if it's followed by a separator like " - "
            if self.name[len(game_name) :].startswith(" - "):
                return self.name[len(game_name) + 3 :].strip()
            # Or just remove the game name if it's followed by a space
            if len(self.name) > len(game_name) and self.name[len(game_name)] == " ":
                return self.name[len(game_name) + 1 :].strip()

        return self.name


class DropBenefit(models.Model):
    """Represents a benefit that can be earned from a drop."""

    DISTRIBUTION_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("DIRECT_ENTITLEMENT", "Direct Entitlement"),
        ("CODE", "Code"),
    ]

    id = models.TextField(primary_key=True)
    name = models.TextField()
    image_asset_url = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField()
    entitlement_limit = models.PositiveIntegerField(default=1)
    is_ios_available = models.BooleanField(default=False)
    distribution_type = models.TextField(choices=DISTRIBUTION_TYPES)

    # Foreign keys
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="drop_benefits")
    owner_organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="drop_benefits")

    def __str__(self) -> str:
        """Return a string representation of the drop benefit."""
        return self.name


class TimeBasedDrop(models.Model):
    """Represents a time-based drop in a drop campaign."""

    id = models.TextField(primary_key=True)
    name = models.TextField()
    required_minutes_watched = models.PositiveIntegerField()
    required_subs = models.PositiveIntegerField(default=0)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()

    # Foreign keys
    campaign = models.ForeignKey(DropCampaign, on_delete=models.CASCADE, related_name="time_based_drops")
    benefits = models.ManyToManyField(DropBenefit, through="DropBenefitEdge", related_name="drops")

    def __str__(self) -> str:
        """Return a string representation of the time-based drop."""
        return self.name


class DropBenefitEdge(models.Model):
    """Represents the relationship between a TimeBasedDrop and a DropBenefit."""

    drop = models.ForeignKey(TimeBasedDrop, on_delete=models.CASCADE)
    benefit = models.ForeignKey(DropBenefit, on_delete=models.CASCADE)
    entitlement_limit = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("drop", "benefit")

    def __str__(self) -> str:
        """Return a string representation of the drop benefit edge."""
        return f"{self.drop.name} - {self.benefit.name}"
