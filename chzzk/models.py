from django.db import models
from django.utils import timezone


class ChzzkCampaign(models.Model):
    """Chzzk campaign, including scraping metadata."""

    campaign_no = models.BigIntegerField()
    title = models.TextField()
    image_url = models.URLField(
        max_length=2000,
        blank=True,
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
