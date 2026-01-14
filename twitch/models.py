from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    import datetime

logger: logging.Logger = logging.getLogger("ttvdrops")


# MARK: Organization
class Organization(models.Model):
    """Represents an organization on Twitch that can own drop campaigns."""

    twitch_id = models.TextField(
        unique=True,
        verbose_name="Organization ID",
        editable=False,
        help_text="The unique Twitch identifier for the organization.",
    )
    name = models.TextField(
        unique=True,
        verbose_name="Name",
        help_text="Display name of the organization.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Added At",
        editable=False,
        help_text="Timestamp when this organization record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
        editable=False,
        help_text="Timestamp when this organization record was last updated.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the organization."""
        return self.name or self.twitch_id


# MARK: Game
class Game(models.Model):
    """Represents a game on Twitch."""

    twitch_id = models.TextField(verbose_name="Twitch game ID", unique=True)
    slug = models.TextField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="Slug",
        help_text="Short unique identifier for the game.",
    )
    name = models.TextField(
        blank=True,
        default="",
        verbose_name="Name",
    )
    display_name = models.TextField(
        blank=True,
        default="",
        verbose_name="Display name",
    )
    box_art = models.URLField(  # noqa: DJ001
        max_length=500,
        blank=True,
        null=True,  # We allow null here to distinguish between no box art and empty string
        default="",
        verbose_name="Box art URL",
    )

    box_art_file = models.FileField(
        upload_to="games/box_art/",
        blank=True,
        null=True,
        help_text="Locally cached box art image served from this site.",
    )

    owners = models.ManyToManyField(
        Organization,
        related_name="games",
        blank=True,
        verbose_name="Organizations",
        help_text="Organizations that own this game.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this game record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this game record was last updated.",
    )

    class Meta:
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["display_name"]),
            models.Index(fields=["name"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
            # For games_grid_view grouping by owners + display_name
            # ManyToManyField does not support direct indexing, so skip these
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
        return self.display_name or self.name or self.slug or self.twitch_id

    @property
    def organizations(self) -> models.QuerySet[Organization]:
        """Return orgs that own games with campaigns for this game."""
        return Organization.objects.filter(
            games__drop_campaigns__game=self,
        ).distinct()

    @property
    def get_game_name(self) -> str:
        """Return the best available name for the game."""
        if self.display_name:
            return self.display_name
        if self.name:
            return self.name
        if self.slug:
            return self.slug
        return self.twitch_id

    @property
    def twitch_directory_url(self) -> str:
        """Return Twitch directory URL with drops filter when slug exists."""
        # TODO(TheLovinator): If no slug, get from Twitch API or IGDB?  # noqa: TD003

        if self.slug:
            return f"https://www.twitch.tv/directory/category/{self.slug}?filter=drops"
        return ""

    @property
    def box_art_best_url(self) -> str:
        """Return the best available URL for the game's box art (local first)."""
        try:
            if self.box_art_file and getattr(self.box_art_file, "url", None):
                return self.box_art_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve Game.box_art_file url: %s", exc)
        return self.box_art or ""


# MARK: TwitchGame
class TwitchGameData(models.Model):
    """Represents game metadata returned from the Twitch API.

    This mirrors the public Twitch API fields for a game and is tied to the
    local `Game` model where possible.

    Fields:
        id: Twitch game id (primary key)
        game: Optional FK to the local Game object
        name: Display name of the game
        box_art_url: URL template for box art with {width}x{height} placeholder
        igdb_id: Optional IGDB id for the game
    """

    twitch_id = models.TextField(
        verbose_name="Twitch Game ID",
        unique=True,
        help_text="The Twitch ID for this game.",
    )
    game = models.ForeignKey(
        Game,
        on_delete=models.SET_NULL,
        related_name="twitch_game_data",
        null=True,
        blank=True,
        verbose_name="Game",
        help_text=("Optional link to the local Game record for this Twitch game."),
    )

    name = models.TextField(blank=True, default="", verbose_name="Name")
    box_art_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Box art URL",
        help_text=("URL template with {width}x{height} placeholders for the box art image."),
    )
    igdb_id = models.TextField(blank=True, default="", verbose_name="IGDB ID")

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Record creation time.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Record last update time.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["game"]),
            models.Index(fields=["igdb_id"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        return self.name or self.twitch_id


# MARK: Channel
class Channel(models.Model):
    """Represents a Twitch channel that can participate in drop campaigns."""

    twitch_id = models.TextField(
        verbose_name="Channel ID",
        help_text="The unique Twitch identifier for the channel.",
        unique=True,
    )
    name = models.TextField(
        verbose_name="Username",
        help_text="The lowercase username of the channel.",
    )
    display_name = models.TextField(
        verbose_name="Display Name",
        help_text="The display name of the channel (with proper capitalization).",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this channel record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this channel record was last updated.",
    )

    class Meta:
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["display_name"]),
            models.Index(fields=["name"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the channel."""
        return self.display_name or self.name or self.twitch_id


# MARK: DropCampaign
class DropCampaign(models.Model):
    """Represents a Twitch drop campaign."""

    twitch_id = models.TextField(
        unique=True,
        editable=False,
        help_text="The Twitch ID for this campaign.",
    )
    name = models.TextField(
        help_text="Name of the drop campaign.",
    )
    description = models.TextField(
        blank=True,
        help_text="Detailed description of the campaign.",
    )
    details_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="URL with campaign details.",
    )
    account_link_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="URL to link a Twitch account for the campaign.",
    )
    image_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="URL to an image representing the campaign.",
    )
    image_file = models.FileField(
        upload_to="campaigns/images/",
        blank=True,
        null=True,
        help_text="Locally cached campaign image served from this site.",
    )
    start_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Datetime when the campaign starts.",
    )
    end_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Datetime when the campaign ends.",
    )
    allow_is_enabled = models.BooleanField(
        default=True,
        help_text="Whether the campaign allows participation.",
    )
    allow_channels = models.ManyToManyField(
        Channel,
        blank=True,
        related_name="allowed_campaigns",
        help_text="Channels that are allowed to participate in this campaign.",
    )

    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="drop_campaigns",
        verbose_name="Game",
        help_text="Game associated with this campaign.",
    )

    operation_name = models.TextField(
        blank=True,
        default="",
        help_text="The GraphQL operation name used to fetch this campaign data (e.g., 'ViewerDropsDashboard').",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this campaign record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this campaign record was last updated.",
    )

    class Meta:
        ordering = ["-start_at"]
        indexes = [
            models.Index(fields=["-start_at"]),
            models.Index(fields=["end_at"]),
            models.Index(fields=["game"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["description"]),
            models.Index(fields=["allow_is_enabled"]),
            models.Index(fields=["operation_name"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
            # Composite indexes for common queries
            models.Index(fields=["game", "-start_at"]),
            models.Index(fields=["start_at", "end_at"]),
            # For dashboard and game_detail active campaign filtering
            models.Index(fields=["start_at", "end_at", "game"]),
            models.Index(fields=["end_at", "-start_at"]),
        ]

    def __str__(self) -> str:
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
            "Skull & Bones - Closed Beta" -> "Closed Beta" (& is replaced
            with "and")
        """
        if not self.game or not self.game.display_name:
            return self.name

        game_variations = [self.game.display_name]
        if "&" in self.game.display_name:
            game_variations.append(self.game.display_name.replace("&", "and"))
        if "and" in self.game.display_name:
            game_variations.append(self.game.display_name.replace("and", "&"))

        for game_name in game_variations:
            # Check for different separators after the game name
            for separator in [" - ", " | ", " "]:
                prefix_to_check = game_name + separator

                name: str = self.name
                if name.startswith(prefix_to_check):
                    return name.removeprefix(prefix_to_check).strip()

        return self.name

    @property
    def image_best_url(self) -> str:
        """Return the best URL for the campaign image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug(
                "Failed to resolve DropCampaign.image_file url: %s",
                exc,
            )
        return self.image_url or ""

    @property
    def is_subscription_only(self) -> bool:
        """Determine if the campaign is subscription only based on its benefits."""
        return any(drop.required_subs > 0 for drop in self.time_based_drops.all())  # pyright: ignore[reportAttributeAccessIssue]


# MARK: DropBenefit
class DropBenefit(models.Model):
    """Represents a benefit that can be earned from a drop."""

    twitch_id = models.TextField(
        unique=True,
        help_text="The Twitch ID for this benefit.",
        editable=False,
    )
    name = models.TextField(
        blank=True,
        default="N/A",
        help_text="Name of the drop benefit.",
    )
    image_asset_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="URL to the benefit's image asset.",
    )
    image_file = models.FileField(
        upload_to="benefits/images/",
        blank=True,
        null=True,
        help_text="Locally cached benefit image served from this site.",
    )
    created_at = models.DateTimeField(
        null=True,
        help_text=("Timestamp when the benefit was created. This is from Twitch API and not auto-generated."),
    )
    entitlement_limit = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of times this benefit can be earned.",
    )

    # NOTE: Default may need revisiting once requirements are confirmed.
    is_ios_available = models.BooleanField(
        default=False,
        help_text="Whether the benefit is available on iOS.",
    )
    distribution_type = models.TextField(
        max_length=50,
        blank=True,
        default="",
        help_text="Type of distribution for this benefit.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this benefit record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this benefit record was last updated.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["distribution_type"]),
            models.Index(fields=["is_ios_available"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop benefit."""
        return self.name

    @property
    def image_best_url(self) -> str:
        """Return the best URL for the benefit image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug(
                "Failed to resolve DropBenefit.image_file url: %s",
                exc,
            )
        return self.image_asset_url or ""


# MARK: DropBenefitEdge
class DropBenefitEdge(models.Model):
    """Link a TimeBasedDrop to a DropBenefit."""

    drop = models.ForeignKey(
        to="twitch.TimeBasedDrop",
        on_delete=models.CASCADE,
        help_text="The time-based drop in this relationship.",
    )
    benefit = models.ForeignKey(
        DropBenefit,
        on_delete=models.CASCADE,
        help_text="The benefit in this relationship.",
    )
    entitlement_limit = models.PositiveIntegerField(
        default=1,
        help_text="Max times this benefit can be claimed for this drop.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this drop-benefit edge was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this drop-benefit edge was last updated.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("drop", "benefit"),
                name="unique_drop_benefit",
            ),
        ]
        indexes = [
            models.Index(fields=["drop"]),
            models.Index(fields=["benefit"]),
            models.Index(fields=["entitlement_limit"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop benefit edge."""
        return f"{self.drop.name} - {self.benefit.name}"


# MARK: TimeBasedDrop
class TimeBasedDrop(models.Model):
    """Represents a time-based drop in a drop campaign."""

    twitch_id = models.TextField(
        unique=True,
        editable=False,
        help_text="The Twitch ID for this time-based drop.",
    )
    name = models.TextField(
        help_text="Name of the time-based drop.",
    )
    required_minutes_watched = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Minutes required to watch before earning this drop.",
    )
    required_subs = models.PositiveIntegerField(
        default=0,
        help_text="Number of subscriptions required to unlock this drop.",
    )
    start_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Datetime when this drop becomes available.",
    )
    end_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Datetime when this drop expires.",
    )

    # Foreign keys
    campaign = models.ForeignKey(
        DropCampaign,
        on_delete=models.CASCADE,
        related_name="time_based_drops",
        help_text="The campaign this drop belongs to.",
    )
    benefits = models.ManyToManyField(
        DropBenefit,
        through=DropBenefitEdge,
        related_name="drops",
        help_text="Benefits unlocked by this drop.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this time-based drop record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text=("Timestamp when this time-based drop record was last updated."),
    )

    class Meta:
        ordering = ["start_at"]
        indexes = [
            models.Index(fields=["start_at"]),
            models.Index(fields=["end_at"]),
            models.Index(fields=["campaign"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["required_minutes_watched"]),
            models.Index(fields=["required_subs"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
            # Composite indexes for common queries
            models.Index(fields=["campaign", "start_at"]),
            models.Index(fields=["campaign", "required_minutes_watched"]),
            models.Index(fields=["start_at", "end_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the time-based drop."""
        return self.name


# MARK: RewardCampaign
class RewardCampaign(models.Model):
    """Represents a Twitch reward campaign (Quest rewards)."""

    twitch_id = models.TextField(
        unique=True,
        editable=False,
        help_text="The Twitch ID for this reward campaign.",
    )
    name = models.TextField(
        help_text="Name of the reward campaign.",
    )
    brand = models.TextField(
        blank=True,
        default="",
        help_text="Brand associated with the reward campaign.",
    )
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Datetime when the reward campaign starts.",
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Datetime when the reward campaign ends.",
    )
    status = models.TextField(
        max_length=50,
        default="UNKNOWN",
        help_text="Status of the reward campaign.",
    )
    summary = models.TextField(
        blank=True,
        default="",
        help_text="Summary description of the reward campaign.",
    )
    instructions = models.TextField(
        blank=True,
        default="",
        help_text="Instructions for the reward campaign.",
    )
    external_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="External URL for the reward campaign.",
    )
    reward_value_url_param = models.TextField(
        blank=True,
        default="",
        help_text="URL parameter for reward value.",
    )
    about_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="About URL for the reward campaign.",
    )
    is_sitewide = models.BooleanField(
        default=False,
        help_text="Whether the reward campaign is sitewide.",
    )
    game = models.ForeignKey(
        Game,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reward_campaigns",
        help_text="Game associated with this reward campaign (if any).",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this reward campaign record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this reward campaign record was last updated.",
    )

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["-starts_at"]),
            models.Index(fields=["ends_at"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["brand"]),
            models.Index(fields=["status"]),
            models.Index(fields=["is_sitewide"]),
            models.Index(fields=["game"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
            # Composite indexes for common queries
            models.Index(fields=["starts_at", "ends_at"]),
            models.Index(fields=["status", "-starts_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the reward campaign."""
        return f"{self.brand}: {self.name}" if self.brand else self.name

    @property
    def is_active(self) -> bool:
        """Check if the reward campaign is currently active."""
        now: datetime.datetime = timezone.now()
        if self.starts_at is None or self.ends_at is None:
            return False
        return self.starts_at <= now <= self.ends_at
