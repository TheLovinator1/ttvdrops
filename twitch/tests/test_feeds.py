"""Test RSS feeds."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone
from hypothesis.extra.django import TestCase

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

    def test_campaign_feed(self) -> None:
        """Test campaign feed returns 200."""
        url: str = reverse("twitch:campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"

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

    def test_organization_campaign_feed(self) -> None:
        """Test organization-specific campaign feed returns 200."""
        url: str = reverse("twitch:organization_campaign_feed", args=[self.org.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"
        # Verify the organization name is in the feed
        content: str = response.content.decode("utf-8")
        assert "Test Organization" in content

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

    def test_organization_campaign_feed_filters_correctly(self) -> None:
        """Test organization campaign feed only shows campaigns for that organization."""
        # Create another organization with a game and campaign
        other_org = Organization.objects.create(
            twitch_id="other-org-123",
            name="Other Organization",
        )
        other_game = Game.objects.create(
            twitch_id="other-game-456",
            slug="other-game-2",
            name="Other Game 2",
            display_name="Other Game 2",
        )
        other_game.owners.add(other_org)
        DropCampaign.objects.create(
            twitch_id="other-campaign-456",
            name="Other Campaign 2",
            game=other_game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )

        # Get feed for first organization
        url: str = reverse("twitch:organization_campaign_feed", args=[self.org.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        content: str = response.content.decode("utf-8")

        # Should contain first campaign
        assert "Test Campaign" in content
        # Should NOT contain other campaign
        assert "Other Campaign 2" not in content


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
    ("twitch:game_list", {}),
    ("twitch:game_list_simple", {}),
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
    ("twitch:organization_campaign_feed", {"twitch_id": "test-org-123"}),
    ("twitch:reward_campaign_feed", {}),
    ("twitch:campaign_feed_v1", {}),
    ("twitch:game_feed_v1", {}),
    ("twitch:game_campaign_feed_v1", {"twitch_id": "test-game-123"}),
    ("twitch:organization_feed_v1", {}),
    ("twitch:organization_campaign_feed_v1", {"twitch_id": "test-org-123"}),
    ("twitch:reward_campaign_feed_v1", {}),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "kwargs"), URL_NAMES)
def test_rss_feeds_return_200(client: Client, url_name: str, kwargs: dict[str, str]) -> None:
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

    badge_set: ChatBadgeSet = ChatBadgeSet.objects.create(
        set_id="test-set-123",
    )

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
