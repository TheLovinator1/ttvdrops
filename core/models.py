from django.db import models
from django.utils import timezone


class BlueskyPost(models.Model):
    """A drop campaign that has been published to Bluesky.

    One row is kept per platform/drop pair so live imports and backfill runs can
    never publish the same campaign twice. ``created_at`` is the timestamp used
    in the Bluesky record, which may be in the past for backfilled posts.
    """

    platform = models.CharField(max_length=20)
    drop_id = models.TextField()
    uri = models.TextField(blank=True, default="")
    cid = models.TextField(blank=True, default="")
    text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "drop_id"],
                name="unique_bluesky_post_per_drop",
            ),
        ]
        indexes = [
            models.Index(fields=["-created_at"], name="core_bluesky_created_desc"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.platform}:{self.drop_id} -> {self.uri or 'pending'}"
