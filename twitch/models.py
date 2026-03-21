import logging
from typing import TYPE_CHECKING

import auto_prefetch
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from twitch.utils import normalize_twitch_box_art_url

if TYPE_CHECKING:
    import datetime


logger: logging.Logger = logging.getLogger("ttvdrops")


# MARK: Organization
class Organization(auto_prefetch.Model):
    """Represents an organization on Twitch that can own drop campaigns."""

    twitch_id = models.TextField(
        help_text="The unique Twitch identifier for the organization.",
        verbose_name="Organization ID",
        editable=False,
        unique=True,
    )

    name = models.TextField(
        help_text="Display name of the organization.",
        verbose_name="Name",
        unique=True,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this organization record was created.",
        verbose_name="Added At",
        auto_now_add=True,
        editable=False,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this organization record was last updated.",
        verbose_name="Updated At",
        editable=False,
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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

    def feed_description(self: Organization) -> str:
        """Return a description of the organization for RSS feeds."""
        name: str = self.name or "Unknown Organization"
        url: str = f"{settings.BASE_URL}{reverse('twitch:organization_detail', args=[self.twitch_id])}"

        return format_html(
            '<p>New Twitch organization added to TTVDrops:</p>\n<p><a href="{}">{}</a></p>',
            url,
            name,
        )


# MARK: Game
class Game(auto_prefetch.Model):
    """Represents a game on Twitch."""

    twitch_id = models.TextField(verbose_name="Twitch game ID", unique=True)
    slug = models.TextField(
        help_text="Short unique identifier for the game.",
        verbose_name="Slug",
        max_length=200,
        blank=True,
        default="",
    )

    name = models.TextField(
        verbose_name="Name",
        blank=True,
        default="",
    )

    display_name = models.TextField(
        verbose_name="Display name",
        blank=True,
        default="",
    )

    box_art = models.URLField(  # noqa: DJ001
        verbose_name="Box art URL",
        max_length=500,
        blank=True,
        default="",
        null=True,
    )

    box_art_file = models.ImageField(
        help_text="Locally cached box art image served from this site.",
        height_field="box_art_height",
        width_field="box_art_width",
        upload_to="games/box_art/",
        blank=True,
        null=True,
    )

    box_art_width = models.PositiveIntegerField(
        help_text="Width of cached box art image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    box_art_height = models.PositiveIntegerField(
        help_text="Height of cached box art image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    box_art_size_bytes = models.PositiveIntegerField(
        help_text="File size of the cached box art image in bytes.",
        editable=False,
        blank=True,
        null=True,
    )

    box_art_mime_type = models.CharField(
        help_text="MIME type of the cached box art image (e.g., 'image/png').",
        editable=False,
        max_length=50,
        blank=True,
        default="",
    )

    owners = models.ManyToManyField(
        help_text="Organizations that own this game.",
        verbose_name="Organizations",
        related_name="games",
        to=Organization,
        blank=True,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this game record was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this game record was last updated.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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

    def get_absolute_url(self) -> str:
        """Return canonical URL to the game details page."""
        return reverse("game_detail", kwargs={"twitch_id": self.twitch_id})

    @property
    def organizations(self) -> models.QuerySet[Organization]:
        """Return orgs that own games with campaigns for this game."""
        return Organization.objects.filter(games__drop_campaigns__game=self).distinct()

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
        return normalize_twitch_box_art_url(self.box_art or "")

    @property
    def image_best_url(self) -> str:
        """Alias for box_art_best_url to provide a common interface with benefits."""
        return self.box_art_best_url


# MARK: TwitchGame
class TwitchGameData(auto_prefetch.Model):
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
        help_text="The Twitch ID for this game.",
        verbose_name="Twitch Game ID",
        unique=True,
    )

    game = auto_prefetch.ForeignKey(
        help_text="Optional link to the local Game record for this Twitch game.",
        related_name="twitch_game_data",
        on_delete=models.SET_NULL,
        verbose_name="Game",
        blank=True,
        null=True,
        to=Game,
    )

    name = models.TextField(
        verbose_name="Name",
        blank=True,
        default="",
    )

    box_art_url = models.URLField(
        help_text="URL template with {width}x{height} placeholders for the box art image.",
        verbose_name="Box art URL",
        blank=True,
        default="",
        max_length=500,
    )

    igdb_id = models.TextField(
        verbose_name="IGDB ID",
        blank=True,
        default="",
    )

    added_at = models.DateTimeField(
        help_text="Record creation time.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Record last update time.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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
class Channel(auto_prefetch.Model):
    """Represents a Twitch channel that can participate in drop campaigns."""

    twitch_id = models.TextField(
        help_text="The unique Twitch identifier for the channel.",
        verbose_name="Channel ID",
        unique=True,
    )

    name = models.TextField(
        help_text="The lowercase username of the channel.",
        verbose_name="Username",
    )

    display_name = models.TextField(
        help_text="The display name of the channel (with proper capitalization).",
        verbose_name="Display Name",
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this channel record was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this channel record was last updated.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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
class DropCampaign(auto_prefetch.Model):
    """Represents a Twitch drop campaign."""

    twitch_id = models.TextField(
        help_text="The Twitch ID for this campaign.",
        editable=False,
        unique=True,
    )

    name = models.TextField(
        help_text="Name of the drop campaign.",
    )

    description = models.TextField(
        help_text="Detailed description of the campaign.",
        blank=True,
    )

    details_url = models.URLField(
        help_text="URL with campaign details.",
        max_length=500,
        blank=True,
        default="",
    )

    account_link_url = models.URLField(
        help_text="URL to link a Twitch account for the campaign.",
        max_length=500,
        blank=True,
        default="",
    )

    image_url = models.URLField(
        help_text="URL to an image representing the campaign.",
        max_length=500,
        blank=True,
        default="",
    )

    image_file = models.ImageField(
        help_text="Locally cached campaign image served from this site.",
        upload_to="campaigns/images/",
        height_field="image_height",
        width_field="image_width",
        blank=True,
        null=True,
    )

    image_width = models.PositiveIntegerField(
        help_text="Width of cached image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    image_height = models.PositiveIntegerField(
        help_text="Height of cached image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    image_size_bytes = models.PositiveIntegerField(
        help_text="File size of the cached campaign image in bytes.",
        editable=False,
        blank=True,
        null=True,
    )

    image_mime_type = models.CharField(
        help_text="MIME type of the cached campaign image (e.g., 'image/png').",
        editable=False,
        max_length=50,
        blank=True,
        default="",
    )

    start_at = models.DateTimeField(
        help_text="Datetime when the campaign starts.",
        blank=True,
        null=True,
    )

    end_at = models.DateTimeField(
        help_text="Datetime when the campaign ends.",
        blank=True,
        null=True,
    )

    allow_is_enabled = models.BooleanField(
        help_text="Whether the campaign allows participation.",
        default=True,
    )

    allow_channels = models.ManyToManyField(
        help_text="Channels that are allowed to participate in this campaign.",
        related_name="allowed_campaigns",
        to=Channel,
        blank=True,
    )

    game = auto_prefetch.ForeignKey(
        help_text="Game associated with this campaign.",
        related_name="drop_campaigns",
        on_delete=models.CASCADE,
        verbose_name="Game",
        to=Game,
    )

    operation_names = models.JSONField(
        help_text="List of GraphQL operation names used to fetch this campaign data (e.g., ['ViewerDropsDashboard', 'Inventory']).",
        default=list,
        blank=True,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this campaign record was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this campaign record was last updated.",
        auto_now=True,
    )

    is_fully_imported = models.BooleanField(
        help_text="True if all images and formats are imported and ready for display.",
        default=False,
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["-start_at"]
        indexes = [
            models.Index(fields=["-start_at"]),
            models.Index(fields=["end_at"]),
            models.Index(fields=["game"]),
            models.Index(fields=["twitch_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["description"]),
            models.Index(fields=["allow_is_enabled"]),
            GinIndex(fields=["operation_names"], name="twitch_drop_operati_gin_idx"),
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
        """Return the best URL for the campaign image.

        Priority:
        1. Local cached image file
        2. Campaign image URL
        3. First benefit image URL (if campaign has no image)
        """
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve DropCampaign.image_file url: %s", exc)

        if self.image_url:
            return self.image_url

        # If no campaign image, use the first benefit image
        for drop in self.time_based_drops.all():  # pyright: ignore[reportAttributeAccessIssue]
            for benefit in drop.benefits.all():  # pyright: ignore[reportAttributeAccessIssue]
                benefit_image_url: str = benefit.image_best_url
                if benefit_image_url:
                    return benefit_image_url

        return ""

    @property
    def duration_iso(self) -> str:
        """Return the campaign duration in ISO 8601 format (e.g., 'P3DT4H30M').

        This is used for the <time> element's datetime attribute to provide
        machine-readable duration. If start_at or end_at is missing, returns
        an empty string.
        """
        if not self.start_at or not self.end_at:
            return ""

        total_seconds: int = int((self.end_at - self.start_at).total_seconds())
        if total_seconds < 0:
            total_seconds = abs(total_seconds)

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        time_parts: list[str] = []
        if hours:
            time_parts.append(f"{hours}H")
        if minutes:
            time_parts.append(f"{minutes}M")
        if seconds or not time_parts:
            time_parts.append(f"{seconds}S")

        if days and time_parts:
            return f"P{days}DT{''.join(time_parts)}"
        if days:
            return f"P{days}D"
        return f"PT{''.join(time_parts)}"

    @property
    def is_subscription_only(self) -> bool:
        """Determine if the campaign is subscription only based on its benefits."""
        return any(drop.required_subs > 0 for drop in self.time_based_drops.all())  # pyright: ignore[reportAttributeAccessIssue]

    @property
    def sorted_benefits(self) -> list[DropBenefit]:
        """Return a sorted list of benefits for the campaign."""
        benefits: list[DropBenefit] = []
        for drop in self.time_based_drops.all():  # pyright: ignore[reportAttributeAccessIssue]
            benefits.extend(drop.benefits.all())  # pyright: ignore[reportAttributeAccessIssue]
        return sorted(benefits, key=lambda benefit: benefit.name)


# MARK: DropBenefit
class DropBenefit(auto_prefetch.Model):
    """Represents a benefit that can be earned from a drop."""

    twitch_id = models.TextField(
        help_text="The Twitch ID for this benefit.",
        editable=False,
        unique=True,
    )

    name = models.TextField(
        help_text="Name of the drop benefit.",
        default="N/A",
        blank=True,
    )

    image_asset_url = models.URLField(
        help_text="URL to the benefit's image asset.",
        max_length=500,
        blank=True,
        default="",
    )

    image_file = models.ImageField(
        help_text="Locally cached benefit image served from this site.",
        upload_to="benefits/images/",
        height_field="image_height",
        width_field="image_width",
        blank=True,
        null=True,
    )

    image_width = models.PositiveIntegerField(
        help_text="Width of cached image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    image_height = models.PositiveIntegerField(
        help_text="Height of cached image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        help_text="Timestamp when the benefit was created. This is from Twitch API and not auto-generated.",
        null=True,
    )

    entitlement_limit = models.PositiveIntegerField(
        help_text="Maximum number of times this benefit can be earned.",
        default=1,
    )

    is_ios_available = models.BooleanField(
        help_text="Whether the benefit is available on iOS.",
        default=False,
    )

    distribution_type = models.TextField(
        help_text="Type of distribution for this benefit.",
        max_length=50,
        blank=True,
        default="",
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this benefit record was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this benefit record was last updated.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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
            logger.debug("Failed to resolve DropBenefit.image_file url: %s", exc)
        return self.image_asset_url or ""


# MARK: DropBenefitEdge
class DropBenefitEdge(auto_prefetch.Model):
    """Link a TimeBasedDrop to a DropBenefit."""

    drop = auto_prefetch.ForeignKey(
        help_text="The time-based drop in this relationship.",
        to="twitch.TimeBasedDrop",
        on_delete=models.CASCADE,
    )

    benefit = auto_prefetch.ForeignKey(
        help_text="The benefit in this relationship.",
        on_delete=models.CASCADE,
        to=DropBenefit,
    )

    entitlement_limit = models.PositiveIntegerField(
        help_text="Max times this benefit can be claimed for this drop.",
        default=1,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this drop-benefit edge was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this drop-benefit edge was last updated.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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
class TimeBasedDrop(auto_prefetch.Model):
    """Represents a time-based drop in a drop campaign."""

    twitch_id = models.TextField(
        help_text="The Twitch ID for this time-based drop.",
        editable=False,
        unique=True,
    )

    name = models.TextField(
        help_text="Name of the time-based drop.",
    )

    required_minutes_watched = models.PositiveIntegerField(
        help_text="Minutes required to watch before earning this drop.",
        blank=True,
        null=True,
    )

    required_subs = models.PositiveIntegerField(
        help_text="Number of subscriptions required to unlock this drop.",
        default=0,
    )

    start_at = models.DateTimeField(
        help_text="Datetime when this drop becomes available.",
        blank=True,
        null=True,
    )

    end_at = models.DateTimeField(
        help_text="Datetime when this drop expires.",
        blank=True,
        null=True,
    )

    campaign = auto_prefetch.ForeignKey(
        help_text="The campaign this drop belongs to.",
        related_name="time_based_drops",
        on_delete=models.CASCADE,
        to=DropCampaign,
    )

    benefits = models.ManyToManyField(
        help_text="Benefits unlocked by this drop.",
        through=DropBenefitEdge,
        related_name="drops",
        to=DropBenefit,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this time-based drop record was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this time-based drop record was last updated.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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
class RewardCampaign(auto_prefetch.Model):
    """Represents a Twitch reward campaign (Quest rewards)."""

    twitch_id = models.TextField(
        help_text="The Twitch ID for this reward campaign.",
        editable=False,
        unique=True,
    )

    name = models.TextField(
        help_text="Name of the reward campaign.",
    )

    brand = models.TextField(
        help_text="Brand associated with the reward campaign.",
        blank=True,
        default="",
    )

    starts_at = models.DateTimeField(
        help_text="Datetime when the reward campaign starts.",
        null=True,
        blank=True,
    )

    ends_at = models.DateTimeField(
        help_text="Datetime when the reward campaign ends.",
        null=True,
        blank=True,
    )

    status = models.TextField(
        max_length=50,
        help_text="Status of the reward campaign.",
        default="UNKNOWN",
    )

    summary = models.TextField(
        help_text="Summary description of the reward campaign.",
        blank=True,
        default="",
    )

    instructions = models.TextField(
        help_text="Instructions for the reward campaign.",
        blank=True,
        default="",
    )

    external_url = models.URLField(
        max_length=500,
        help_text="External URL for the reward campaign.",
        blank=True,
        default="",
    )

    reward_value_url_param = models.TextField(
        help_text="URL parameter for reward value.",
        blank=True,
        default="",
    )

    about_url = models.URLField(
        max_length=500,
        help_text="About URL for the reward campaign.",
        blank=True,
        default="",
    )

    image_url = models.URLField(
        max_length=500,
        help_text="URL to an image representing the reward campaign.",
        blank=True,
        default="",
    )

    image_file = models.ImageField(
        help_text="Locally cached reward campaign image served from this site.",
        upload_to="reward_campaigns/images/",
        width_field="image_width",
        height_field="image_height",
        blank=True,
        null=True,
    )

    image_width = models.PositiveIntegerField(
        help_text="Width of cached image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    image_height = models.PositiveIntegerField(
        help_text="Height of cached image in pixels.",
        editable=False,
        blank=True,
        null=True,
    )

    is_sitewide = models.BooleanField(
        help_text="Whether the reward campaign is sitewide.",
        default=False,
    )

    game = auto_prefetch.ForeignKey(
        help_text="Game associated with this reward campaign (if any).",
        related_name="reward_campaigns",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        to=Game,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this reward campaign record was created.",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this reward campaign record was last updated.",
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
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

    @property
    def image_best_url(self) -> str:
        """Return the best URL for the reward campaign image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve RewardCampaign.image_file url: %s", exc)
        return self.image_url or ""


# MARK: ChatBadgeSet
class ChatBadgeSet(auto_prefetch.Model):
    """Represents a set of Twitch global chat badges (e.g., VIP, Subscriber, Bits)."""

    set_id = models.TextField(
        help_text="Identifier for this badge set (e.g., 'vip', 'subscriber', 'bits').",
        verbose_name="Set ID",
        unique=True,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this badge set record was created.",
        verbose_name="Added At",
        auto_now_add=True,
        editable=False,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this badge set record was last updated.",
        verbose_name="Updated At",
        editable=False,
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["set_id"]
        indexes = [
            models.Index(fields=["set_id"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the badge set."""
        return self.set_id


# MARK: ChatBadge
class ChatBadge(auto_prefetch.Model):
    """Represents a specific version of a Twitch global chat badge."""

    badge_set = auto_prefetch.ForeignKey(
        help_text="The badge set this badge belongs to.",
        on_delete=models.CASCADE,
        verbose_name="Badge Set",
        related_name="badges",
        to=ChatBadgeSet,
    )

    badge_id = models.TextField(
        help_text="Version identifier for this badge (e.g., '1', 'Alliance', '10000').",
        verbose_name="Badge ID",
    )

    image_url_1x = models.URLField(
        help_text="URL to the small version (18px x 18px) of the badge.",
        verbose_name="Image URL (18px)",
        max_length=500,
    )

    image_url_2x = models.URLField(
        help_text="URL to the medium version (36px x 36px) of the badge.",
        verbose_name="Image URL (36px)",
        max_length=500,
    )

    image_url_4x = models.URLField(
        help_text="URL to the large version (72px x 72px) of the badge.",
        verbose_name="Image URL (72px)",
        max_length=500,
    )

    title = models.TextField(
        help_text="The title of the badge (e.g., 'VIP').",
        verbose_name="Title",
    )

    description = models.TextField(
        help_text="The description of the badge.",
        verbose_name="Description",
    )

    click_action = models.TextField(  # noqa: DJ001
        help_text="The action to take when clicking on the badge (e.g., 'visit_url').",
        verbose_name="Click Action",
        blank=True,
        null=True,
    )

    click_url = models.URLField(  # noqa: DJ001
        help_text="The URL to navigate to when clicking on the badge.",
        verbose_name="Click URL",
        max_length=500,
        blank=True,
        null=True,
    )

    added_at = models.DateTimeField(
        help_text="Timestamp when this badge record was created.",
        verbose_name="Added At",
        auto_now_add=True,
        editable=False,
    )

    updated_at = models.DateTimeField(
        help_text="Timestamp when this badge record was last updated.",
        verbose_name="Updated At",
        editable=False,
        auto_now=True,
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["badge_set", "badge_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["badge_set", "badge_id"],
                name="unique_badge_set_id",
            ),
        ]
        indexes = [
            models.Index(fields=["badge_set"]),
            models.Index(fields=["badge_id"]),
            models.Index(fields=["title"]),
            models.Index(fields=["added_at"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the badge."""
        return f"{self.badge_set.set_id}/{self.badge_id}: {self.title}"
