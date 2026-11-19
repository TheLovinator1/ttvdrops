import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pghistory.models import Events

from twitch.history import build_event_rows
from twitch.history import campaign_history_querysets
from twitch.history import history_page_for_querysets
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from django.test import Client

pytestmark = pytest.mark.django_db


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
    assert page.total >= 2


def test_source_labels_come_from_data_source() -> None:
    """History rows should show the importer source, not just replay/live."""
    event: dict[str, object] = {
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


def _create_history_fixtures() -> dict[str, object]:
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
        pgh_obj=campaign, pgh_label="updated"
    ).first()
    assert event is not None

    _assert_query_bound(
        client,
        reverse(
            "twitch:history_event_detail", args=[f"{event._meta.label}:{event.pk}"]
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
    _assert_query_bound(
        client,
        f"{reverse('twitch:history')}?period=all&page=2",
        40,
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
