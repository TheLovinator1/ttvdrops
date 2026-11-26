import io
import threading
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone
from PIL import Image

from chzzk.management.commands.download_chzzk_images import Command
from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward


def _png_bytes() -> bytes:
    """Return a small valid PNG for ImageField tests."""
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), (255, 0, 0)).save(buffer, "PNG")
    return buffer.getvalue()


def _make_campaign(campaign_no: int = 1) -> ChzzkCampaign:
    now = timezone.now()
    return ChzzkCampaign.objects.create(
        campaign_no=campaign_no,
        title=f"Campaign {campaign_no}",
        image_url="https://example.com/campaign.png",
        description="",
        category_type="game",
        category_id="1",
        category_value="Game",
        service_id="chzzk",
        state="ACTIVE",
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
        has_ios_based_reward=False,
        drops_campaign_not_started=False,
        source_api="unit-test",
    )


def _mock_response(content: bytes) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.raise_for_status.return_value = None
    response.url.path = "/campaign.png"
    return response


class DownloadChzzkImagesTests(TestCase):
    """Tests for the download_chzzk_images management command."""

    def test_downloads_campaign_image_and_updates_dimensions(self) -> None:
        """Campaign images should be saved to media and get width/height metadata."""
        campaign: ChzzkCampaign = _make_campaign()
        command = Command()
        client = MagicMock()
        client.get.return_value = _mock_response(_png_bytes())

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=Path(media_root)),
        ):
            stats = command._download_campaign_images(
                client=client,
                limit=None,
                force=False,
            )
            campaign.refresh_from_db()

            assert stats["downloaded"] == 1
            assert campaign.image_file
            assert campaign.image_width == 2
            assert campaign.image_height == 3
            assert campaign.image_best_url.startswith("/media/")

    def test_skips_existing_campaign_image_without_force(self) -> None:
        """Existing local images should not be downloaded again by default."""
        campaign: ChzzkCampaign = _make_campaign()
        command = Command()
        client = MagicMock()

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=Path(media_root)),
        ):
            campaign.image_file.save(
                "campaign-1.png",
                ContentFile(_png_bytes()),
                save=True,
            )
            stats = command._download_campaign_images(
                client=client,
                limit=None,
                force=False,
            )

        assert stats["skipped"] == 1
        client.get.assert_not_called()

    def test_downloads_multiple_campaign_images_concurrently(self) -> None:
        """Multiple images should be fetched by several worker threads."""
        for campaign_no in range(1, 5):
            _make_campaign(campaign_no)

        command = Command()
        client = MagicMock()
        client.get.side_effect = lambda _url: _mock_response(_png_bytes())
        thread_names: set[str] = set()
        barrier = threading.Barrier(4)
        original_fetch = command._fetch_image

        def fetch(
            client_: MagicMock,
            image_url: str,
            label: str,
        ) -> tuple[str, bytes | None]:
            thread_names.add(threading.current_thread().name)
            barrier.wait(timeout=5)
            return original_fetch(client_, image_url, label)

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=Path(media_root)),
            patch.object(command, "_fetch_image", side_effect=fetch),
        ):
            stats = command._download_campaign_images(
                client=client,
                limit=None,
                force=False,
                workers=4,
            )

        assert stats["downloaded"] == 4
        assert len(thread_names) > 1

    def test_downloads_reward_image(self) -> None:
        """Reward images should be downloaded using campaign and reward IDs."""
        campaign: ChzzkCampaign = _make_campaign()
        reward: ChzzkReward = ChzzkReward.objects.create(
            campaign=campaign,
            reward_no=10,
            image_url="https://example.com/reward.png",
            title="Reward",
            reward_type="ITEM",
            condition_type="watch",
            condition_for_minutes=10,
            ios_based_reward=False,
            code_remaining_count=1,
        )
        command = Command()
        client = MagicMock()
        client.get.return_value = _mock_response(_png_bytes())

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=Path(media_root)),
        ):
            stats = command._download_reward_images(
                client=client,
                limit=None,
                force=False,
            )
            reward.refresh_from_db()

            assert stats["downloaded"] == 1
            assert reward.image_file
            assert "campaign-1-reward-10" in reward.image_file.name
            assert reward.image_best_url.startswith("/media/")
