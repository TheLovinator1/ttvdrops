from django.db import models
from django.utils import timezone


class ChzzkCampaign(models.Model):
    """Chzzk campaign, including scraping metadata."""

    campaign_no = models.BigIntegerField()
    title = models.CharField(max_length=255)
    image_url = models.URLField()
    description = models.TextField()
    category_type = models.CharField(max_length=64)
    category_id = models.CharField(max_length=128)
    category_value = models.CharField(max_length=128)
    pc_link_url = models.URLField()
    mobile_link_url = models.URLField()
    service_id = models.CharField(max_length=128)
    state = models.CharField(max_length=64)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    has_ios_based_reward = models.BooleanField()
    drops_campaign_not_started = models.BooleanField()
    campaign_reward_type = models.CharField(max_length=64, blank=True, default="")
    reward_type = models.CharField(max_length=64, blank=True, default="")
    account_link_url = models.URLField()

    # Scraping metadata
    scraped_at = models.DateTimeField(default=timezone.now)
    source_api = models.CharField(max_length=16)  # 'v1' or 'v2'
    scrape_status = models.CharField(max_length=32, default="success")
    raw_json = models.JSONField()

    class Meta:
        unique_together = ("campaign_no", "source_api")
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
    image_url = models.URLField()
    title = models.CharField(max_length=255)
    reward_type = models.CharField(max_length=64)
    campaign_reward_type = models.CharField(max_length=64, blank=True, default="")
    condition_type = models.CharField(max_length=64)
    condition_for_minutes = models.IntegerField()
    ios_based_reward = models.BooleanField()
    code_remaining_count = models.IntegerField()

    class Meta:
        unique_together = ("campaign", "reward_no")

    def __str__(self) -> str:
        return f"{self.title} (#{self.reward_no})"
