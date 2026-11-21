import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import TypedDict
from urllib.parse import quote

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pghistory.models import Context
from pghistory.models import Events

from twitch.history import HistoryResolver
from twitch.history import build_event_detail
from twitch.history import build_event_rows
from twitch.history import campaign_history_querysets
from twitch.history import history_page
from twitch.history import history_page_for_querysets
from twitch.history_replay import HistoryReplay
from twitch.management.commands.build_history import _reset_history
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import GameOwner
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.schemas import ChannelInfoSchema
from twitch.schemas import GameSchema

if TYPE_CHECKING:
    from django.test import Client

pytestmark = pytest.mark.django_db

type HistoryEventData = dict[str, str | int | datetime | dict[str, str] | None]


class HistoryFixtures(TypedDict):
    """Archive records created for query-budget tests."""

    org: Organization
    game: Game
    channel: Channel
    campaign: DropCampaign
    drop: TimeBasedDrop
    benefit: DropBenefit
    reward_campaign: RewardCampaign


def _example_payload() -> list[dict]:
    repo_root: Path = Path(__file__).resolve().parents[2]
    return json.loads((repo_root / "twitch" / "tests" / "example.json").read_text())


def test_build_history_backdates_events_and_resumes(tmp_path: Path) -> None:
    """Historical imports should use filename timestamps and skip completed files."""
    source_dir: Path = tmp_path / "imported" / "DropCampaignDetails"
    source_dir.mkdir(parents=True)
    source: Path = source_dir / "20261119T041604_555664.json"
    source.write_text(json.dumps(_example_payload()), encoding="utf-8")

    call_command("build_history", str(tmp_path), "--recursive")

    campaign: DropCampaign = DropCampaign.objects.get(
        twitch_id="3b965979-ecd2-11f0-876e-0a58a9feac02",
    )
    event_model = DropCampaign.pgh_event_model
    event = event_model.objects.get(pgh_label="created", pgh_obj=campaign)
    assert event.pgh_created_at == datetime(
        2026,
        11,
        19,
        4,
        16,
        4,
        555664,
        tzinfo=UTC,
    )

    event_count: int = event_model.objects.count()
    call_command("build_history", str(tmp_path), "--recursive")
    assert event_model.objects.count() == event_count


def test_history_replay_game_name_does_not_flap() -> None:
    """Missing game name fields must not alternate between populated and empty."""
    game: Game = Game.objects.create(
        twitch_id="flap-game",
        name="Marvel Rivals",
        display_name="Marvel Rivals",
    )
    replay: HistoryReplay = HistoryReplay()
    without_name: GameSchema = GameSchema.model_validate({
        "id": "flap-game",
        "displayName": "Marvel Rivals",
        "__typename": "Game",
    })
    with_name: GameSchema = GameSchema.model_validate({
        "id": "flap-game",
        "name": "Marvel Rivals",
        "displayName": "Marvel Rivals",
        "__typename": "Game",
    })

    replay._process_game(without_name)
    replay._process_game(with_name)
    count_after_name: int = Game.pgh_event_model.objects.filter(pgh_obj=game).count()

    replay._process_game(without_name)

    assert Game.pgh_event_model.objects.filter(pgh_obj=game).count() == count_after_name


def test_history_replay_does_not_degrade_channel_display_name() -> None:
    """Replaying lowercase login names must not overwrite capitalization."""
    channel: Channel = Channel.objects.create(
        twitch_id="ztockerstein",
        name="ztockerstein",
        display_name="Ztockerstein",
    )
    replay: HistoryReplay = HistoryReplay()
    without_display_name: ChannelInfoSchema = ChannelInfoSchema.model_validate({
        "id": "ztockerstein",
        "name": "ztockerstein",
        "__typename": "Channel",
    })
    with_lowercase_display_name: ChannelInfoSchema = ChannelInfoSchema.model_validate({
        "id": "ztockerstein",
        "name": "ztockerstein",
        "displayName": "ztockerstein",
        "__typename": "Channel",
    })

    events = Channel.pgh_event_model.objects.filter(pgh_obj=channel)
    count_before_replay: int = events.count()

    replay._process_channel(without_display_name)
    count_after_first_replay: int = events.count()
    replay._process_channel(with_lowercase_display_name)

    assert count_after_first_replay == count_before_replay + 1
    assert events.count() == count_after_first_replay
    latest = events.order_by("-pgh_id").first()
    assert latest is not None
    assert latest.display_name == "Ztockerstein"


def test_history_replay_preserves_non_latin_display_name() -> None:
    """Replaying a login fallback must not replace a CJK or Korean name."""
    channel: Channel = Channel.objects.create(
        twitch_id="zousenpai",
        name="zousenpai",
        display_name="象先輩",
    )
    replay: HistoryReplay = HistoryReplay()
    without_display_name: ChannelInfoSchema = ChannelInfoSchema.model_validate({
        "id": "zousenpai",
        "name": "zousenpai",
        "__typename": "Channel",
    })
    last_id_before_replay: int = (
        Channel.pgh_event_model.objects
        .filter(pgh_obj=channel)
        .order_by("-pgh_id")
        .values_list("pgh_id", flat=True)
        .first()
        or 0
    )

    replay._process_channel(without_display_name)

    event = Channel.pgh_event_model.objects.get(
        pgh_obj=channel,
        pgh_id__gt=last_id_before_replay,
    )
    assert event.display_name == "象先輩"
    assert event.display_names == ["象先輩", "zousenpai"]


def test_history_replay_channel_display_name_does_not_flap() -> None:
    """Alternating displayName presence must not flip capitalization during replay."""
    channel: Channel = Channel.objects.create(
        twitch_id="walkyrip",
        name="walkyrip",
        display_name="walkyrip",
    )
    replay: HistoryReplay = HistoryReplay()
    with_display_name: ChannelInfoSchema = ChannelInfoSchema.model_validate({
        "id": "walkyrip",
        "name": "walkyrip",
        "displayName": "Walkyrip",
        "__typename": "Channel",
    })
    without_display_name: ChannelInfoSchema = ChannelInfoSchema.model_validate({
        "id": "walkyrip",
        "name": "walkyrip",
        "__typename": "Channel",
    })
    last_id_before_replay: int = (
        Channel.pgh_event_model.objects
        .filter(pgh_obj=channel)
        .order_by("-pgh_id")
        .values_list("pgh_id", flat=True)
        .first()
        or 0
    )

    replay._process_channel(with_display_name)
    replay._process_channel(without_display_name)
    replay._process_channel(with_display_name)

    replayed = Channel.pgh_event_model.objects.filter(
        pgh_obj=channel,
        pgh_id__gt=last_id_before_replay,
    )
    assert replayed.count() == 1
    event = replayed.get()
    assert event.display_name == "Walkyrip"


def test_history_replay_owner_membership_is_additive() -> None:
    """Missing owner data must not produce owner_removed events."""
    game: Game = Game.objects.create(
        twitch_id="owner-history-game",
        display_name="Owner History Game",
    )
    org: Organization = Organization.objects.create(
        twitch_id="owner-history-org",
        name="Owner History Org",
    )
    game.owners.add(org)

    replay: HistoryReplay = HistoryReplay()
    replay._emit_owner_membership(game, {int(org.pk)})
    count_after_add: int = GameOwner.pgh_event_model.objects.filter(
        game_id=game.pk,
    ).count()

    replay._emit_owner_membership(game, set())

    assert (
        GameOwner.pgh_event_model.objects.filter(
            game_id=game.pk,
        ).count()
        == count_after_add
    )
    assert not GameOwner.pgh_event_model.objects.filter(
        pgh_label="owner_removed",
        game_id=game.pk,
    ).exists()


def test_history_only_does_not_modify_live_rows(tmp_path: Path) -> None:
    """History-only replay must write events without saving live archive rows."""
    org: Organization = Organization.objects.create(
        twitch_id="51a157a0-674a-4863-b120-7bb6ee2466a8",
        name="Hi-Rez Studios",
    )
    game: Game = Game.objects.create(
        twitch_id="2094865572",
        slug="smite-2",
        name="SMITE 2",
        display_name="SMITE 2",
        box_art="https://static-cdn.jtvnw.net/ttv-boxart/2094865572_IGDB.jpg",
    )
    game.owners.add(org)
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="3b965979-ecd2-11f0-876e-0a58a9feac02",
        name="Live Production Name",
        game=game,
        owner=org,
        last_seen_at=datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
    )

    source_dir: Path = tmp_path / "imported" / "DropCampaignDetails"
    source_dir.mkdir(parents=True)
    source: Path = source_dir / "20261119T041604_555664.json"
    source.write_text(json.dumps(_example_payload()), encoding="utf-8")

    call_command("build_history", str(tmp_path), "--recursive", "--history-only")

    campaign.refresh_from_db()
    assert campaign.name == "Live Production Name"
    assert campaign.last_seen_at == datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    assert not TimeBasedDrop.objects.filter(campaign=campaign).exists()

    event = DropCampaign.pgh_event_model.objects.get(
        pgh_obj=campaign,
        pgh_label="created",
        name="Jan Drops Week 2",
    )
    assert event.name == "Jan Drops Week 2"
    assert event.pgh_created_at == datetime(
        2026,
        11,
        19,
        4,
        16,
        4,
        555664,
        tzinfo=UTC,
    )


def test_build_history_skips_older_files(tmp_path: Path) -> None:
    """Files older than the stored observation must not rewind campaign data."""
    game: Game = Game.objects.create(
        twitch_id="older-file-game",
        display_name="Older File Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="3b965979-ecd2-11f0-876e-0a58a9feac02",
        name="Newer State",
        game=game,
        last_seen_at=datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
    )

    source_dir: Path = tmp_path / "imported" / "DropCampaignDetails"
    source_dir.mkdir(parents=True)
    source: Path = source_dir / "20261119T100000_000000.json"
    source.write_text(json.dumps(_example_payload()), encoding="utf-8")

    call_command("build_history", str(tmp_path), "--recursive")

    campaign.refresh_from_db()
    assert campaign.name == "Newer State"


def test_build_history_copies_files_without_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Files without a timestamp should be copied out, never moved."""
    quarantine: Path = tmp_path / "quarantine"
    monkeypatch.setattr(
        "twitch.management.commands.build_history._QUARANTINE_DIR",
        quarantine,
    )
    source_dir: Path = tmp_path / "imported" / "Unknown"
    source_dir.mkdir(parents=True)
    source: Path = source_dir / "no_timestamp.json"
    source.write_text("{}", encoding="utf-8")

    call_command("build_history", str(tmp_path / "imported"), "--recursive")

    assert source.exists()
    copied: Path = quarantine / "imported" / "Unknown" / "no_timestamp.json"
    assert copied.exists()


def test_channel_membership_history_is_included() -> None:
    """Channel add/remove operations should appear in campaign history."""
    game: Game = Game.objects.create(
        twitch_id="history-game",
        display_name="History Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="history-campaign",
        name="History Campaign",
        game=game,
    )
    channel: Channel = Channel.objects.create(
        twitch_id="history-channel",
        name="historychannel",
        display_name="HistoryChannel",
    )

    campaign.allow_channels.add(channel)
    campaign.allow_channels.remove(channel)

    labels: set[str] = set(
        Events.objects
        .references(campaign)
        .filter(pgh_model="twitch.DropCampaignAllowedChannelEvent")
        .values_list("pgh_label", flat=True),
    )
    assert "channel_added" in labels
    assert "channel_removed" in labels


def test_history_page_computes_diffs_without_cte() -> None:
    """Direct event-model queries should still produce update diffs."""
    game: Game = Game.objects.create(
        twitch_id="history-diff-game",
        display_name="History Diff Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="history-diff-campaign",
        name="Old Campaign Name",
        game=game,
        api_status="ACTIVE",
    )
    campaign.name = "New Campaign Name"
    campaign.api_status = "EXPIRED"
    campaign.save(update_fields=["name", "api_status"])

    page = history_page_for_querysets(
        campaign_history_querysets(campaign),
        per_page=10,
    )
    rows = build_event_rows(page.rows)
    updated = next(row for row in rows if row["label_key"] == "updated")
    changed_fields = {
        row["field"]: (row["old"].text, row["new"].text) for row in updated["diff"]
    }
    assert changed_fields["Name"] == ("Old Campaign Name", "New Campaign Name")
    assert changed_fields["Status"] == ("ACTIVE", "EXPIRED")


def test_history_views_render(client: Client) -> None:
    """Global, entity, and event detail history pages should render."""
    org: Organization = Organization.objects.create(
        twitch_id="history-org",
        name="History Org",
    )
    game: Game = Game.objects.create(
        twitch_id="history-view-game",
        display_name="History View Game",
    )
    game.owners.add(org)
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="history-view-campaign",
        name="History View Campaign",
        game=game,
        api_status="ACTIVE",
    )
    TimeBasedDrop.objects.create(
        twitch_id="history-view-drop",
        name="History View Drop",
        campaign=campaign,
    )

    assert client.get(reverse("twitch:history")).status_code == 200
    campaign_history = client.get(
        reverse("twitch:campaign_history", args=[campaign.twitch_id]),
    )
    assert campaign_history.status_code == 200
    assert b"History View Campaign" in campaign_history.content

    event = Events.objects.tracks(campaign).filter(pgh_label="created").first()
    assert event is not None
    event_detail = client.get(
        reverse("twitch:history_event_detail", args=[event.pgh_slug]),
    )
    assert event_detail.status_code == 200
    assert b"Full event snapshot" in event_detail.content


def test_history_for_objects_groups_model_types() -> None:
    """Mixed tracked model types must not break campaign history."""
    game: Game = Game.objects.create(
        twitch_id="history-mixed-game",
        display_name="History Mixed Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="history-mixed-campaign",
        name="History Mixed Campaign",
        game=game,
    )
    TimeBasedDrop.objects.create(
        twitch_id="history-mixed-drop",
        name="History Mixed Drop",
        campaign=campaign,
    )

    page = history_page_for_querysets(campaign_history_querysets(campaign))
    assert len(page.rows) >= 2


def test_source_labels_come_from_data_source() -> None:
    """History rows should show the importer source, not just replay/live."""
    event: HistoryEventData = {
        "pgh_slug": "twitch.DropCampaignEvent:1",
        "pgh_model": "twitch.DropCampaignEvent",
        "pgh_id": 1,
        "pgh_created_at": datetime.now(UTC),
        "pgh_label": "created",
        "pgh_data": {"name": "Source Campaign", "data_source": "TwitchDropsMiner"},
        "pgh_diff": None,
        "pgh_obj_model": "twitch.DropCampaign",
        "pgh_obj_id": 1,
        "pgh_context": {"mode": "history"},
    }

    row = build_event_rows([event])[0]
    assert row["source"] == "TwitchDropsMiner"
    assert row["source_url"] == "https://github.com/DevilXD/TwitchDropsMiner"
    assert row["mode"] == "replay"


def test_history_pagination_is_cursor_based(client: Client) -> None:
    """Pagination must not fetch page-sized offsets from every event table."""
    game: Game = Game.objects.create(
        twitch_id="cursor-game",
        display_name="Cursor Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="cursor-campaign",
        name="Cursor Campaign 0",
        game=game,
    )
    for index in range(1, 150):
        campaign.name = f"Cursor Campaign {index}"
        campaign.save(update_fields=["name"])

    with CaptureQueriesContext(connection) as first_queries:
        first_response = client.get(f"{reverse('twitch:history')}?period=all")

    assert first_response.status_code == 200
    assert b"[last]" not in first_response.content

    page = history_page(per_page=100)
    assert page.older_cursor is not None

    with CaptureQueriesContext(connection) as older_queries:
        older_response = client.get(
            f"{reverse('twitch:history')}?period=all&older={quote(page.older_cursor)}",
        )

    assert older_response.status_code == 200
    assert b"[newer]" in older_response.content
    assert b"[last]" not in older_response.content

    for query in (*first_queries.captured_queries, *older_queries.captured_queries):
        sql: str = query["sql"].upper()
        assert "OFFSET" not in sql
        if "ORDER BY" in sql and "LIMIT" in sql and "PGH_CREATED_AT" in sql:
            assert "LIMIT 101" in sql


def test_image_fields_get_preview_urls() -> None:
    """Image fields should expose a browser-loadable preview URL."""
    resolver: HistoryResolver = HistoryResolver([])

    external = resolver.value(
        "box_art",
        "https://static-cdn.jtvnw.net/ttv-boxart/1630982727_IGDB-120x160.jpg",
    )
    assert external.image_url == (
        "https://static-cdn.jtvnw.net/ttv-boxart/1630982727_IGDB.jpg"
    )

    profile = resolver.value(
        "image_url",
        "https://static-cdn.jtvnw.net/jtv_user_pictures/example-profile_image-70x70.png",
    )
    assert profile.image_url == (
        "https://static-cdn.jtvnw.net/jtv_user_pictures/example-profile_image-300x300.png"
    )

    emote = resolver.value(
        "image_asset_url",
        "https://static-cdn.jtvnw.net/emoticons/v1/414579/1.0",
    )
    assert emote.image_url == "https://static-cdn.jtvnw.net/emoticons/v1/414579/3.0"

    local = resolver.value("image_file", "campaigns/images/example.png")
    assert local.image_url == "/media/campaigns/images/example.png"

    empty = resolver.value("box_art", "")
    assert not empty.image_url


def test_first_observed_update_is_labeled_clearly() -> None:
    """An update without a previous snapshot should not say 0 fields changed."""
    event: HistoryEventData = {
        "pgh_slug": "twitch.DropCampaignEvent:1",
        "pgh_model": "twitch.DropCampaignEvent",
        "pgh_id": 1,
        "pgh_created_at": datetime.now(UTC),
        "pgh_label": "updated",
        "pgh_data": {
            "twitch_id": "first-observed-campaign",
            "name": "First Observed Campaign",
        },
        "pgh_diff": None,
        "pgh_obj_model": "twitch.DropCampaign",
        "pgh_obj_id": 1,
        "pgh_context": {},
    }

    row = build_event_rows([event])[0]
    assert row["summary"] == "First observed"

    detail = build_event_detail(event)
    assert detail["before"] == []
    assert detail["after"]


def test_reset_history_truncates_all_events_and_contexts() -> None:
    """Reset should remove every pghistory event and context row."""
    game: Game = Game.objects.create(
        twitch_id="reset-history-game",
        display_name="Reset History Game",
    )
    DropCampaign.objects.create(
        twitch_id="reset-history-campaign",
        name="Reset History Campaign",
        game=game,
    )
    Context.objects.create(metadata={"mode": "history-only"})

    assert DropCampaign.pgh_event_model.objects.exists()
    assert Context.objects.exists()

    _reset_history(dry_run=False)

    assert not DropCampaign.pgh_event_model.objects.exists()
    assert not Context.objects.exists()


def test_history_page_query_count(client: Client) -> None:
    """The global history page should use a bounded number of queries."""
    game: Game = Game.objects.create(
        twitch_id="history-query-game",
        display_name="History Query Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="history-query-campaign",
        name="History Query Campaign",
        game=game,
    )
    for index in range(5):
        campaign.name = f"History Query Campaign {index}"
        campaign.save(update_fields=["name"])

    with CaptureQueriesContext(connection) as queries:
        response = client.get(f"{reverse('twitch:history')}?period=all")

    assert response.status_code == 200
    assert len(queries) < 40


def _create_history_fixtures() -> HistoryFixtures:
    """Create a compact set of archive records with history events.

    Returns:
        Mapping of fixture names to created objects.
    """
    org: Organization = Organization.objects.create(
        twitch_id="query-org",
        name="Query Org",
    )
    game: Game = Game.objects.create(
        twitch_id="query-game",
        display_name="Query Game",
    )
    game.owners.add(org)
    channel: Channel = Channel.objects.create(
        twitch_id="query-channel",
        name="querychannel",
        display_name="QueryChannel",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="query-campaign",
        name="Query Campaign",
        game=game,
        owner=org,
        api_status="ACTIVE",
    )
    campaign.allow_channels.add(channel)
    drop: TimeBasedDrop = TimeBasedDrop.objects.create(
        twitch_id="query-drop",
        name="Query Drop",
        campaign=campaign,
    )
    benefit: DropBenefit = DropBenefit.objects.create(
        twitch_id="query-benefit",
        name="Query Benefit",
        game=game,
        owner_organization=org,
    )
    DropBenefitEdge.objects.create(drop=drop, benefit=benefit)
    reward_campaign: RewardCampaign = RewardCampaign.objects.create(
        twitch_id="query-reward-campaign",
        name="Query Reward Campaign",
        game=game,
    )
    Reward.objects.create(
        twitch_id="query-reward",
        name="Query Reward",
        reward_campaign=reward_campaign,
    )
    for index in range(5):
        campaign.name = f"Query Campaign {index}"
        campaign.save(update_fields=["name"])
    return {
        "org": org,
        "game": game,
        "channel": channel,
        "campaign": campaign,
        "drop": drop,
        "benefit": benefit,
        "reward_campaign": reward_campaign,
    }


def _assert_query_bound(client: Client, url: str, maximum: int) -> None:
    """Assert that a history URL stays under a query budget.

    Args:
        client: Django test client.
        url: URL to request.
        maximum: Maximum allowed SQL query count.
    """
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    assert len(queries) <= maximum, (
        f"{url} executed {len(queries)} queries, expected <= {maximum}"
    )


def test_global_history_query_counts_are_bounded(client: Client) -> None:
    """Global history filters should not introduce N+1 queries."""
    _create_history_fixtures()

    _assert_query_bound(client, f"{reverse('twitch:history')}?period=all", 40)
    _assert_query_bound(client, f"{reverse('twitch:history')}?period=7d", 40)
    _assert_query_bound(
        client,
        f"{reverse('twitch:history')}?model=campaigns&period=all",
        25,
    )
    _assert_query_bound(
        client,
        f"{reverse('twitch:history')}?action=updated&period=all",
        30,
    )
    _assert_query_bound(
        client,
        f"{reverse('twitch:history')}?q=Query&period=all",
        40,
    )


def test_entity_history_query_counts_are_bounded(client: Client) -> None:
    """Every per-object history page should stay under a query budget."""
    fixtures = _create_history_fixtures()
    campaign: DropCampaign = fixtures["campaign"]  # type: ignore[assignment]
    game: Game = fixtures["game"]  # type: ignore[assignment]
    org: Organization = fixtures["org"]  # type: ignore[assignment]
    channel: Channel = fixtures["channel"]  # type: ignore[assignment]
    reward_campaign: RewardCampaign = fixtures["reward_campaign"]  # type: ignore[assignment]

    _assert_query_bound(
        client,
        reverse("twitch:campaign_history", args=[campaign.twitch_id]),
        30,
    )
    _assert_query_bound(
        client,
        reverse("twitch:game_history", args=[game.twitch_id]),
        30,
    )
    _assert_query_bound(
        client,
        reverse("twitch:organization_history", args=[org.twitch_id]),
        30,
    )
    _assert_query_bound(
        client,
        reverse("twitch:channel_history", args=[channel.twitch_id]),
        20,
    )
    _assert_query_bound(
        client,
        reverse("twitch:reward_campaign_history", args=[reward_campaign.twitch_id]),
        20,
    )


def test_history_event_detail_query_count_is_bounded(client: Client) -> None:
    """Event detail should not fetch its previous event per field."""
    fixtures = _create_history_fixtures()
    campaign: DropCampaign = fixtures["campaign"]  # type: ignore[assignment]
    event = DropCampaign.pgh_event_model.objects.filter(
        pgh_obj=campaign,
        pgh_label="updated",
    ).first()
    assert event is not None

    _assert_query_bound(
        client,
        reverse(
            "twitch:history_event_detail",
            args=[f"{event._meta.label}:{event.pk}"],
        ),
        20,
    )


def test_query_count_does_not_scale_with_event_volume(client: Client) -> None:
    """Large event volumes should not increase queries per history page."""
    game: Game = Game.objects.create(
        twitch_id="volume-game",
        display_name="Volume Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="volume-campaign",
        name="Volume Campaign 0",
        game=game,
    )
    for index in range(1, 60):
        campaign.name = f"Volume Campaign {index}"
        campaign.save(update_fields=["name"])

    _assert_query_bound(client, f"{reverse('twitch:history')}?period=all", 40)
    _assert_query_bound(
        client,
        reverse("twitch:campaign_history", args=[campaign.twitch_id]),
        30,
    )


def test_campaign_history_query_count_does_not_scale_with_children(
    client: Client,
) -> None:
    """Campaign history should batch child events instead of querying each child."""
    game: Game = Game.objects.create(
        twitch_id="children-game",
        display_name="Children Game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="children-campaign",
        name="Children Campaign",
        game=game,
    )
    benefit: DropBenefit = DropBenefit.objects.create(
        twitch_id="children-benefit",
        name="Children Benefit",
    )
    for index in range(20):
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id=f"children-drop-{index}",
            name=f"Children Drop {index}",
            campaign=campaign,
            required_minutes_watched=index * 15,
        )
        DropBenefitEdge.objects.create(drop=drop, benefit=benefit)

    _assert_query_bound(
        client,
        reverse("twitch:campaign_history", args=[campaign.twitch_id]),
        30,
    )
