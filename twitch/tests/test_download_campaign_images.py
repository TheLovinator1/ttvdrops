"""Tests for the download_campaign_images management command (Reward images)."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.test import TestCase

from twitch.management.commands.download_campaign_images import Command
from twitch.models import Reward
from twitch.models import RewardCampaign


@pytest.mark.no_zeal
class DownloadRewardImagesTests(TestCase):
    """Tests for downloading individual Reward images."""

    def setUp(self) -> None:
        """Create a command instance and a parent reward campaign."""
        self.command = Command()
        self.campaign = RewardCampaign.objects.create(
            twitch_id="dl-campaign",
            name="Download Campaign",
            brand="Brand",
            status="ACTIVE",
        )

    def _make_reward(
        self,
        twitch_id: str,
        *,
        thumbnail: str = "",
        banner: str = "",
    ) -> Reward:
        return Reward.objects.create(
            reward_campaign=self.campaign,
            twitch_id=twitch_id,
            name=f"Reward {twitch_id}",
            thumbnail_image_url=thumbnail,
            banner_image_url=banner,
        )

    @patch.object(Command, "_download_image", return_value="downloaded")
    def test_uses_thumbnail_preferred_over_banner(
        self,
        mock_download: MagicMock,
    ) -> None:
        """The thumbnail URL should be downloaded when both are present."""
        reward = self._make_reward(
            "r-thumb",
            thumbnail="https://example.com/thumb.png",
            banner="https://example.com/banner.png",
        )
        client = MagicMock()

        stats = self.command._download_reward_images(
            client=client,
            limit=None,
            force=False,
        )

        assert stats["total"] == 1
        assert stats["downloaded"] == 1
        mock_download.assert_called_once()
        call_args = mock_download.call_args.args
        assert call_args[0] is client
        assert call_args[1] == "https://example.com/thumb.png"
        assert call_args[2] == reward.twitch_id

    @patch.object(Command, "_download_image", return_value="downloaded")
    def test_falls_back_to_banner_without_thumbnail(
        self,
        mock_download: MagicMock,
    ) -> None:
        """The banner URL should be downloaded when there is no thumbnail."""
        reward = self._make_reward(
            "r-banner",
            banner="https://example.com/banner.png",
        )
        client = MagicMock()

        stats = self.command._download_reward_images(
            client=client,
            limit=None,
            force=False,
        )

        assert stats["downloaded"] == 1
        mock_download.assert_called_once()
        call_args = mock_download.call_args.args
        assert call_args[0] is client
        assert call_args[1] == "https://example.com/banner.png"
        assert call_args[2] == reward.twitch_id

    @patch.object(Command, "_download_image", return_value="downloaded")
    def test_skips_reward_without_image_urls(
        self,
        mock_download: MagicMock,
    ) -> None:
        """Rewards without any image URL should be skipped."""
        self._make_reward("r-no-image")

        stats = self.command._download_reward_images(
            client=MagicMock(),
            limit=None,
            force=False,
        )

        assert stats["total"] == 1
        assert stats["skipped"] == 1
        mock_download.assert_not_called()

    @patch.object(Command, "_download_image", return_value="downloaded")
    def test_skips_when_already_cached_without_force(
        self,
        mock_download: MagicMock,
    ) -> None:
        """Cached rewards should be skipped unless --force is given."""
        reward = self._make_reward(
            "r-cached",
            thumbnail="https://example.com/thumb.png",
        )
        reward.image_file.name = "rewards/images/cached.png"
        reward.save(update_fields=["image_file"])

        stats = self.command._download_reward_images(
            client=MagicMock(),
            limit=None,
            force=False,
        )

        assert stats["skipped"] == 1
        mock_download.assert_not_called()

    @patch.object(Command, "_download_image", return_value="downloaded")
    def test_force_redownloads_cached_image(
        self,
        mock_download: MagicMock,
    ) -> None:
        """--force should re-download even when an image file exists."""
        reward = self._make_reward(
            "r-force",
            thumbnail="https://example.com/thumb.png",
        )
        reward.image_file.name = "rewards/images/cached.png"
        reward.save(update_fields=["image_file"])

        stats = self.command._download_reward_images(
            client=MagicMock(),
            limit=None,
            force=True,
        )

        assert stats["downloaded"] == 1
        mock_download.assert_called_once()

    @patch.object(Command, "_download_image", return_value="failed")
    def test_counts_failures(self, mock_download: MagicMock) -> None:
        """Download failures should be counted in the stats."""
        self._make_reward("r-fail", thumbnail="https://example.com/thumb.png")

        stats = self.command._download_reward_images(
            client=MagicMock(),
            limit=None,
            force=False,
        )

        assert stats["failed"] == 1
        mock_download.assert_called_once()

    def test_image_best_url_prefers_local_file(self) -> None:
        """image_best_url should return the local file URL first."""
        reward = self._make_reward(
            "r-best",
            thumbnail="https://example.com/thumb.png",
            banner="https://example.com/banner.png",
        )
        reward.image_file.name = "rewards/images/cached.png"
        reward.save(update_fields=["image_file"])

        assert reward.image_best_url == "/media/rewards/images/cached.png"

    def test_image_best_url_falls_back_to_thumbnail(self) -> None:
        """image_best_url should use the thumbnail when nothing is cached."""
        reward = self._make_reward(
            "r-best-thumb",
            thumbnail="https://example.com/thumb.png",
            banner="https://example.com/banner.png",
        )

        assert reward.image_best_url == "https://example.com/thumb.png"

    def test_image_best_url_empty_without_sources(self) -> None:
        """image_best_url should be empty when no image sources exist."""
        reward = self._make_reward("r-best-empty")

        assert not reward.image_best_url
