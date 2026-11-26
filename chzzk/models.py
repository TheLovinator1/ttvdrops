import logging
from pathlib import Path
from urllib.parse import unquote
from urllib.parse import urlparse

from django.db import models
from django.urls import reverse
from django.utils import timezone

logger: logging.Logger = logging.getLogger("ttvdrops")


def _decode_filename(image_url: str) -> str:
    """Return the decoded filename from an image URL."""
    if not image_url:
        return ""
    return unquote(Path(urlparse(image_url).path).name)


class ChzzkCampaign(models.Model):
    """Chzzk campaign, including scraping metadata."""

    campaign_no = models.BigIntegerField()
    title = models.TextField()
    image_url = models.URLField(
        max_length=2000,
        blank=True,
    )
    image_file = models.ImageField(
        help_text="Locally cached campaign image served from this site.",
        upload_to="chzzk/campaigns/images/",
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
    description = models.TextField()
    category_type = models.TextField()
    category_id = models.TextField()
    category_value = models.TextField()
    pc_link_url = models.URLField(
        max_length=2000,
        blank=True,
    )
    mobile_link_url = models.URLField(
        max_length=2000,
        blank=True,
    )
    service_id = models.TextField()
    state = models.TextField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    has_ios_based_reward = models.BooleanField()
    drops_campaign_not_started = models.BooleanField()
    campaign_reward_type = models.TextField(blank=True, default="")
    reward_type = models.TextField(blank=True, default="")
    account_link_url = models.URLField(
        max_length=2000,
        blank=True,
    )

    # Scraping metadata
    scraped_at = models.DateTimeField(default=timezone.now)
    source_api = models.TextField()
    scrape_status = models.TextField(default="success")
    raw_json_v1 = models.JSONField(null=True, blank=True)
    raw_json_v2 = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return f"{self.title} (#{self.campaign_no})"

    @property
    def image_best_url(self) -> str:
        """The best available URL for the campaign image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve ChzzkCampaign.image_file url: %s", exc)
        return self.image_url or ""

    @property
    def image_filename(self) -> str:
        """The decoded filename of the source image URL."""
        return _decode_filename(self.image_url)

    @property
    def chzzk_api_url(self) -> str:
        """The raw Chzzk API endpoint for this campaign."""
        return (
            f"https://api.chzzk.naver.com/service/v2/drops/campaigns/{self.campaign_no}"
        )

    @property
    def category_id_display(self) -> str:
        """Human-readable version of the category ID."""
        return self.category_id.replace("_", " ")

    @property
    def category_display(self) -> str:
        """Readable category label with its ID when they differ."""
        category_value: str = self.category_value or self.category_id_display
        if self.category_id_display and self.category_id_display != category_value:
            return f"{category_value} ({self.category_id_display})"
        return category_value

    @property
    def category_url(self) -> str:
        """The archive URL for the campaign's category."""
        if self.category_type.casefold() == "game":
            return reverse("chzzk:game_detail", args=[self.category_id])
        return reverse(
            "chzzk:category_detail",
            args=[self.category_type, self.category_id],
        )


class ChzzkReward(models.Model):
    """Chzzk reward belonging to a campaign."""

    campaign = models.ForeignKey(
        ChzzkCampaign,
        related_name="rewards",
        on_delete=models.CASCADE,
    )
    reward_no = models.BigIntegerField()
    image_url = models.URLField(
        max_length=2000,
        blank=True,
    )
    image_file = models.ImageField(
        help_text="Locally cached reward image served from this site.",
        upload_to="chzzk/rewards/images/",
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
    title = models.TextField()
    reward_type = models.TextField()
    campaign_reward_type = models.TextField(blank=True, default="")
    condition_type = models.TextField()
    condition_for_minutes = models.IntegerField()
    ios_based_reward = models.BooleanField()
    code_remaining_count = models.IntegerField()

    class Meta:
        unique_together = ("campaign", "reward_no")

    def __str__(self) -> str:
        return f"{self.title} (#{self.reward_no})"

    @property
    def image_best_url(self) -> str:
        """The best available URL for the reward image (local first)."""
        try:
            if self.image_file and getattr(self.image_file, "url", None):
                return self.image_file.url
        except (AttributeError, OSError, ValueError) as exc:
            logger.debug("Failed to resolve ChzzkReward.image_file url: %s", exc)
        return self.image_url or ""

    @property
    def image_filename(self) -> str:
        """The decoded filename of the source image URL."""
        return _decode_filename(self.image_url)
