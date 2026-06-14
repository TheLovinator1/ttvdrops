"""Test RSS feeds."""

import datetime
import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from hypothesis.extra.django import TestCase

from twitch.feeds import RSS_STYLESHEETS
from twitch.feeds import DropCampaignFeed
from twitch.feeds import GameCampaignFeed
from twitch.feeds import GameFeed
from twitch.feeds import GameRewardCampaignFeed
from twitch.feeds import OrganizationRSSFeed
from twitch.feeds import RewardCampaignFeed
from twitch.feeds import TTVDropsBaseFeed
from twitch.feeds import discord_timestamp
from twitch.models import Channel
from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop

logger: logging.Logger = logging.getLogger(__name__)
STYLESHEET_PATH: Path = (
    Path(__file__).resolve().parents[2] / "static" / "rss_styles.xslt"
)

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse
    from django.utils.feedgenerator import Enclosure

    from twitch.tests.test_badge_views import Client


class RSSFeedTestCase(TestCase):
    """Test RSS feeds."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.org: Organization = Organization.objects.create(
            twitch_id="test-org-123",
            name="Test Organization",
        )
        self.org.save()

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

        self.reward_campaign: RewardCampaign = RewardCampaign.objects.create(
            twitch_id="test-reward-123",
            name="Test Reward Campaign",
            brand="Test Brand",
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=7),
            status="ACTIVE",
            summary="Test reward summary",
            instructions="Watch and complete objectives",
            external_url="https://example.com/reward",
            about_url="https://example.com/about",
            is_sitewide=False,
            game=self.game,
        )

    def _create_mixed_paid_and_free_campaign(self) -> DropCampaign:
        """Create an active campaign containing both free and subscription-gated drops.

        Returns:
            DropCampaign: The mixed campaign fixture.
        """
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="mixed-campaign-123",
            name="Mixed Watch And Subscription Campaign",
            game=self.game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )
        channel: Channel = Channel.objects.create(
            twitch_id="mixed-channel-123",
            name="mixedchannel",
            display_name="MixedChannel",
        )
        campaign.allow_channels.add(channel)

        free_drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="free-drop-123",
            name="Watch Drop",
            campaign=campaign,
            required_minutes_watched=30,
            required_subs=0,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
        )
        paid_drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="paid-drop-123",
            name="Subscription Required Drop",
            campaign=campaign,
            required_minutes_watched=0,
            required_subs=1,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
        )
        free_benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="free-benefit-123",
            name="Free Benefit",
            distribution_type="ITEM",
        )
        paid_benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="paid-benefit-123",
            name="Paid Benefit",
            distribution_type="ITEM",
        )
        free_drop.benefits.add(free_benefit)
        paid_drop.benefits.add(paid_benefit)
        return campaign

    def _create_paid_only_campaign(self) -> DropCampaign:
        """Create an active campaign where every drop requires a subscription.

        Returns:
            DropCampaign: The paid-only campaign fixture.
        """
        campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="paid-only-campaign-123",
            name="Paid Only Campaign",
            game=self.game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="paid-only-drop-123",
            name="Paid Only Drop",
            campaign=campaign,
            required_minutes_watched=0,
            required_subs=1,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="paid-only-benefit-123",
            name="Paid Only Benefit",
            distribution_type="ITEM",
        )
        drop.benefits.add(benefit)
        return campaign

    def test_organization_feed(self) -> None:
        """Test organization feed returns 200."""
        url: str = reverse("core:organization_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        assert response["Content-Disposition"] == "inline"

    def test_game_feed(self) -> None:
        """Test game feed returns 200."""
        url: str = reverse("core:game_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        assert response["Content-Disposition"] == "inline"
        content: str = response.content.decode("utf-8")
        assert "Owned by Test Organization." in content

        expected_rss_link: str = reverse(
            "core:game_campaign_feed",
            args=[self.game.twitch_id],
        )
        assert expected_rss_link in content

        # enclosure metadata from our new fields should be present
        msg: str = f"Expected enclosure length from image_size_bytes, got: {content}"
        assert 'length="42"' in content, msg

        msg = f"Expected enclosure type from image_mime_type, got: {content}"
        assert 'type="image/png"' in content, msg

    def test_organization_atom_feed(self) -> None:
        """Test organization Atom feed returns 200 and Atom XML."""
        url: str = reverse("core:organization_feed_atom")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)

        msg: str = f"Expected 200 OK, got {response.status_code} with content: {response.content.decode('utf-8')}"
        assert response.status_code == 200, msg
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content, f"Expected Atom feed XML, got: {content}"

        msg = f"Expected entry element in Atom feed, got: {content}"
        assert "<entry" in content or "<entry" in content, msg

    def test_game_atom_feed(self) -> None:
        """Test game Atom feed returns 200 and contains expected content."""
        url: str = reverse("core:game_feed_atom")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "Owned by Test Organization." in content
        expected_atom_link: str = reverse(
            "core:game_campaign_feed",
            args=[self.game.twitch_id],
        )
        assert expected_atom_link in content
        # Atom should include box art URL somewhere in content
        assert "https://example.com/box.png" in content

    def test_campaign_atom_feed_uses_url_ids_and_correct_self_link(self) -> None:
        """Atom campaign feed should use URL ids and a matching self link."""
        url: str = reverse("core:campaign_feed_atom")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)

        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        msg: str = f"Expected self link in Atom feed, got: {content}"
        assert 'rel="self"' in content, msg

        msg: str = f"Expected self link to point to campaign feed URL, got: {content}"
        assert 'href="https://ttvdrops.lovinator.space/atom/campaigns/"' in content, msg

        msg: str = f"Expected entry ID to be the campaign URL, got: {content}"
        assert (
            "<id>https://ttvdrops.lovinator.space/twitch/campaigns/test-campaign-123/</id>"
            in content
        ), msg

    def test_all_atom_feeds_use_url_ids_and_correct_self_links(self) -> None:
        """All Atom feeds should use absolute URL entry IDs and matching self links."""
        atom_feed_cases: list[tuple[str, dict[str, str], str]] = [
            (
                "core:campaign_feed_atom",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:campaign_detail', args=[self.campaign.twitch_id])}",
            ),
            (
                "core:game_feed_atom",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:game_detail', args=[self.game.twitch_id])}",
            ),
            (
                "core:game_campaign_feed_atom",
                {"twitch_id": self.game.twitch_id},
                f"https://ttvdrops.lovinator.space{reverse('twitch:campaign_detail', args=[self.campaign.twitch_id])}",
            ),
            (
                "core:organization_feed_atom",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:organization_detail', args=[self.org.twitch_id])}",
            ),
            (
                "core:reward_campaign_feed_atom",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:reward_campaign_detail', args=[self.reward_campaign.twitch_id])}",
            ),
            (
                "core:game_reward_feed_atom",
                {"twitch_id": self.game.twitch_id},
                f"https://ttvdrops.lovinator.space{reverse('twitch:reward_campaign_detail', args=[self.reward_campaign.twitch_id])}",
            ),
        ]

        for url_name, kwargs, expected_entry_id in atom_feed_cases:
            url: str = reverse(url_name, kwargs=kwargs)
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)

            assert response.status_code == 200
            content: str = response.content.decode("utf-8")

            expected_self_link: str = f'href="https://ttvdrops.lovinator.space{url}"'
            msg: str = f"Expected self link in Atom feed {url_name}, got: {content}"
            assert 'rel="self"' in content, msg

            msg = f"Expected self link to match feed URL for {url_name}, got: {content}"
            assert expected_self_link in content, msg

            msg = f"Expected entry ID to be absolute URL for {url_name}, got: {content}"
            assert f"<id>{expected_entry_id}</id>" in content, msg

    def test_campaign_atom_feed_summary_does_not_wrap_list_in_paragraph(self) -> None:
        """Atom summary HTML should not include invalid <p><ul> nesting."""
        drop: TimeBasedDrop = TimeBasedDrop.objects.create(
            twitch_id="atom-drop-1",
            name="Atom Drop",
            campaign=self.campaign,
            required_minutes_watched=15,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
        )
        benefit: DropBenefit = DropBenefit.objects.create(
            twitch_id="atom-benefit-1",
            name="Atom Benefit",
            distribution_type="ITEM",
        )
        drop.benefits.add(benefit)

        url: str = reverse("core:campaign_feed_atom")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)

        assert response.status_code == 200
        content: str = response.content.decode("utf-8")
        assert "&lt;ul&gt;" in content
        assert "&lt;p&gt;&lt;ul&gt;" not in content

    def test_atom_feeds_include_stylesheet_processing_instruction(self) -> None:
        """Atom feeds should include an xml-stylesheet processing instruction."""
        feed_urls: list[str] = [
            reverse("core:campaign_feed_atom"),
            reverse("core:game_feed_atom"),
            reverse("core:game_campaign_feed_atom", args=[self.game.twitch_id]),
            reverse("core:game_reward_feed_atom", args=[self.game.twitch_id]),
            reverse("core:organization_feed_atom"),
            reverse("core:reward_campaign_feed_atom"),
        ]

        for url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200

            content: str = response.content.decode("utf-8")
            assert "<?xml-stylesheet" in content
            assert "rss_styles.xslt" in content
            assert 'type="text/xsl"' in content
            assert 'media="screen"' in content

    def test_campaign_and_game_feeds_use_absolute_media_enclosure_urls(self) -> None:
        """Campaign/game RSS+Atom enclosures should use absolute URLs for local media files."""
        self.game.box_art = ""
        assert self.game.box_art_file is not None
        self.game.box_art_file.save(
            "box.png",
            ContentFile(b"game-image-bytes"),
            save=False,
        )
        self.game.box_art_size_bytes = len(b"game-image-bytes")
        self.game.box_art_mime_type = "image/png"
        self.game.save()

        self.campaign.image_url = ""
        assert self.campaign.image_file is not None
        self.campaign.image_file.save(
            "campaign.png",
            ContentFile(b"campaign-image-bytes"),
            save=False,
        )
        self.campaign.image_size_bytes = len(b"campaign-image-bytes")
        self.campaign.image_mime_type = "image/png"
        self.campaign.save()

        feed_urls: list[str] = [
            reverse("core:game_feed"),
            reverse("core:campaign_feed"),
            reverse("core:game_campaign_feed", args=[self.game.twitch_id]),
            reverse("core:game_feed_atom"),
            reverse("core:campaign_feed_atom"),
            reverse("core:game_campaign_feed_atom", args=[self.game.twitch_id]),
        ]

        for url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200
            content: str = response.content.decode("utf-8")

            msg: str = (
                f"Expected absolute media enclosure URLs for {url}, got: {content}"
            )
            assert "https://ttvdrops.lovinator.space/media/" in content, msg
            assert 'url="/media/' not in content, msg
            assert 'href="/media/' not in content, msg

    def test_all_campaign_game_reward_feeds_skip_enclosures_when_length_unknown(
        self,
    ) -> None:
        """RSS/Atom feeds should not emit enclosure elements with length=0."""
        self.game.box_art = "https://example.com/box.png"
        self.game.box_art_size_bytes = None
        self.game.save()

        self.campaign.image_url = "https://example.com/campaign.png"
        self.campaign.image_size_bytes = None
        self.campaign.save()

        self.reward_campaign.image_url = "https://example.com/reward.png"
        self.reward_campaign.save()

        feed_urls: list[str] = [
            reverse("core:game_feed"),
            reverse("core:campaign_feed"),
            reverse("core:game_campaign_feed", args=[self.game.twitch_id]),
            reverse("core:game_reward_feed", args=[self.game.twitch_id]),
            reverse("core:reward_campaign_feed"),
            reverse("core:game_feed_atom"),
            reverse("core:campaign_feed_atom"),
            reverse("core:game_campaign_feed_atom", args=[self.game.twitch_id]),
            reverse("core:game_reward_feed_atom", args=[self.game.twitch_id]),
            reverse("core:reward_campaign_feed_atom"),
        ]

        for url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200
            content: str = response.content.decode("utf-8")

            msg: str = (
                f"Feed {url} unexpectedly emitted a zero-length enclosure: {content}"
            )
            assert 'length="0"' not in content, msg

    def test_game_feed_enclosure_helpers(self) -> None:
        """Helper methods should return values from model fields."""
        feed = GameFeed()
        feed_item_enclosures: list[Enclosure] = feed.item_enclosures(self.game)

        assert feed_item_enclosures, (
            "Expected at least one enclosure for game feed item, got none"
        )

        msg: str = (
            f"Expected one enclosure for game feed item, got: {feed_item_enclosures}"
        )
        assert len(feed_item_enclosures) == 1, msg
        enclosure: Enclosure = feed_item_enclosures[0]

        msg = f"Expected enclosure URL from box_art, got: {enclosure.url}"
        assert enclosure.url == "https://example.com/box.png", msg

        msg = (
            f"Expected enclosure length from image_size_bytes, got: {enclosure.length}"
        )
        assert enclosure.length == str(42), msg

    def test_campaign_feed(self) -> None:
        """Test campaign feed returns 200."""
        url: str = reverse("core:campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        assert response["Content-Disposition"] == "inline"

        content: str = response.content.decode("utf-8")
        # verify enclosure meta
        assert 'length="314"' in content
        assert 'type="image/gif"' in content

    def test_rss_feeds_include_stylesheet_processing_instruction(self) -> None:
        """RSS feeds should include an xml-stylesheet processing instruction."""
        feed_urls: list[str] = [
            reverse("core:campaign_feed"),
            reverse("core:game_feed"),
            reverse("core:game_campaign_feed", args=[self.game.twitch_id]),
            reverse("core:game_reward_feed", args=[self.game.twitch_id]),
            reverse("core:organization_feed"),
            reverse("core:reward_campaign_feed"),
        ]

        for url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200

            content: str = response.content.decode("utf-8")
            assert "<?xml-stylesheet" in content
            assert "rss_styles.xslt" in content
            assert 'type="text/xsl"' in content
            assert 'media="screen"' in content

    def test_base_feed_default_metadata_is_inherited(self) -> None:
        """RSS feed classes should inherit shared defaults from TTVDropsBaseFeed."""
        msg: str = f"Expected TTVDropsBaseFeed.feed_copyright to be 'CC0; Information wants to be free.', got: {TTVDropsBaseFeed.feed_copyright}"
        assert (
            TTVDropsBaseFeed.feed_copyright == "CC0; Information wants to be free."
        ), msg

        msg = f"Expected TTVDropsBaseFeed.stylesheets to be {RSS_STYLESHEETS}, got: {TTVDropsBaseFeed.stylesheets}"
        assert TTVDropsBaseFeed.stylesheets == RSS_STYLESHEETS, msg

        msg = f"Expected TTVDropsBaseFeed.ttl to be 1, got: {TTVDropsBaseFeed.ttl}"
        assert TTVDropsBaseFeed.ttl == 1, msg

        for feed_class in (
            OrganizationRSSFeed,
            GameFeed,
            DropCampaignFeed,
            GameCampaignFeed,
            GameRewardCampaignFeed,
            RewardCampaignFeed,
        ):
            feed: (
                DropCampaignFeed
                | GameCampaignFeed
                | GameFeed
                | GameRewardCampaignFeed
                | OrganizationRSSFeed
                | RewardCampaignFeed
            ) = feed_class()
            assert feed.feed_copyright == "CC0; Information wants to be free."
            assert feed.stylesheets == RSS_STYLESHEETS
            assert feed.ttl == 1

    def test_rss_feeds_include_shared_metadata_fields(self) -> None:
        """RSS output should contain base feed metadata fields."""
        feed_urls: list[str] = [
            reverse("core:campaign_feed"),
            reverse("core:game_feed"),
            reverse("core:game_campaign_feed", args=[self.game.twitch_id]),
            reverse("core:game_reward_feed", args=[self.game.twitch_id]),
            reverse("core:organization_feed"),
            reverse("core:reward_campaign_feed"),
        ]

        for url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200

            content: str = response.content.decode("utf-8")
            assert (
                "<copyright>CC0; Information wants to be free.</copyright>" in content
            )
            assert "<ttl>1</ttl>" in content

    def test_campaign_feed_only_includes_active_campaigns(self) -> None:
        """Campaign feed should exclude past and upcoming campaigns."""
        now: datetime.datetime = timezone.now()
        DropCampaign.objects.create(
            twitch_id="past-campaign-123",
            name="Past Campaign",
            game=self.game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=1),
            operation_names=["DropCampaignDetails"],
        )
        DropCampaign.objects.create(
            twitch_id="upcoming-campaign-123",
            name="Upcoming Campaign",
            game=self.game,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
        )

        url: str = reverse("core:campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        assert "Test Campaign" in content
        assert "Past Campaign" not in content
        assert "Upcoming Campaign" not in content

    def test_campaign_feeds_can_hide_paid_drops(self) -> None:
        """Campaign feeds should support ?hide_paid=1 for subscription-gated drops."""
        mixed_campaign: DropCampaign = self._create_mixed_paid_and_free_campaign()
        paid_only_campaign: DropCampaign = self._create_paid_only_campaign()

        feed_urls: list[str] = [
            reverse("core:campaign_feed"),
            reverse("core:game_campaign_feed", args=[self.game.twitch_id]),
            reverse("core:campaign_feed_atom"),
            reverse("core:game_campaign_feed_atom", args=[self.game.twitch_id]),
            reverse("core:campaign_feed_discord"),
            reverse("core:game_campaign_feed_discord", args=[self.game.twitch_id]),
        ]

        for url in feed_urls:
            hidden_response: _MonkeyPatchedWSGIResponse = self.client.get(
                url,
                {"hide_paid": "1"},
            )
            assert hidden_response.status_code == 200
            hidden_content: str = hidden_response.content.decode("utf-8")
            assert mixed_campaign.name in hidden_content
            assert paid_only_campaign.name not in hidden_content
            assert "Free Benefit" in hidden_content
            assert "Paid Only Benefit" not in hidden_content
            assert "30 minutes watched" in hidden_content
            assert "1 sub required" not in hidden_content
            assert "MixedChannel" in hidden_content

            default_response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert default_response.status_code == 200
            default_content: str = default_response.content.decode("utf-8")
            assert mixed_campaign.name in default_content
            assert paid_only_campaign.name in default_content
            assert "Paid Only Benefit" in default_content
            assert "1 sub required" in default_content

    def test_campaign_feed_enclosure_helpers(self) -> None:
        """Helper methods for campaigns should respect new fields."""
        feed = DropCampaignFeed()

        item_enclosures: list[Enclosure] = feed.item_enclosures(self.campaign)
        assert item_enclosures, (
            "Expected at least one enclosure for campaign feed item, got none"
        )

        msg: str = (
            f"Expected one enclosure for campaign feed item, got: {item_enclosures}"
        )
        assert len(item_enclosures) == 1, msg

        msg = f"Expected enclosure URL from campaign image_url, got: {item_enclosures[0].url}"
        assert item_enclosures[0].url == "https://example.com/campaign.png", msg

        msg = f"Expected enclosure length from image_size_bytes, got: {item_enclosures[0].length}"
        assert item_enclosures[0].length == str(314), msg

        msg = f"Expected enclosure type from image_mime_type, got: {item_enclosures[0].mime_type}"
        assert item_enclosures[0].mime_type == "image/gif", msg

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

        url: str = reverse("core:campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")
        assert "This badge was earned by subscribing." in content

    def test_game_campaign_feed(self) -> None:
        """Test game-specific campaign feed returns 200."""
        url: str = reverse("core:game_campaign_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        assert response["Content-Disposition"] == "inline"
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
        url: str = reverse("core:game_campaign_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        content: str = response.content.decode("utf-8")

        # Should contain first campaign
        assert "Test Campaign" in content
        # Should NOT contain other campaign
        assert "Other Campaign" not in content

        # enclosure metadata also should appear here
        assert 'length="314"' in content
        assert 'type="image/gif"' in content

    def test_game_campaign_feed_only_includes_active_campaigns(self) -> None:
        """Game campaign feed should exclude old and upcoming campaigns."""
        now: datetime.datetime = timezone.now()
        DropCampaign.objects.create(
            twitch_id="game-past-campaign-123",
            name="Game Past Campaign",
            game=self.game,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=1),
            operation_names=["DropCampaignDetails"],
        )
        DropCampaign.objects.create(
            twitch_id="game-upcoming-campaign-123",
            name="Game Upcoming Campaign",
            game=self.game,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=10),
            operation_names=["DropCampaignDetails"],
        )

        url: str = reverse("core:game_campaign_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        assert "Test Campaign" in content
        assert "Game Past Campaign" not in content
        assert "Game Upcoming Campaign" not in content

    def test_reward_campaign_feed_only_includes_active_campaigns(self) -> None:
        """Reward campaign feed should exclude old and upcoming campaigns."""
        now: datetime.datetime = timezone.now()
        RewardCampaign.objects.create(
            twitch_id="active-reward-123",
            name="Active Reward Campaign",
            brand="Test Brand",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
            status="ACTIVE",
            summary="Active reward",
            instructions="Do things",
            external_url="https://example.com/active-reward",
            about_url="https://example.com/about-active-reward",
            is_sitewide=False,
            game=self.game,
        )
        RewardCampaign.objects.create(
            twitch_id="past-reward-123",
            name="Past Reward Campaign",
            brand="Test Brand",
            starts_at=now - timedelta(days=10),
            ends_at=now - timedelta(days=1),
            status="EXPIRED",
            summary="Past reward",
            instructions="Was active",
            external_url="https://example.com/past-reward",
            about_url="https://example.com/about-past-reward",
            is_sitewide=False,
            game=self.game,
        )
        RewardCampaign.objects.create(
            twitch_id="upcoming-reward-123",
            name="Upcoming Reward Campaign",
            brand="Test Brand",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status="UPCOMING",
            summary="Upcoming reward",
            instructions="Wait",
            external_url="https://example.com/upcoming-reward",
            about_url="https://example.com/about-upcoming-reward",
            is_sitewide=False,
            game=self.game,
        )

        url: str = reverse("core:reward_campaign_feed")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        assert "Active Reward Campaign" in content
        assert "Past Reward Campaign" not in content
        assert "Upcoming Reward Campaign" not in content

    def test_game_reward_campaign_feed(self) -> None:
        """Test game-specific reward campaign feed returns 200."""
        url: str = reverse("core:game_reward_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        assert response["Content-Disposition"] == "inline"
        content: str = response.content.decode("utf-8")
        assert "Test Game" in content
        assert "Test Brand: Test Reward Campaign" in content

    def test_game_reward_campaign_feed_filters_correctly(self) -> None:
        """Test game reward feed only shows rewards for that game."""
        other_game: Game = Game.objects.create(
            twitch_id="other-reward-game-123",
            slug="other-reward-game",
            name="Other Game Rewards",
            display_name="Other Game Rewards",
        )
        other_game.owners.add(self.org)
        RewardCampaign.objects.create(
            twitch_id="other-reward-123",
            name="Other Reward Campaign",
            brand="Other Brand",
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=7),
            status="ACTIVE",
            summary="Other reward summary",
            instructions="Do other things",
            external_url="https://example.com/other-reward",
            about_url="https://example.com/about-other-reward",
            is_sitewide=False,
            game=other_game,
        )

        url: str = reverse("core:game_reward_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        content: str = response.content.decode("utf-8")

        assert "Test Brand: Test Reward Campaign" in content
        assert "Other Brand: Other Reward Campaign" not in content

    def test_game_reward_campaign_feed_only_includes_active_campaigns(self) -> None:
        """Game reward campaign feed should exclude old and upcoming campaigns."""
        now: datetime.datetime = timezone.now()
        RewardCampaign.objects.create(
            twitch_id="game-reward-past-123",
            name="Game Past Reward",
            brand="Test Brand",
            starts_at=now - timedelta(days=10),
            ends_at=now - timedelta(days=1),
            status="EXPIRED",
            summary="Past reward",
            instructions="Was active",
            external_url="https://example.com/past-reward",
            about_url="https://example.com/about-past-reward",
            is_sitewide=False,
            game=self.game,
        )
        RewardCampaign.objects.create(
            twitch_id="game-reward-upcoming-123",
            name="Game Upcoming Reward",
            brand="Test Brand",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status="UPCOMING",
            summary="Upcoming reward",
            instructions="Wait",
            external_url="https://example.com/upcoming-reward",
            about_url="https://example.com/about-upcoming-reward",
            is_sitewide=False,
            game=self.game,
        )

        url: str = reverse("core:game_reward_feed", args=[self.game.twitch_id])
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        assert "Test Reward Campaign" in content
        assert "Game Past Reward" not in content
        assert "Game Upcoming Reward" not in content

    def test_game_campaign_feed_enclosure_helpers(self) -> None:
        """GameCampaignFeed helper methods should pull from the model fields."""
        feed = GameCampaignFeed()

        item_enclosures: list[Enclosure] = feed.item_enclosures(self.campaign)
        assert item_enclosures, (
            "Expected at least one enclosure for campaign feed item, got none"
        )

        msg: str = (
            f"Expected one enclosure for campaign feed item, got: {item_enclosures}"
        )
        assert len(item_enclosures) == 1, msg

        msg = f"Expected enclosure URL from campaign image_url, got: {item_enclosures[0].url}"
        assert item_enclosures[0].url == "https://example.com/campaign.png", msg

        msg = f"Expected enclosure length from image_size_bytes, got: {item_enclosures[0].length}"
        assert item_enclosures[0].length == str(314), msg

        msg = f"Expected enclosure type from image_mime_type, got: {item_enclosures[0].mime_type}"
        assert item_enclosures[0].mime_type == "image/gif", msg

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
        assert game2.box_art_file is not None
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
        assert campaign2.image_file is not None
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
        # run again; nothing should error and metadata should still be present
        call_command("backfill_image_dimensions")
        campaign2.refresh_from_db()
        game2.refresh_from_db()
        assert campaign2.image_size_bytes == len(b"world")
        assert campaign2.image_mime_type == "image/jpeg"
        assert game2.box_art_size_bytes == len(b"hello")
        assert game2.box_art_mime_type == "image/png"

        # simulate a case where width is already set but mime/size empty; the
        # command should still fill size/mime even if width gets cleared by the
        # model on save (invalid image data may reset the dimensions).
        game2.box_art_width = 999
        game2.box_art_size_bytes = None
        game2.box_art_mime_type = ""
        game2.save()
        campaign2.image_width = 888
        campaign2.image_size_bytes = None
        campaign2.image_mime_type = ""
        campaign2.save()

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

    url: str = reverse("core:campaign_feed")
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

    url: str = reverse("core:campaign_feed")

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

    url: str = reverse("core:game_campaign_feed", args=[game.twitch_id])

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

    url: str = reverse("core:game_campaign_feed", args=[game.twitch_id])

    with django_assert_num_queries(6, exact=False):
        response: _MonkeyPatchedWSGIResponse = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_game_reward_campaign_feed_queries_bounded(
    client: Client,
    django_assert_num_queries: QueryAsserter,
) -> None:
    """Game reward campaign feed should stay within a modest query budget."""
    org: Organization = Organization.objects.create(
        twitch_id="test-org-game-reward-queries",
        name="Query Org Game Rewards",
    )
    game: Game = Game.objects.create(
        twitch_id="test-game-reward-queries",
        slug="query-game-reward",
        name="Query Game Rewards",
        display_name="Query Game Rewards",
    )
    game.owners.add(org)

    for i in range(3):
        _build_reward_campaign(game, i)

    url: str = reverse("core:game_reward_feed", args=[game.twitch_id])

    with django_assert_num_queries(2, exact=True):
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

    url: str = reverse("core:organization_feed")
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

    url: str = reverse("core:game_feed")
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

    url: str = reverse("core:reward_campaign_feed")
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

    url: str = reverse("core:docs_rss")

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
    ("core:debug", {}),
    ("core:docs_rss", {}),
    ("twitch:emote_gallery", {}),
    ("twitch:games_grid", {}),
    ("twitch:games_list", {}),
    ("twitch:game_detail", {"twitch_id": "test-game-123"}),
    ("twitch:org_list", {}),
    ("twitch:organization_detail", {"twitch_id": "test-org-123"}),
    ("twitch:reward_campaign_list", {}),
    ("twitch:reward_campaign_detail", {"twitch_id": "test-reward-123"}),
    ("core:search", {}),
    ("core:campaign_feed", {}),
    ("core:game_feed", {}),
    ("core:game_campaign_feed", {"twitch_id": "test-game-123"}),
    ("core:game_reward_feed", {"twitch_id": "test-game-123"}),
    ("core:organization_feed", {}),
    ("core:reward_campaign_feed", {}),
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


class DiscordFeedTestCase(TestCase):
    """Test Discord feeds with relative timestamps."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.org: Organization = Organization.objects.create(
            twitch_id="test-org-discord",
            name="Test Organization Discord",
        )
        self.org.save()

        self.game: Game = Game.objects.create(
            twitch_id="test-game-discord",
            slug="test-game-discord",
            name="Test Game Discord",
            display_name="Test Game Discord",
        )
        self.game.owners.add(self.org)
        self.campaign: DropCampaign = DropCampaign.objects.create(
            twitch_id="test-campaign-discord",
            name="Test Campaign Discord",
            game=self.game,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(days=7),
            operation_names=["DropCampaignDetails"],
        )

        self.game.box_art_size_bytes = 42
        self.game.box_art_mime_type = "image/png"
        self.game.box_art = "https://example.com/box.png"
        self.game.save()

        self.campaign.image_size_bytes = 314
        self.campaign.image_mime_type = "image/gif"
        self.campaign.image_url = "https://example.com/campaign.png"
        self.campaign.save()

        self.reward_campaign: RewardCampaign = RewardCampaign.objects.create(
            twitch_id="test-reward-discord",
            name="Test Reward Campaign Discord",
            brand="Test Brand",
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=7),
            status="ACTIVE",
            summary="Test reward summary",
            instructions="Watch and complete objectives",
            external_url="https://example.com/reward",
            about_url="https://example.com/about",
            is_sitewide=False,
            game=self.game,
        )

    def test_discord_timestamp_helper(self) -> None:
        """Test discord_timestamp helper function."""
        dt: datetime.datetime = datetime.datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
        result: str = str(discord_timestamp(dt))
        assert result.startswith("&lt;t:")
        assert result.endswith(":R&gt;")

        # Test None input
        assert not str(discord_timestamp(None))

    def test_organization_discord_feed(self) -> None:
        """Test organization Discord feed returns 200."""
        url: str = reverse("core:organization_feed_discord")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        assert response["Content-Disposition"] == "inline"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content
        assert "http://www.w3.org/2005/Atom" in content

    def test_game_discord_feed(self) -> None:
        """Test game Discord feed returns 200."""
        url: str = reverse("core:game_feed_discord")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content
        assert "Owned by Test Organization Discord." in content

    def test_campaign_discord_feed(self) -> None:
        """Test campaign Discord feed returns 200 with Discord timestamps."""
        url: str = reverse("core:campaign_feed_discord")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content
        # Should contain Discord timestamp format (double-escaped in XML payload)
        assert "&amp;lt;t:" in content
        assert ":R&amp;gt;" in content
        assert "()" not in content

    def test_game_campaign_discord_feed(self) -> None:
        """Test game-specific campaign Discord feed returns 200."""
        url: str = reverse(
            "core:game_campaign_feed_discord",
            args=[self.game.twitch_id],
        )
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content
        assert "Test Game Discord" in content

    def test_reward_campaign_discord_feed(self) -> None:
        """Test reward campaign Discord feed returns 200."""
        url: str = reverse("core:reward_campaign_feed_discord")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content
        # Should contain Discord timestamp format (double-escaped in XML payload)
        assert "&amp;lt;t:" in content
        assert ":R&amp;gt;" in content
        assert "()" not in content

    def test_discord_feeds_use_url_ids_and_correct_self_links(self) -> None:
        """All Discord feeds should use absolute URL entry IDs and matching self links."""
        discord_feed_cases: list[tuple[str, dict[str, str], str]] = [
            (
                "core:campaign_feed_discord",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:campaign_detail', args=[self.campaign.twitch_id])}",
            ),
            (
                "core:game_feed_discord",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:game_detail', args=[self.game.twitch_id])}",
            ),
            (
                "core:game_campaign_feed_discord",
                {"twitch_id": self.game.twitch_id},
                f"https://ttvdrops.lovinator.space{reverse('twitch:campaign_detail', args=[self.campaign.twitch_id])}",
            ),
            (
                "core:organization_feed_discord",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:organization_detail', args=[self.org.twitch_id])}",
            ),
            (
                "core:reward_campaign_feed_discord",
                {},
                f"https://ttvdrops.lovinator.space{reverse('twitch:reward_campaign_detail', args=[self.reward_campaign.twitch_id])}",
            ),
            (
                "core:game_reward_feed_discord",
                {"twitch_id": self.game.twitch_id},
                f"https://ttvdrops.lovinator.space{reverse('twitch:reward_campaign_detail', args=[self.reward_campaign.twitch_id])}",
            ),
        ]

        for url_name, kwargs, expected_entry_id in discord_feed_cases:
            url: str = reverse(url_name, kwargs=kwargs)
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)

            assert response.status_code == 200
            content: str = response.content.decode("utf-8")

            expected_self_link: str = f'href="https://ttvdrops.lovinator.space{url}"'
            msg: str = f"Expected self link in Discord feed {url_name}, got: {content}"
            assert 'rel="self"' in content, msg

            msg = f"Expected self link to match feed URL for {url_name}, got: {content}"
            assert expected_self_link in content, msg

            msg = f"Expected entry ID to be absolute URL for {url_name}, got: {content}"
            assert f"<id>{expected_entry_id}</id>" in content, msg

    def test_discord_feeds_include_stylesheet_processing_instruction(self) -> None:
        """Discord feeds should include an xml-stylesheet processing instruction."""
        feed_urls: list[str] = [
            reverse("core:campaign_feed_discord"),
            reverse("core:game_feed_discord"),
            reverse("core:game_campaign_feed_discord", args=[self.game.twitch_id]),
            reverse("core:game_reward_feed_discord", args=[self.game.twitch_id]),
            reverse("core:organization_feed_discord"),
            reverse("core:reward_campaign_feed_discord"),
        ]

        for url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(url)
            assert response.status_code == 200

            content: str = response.content.decode("utf-8")
            assert "<?xml-stylesheet" in content
            assert "rss_styles.xslt" in content
            assert 'type="text/xsl"' in content
            assert 'media="screen"' in content

    def test_discord_campaign_feed_contains_discord_timestamps(self) -> None:
        """Discord campaign feed should contain Discord relative timestamps."""
        url: str = reverse("core:campaign_feed_discord")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        # Should contain Discord timestamp format (double-escaped in XML payload)
        discord_pattern: re.Pattern[str] = re.compile(r"&amp;lt;t:\d+:R&amp;gt;")
        assert discord_pattern.search(content), (
            f"Expected Discord timestamp format &amp;lt;t:UNIX_TIMESTAMP:R&amp;gt; in content, got: {content}"
        )
        assert "()" not in content

    def test_game_reward_campaign_discord_feed(self) -> None:
        """Test game-specific reward campaign Discord feed returns 200."""
        url: str = reverse(
            "core:game_reward_feed_discord",
            args=[self.game.twitch_id],
        )
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml; charset=utf-8"
        content: str = response.content.decode("utf-8")
        assert "<feed" in content
        assert "Test Game Discord" in content

    def test_discord_reward_campaign_feed_contains_discord_timestamps(self) -> None:
        """Discord reward campaign feed should contain Discord relative timestamps."""
        url: str = reverse("core:reward_campaign_feed_discord")
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        # Should contain Discord timestamp format (double-escaped in XML payload)
        discord_pattern: re.Pattern[str] = re.compile(r"&amp;lt;t:\d+:R&amp;gt;")
        assert discord_pattern.search(content), (
            f"Expected Discord timestamp format &amp;lt;t:UNIX_TIMESTAMP:R&amp;gt; in content, got: {content}"
        )
        assert "()" not in content

    def test_discord_game_reward_campaign_feed_contains_discord_timestamps(
        self,
    ) -> None:
        """Discord game reward campaign feed should contain Discord relative timestamps."""
        url: str = reverse(
            "core:game_reward_feed_discord",
            args=[self.game.twitch_id],
        )
        response: _MonkeyPatchedWSGIResponse = self.client.get(url)
        assert response.status_code == 200
        content: str = response.content.decode("utf-8")

        # Should contain Discord timestamp format (double-escaped in XML payload)
        discord_pattern: re.Pattern[str] = re.compile(r"&amp;lt;t:\d+:R&amp;gt;")
        assert discord_pattern.search(content), (
            f"Expected Discord timestamp format &amp;lt;t:UNIX_TIMESTAMP:R&amp;gt; in content, got: {content}"
        )
        assert "()" not in content

    def test_feed_links_return_200(self) -> None:
        """Test that all links in the feeds return 200 OK."""
        feed_urls: list[str] = [
            reverse("core:organization_feed"),
            reverse("core:game_feed"),
            reverse("core:organization_feed_atom"),
        ]

        for feed_url in feed_urls:
            response: _MonkeyPatchedWSGIResponse = self.client.get(feed_url)
            assert response.status_code == 200, f"Feed {feed_url} did not return 200."

            # Extract all links from the feed content
            content: str = response.content.decode("utf-8")
            links: list[str] = re.findall(r'href=["\'](https?://.*?)["\']', content)

            for link in links:
                skip_links: list[str] = [
                    "rss_styles.xslt",
                    "twitch.tv",
                ]

                if any(skip in link for skip in skip_links):
                    # Skip testing rss_styles.xslt, external Twitch links, and internal links that may not exist in test environment
                    continue
                link_response: _MonkeyPatchedWSGIResponse = self.client.get(link)

                msg: str = f"Link {link} in feed {feed_url} did not return 200, got {link_response.status_code}."
                assert link_response.status_code == 200, msg
