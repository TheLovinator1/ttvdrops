"""Tests for Bluesky posting, backdating, and backfill."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock
from unittest.mock import patch

import httpx
import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from chzzk.models import ChzzkCampaign
from core.bluesky import BlueskyClient
from core.bluesky import _grapheme_count
from core.bluesky import compose_drop_post
from core.bluesky import format_created_at
from core.bluesky import publish_drop_to_bluesky
from core.bluesky import suppress_bluesky_posts
from core.bluesky import url_facets
from core.mcp import DropDetail
from core.mcp import RewardSummary
from core.models import BlueskyPost
from kick.models import KickDropCampaign
from twitch.models import DropCampaign
from twitch.models import Game

if TYPE_CHECKING:
    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

    from core.types import JsonObject


def _create_chzzk_campaign(campaign_no: int = 9001) -> ChzzkCampaign:
    return ChzzkCampaign.objects.create(
        campaign_no=campaign_no,
        title="Chzzk Drop",
        description="Chzzk rewards.",
        category_type="GAME",
        category_id="chzzk-game",
        category_value="Game",
        service_id="chzzk",
        state="ACTIVE",
        start_date=timezone.now(),
        end_date=timezone.now() + timedelta(days=1),
        has_ios_based_reward=False,
        drops_campaign_not_started=False,
    )


def test_compose_drop_post_includes_details_and_link_facet() -> None:
    detail: DropDetail = DropDetail(
        platform="twitch",
        id="c1",
        name="Test Drop",
        game="Test Game",
        organization="Acme",
        url="https://ttvdrops.lovinator.space/twitch/c1/",
        rewards=[RewardSummary(name="Reward")],
    )
    text, facets = compose_drop_post(detail)

    assert "New Twitch drop: Test Drop" in text
    assert "Test Game • Acme • 1 reward" in text
    assert detail.url in text
    assert len(facets) == 1
    assert facets[0]["features"][0]["uri"] == detail.url
    expected_start: int = len(text[: text.index(detail.url)].encode("utf-8"))
    assert facets[0]["index"]["byteStart"] == expected_start
    assert facets[0]["index"]["byteEnd"] == expected_start + len(
        detail.url.encode("utf-8"),
    )


def test_compose_drop_post_truncates_long_names() -> None:
    detail: DropDetail = DropDetail(
        platform="kick",
        id="k1",
        name="D" * 1000,
        url="https://ttvdrops.lovinator.space/kick/k1/",
    )
    text, _ = compose_drop_post(detail)

    assert _grapheme_count(text) <= 300
    assert text.endswith(detail.url)
    assert "…" in text


def test_url_facets_use_byte_offsets() -> None:
    text = "🎮 https://example.com/drop"
    facets = url_facets(text)

    assert facets[0]["index"] == {
        "byteStart": len("🎮 ".encode()),
        "byteEnd": len(text.encode("utf-8")),
    }


def test_format_created_at_backdates_with_trailing_z() -> None:
    value: datetime = datetime(2024, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)
    assert format_created_at(value) == "2024-01-02T03:04:05.123Z"


def test_client_reuses_session_and_sends_custom_created_at() -> None:
    session_response: Mock = Mock(status_code=200)
    session_response.json.return_value = {
        "did": "did:plc:123",
        "accessJwt": "access-token",
        "refreshJwt": "refresh-token",
    }
    post_response: Mock = Mock(status_code=200)
    post_response.json.return_value = {"uri": "at://post/1", "cid": "cid-1"}
    http_client: Mock = Mock(spec=httpx.Client)
    http_client.post.side_effect = [session_response, post_response]
    client: BlueskyClient = BlueskyClient(
        service_url="https://bsky.social",
        handle="test.bsky.social",
        app_password="xxxx-xxxx",
        http_client=http_client,
    )
    created_at: datetime = datetime(2020, 5, 6, 7, 8, 9, tzinfo=UTC)
    facets: list[JsonObject] = url_facets("https://example.com")

    result: dict[str, str] = client.post(
        "Hello",
        created_at=created_at,
        facets=facets,
    )

    assert result == {"uri": "at://post/1", "cid": "cid-1"}
    assert http_client.post.call_count == 2
    session_payload = http_client.post.call_args_list[0].kwargs["json"]
    assert session_payload == {
        "identifier": "test.bsky.social",
        "password": "xxxx-xxxx",
    }
    second_call = http_client.post.call_args_list[1]
    assert second_call.args[0].endswith("/xrpc/com.atproto.repo.createRecord")
    record = second_call.kwargs["json"]["record"]
    assert second_call.kwargs["json"]["repo"] == "did:plc:123"
    assert record["createdAt"] == "2020-05-06T07:08:09.000Z"
    assert record["facets"] == facets


@pytest.mark.django_db
@pytest.mark.no_zeal
@override_settings(BLUESKY_ENABLED=True)
def test_publish_uses_discovery_time_and_skips_duplicates() -> None:
    game: Game = Game.objects.create(
        twitch_id="game-1",
        name="Game",
        display_name="Game",
        slug="game",
    )
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="campaign-1",
        name="Old Drop",
        game=game,
    )
    discovered: datetime = timezone.now() - timedelta(days=30)
    DropCampaign.objects.filter(pk=campaign.pk).update(added_at=discovered)
    client: Mock = Mock()
    client.post.return_value = {"uri": "at://post/old", "cid": "cid-old"}

    entry: BlueskyPost | None = publish_drop_to_bluesky(
        "twitch",
        "campaign-1",
        client=client,
    )

    assert entry is not None
    assert entry.created_at == discovered
    assert entry.uri == "at://post/old"
    assert client.post.call_args.kwargs["created_at"] == discovered
    assert BlueskyPost.objects.count() == 1

    assert publish_drop_to_bluesky("twitch", "campaign-1", client=client) is None
    assert client.post.call_count == 1


@pytest.mark.django_db
@override_settings(BLUESKY_ENABLED=True)
def test_publish_ignores_chzzk() -> None:
    client: Mock = Mock()

    assert publish_drop_to_bluesky("chzzk", "9001", client=client) is None

    client.post.assert_not_called()
    assert BlueskyPost.objects.count() == 0


@pytest.mark.django_db
@override_settings(BLUESKY_ENABLED=True)
def test_suppressed_imports_do_not_queue_posts(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    game: Game = Game.objects.create(
        twitch_id="game-2",
        name="Game",
        display_name="Game",
        slug="game-2",
    )
    with (
        patch("core.tasks.publish_bluesky_post.apply_async") as enqueue,
        suppress_bluesky_posts(),
        django_capture_on_commit_callbacks(execute=True),
    ):
        DropCampaign.objects.create(
            twitch_id="campaign-2",
            name="Replayed Drop",
            game=game,
        )
    enqueue.assert_not_called()


@pytest.mark.django_db
@override_settings(BLUESKY_ENABLED=True)
def test_new_campaign_queues_post(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    game: Game = Game.objects.create(
        twitch_id="game-3",
        name="Game",
        display_name="Game",
        slug="game-3",
    )
    with (
        patch("core.tasks.publish_bluesky_post.apply_async") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        DropCampaign.objects.create(
            twitch_id="campaign-3",
            name="New Drop",
            game=game,
        )
    enqueue.assert_called_once_with(
        args=["twitch", "campaign-3"],
        countdown=10,
    )


@pytest.mark.django_db
@override_settings(BLUESKY_ENABLED=True)
def test_chzzk_campaign_does_not_queue_post(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    with (
        patch("core.tasks.publish_bluesky_post.apply_async") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        _create_chzzk_campaign()
    enqueue.assert_not_called()


@pytest.mark.django_db
@override_settings(
    BLUESKY_ENABLED=True,
    BLUESKY_HANDLE="test.bsky.social",
    BLUESKY_APP_PASSWORD="xxxx",
)
def test_backfill_posts_drops_in_chronological_order() -> None:
    game: Game = Game.objects.create(
        twitch_id="game-4",
        name="Game",
        display_name="Game",
        slug="game-4",
    )
    older: DropCampaign = DropCampaign.objects.create(
        twitch_id="old-campaign",
        name="Older Drop",
        game=game,
    )
    newer: DropCampaign = DropCampaign.objects.create(
        twitch_id="new-campaign",
        name="Newer Drop",
        game=game,
    )
    old_time: datetime = timezone.now() - timedelta(days=10)
    new_time: datetime = timezone.now() - timedelta(days=2)
    DropCampaign.objects.filter(pk=older.pk).update(added_at=old_time)
    DropCampaign.objects.filter(pk=newer.pk).update(added_at=new_time)
    KickDropCampaign.objects.create(kick_id="kick-1", name="Kick Drop")
    _create_chzzk_campaign(campaign_no=9002)

    with (
        patch("core.management.commands.backfill_bluesky.BlueskyClient"),
        patch(
            "core.management.commands.backfill_bluesky.publish_drop_to_bluesky",
            return_value=SimpleNamespace(uri="at://post/backfill"),
        ) as publish,
    ):
        call_command("backfill_bluesky")

    assert publish.call_count == 3
    calls = publish.call_args_list
    assert [(call.args[0], call.args[1]) for call in calls] == [
        ("twitch", "old-campaign"),
        ("twitch", "new-campaign"),
        ("kick", "kick-1"),
    ]
    assert calls[0].kwargs["created_at"] < calls[1].kwargs["created_at"]


@pytest.mark.django_db
@override_settings(BLUESKY_ENABLED=False)
def test_backfill_dry_run_does_not_post() -> None:
    game: Game = Game.objects.create(
        twitch_id="game-5",
        name="Game",
        display_name="Game",
        slug="game-5",
    )
    DropCampaign.objects.create(
        twitch_id="campaign-5",
        name="Drop",
        game=game,
    )
    with patch(
        "core.management.commands.backfill_bluesky.publish_drop_to_bluesky",
    ) as publish:
        call_command("backfill_bluesky", "--dry-run")
    publish.assert_not_called()
