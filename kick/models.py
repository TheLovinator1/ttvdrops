import logging
import re
from typing import TYPE_CHECKING

import auto_prefetch
from django.db import models
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    import datetime

logger: logging.Logger = logging.getLogger("ttvdrops")

KICK_IMAGE_BASE_URL = "https://files.kick.com/"


# MARK: KickOrganization
class KickOrganization(auto_prefetch.Model):
    """Represents an organization on Kick that owns drop campaigns."""

    kick_id = models.TextField(
        unique=True,
        editable=False,
        verbose_name="Kick Organization ID",
        help_text="ULID string identifier from the Kick API.",
    )
    name = models.TextField(verbose_name="Name")
    logo_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Logo URL",
    )
    url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="URL",
    )
    restricted = models.BooleanField(default=False, verbose_name="Restricted")

    added_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["name"]
        verbose_name = "Kick Organization"
        verbose_name_plural = "Kick Organizations"
        indexes = [
            models.Index(fields=["kick_id"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:
        return self.name or self.kick_id


# MARK: KickCategory
class KickCategory(auto_prefetch.Model):
    """Represents a game/category on Kick."""

    kick_id = models.PositiveIntegerField(
        unique=True,
        editable=False,
        verbose_name="Kick Category ID",
        help_text="Integer identifier from the Kick API.",
    )
    name = models.TextField(verbose_name="Name")
    slug = models.SlugField(max_length=200, blank=True, default="", verbose_name="Slug")
    image_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Image URL",
    )

    added_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["name"]
        verbose_name = "Kick Category"
        verbose_name_plural = "Kick Categories"
        indexes = [
            models.Index(fields=["kick_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self) -> str:
        return self.name or str(self.kick_id)

    @property
    def get_absolute_url(self) -> str:
        """Return the URL to the game detail page."""
        return reverse("kick:game_detail", args=[self.kick_id])

    @property
    def kick_url(self) -> str:
        """Return the URL to the game page on Kick."""
        return f"https://kick.com/category/{self.slug}" if self.slug else ""


# MARK: KickUser
class KickUser(auto_prefetch.Model):
    """Represents a Kick user associated with a channel."""

    kick_id = models.PositiveBigIntegerField(
        unique=True,
        editable=False,
        verbose_name="Kick User ID",
    )
    username = models.TextField(verbose_name="Username")
    profile_picture = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Profile Picture URL",
    )

    added_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["username"]
        verbose_name = "Kick User"
        verbose_name_plural = "Kick Users"
        indexes = [
            models.Index(fields=["kick_id"]),
            models.Index(fields=["username"]),
        ]

    def __str__(self) -> str:
        return self.username or str(self.kick_id)

    @property
    def kick_profile_url(self) -> str:
        """Return the Kick profile URL for this user."""
        return f"https://kick.com/{self.username}" if self.username else ""


# MARK: KickChannel
class KickChannel(auto_prefetch.Model):
    """Represents a Kick channel that participates in drop campaigns."""

    kick_id = models.PositiveBigIntegerField(
        unique=True,
        editable=False,
        verbose_name="Kick Channel ID",
    )
    slug = models.TextField(blank=True, default="", verbose_name="Slug")
    description = models.TextField(blank=True, default="", verbose_name="Description")
    banner_picture_url = models.TextField(
        blank=True,
        default="",
        verbose_name="Banner Picture URL",
        help_text="May be empty or a relative path.",
    )
    user = auto_prefetch.ForeignKey(
        KickUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="channels",
        verbose_name="User",
    )

    added_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["slug"]
        verbose_name = "Kick Channel"
        verbose_name_plural = "Kick Channels"
        indexes = [
            models.Index(fields=["kick_id"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self) -> str:
        return self.slug or str(self.kick_id)

    @property
    def channel_url(self) -> str:
        """Return the Kick channel URL."""
        return f"https://kick.com/{self.slug}" if self.slug else ""


# MARK: KickDropCampaign
class KickDropCampaign(auto_prefetch.Model):
    """Represents a Kick drop campaign."""

    kick_id = models.TextField(
        unique=True,
        editable=False,
        verbose_name="Kick Campaign ID",
        help_text="ULID string identifier from the Kick API.",
    )
    name = models.TextField(verbose_name="Name")
    status = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Status",
        help_text="e.g. 'active' or 'expired'.",
    )
    starts_at = models.DateTimeField(null=True, blank=True, verbose_name="Starts At")
    ends_at = models.DateTimeField(null=True, blank=True, verbose_name="Ends At")
    connect_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Connect URL",
        help_text="URL to link an account for the campaign.",
    )
    url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="URL",
    )
    rule_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="Rule ID")
    rule_name = models.TextField(blank=True, default="", verbose_name="Rule Name")

    organization = auto_prefetch.ForeignKey(
        KickOrganization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name="Organization",
    )
    category = auto_prefetch.ForeignKey(
        KickCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name="Category",
    )
    channels = models.ManyToManyField(
        KickChannel,
        blank=True,
        related_name="campaigns",
        verbose_name="Channels",
    )

    created_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Created At (Kick)",
        help_text="When the campaign was created on Kick.",
    )
    api_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Updated At (Kick)",
        help_text="When the campaign was last updated on Kick.",
    )
    added_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)
    is_fully_imported = models.BooleanField(
        default=False,
        help_text="True if all images and formats are imported and ready for display.",
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["-starts_at"]
        verbose_name = "Kick Drop Campaign"
        verbose_name_plural = "Kick Drop Campaigns"
        indexes = [
            models.Index(fields=["kick_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["status"]),
            models.Index(fields=["-starts_at"]),
            models.Index(fields=["ends_at"]),
        ]

    def __str__(self) -> str:
        return self.name or self.kick_id

    @property
    def is_active(self) -> bool:
        """Check if the campaign is currently active based on dates."""
        now: datetime.datetime = timezone.now()
        if self.starts_at is None or self.ends_at is None:
            return False
        return self.starts_at <= now <= self.ends_at

    @property
    def image_url(self) -> str:
        """Return the image URL for the campaign."""
        # Image from first drop
        if self.rewards.exists():  # pyright: ignore[reportAttributeAccessIssue]
            first_reward: KickReward | None = self.rewards.first()  # pyright: ignore[reportAttributeAccessIssue]
            if first_reward and first_reward.image_url:
                return first_reward.full_image_url

        if self.category and self.category.image_url:
            return self.category.image_url

        if self.organization and self.organization.logo_url:
            return self.organization.logo_url

        return ""

    @property
    def duration(self) -> str | None:
        """Human-readable duration of the campaign, or None if not available.

        Uses Django's timesince filter format (e.g. "2 days, 3 hours").
        """
        if self.starts_at and self.ends_at:
            delta: datetime.timedelta = self.ends_at - self.starts_at
            total_seconds: int = int(delta.total_seconds())
            days, remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, _ = divmod(remainder, 60)

            parts: list[str] = []
            if days > 0:
                parts.append(f"{days} day{'s' if days != 1 else ''}")
            if hours > 0:
                parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0:
                parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

            return ", ".join(parts) if parts else "0 minutes"
        return None

    @staticmethod
    def _normalized_reward_name(name: str) -> str:
        """Normalize reward names to merge console/connected variants.

        Some Kick rewards appear twice with and without a trailing "(Con)" marker,
        and occasionally differ only by spacing around punctuation like "&".

        Returns:
            A normalized, case-insensitive reward name key.
        """
        normalized: str = name.strip()
        normalized = re.sub(r"\s*\(con\)\s*$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*&\s*", " & ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.casefold()

    @property
    def merged_rewards(self) -> list[KickReward]:
        """Return rewards de-duplicated by normalized name.

        If both a base reward and a "(Con)" variant exist, prefer the base reward name.
        """
        rewards_by_name: dict[str, KickReward] = {}
        for reward in self.rewards.all().order_by("required_units", "name", "kick_id"):  # pyright: ignore[reportAttributeAccessIssue]
            key: str = self._normalized_reward_name(reward.name)
            existing: KickReward | None = rewards_by_name.get(key)
            if existing is None:
                rewards_by_name[key] = reward
                continue

            existing_is_con: bool = existing.name.strip().casefold().endswith("(con)")
            reward_is_con: bool = reward.name.strip().casefold().endswith("(con)")
            if existing_is_con and not reward_is_con:
                rewards_by_name[key] = reward

        return list(rewards_by_name.values())


# MARK: KickReward
class KickReward(auto_prefetch.Model):
    """Represents a reward that can be earned from a Kick drop campaign."""

    kick_id = models.TextField(
        unique=True,
        editable=False,
        verbose_name="Kick Reward ID",
        help_text="ULID string identifier from the Kick API.",
    )
    name = models.TextField(verbose_name="Name")
    image_url = models.TextField(
        blank=True,
        default="",
        verbose_name="Image URL",
        help_text="May be a relative path (e.g. 'drops/reward-image/...').",
    )
    required_units = models.PositiveIntegerField(
        default=0,
        verbose_name="Required Units",
        help_text="Number of watch-minutes required to earn this reward.",
    )
    campaign = auto_prefetch.ForeignKey(
        KickDropCampaign,
        on_delete=models.CASCADE,
        related_name="rewards",
        verbose_name="Campaign",
    )
    category = auto_prefetch.ForeignKey(
        KickCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rewards",
        verbose_name="Category",
    )
    organization = auto_prefetch.ForeignKey(
        KickOrganization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rewards",
        verbose_name="Organization",
    )

    added_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["required_units", "name"]
        verbose_name = "Kick Reward"
        verbose_name_plural = "Kick Rewards"
        indexes = [
            models.Index(fields=["kick_id"]),
            models.Index(fields=["required_units"]),
        ]

    def __str__(self) -> str:
        return self.name or self.kick_id

    @property
    def full_image_url(self) -> str:
        """Return the absolute image URL for this reward.

        If the image_url is a relative path, prepend the Kick image base URL.
        """
        if not self.image_url:
            return ""
        if self.image_url.startswith("http"):
            return self.image_url
        return "https://ext.cdn.kick.com/" + self.image_url
