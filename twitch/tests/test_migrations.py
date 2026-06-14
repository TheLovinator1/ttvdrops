from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

if TYPE_CHECKING:
    from django.db.migrations.state import StateApps

    from twitch.models import Channel
    from twitch.models import DropCampaign
    from twitch.models import Game


@pytest.mark.django_db(transaction=True)
def test_0021_backfills_allowed_campaign_count() -> None:  # noqa: PLR0914
    """Migration 0021 should backfill cached allowed campaign counts."""
    migrate_from: list[tuple[str, str]] = [
        ("twitch", "0020_rewardcampaign_tw_reward_ends_starts_idx"),
    ]
    migrate_to: list[tuple[str, str]] = [
        ("twitch", "0021_channel_allowed_campaign_count_cache"),
    ]

    executor = MigrationExecutor(connection)
    executor.migrate(migrate_from)
    old_apps: StateApps = executor.loader.project_state(migrate_from).apps

    Game: type[Game] = old_apps.get_model("twitch", "Game")
    Channel: type[Channel] = old_apps.get_model("twitch", "Channel")
    DropCampaign: type[DropCampaign] = old_apps.get_model("twitch", "DropCampaign")

    game = Game.objects.create(
        twitch_id="migration_backfill_game",
        name="Migration Backfill Game",
        display_name="Migration Backfill Game",
    )
    channel1 = Channel.objects.create(
        twitch_id="migration_backfill_channel_1",
        name="migrationbackfillchannel1",
        display_name="Migration Backfill Channel 1",
    )
    channel2 = Channel.objects.create(
        twitch_id="migration_backfill_channel_2",
        name="migrationbackfillchannel2",
        display_name="Migration Backfill Channel 2",
    )
    _channel3 = Channel.objects.create(
        twitch_id="migration_backfill_channel_3",
        name="migrationbackfillchannel3",
        display_name="Migration Backfill Channel 3",
    )
    campaign1 = DropCampaign.objects.create(
        twitch_id="migration_backfill_campaign_1",
        name="Migration Backfill Campaign 1",
        game=game,
        operation_names=["DropCampaignDetails"],
    )
    campaign2 = DropCampaign.objects.create(
        twitch_id="migration_backfill_campaign_2",
        name="Migration Backfill Campaign 2",
        game=game,
        operation_names=["DropCampaignDetails"],
    )

    campaign1.allow_channels.add(channel1, channel2)
    campaign2.allow_channels.add(channel1)

    executor = MigrationExecutor(connection)
    executor.migrate(migrate_to)
    new_apps: StateApps = executor.loader.project_state(migrate_to).apps
    new_channel: type[Channel] = new_apps.get_model("twitch", "Channel")

    counts_by_twitch_id: dict[str, int] = {
        channel.twitch_id: channel.allowed_campaign_count
        for channel in new_channel.objects.order_by("twitch_id")
    }

    assert counts_by_twitch_id == {
        "migration_backfill_channel_1": 2,
        "migration_backfill_channel_2": 1,
        "migration_backfill_channel_3": 0,
    }

    # Restore database to the latest migration so subsequent tests don't
    # encounter missing columns (SQLite DDL persists across transaction
    # rollbacks, so the schema changes made by this test are permanent).
    latest: list[tuple[str, str]] = [
        ("twitch", "0024_dropcampaign_data_source_rewardcampaign_data_source"),
    ]
    executor = MigrationExecutor(connection)
    executor.migrate(latest)
