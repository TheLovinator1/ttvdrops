"""Test RSS feeds."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop


class RSSFeedTestCase(TestCase):
    """Test RSS feeds."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.org = Organization.objects.create(
            twitch_id="test-org-123",
            name="Test Organization",
        )
        self.game = Game.objects.create(
            twitch_id="test-game-123",
            slug="test-game",
            name="Test Game",
            display_name="Test Game",
        )
        self.game.owners.add(self.org)
        self.campaign = DropCampaign.objects.create(
            twitch_id="test-campaign-123",
            name="Test Campaign",
            game=self.game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )

    def test_organization_feed(self) -> None:
        """Test organization feed returns 200."""
        url = reverse("twitch:organization_feed")
        response = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"

    def test_game_feed(self) -> None:
        """Test game feed returns 200."""
        url = reverse("twitch:game_feed")
        response = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"

    def test_campaign_feed(self) -> None:
        """Test campaign feed returns 200."""
        url = reverse("twitch:campaign_feed")
        response = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"

    def test_campaign_feed_includes_badge_description(self) -> None:
        """Badge benefit descriptions should be visible in the RSS drop summary."""
        drop = TimeBasedDrop.objects.create(
            twitch_id="drop-1",
            name="Diana Chat Badge",
            campaign=self.campaign,
            required_minutes_watched=0,
            required_subs=1,
        )
        benefit = DropBenefit.objects.create(
            twitch_id="benefit-1",
            name="Diana",
            distribution_type="BADGE",
        )
        drop.benefits.add(benefit)

        badge_set = ChatBadgeSet.objects.create(set_id="diana")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Diana",
            description="This badge was earned by subscribing.",
        )

        url = reverse("twitch:campaign_feed")
        response = self.client.get(url)
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "This badge was earned by subscribing." in content

    def test_game_campaign_feed(self) -> None:
        """Test game-specific campaign feed returns 200."""
        url = reverse("twitch:game_campaign_feed", args=[self.game.twitch_id])
        response = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"
        # Verify the game name is in the feed
        content = response.content.decode("utf-8")
        assert "Test Game" in content

    def test_organization_campaign_feed(self) -> None:
        """Test organization-specific campaign feed returns 200."""
        url = reverse("twitch:organization_campaign_feed", args=[self.org.twitch_id])
        response = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"
        # Verify the organization name is in the feed
        content = response.content.decode("utf-8")
        assert "Test Organization" in content

    def test_game_campaign_feed_filters_correctly(self) -> None:
        """Test game campaign feed only shows campaigns for that game."""
        # Create another game with a campaign
        other_game = Game.objects.create(
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
        url = reverse("twitch:game_campaign_feed", args=[self.game.twitch_id])
        response = self.client.get(url)
        content = response.content.decode("utf-8")

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
        url = reverse("twitch:organization_campaign_feed", args=[self.org.twitch_id])
        response = self.client.get(url)
        content = response.content.decode("utf-8")

        # Should contain first campaign
        assert "Test Campaign" in content
        # Should NOT contain other campaign
        assert "Other Campaign 2" not in content
