from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlsplit, urlunsplit

import auto_prefetch
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone

from accounts.models import User

if TYPE_CHECKING:
    import datetime

logger: logging.Logger = logging.getLogger("ttvdrops")


# MARK: Organization
class Organization(auto_prefetch.Model):
    """Represents an organization on Twitch that can own drop campaigns."""

    id = models.CharField(
        max_length=255,
        primary_key=True,
        verbose_name="Organization ID",
        help_text="The unique Twitch identifier for the organization.",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        unique=True,
        verbose_name="Name",
        help_text="Display name of the organization.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this organization record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this organization record was last updated.",
    )

    # PostgreSQL full-text search field
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["name"]
        indexes: ClassVar[list] = [
            # Regular B-tree index for name lookups
            models.Index(fields=["name"]),
            # Full-text search index (GIN works with SearchVectorField)
            GinIndex(fields=["search_vector"], name="org_search_vector_idx"),
        ]

    def __str__(self) -> str:
        """Return a string representation of the organization."""
        return self.name or self.id


# MARK: Game
class Game(auto_prefetch.Model):
    """Represents a game on Twitch."""

    id = models.CharField(max_length=255, primary_key=True, verbose_name="Game ID")
    slug = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Slug",
        help_text="Short unique identifier for the game.",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Name",
    )
    display_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Display name",
    )
    box_art = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Box art URL",
    )
    # Locally cached image file for the game's box art
    box_art_file = models.FileField(
        upload_to="games/box_art/",
        blank=True,
        null=True,
        help_text="Locally cached box art image served from this site.",
    )

    # PostgreSQL full-text search field
    search_vector = SearchVectorField(null=True, blank=True)

    owner = auto_prefetch.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        related_name="games",
        null=True,
        blank=True,
        verbose_name="Organization",
        help_text="The organization that owns this game.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this game record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this game record was last updated.",
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["display_name"]
        indexes: ClassVar[list] = [
            models.Index(fields=["slug"]),
            # Regular B-tree indexes for name lookups
            models.Index(fields=["display_name"]),
            models.Index(fields=["name"]),
            # Full-text search index (GIN works with SearchVectorField)
            GinIndex(fields=["search_vector"], name="game_search_vector_idx"),
            # Partial index for games with owners only
            models.Index(fields=["owner"], condition=models.Q(owner__isnull=False), name="game_owner_partial_idx"),
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
        return self.display_name or self.name or self.slug or self.id

    @property
    def organizations(self) -> models.QuerySet[Organization]:
        """Return all organizations that own games with campaigns for this game."""
        return Organization.objects.filter(games__drop_campaigns__game=self).distinct()

    @property
    def box_art_base_url(self) -> str:
        """Return the base box art URL without Twitch size suffixes."""
        if not self.box_art:
            return ""
        parts = urlsplit(self.box_art)
        path = re.sub(
            r"(-\d+x\d+)(\.(?:jpg|jpeg|png|gif|webp))$",
            r"\2",
            parts.path,
            flags=re.IGNORECASE,
        )
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    @property
    def box_art_best_url(self) -> str:
        """Return the best available URL for the game's box art.

        Preference order:
        1) Local cached file (MEDIA)
        2) Remote Twitch base URL
        3) Empty string
        """
        try:
            if self.box_art_file and getattr(self.box_art_file, "url", None):
                return self.box_art_file.url
        except (AttributeError, OSError, ValueError) as exc:  # storage might not be configured in some contexts
            logger.debug("Failed to resolve Game.box_art_file url: %s", exc)
        return self.box_art_base_url

    @property
    def get_game_name(self) -> str:
        """Return the best available name for the game."""
        if self.display_name:
            return self.display_name
        if self.name:
            return self.name
        if self.slug:
            return self.slug
        return self.id

    @property
    def twitch_directory_url(self) -> str:
        """Return the Twitch directory URL for this game with drops filter if slug is available."""
        if self.slug:
            return f"https://www.twitch.tv/directory/category/{self.slug}?filter=drops"
        return ""


# MARK: TwitchGame
class TwitchGameData(auto_prefetch.Model):
    """Represents game metadata returned from the Twitch API.

    This mirrors the public Twitch API fields for a game and is tied to the local `Game` model where possible.

    Fields:
        id: Twitch game id (primary key)
        game: Optional FK to the local Game object
        name: Display name of the game
        box_art_url: URL template for box art with {width}x{height} placeholder
        igdb_id: Optional IGDB id for the game
    """

    id = models.CharField(max_length=255, primary_key=True, verbose_name="Twitch Game ID")
    game = auto_prefetch.ForeignKey(
        Game,
        on_delete=models.SET_NULL,
        related_name="twitch_game_data",
        null=True,
        blank=True,
        verbose_name="Game",
        help_text="Optional link to the local Game record for this Twitch game.",
    )

    name = models.CharField(max_length=255, blank=True, default="", db_index=True, verbose_name="Name")
    box_art_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Box art URL",
        help_text="URL template with {width}x{height} placeholders for the box art image.",
    )
    igdb_id = models.CharField(max_length=255, blank=True, default="", verbose_name="IGDB ID")

    added_at = models.DateTimeField(auto_now_add=True, db_index=True, help_text="Record creation time.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Record last update time.")

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["name"]
        indexes: ClassVar[list] = [
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name or self.id


# MARK: Channel
class Channel(auto_prefetch.Model):
    """Represents a Twitch channel that can participate in drop campaigns."""

    id = models.CharField(
        max_length=255,
        primary_key=True,
        verbose_name="Channel ID",
        help_text="The unique Twitch identifier for the channel.",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Username",
        help_text="The lowercase username of the channel.",
    )
    display_name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Display Name",
        help_text="The display name of the channel (with proper capitalization).",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this channel record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this channel record was last updated.",
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["display_name"]
        indexes: ClassVar[list] = [
            models.Index(fields=["name"]),
            models.Index(fields=["display_name"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the channel."""
        return self.display_name or self.name or self.id


# MARK: DropCampaign
class DropCampaign(auto_prefetch.Model):
    """Represents a Twitch drop campaign."""

    id = models.CharField(
        max_length=255,
        primary_key=True,
        help_text="Unique Twitch identifier for the campaign.",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
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
    # Locally cached campaign image
    image_file = models.FileField(
        upload_to="campaigns/images/",
        blank=True,
        null=True,
        help_text="Locally cached campaign image served from this site.",
    )
    start_at = models.DateTimeField(
        db_index=True,
        null=True,
        blank=True,
        help_text="Datetime when the campaign starts.",
    )
    end_at = models.DateTimeField(
        db_index=True,
        null=True,
        blank=True,
        help_text="Datetime when the campaign ends.",
    )
    is_account_connected = models.BooleanField(
        default=False,
        help_text="Indicates if the user account is linked.",
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

    # PostgreSQL full-text search field
    search_vector = SearchVectorField(null=True, blank=True)

    game = auto_prefetch.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="drop_campaigns",
        verbose_name="Game",
        help_text="Game associated with this campaign.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this campaign record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this campaign record was last updated.",
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["-start_at"]
        constraints = [
            # Ensure end_at is after start_at when both are set
            models.CheckConstraint(
                condition=models.Q(start_at__isnull=True) | models.Q(end_at__isnull=True) | models.Q(end_at__gt=models.F("start_at")),
                name="campaign_valid_date_range",
            ),
        ]
        indexes: ClassVar[list] = [
            # Regular B-tree index for campaign name lookups
            models.Index(fields=["name"]),
            # Full-text search index (GIN works with SearchVectorField)
            GinIndex(fields=["search_vector"], name="campaign_search_vector_idx"),
            # Composite index for time range queries
            models.Index(fields=["start_at", "end_at"]),
            # Partial index for active campaigns
            models.Index(fields=["start_at", "end_at"], condition=models.Q(start_at__isnull=False, end_at__isnull=False), name="campaign_active_partial_idx"),
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
            "Skull & Bones - Closed Beta" -> "Closed Beta" (& is replaced with "and")
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
        """Return the best available URL for the campaign image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve DropCampaign.image_file url: %s", exc)
        return self.image_url or ""


# MARK: DropBenefit
class DropBenefit(auto_prefetch.Model):
    """Represents a benefit that can be earned from a drop."""

    id = models.CharField(
        max_length=255,
        primary_key=True,
        help_text="Unique Twitch identifier for the benefit.",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
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
    # Locally cached benefit image
    image_file = models.FileField(
        upload_to="benefits/images/",
        blank=True,
        null=True,
        help_text="Locally cached benefit image served from this site.",
    )
    created_at = models.DateTimeField(
        null=True,
        db_index=True,
        help_text="Timestamp when the benefit was created. This is from Twitch API and not auto-generated.",
    )
    entitlement_limit = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of times this benefit can be earned.",
    )

    # TODO(TheLovinator): Check if this should be default True or False  # noqa: TD003
    is_ios_available = models.BooleanField(
        default=False,
        help_text="Whether the benefit is available on iOS.",
    )
    distribution_type = models.CharField(
        max_length=50,
        db_index=True,
        blank=True,
        default="",
        help_text="Type of distribution for this benefit.",
    )

    # PostgreSQL full-text search field
    search_vector = SearchVectorField(null=True, blank=True)

    added_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this benefit record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this benefit record was last updated.",
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["-created_at"]
        indexes: ClassVar[list] = [
            # Regular B-tree index for benefit name lookups
            models.Index(fields=["name"]),
            # Full-text search index (GIN works with SearchVectorField)
            GinIndex(fields=["search_vector"], name="benefit_search_vector_idx"),
            models.Index(fields=["created_at"]),
            models.Index(fields=["distribution_type"]),
            # Partial index for iOS available benefits
            models.Index(fields=["is_ios_available"], condition=models.Q(is_ios_available=True), name="benefit_ios_available_idx"),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop benefit."""
        return self.name

    @property
    def image_best_url(self) -> str:
        """Return the best available URL for the benefit image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve DropBenefit.image_file url: %s", exc)
        return self.image_asset_url or ""


# MARK: TimeBasedDrop
class TimeBasedDrop(auto_prefetch.Model):
    """Represents a time-based drop in a drop campaign."""

    id = models.CharField(
        max_length=255,
        primary_key=True,
        help_text="Unique Twitch identifier for the time-based drop.",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Name of the time-based drop.",
    )
    required_minutes_watched = models.PositiveIntegerField(
        db_index=True,
        null=True,
        blank=True,
        help_text="Minutes required to watch before earning this drop.",
    )
    required_subs = models.PositiveIntegerField(
        default=0,
        help_text="Number of subscriptions required to unlock this drop.",
    )
    start_at = models.DateTimeField(
        db_index=True,
        null=True,
        blank=True,
        help_text="Datetime when this drop becomes available.",
    )
    end_at = models.DateTimeField(
        db_index=True,
        null=True,
        blank=True,
        help_text="Datetime when this drop expires.",
    )

    # PostgreSQL full-text search field
    search_vector = SearchVectorField(null=True, blank=True)

    # Foreign keys
    campaign = auto_prefetch.ForeignKey(
        DropCampaign,
        on_delete=models.CASCADE,
        related_name="time_based_drops",
        help_text="The campaign this drop belongs to.",
    )
    benefits = models.ManyToManyField(
        DropBenefit,
        through="DropBenefitEdge",
        related_name="drops",
        help_text="Benefits unlocked by this drop.",
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this time-based drop record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this time-based drop record was last updated.",
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["start_at"]
        constraints = [
            # Ensure end_at is after start_at when both are set
            models.CheckConstraint(
                condition=models.Q(start_at__isnull=True) | models.Q(end_at__isnull=True) | models.Q(end_at__gt=models.F("start_at")),
                name="drop_valid_date_range",
            ),
            # Ensure required_minutes_watched is non-negative when set
            models.CheckConstraint(
                condition=models.Q(required_minutes_watched__isnull=True) | models.Q(required_minutes_watched__gte=0), name="drop_positive_minutes"
            ),
        ]
        indexes: ClassVar[list] = [
            # Regular B-tree index for drop name lookups
            models.Index(fields=["name"]),
            # Full-text search index (GIN works with SearchVectorField)
            GinIndex(fields=["search_vector"], name="drop_search_vector_idx"),
            models.Index(fields=["start_at", "end_at"]),
            models.Index(fields=["required_minutes_watched"]),
            # Covering index for common queries (includes campaign_id from FK)
            models.Index(fields=["campaign", "start_at", "required_minutes_watched"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the time-based drop."""
        return self.name


# MARK: DropBenefitEdge
class DropBenefitEdge(auto_prefetch.Model):
    """Represents the relationship between a TimeBasedDrop and a DropBenefit."""

    drop = auto_prefetch.ForeignKey(
        TimeBasedDrop,
        on_delete=models.CASCADE,
        help_text="The time-based drop in this relationship.",
    )
    benefit = auto_prefetch.ForeignKey(
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
        db_index=True,
        help_text="Timestamp when this drop-benefit edge was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this drop-benefit edge was last updated.",
    )

    class Meta(auto_prefetch.Model.Meta):
        constraints = [
            models.UniqueConstraint(fields=("drop", "benefit"), name="unique_drop_benefit"),
        ]
        indexes: ClassVar[list] = [
            models.Index(fields=["drop", "benefit"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the drop benefit edge."""
        return f"{self.drop.name} - {self.benefit.name}"


# MARK: NotificationSubscription
class NotificationSubscription(auto_prefetch.Model):
    """Users can subscribe to games to get notified."""

    user = auto_prefetch.ForeignKey(User, on_delete=models.CASCADE)
    game = auto_prefetch.ForeignKey(Game, null=True, blank=True, on_delete=models.CASCADE)
    organization = auto_prefetch.ForeignKey(Organization, null=True, blank=True, on_delete=models.CASCADE)

    notify_found = models.BooleanField(default=False)
    notify_live = models.BooleanField(default=False)

    added_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(auto_prefetch.Model.Meta):
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
