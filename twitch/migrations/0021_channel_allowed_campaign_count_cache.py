from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import migrations
from django.db import models

if TYPE_CHECKING:
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.state import StateApps

    from twitch.models import Channel
    from twitch.models import DropCampaign


def backfill_allowed_campaign_count(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    """Populate Channel.allowed_campaign_count from the M2M through table."""
    del schema_editor

    Channel: type[Channel] = apps.get_model("twitch", "Channel")
    DropCampaign: type[DropCampaign] = apps.get_model("twitch", "DropCampaign")
    through_model: type[Channel] = DropCampaign.allow_channels.through

    counts_by_channel = {
        row["channel_id"]: row["campaign_count"]
        for row in (
            through_model.objects.values("channel_id").annotate(
                campaign_count=models.Count("dropcampaign_id"),
            )
        )
    }

    channels: list[Channel] = list(
        Channel.objects.all().only("id", "allowed_campaign_count"),
    )
    for channel in channels:
        channel.allowed_campaign_count = counts_by_channel.get(channel.pk, 0)

    if channels:
        Channel.objects.bulk_update(
            channels,
            ["allowed_campaign_count"],
            batch_size=1000,
        )


def noop_reverse(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    """No-op reverse migration for cached counters."""
    del apps
    del schema_editor


class Migration(migrations.Migration):
    """Add cached channel campaign counts and backfill existing rows."""

    dependencies = [
        ("twitch", "0020_rewardcampaign_tw_reward_ends_starts_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="allowed_campaign_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Cached number of drop campaigns that allow this channel.",
            ),
        ),
        migrations.AddIndex(
            model_name="channel",
            index=models.Index(
                fields=["-allowed_campaign_count", "name"],
                name="tw_chan_cc_name_idx",
            ),
        ),
        migrations.RunPython(backfill_allowed_campaign_count, noop_reverse),
    ]
