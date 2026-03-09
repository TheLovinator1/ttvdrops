"""Test RSS feeds."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from hypothesis.extra.django import TestCase

from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameFeed
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

    from twitch.tests.test_badge_views import Client


class RSSFeedTestCase(TestCase):
    """Test RSS feeds."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.org: Organization = Organization.objects.create(
            twitch_id="test-org-123",
            name="Test Organization",
        )
        self.game: Game = Game.objects.create(
            twitch_id="test-game-123",
            slug="test-game",
            name="Test Game",
            display_name="Test Game",
        )
        self.game.owners.add(self.org)
        self.campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="test-campaign-123",
            name="Test Campaign",
            game=self.game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )

        # populate the new enclosure metadata fields so feeds can return them
        self.game.box_art_size_bytes = 42
        self.game.box_art_mime_type = "image/png"
        # provide a URL so that the RSS enclosure element is emitted
        self.game.box_art = "https://example.com/box.png"
        self.game.save()

        self.campaign.image_size_bytes = 314
        self.campaign.image_mime_type = "image/gif"
        # feed will only include an enclosure if there is some image URL/field
        self.campaign.image_url = "https://example.com/campaign.png"
        self.campaign.save()

    def test_organization_feed(self) -> None:
        """Test organization feed returns 200."""
        url: str = reverse("twitch:organization_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"

    def test_game_feed(self) -> None:
        """Test game feed returns 200."""
        url: str = reverse("twitch:game_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "Owned by Test Organization." in content

        expected_rss_link: str = reverse(
            "twitch:game_campaign_feed",
            args=[self.game.twitch_id],
        )
        assert expected_rss_link in content

        # enclosure metadata from our new fields should be present
        assert 'length="42"' in content
        assert 'type="image/png"' in content

    def test_game_feed_enclosure_helpers(self) -> None:
        """Helper methods should return values from model fields."""
        feed = GameFeed()
        assert feed.item_enclosure_length(self.game) == 42
        assert feed.item_enclosure_mime_type(self.game) == "image/png"

    def test_campaign_feed(self) -> None:
        """Test campaign feed returns 200."""
        url: str = reverse("twitch:campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"

        content: str = response.content.decode("utf-8")
        # verify enclosure meta
        assert 'length="314"' in content
        assert 'type="image/gif"' in content

    def test_campaign_feed_enclosure_helpers(self) -> None:
        """Helper methods for campaigns should respect new fields."""
        feed = DropCampaignFeed()
        assert feed.item_enclosure_length(self.campaign) == 314
        assert feed.item_enclosure_mime_type(self.campaign) == "image/gif"

    def test_campaign_feed_includes_badge_description(self) -> None:
        """Badge benefit descriptions should be visible in the RSS drop summary."""
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="drop-1",
            name="Diana Chat Badge",
            campaign=self.campaign,
            required_minutes_watched=0,
            required_subs=1,
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="benefit-1",
            name="Diana",
            distribution_type="BADGE",
        )
        drop.benefits.add(benefit)

        badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="diana")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Diana",
            description="This badge was earned by subscribing.",
        )

        url: str = reverse("twitch:campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")
        assert "This badge was earned by subscribing." in content

    def test_game_campaign_feed(self) -> None:
        """Test game-specific campaign feed returns 200."""
        url: str = reverse("twitch:game_campaign_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"
        # Verify the game name is in the feed
        content: str = response.content.decode("utf-8")
        assert "Test Game" in content

    def test_game_campaign_feed_filters_correctly(self) -> None:
        """Test game campaign feed only shows campaigns for that game."""
        # Create another game with a campaign
        other_game: Game = Game.objects.create(
            twitch_id="other-game-123",
            slug="other-game",
            name="Other Game",
            display_name="Other Game",
        )
        other_game.owners.add(self.org)
        DropCampaign.objects.create(
            twitch_id="other-campaign-123",
            name="Other Campaign",
            game=other_game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )

        # Get feed for first game
        url: str = reverse("twitch:game_campaign_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        content: str = response.content.decode("utf-8")

        # Should contain first campaign
        assert "Test Campaign" in content
        # Should NOT contain other campaign
        assert "Other Campaign" not in content

        # enclosure metadata also should appear here
        assert 'length="314"' in content
        assert 'type="image/gif"' in content

    def test_game_campaign_feed_enclosure_helpers(self) -> None:
        """GameCampaignFeed helper methods should pull from the model fields."""
        feed = GameCampaignFeed()
        assert feed.item_enclosure_length(self.campaign) == 314
        assert feed.item_enclosure_mime_type(self.campaign) == "image/gif"

    def test_backfill_command_sets_metadata(self) -> None:
        """Running the backfill command should populate size and mime fields.

        We create a fake file for both a game and a campaign and then execute the
        management command.  Afterward, the corresponding metadata fields should
        be filled in.
        """
        # create game with local file
        game2: Game = Game.objects.create(
            twitch_id="file-game",
            slug="file-game",
            name="File Game",
            display_name="File Game",
        )
        game2.box_art_file.save("sample.png", ContentFile(b"hello"))
        game2.save()

        campaign2: DropCampaign = DropCampaign.objects.create(
            twitch_id="file-camp",
            name="File Campaign",
            game=self.game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=1),
            operation_names=["DropCampaignDetails"],
        )
        campaign2.image_file.save("camp.jpg", ContentFile(b"world"))
        campaign2.save()

        # ensure fields are not populated initially
        assert campaign2.image_size_bytes is None
        assert not campaign2.image_mime_type
        assert game2.box_art_size_bytes is None
        assert not game2.box_art_mime_type

        call_command("backfill_image_dimensions")

        campaign2.refresh_from_db()
        game2.refresh_from_db()

        assert campaign2.image_size_bytes == len(b"world")
        assert campaign2.image_mime_type == "image/jpeg"
        assert game2.box_art_size_bytes == len(b"hello")
        assert game2.box_art_mime_type == "image/png"


QueryAsserter = Callable[..., AbstractContextManager[object]]


def _build_campaign(game: Game, idx: int) -> DropCampaign:
    """Create a campaign with a channel, drop, and benefit for query counting.

    Returns:
        DropCampaign: Newly created campaign instance.
    """
    campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id=f"test-campaign-{idx}",
        name=f"Test Campaign {idx}",
        game=game,
        start_at=timezone.now(),
        end_at=timezone.now() + timedelta(days=7),
        operation_names=["DropCampaignDetails"],
    )

    channel: Channel = Channel.objects.create(
        twitch_id=f"test-channel-{idx}",
        name=f"testchannel{idx}",
        display_name=f"TestChannel{idx}",
    )
    campaign.allow_channels.add(channel)

    drop: TimeBasedDrop = TimeBasedDrop.objects.create(
        twitch_id=f"drop-{idx}",
        name=f"Drop {idx}",
        campaign=campaign,
        required_minutes_watched=30,
        start_at=timezone.now(),
        end_at=timezone.now() + timedelta(hours=1),
    )
    benefit: DropBenefit = DropBenefit.objects.create(
        twitch_id=f"benefit-{idx}",
        name=f"Benefit {idx}",
        distribution_type="ITEM",
    )
    drop.benefits.add(benefit)

    return campaign


def _build_reward_campaign(game: Game, idx: int) -> RewardCampaign:
    """Create a reward campaign for query counting.

    Returns:
        RewardCampaign: Newly created reward campaign instance.
    """
    return RewardCampaign.objects.create(
        twitch_id=f"test-reward-{idx}",
        name=f"Test Reward {idx}",
        brand="Test Brand",
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(days=14),
        status="ACTIVE",
        summary="Test reward summary",
        instructions="Watch and complete objectives",
        external_url="https://example.com/reward",
        about_url="https://example.com/about",
        is_sitewide=False,
        game=game,
    )


@pytest.mark.django_db
def test_campaign_feed_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Campaign feed should stay within a small, fixed query budget."""
    org: Organization = Organization.objects.create(
        twitch_id="test-org-queries",
        name="Query Org",
    )
    game: Game = Game.objects.create(
        twitch_id="test-game-queries",
        slug="query-game",
        name="Query Game",
        display_name="Query Game",
    )
    game.owners.add(org)

    for i in range(3):
        _build_campaign(game, i)

    url: str = reverse("twitch:campaign_feed")
    # TODO(TheLovinator): 14 queries is still quite high for a feed - we should be able to optimize this further, but this is a good starting point to prevent regressions for now.  # noqa: TD003
    with django_assert_num_queries(14, exact=False):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_campaign_feed_queries_do_not_scale_with_items(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Campaign RSS feed query count should stay roughly constant as item count grows."""
    org: Organization = Organization.objects.create(
        twitch_id="test-org-scale-queries",
        name="Scale Query Org",
    )
    game: Game = Game.objects.create(
        twitch_id="test-game-scale-queries",
        slug="scale-query-game",
        name="Scale Query Game",
        display_name="Scale Query Game",
    )
    game.owners.add(org)

    for i in range(50):
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id=f"scale-campaign-{i}",
            name=f"Scale Campaign {i}",
            game=game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )
        channel: Channel = Channel.objects.create(
            twitch_id=f"scale-channel-{i}",
            name=f"scalechannel{i}",
            display_name=f"ScaleChannel{i}",
        )
        campaign.allow_channels.add(channel)
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id=f"scale-drop-{i}",
            name=f"Scale Drop {i}",
            campaign=campaign,
            required_subs=1,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id=f"scale-benefit-{i}",
            name=f"Scale Benefit {i}",
            distribution_type="ITEM",
        )
        drop.benefits.add(benefit)

    url: str = reverse("twitch:campaign_feed")

    # N+1 safeguard: query count should not scale linearly with campaign count.
    with django_assert_num_queries(40, exact=False):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_game_campaign_feed_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Game campaign feed should not issue excess queries when rendering multiple campaigns."""
    org: Organization = Organization.objects.create(
        twitch_id="test-org-game-queries",
        name="Query Org Game",
    )
    game: Game = Game.objects.create(
        twitch_id="test-game-campaign-queries",
        slug="query-game-campaign",
        name="Query Game Campaign",
        display_name="Query Game Campaign",
    )
    game.owners.add(org)

    for i in range(3):
        _build_campaign(game, i)

    url: str = reverse("twitch:game_campaign_feed", args=[game.twitch_id])

    # TODO(TheLovinator): 15 queries is still quite high for a feed - we should be able to optimize this further, but this is a good starting point to prevent regressions for now.  # noqa: TD003
    with django_assert_num_queries(6, exact=False):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_game_campaign_feed_queries_do_not_scale_with_items(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Game campaign RSS feed query count should remain bounded as item count grows."""
    org: Organization = Organization.objects.create(
        twitch_id="test-org-game-scale-queries",
        name="Game Scale Query Org",
    )
    game: Game = Game.objects.create(
        twitch_id="test-game-scale-campaign-queries",
        slug="scale-game-campaign",
        name="Scale Game Campaign",
        display_name="Scale Game Campaign",
    )
    game.owners.add(org)

    for i in range(50):
        _build_campaign(game, i)

    url: str = reverse("twitch:game_campaign_feed", args=[game.twitch_id])

    with django_assert_num_queries(6, exact=False):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_organization_feed_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Organization RSS feed should stay within a modest query budget."""
    for i in range(5):
        Organization.objects.create(twitch_id=f"org-feed-{i}", name=f"Org Feed {i}")

    url: str = reverse("twitch:organization_feed")
    with django_assert_num_queries(1, exact=True):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_game_feed_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Game RSS feed should stay within a modest query budget with multiple games."""
    org: Organization = Organization.objects.create(
        twitch_id="game-feed-org",
        name="Game Feed Org",
    )

    for i in range(3):
        game: Game = Game.objects.create(
            twitch_id=f"game-feed-{i}",
            slug=f"game-feed-{i}",
            name=f"Game Feed {i}",
            display_name=f"Game Feed {i}",
        )
        game.owners.add(org)

    url: str = reverse("twitch:game_feed")
    # One query for games + one prefetch query for owners.
    with django_assert_num_queries(2, exact=True):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_reward_campaign_feed_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Reward campaign feed should stay within a modest query budget."""
    org: Organization = Organization.objects.create(
        twitch_id="reward-feed-org",
        name="Reward Feed Org",
    )
    game: Game = Game.objects.create(
        twitch_id="reward-feed-game",
        slug="reward-feed-game",
        name="Reward Feed Game",
        display_name="Reward Feed Game",
    )
    game.owners.add(org)

    for i in range(3):
        _build_reward_campaign(game, i)

    url: str = reverse("twitch:reward_campaign_feed")
    with django_assert_num_queries(1, exact=True):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_docs_rss_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Docs RSS page should stay within a reasonable query budget.

    With limit=1 for documentation examples, we should have dramatically fewer queries
    than if we were rendering 200+ items per feed.
    """
    org: Organization = Organization.objects.create(
        twitch_id="docs-org",
        name="Docs Org",
    )
    game: Game = Game.objects.create(
        twitch_id="docs-game",
        slug="docs-game",
        name="Docs Game",
        display_name="Docs Game",
    )
    game.owners.add(org)

    for i in range(2):
        _build_campaign(game, i)
        _build_reward_campaign(game, i)

    url: str = reverse("twitch:docs_rss")

    # TODO(TheLovinator): 31 queries is still quite high for a feed - we should be able to optimize this further, but this is a good starting point to prevent regressions for now.  # noqa: TD003
    with django_assert_num_queries(31, exact=False):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


URL_NAMES: list[tuple[str, dict[str, str]]] = [
    ("twitch:dashboard", {}),
    ("twitch:badge_list", {}),
    ("twitch:badge_set_detail", {"set_id": "test-set-123"}),
    ("twitch:campaign_list", {}),
    ("twitch:campaign_detail", {"twitch_id": "test-campaign-123"}),
    ("twitch:channel_list", {}),
    ("twitch:channel_detail", {"twitch_id": "test-channel-123"}),
    ("twitch:debug", {}),
    ("twitch:docs_rss", {}),
    ("twitch:emote_gallery", {}),
    ("twitch:games_grid", {}),
    ("twitch:games_list", {}),
    ("twitch:game_detail", {"twitch_id": "test-game-123"}),
    ("twitch:org_list", {}),
    ("twitch:organization_detail", {"twitch_id": "test-org-123"}),
    ("twitch:reward_campaign_list", {}),
    ("twitch:reward_campaign_detail", {"twitch_id": "test-reward-123"}),
    ("twitch:search", {}),
    ("twitch:campaign_feed", {}),
    ("twitch:game_feed", {}),
    ("twitch:game_campaign_feed", {"twitch_id": "test-game-123"}),
    ("twitch:organization_feed", {}),
    ("twitch:reward_campaign_feed", {}),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "kwargs"), URL_NAMES)
def test_rss_feeds_return_200(
    client: Client,
    url_name: str,
    kwargs: dict[str, str],
) -> None:
    """Test if feeds return HTTP 200.

    Args:
        client (Client): Django test client instance.
        url_name (str): URL pattern from urls.py.
        kwargs (dict[str, str]): Extra data used in URL.
            For example 'rss/organizations/<str:twitch_id>/campaigns/' wants twitch_id.
    """
    org: Organization = Organization.objects.create(
        twitch_id="test-org-123",
        name="Test Organization",
    )
    game: Game = Game.objects.create(
        twitch_id="test-game-123",
        slug="test-game",
        name="Test Game",
        display_name="Test Game",
    )
    game.owners.add(org)
    _campaign: DropCampaign = DropCampaign.objects.create(
        twitch_id="test-campaign-123",
        name="Test Campaign",
        game=game,
        start_at=timezone.now(),
        end_at=timezone.now() + timedelta(days=7),
        operation_names=["DropCampaignDetails"],
    )

    _reward_campaign: RewardCampaign = RewardCampaign.objects.create(
        twitch_id="test-reward-123",
        name="Test Reward Campaign",
        brand="Test Brand",
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(days=14),
        status="ACTIVE",
        summary="Test reward summary",
        instructions="Watch and complete objectives",
        external_url="https://example.com/reward",
        about_url="https://example.com/about",
        is_sitewide=False,
        game=game,
    )

    _channel: Channel = Channel.objects.create(
        twitch_id="test-channel-123",
        name="testchannel",
        display_name="TestChannel",
    )

    badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(set_id="test-set-123")

    _badge: ChatBadge = ChatBadge.objects.create(
        badge_set=badge_set,
        badge_id="1",
        image_url_1x="https://example.com/badge_18.png",
        image_url_2x="https://example.com/badge_36.png",
        image_url_4x="https://example.com/badge_72.png",
        title="Test Badge",
        description="Test badge description",
        click_action="visit_url",
        click_url="https://example.com",
    )

    url: str = reverse(viewname=url_name, kwargs=kwargs)
    response: _MonkeyPatchedWSGIResponse = client.get(url)
    assert response.status_code == 200
