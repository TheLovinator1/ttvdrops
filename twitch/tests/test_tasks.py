from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

import httpx
import pytest

from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import RewardCampaign
from twitch.tasks import _convert_to_modern_formats
from twitch.tasks import _download_and_save
from twitch.tasks import backup_database
from twitch.tasks import download_all_images
from twitch.tasks import download_benefit_image
from twitch.tasks import download_campaign_image
from twitch.tasks import download_game_image
from twitch.tasks import download_reward_campaign_image
from twitch.tasks import import_chat_badges
from twitch.tasks import import_twitch_file
from twitch.tasks import scan_pending_twitch_files


def test_scan_pending_twitch_files_dispatches_json_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """It should dispatch an import task for each JSON file in the pending directory."""
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    ignored = tmp_path / "notes.txt"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")
    monkeypatch.setenv("TTVDROPS_PENDING_DIR", str(tmp_path))

    with patch("twitch.tasks.import_twitch_file.delay") as delay_mock:
        scan_pending_twitch_files.run()

    delay_mock.assert_has_calls([call(str(first)), call(str(second))], any_order=True)
    assert delay_mock.call_count == 2


def test_scan_pending_twitch_files_skips_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It should do nothing when TTVDROPS_PENDING_DIR is not configured."""
    monkeypatch.delenv("TTVDROPS_PENDING_DIR", raising=False)

    with patch("twitch.tasks.import_twitch_file.delay") as delay_mock:
        scan_pending_twitch_files.run()

    delay_mock.assert_not_called()


def test_import_twitch_file_calls_importer_for_existing_file(tmp_path: Path) -> None:
    """It should run BetterImportDrops with the provided path when the file exists."""
    source = tmp_path / "drops.json"
    source.write_text("{}", encoding="utf-8")

    with patch(
        "twitch.management.commands.better_import_drops.Command.handle",
    ) as handle_mock:
        import_twitch_file.run(str(source))

    assert handle_mock.call_count == 1
    assert handle_mock.call_args.kwargs["path"] == source


def test_import_twitch_file_retries_on_import_error(tmp_path: Path) -> None:
    """It should retry when the importer raises an exception."""
    source = tmp_path / "drops.json"
    source.write_text("{}", encoding="utf-8")
    error = RuntimeError("boom")

    with (
        patch(
            "twitch.management.commands.better_import_drops.Command.handle",
            side_effect=error,
        ),
        patch.object(
            import_twitch_file,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        import_twitch_file.run(str(source))

    retry_mock.assert_called_once_with(exc=error)


def test_download_and_save_skips_when_image_already_cached() -> None:
    """It should not download when the target ImageField already has a file name."""
    file_field = MagicMock()
    file_field.name = "already-there.jpg"

    with patch("twitch.tasks.httpx.Client") as client_mock:
        result = _download_and_save(
            url="https://example.com/test.jpg",
            name="game-1",
            file_field=file_field,
        )

    assert result is False
    client_mock.assert_not_called()


def test_download_and_save_saves_downloaded_content() -> None:
    """It should save downloaded bytes and trigger modern format conversion."""
    file_field = MagicMock()
    file_field.name = ""
    file_field.path = "C:/cache/game-1.png"

    response = MagicMock()
    response.content = b"img-bytes"
    response.raise_for_status.return_value = None

    client = MagicMock()
    client.get.return_value = response
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch("twitch.tasks.httpx.Client", return_value=client_cm),
        patch("twitch.tasks._convert_to_modern_formats") as convert_mock,
    ):
        result = _download_and_save(
            url="https://example.com/path/picture.png",
            name="game-1",
            file_field=file_field,
        )

    assert result is True
    file_field.save.assert_called_once()
    assert file_field.save.call_args.args[0] == "game-1.png"
    assert file_field.save.call_args.kwargs["save"] is True
    convert_mock.assert_called_once_with(Path("C:/cache/game-1.png"))


def test_download_and_save_returns_false_on_http_error() -> None:
    """It should return False when HTTP requests fail."""
    file_field = MagicMock()
    file_field.name = ""

    client = MagicMock()
    client.get.side_effect = httpx.HTTPError("network down")
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with patch("twitch.tasks.httpx.Client", return_value=client_cm):
        result = _download_and_save(
            url="https://example.com/path/picture.png",
            name="game-1",
            file_field=file_field,
        )

    assert result is False
    file_field.save.assert_not_called()


def test_download_game_image_downloads_normalized_twitch_url() -> None:
    """It should normalize Twitch box art URLs before downloading."""
    game = SimpleNamespace(
        box_art="https://static-cdn.jtvnw.net/ttv-boxart/1-{width}x{height}.jpg",
        twitch_id="123",
        box_art_file=MagicMock(),
    )

    with (
        patch("twitch.models.Game.objects.get", return_value=game),
        patch(
            "twitch.utils.is_twitch_box_art_url",
            return_value=True,
        ) as is_twitch_mock,
        patch(
            "twitch.utils.normalize_twitch_box_art_url",
            return_value="https://cdn.example.com/box.jpg",
        ) as normalize_mock,
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_game_image.run(1)

    is_twitch_mock.assert_called_once_with(game.box_art)
    normalize_mock.assert_called_once_with(game.box_art)
    save_mock.assert_called_once_with(
        "https://cdn.example.com/box.jpg",
        "123",
        game.box_art_file,
    )


def test_download_game_image_retries_on_download_error() -> None:
    """It should retry when the image download helper fails unexpectedly."""
    game = SimpleNamespace(
        box_art="https://static-cdn.jtvnw.net/ttv-boxart/1-{width}x{height}.jpg",
        twitch_id="123",
        box_art_file=MagicMock(),
    )
    error = RuntimeError("boom")

    with (
        patch("twitch.models.Game.objects.get", return_value=game),
        patch("twitch.utils.is_twitch_box_art_url", return_value=True),
        patch(
            "twitch.utils.normalize_twitch_box_art_url",
            return_value="https://cdn.example.com/box.jpg",
        ),
        patch("twitch.tasks._download_and_save", side_effect=error),
        patch.object(
            download_game_image,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        download_game_image.run(1)

    retry_mock.assert_called_once_with(exc=error)


def test_download_all_images_calls_expected_commands() -> None:
    """It should invoke both image import management commands."""
    with patch("twitch.tasks.call_command") as call_command_mock:
        download_all_images.run()

    call_command_mock.assert_has_calls([
        call("download_box_art"),
        call("download_campaign_images", model="all"),
    ])


def test_import_chat_badges_retries_on_error() -> None:
    """It should retry when import_chat_badges command execution fails."""
    error = RuntimeError("boom")

    with (
        patch("twitch.tasks.call_command", side_effect=error),
        patch.object(
            import_chat_badges,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        import_chat_badges.run()

    retry_mock.assert_called_once_with(exc=error)


def test_backup_database_calls_backup_command() -> None:
    """It should invoke the backup_db management command."""
    with patch("twitch.tasks.call_command") as call_command_mock:
        backup_database.run()

    call_command_mock.assert_called_once_with("backup_db")


def test_convert_to_modern_formats_skips_non_image_files(tmp_path: Path) -> None:
    """It should skip files that are not jpg, jpeg, or png."""
    text_file = tmp_path / "document.txt"
    text_file.write_text("Not an image", encoding="utf-8")

    with patch("PIL.Image.open") as open_mock:
        _convert_to_modern_formats(text_file)

    open_mock.assert_not_called()


def test_convert_to_modern_formats_skips_missing_files(tmp_path: Path) -> None:
    """It should skip files that do not exist."""
    missing_file = tmp_path / "nonexistent.jpg"

    with patch("PIL.Image.open") as open_mock:
        _convert_to_modern_formats(missing_file)

    open_mock.assert_not_called()


def test_convert_to_modern_formats_converts_rgb_image(tmp_path: Path) -> None:
    """It should save image in WebP and AVIF formats for RGB images."""
    original = tmp_path / "image.jpg"
    original.write_bytes(b"fake-jpeg")

    mock_image = MagicMock()
    mock_image.mode = "RGB"
    mock_copy = MagicMock()
    mock_image.copy.return_value = mock_copy

    with (
        patch("PIL.Image.open") as open_mock,
    ):
        open_mock.return_value.__enter__.return_value = mock_image
        _convert_to_modern_formats(original)

    open_mock.assert_called_once_with(original)
    assert mock_copy.save.call_count == 2
    webp_call = [c for c in mock_copy.save.call_args_list if "webp" in str(c)]
    avif_call = [c for c in mock_copy.save.call_args_list if "avif" in str(c)]
    assert len(webp_call) == 1
    assert len(avif_call) == 1


def test_convert_to_modern_formats_converts_rgba_image_with_background(
    tmp_path: Path,
) -> None:
    """It should create RGB background and paste RGBA image onto it."""
    original = tmp_path / "image.png"
    original.write_bytes(b"fake-png")

    mock_rgba = MagicMock()
    mock_rgba.mode = "RGBA"
    mock_rgba.split.return_value = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    mock_rgba.size = (100, 100)
    mock_rgba.convert.return_value = mock_rgba

    mock_bg = MagicMock()

    with (
        patch("PIL.Image.open") as open_mock,
        patch("PIL.Image.new", return_value=mock_bg) as new_mock,
    ):
        open_mock.return_value.__enter__.return_value = mock_rgba
        _convert_to_modern_formats(original)

    new_mock.assert_called_once_with("RGB", (100, 100), (255, 255, 255))
    mock_bg.paste.assert_called_once()
    assert mock_bg.save.call_count == 2


def test_convert_to_modern_formats_handles_conversion_error(tmp_path: Path) -> None:
    """It should gracefully handle format conversion failures."""
    original = tmp_path / "image.jpg"
    original.write_bytes(b"fake-jpeg")

    mock_image = MagicMock()
    mock_image.mode = "RGB"
    mock_image.copy.return_value = MagicMock()
    mock_image.copy.return_value.save.side_effect = Exception("PIL error")

    with (
        patch("PIL.Image.open") as open_mock,
    ):
        open_mock.return_value.__enter__.return_value = mock_image
        _convert_to_modern_formats(original)  # Should not raise


def test_download_campaign_image_downloads_when_url_exists() -> None:
    """It should download and save campaign image when image_url is present."""
    campaign = SimpleNamespace(
        image_url="https://example.com/campaign.jpg",
        twitch_id="camp123",
        image_file=MagicMock(),
    )

    with (
        patch("twitch.models.DropCampaign.objects.get", return_value=campaign),
        patch("twitch.tasks._download_and_save", return_value=True) as save_mock,
    ):
        download_campaign_image.run(1)

    save_mock.assert_called_once_with(
        "https://example.com/campaign.jpg",
        "camp123",
        campaign.image_file,
    )


def test_download_campaign_image_skips_when_no_url() -> None:
    """It should skip when campaign has no image_url."""
    campaign = SimpleNamespace(
        image_url=None,
        twitch_id="camp123",
        image_file=MagicMock(),
    )

    with (
        patch("twitch.models.DropCampaign.objects.get", return_value=campaign),
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_campaign_image.run(1)

    save_mock.assert_not_called()


def test_download_campaign_image_skips_when_not_found() -> None:
    """It should skip when campaign does not exist."""
    with (
        patch(
            "twitch.models.DropCampaign.objects.get",
            side_effect=DropCampaign.DoesNotExist(),
        ),
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_campaign_image.run(1)

    save_mock.assert_not_called()


def test_download_campaign_image_retries_on_error() -> None:
    """It should retry when image download fails unexpectedly."""
    campaign = SimpleNamespace(
        image_url="https://example.com/campaign.jpg",
        twitch_id="camp123",
        image_file=MagicMock(),
    )
    error = RuntimeError("boom")

    with (
        patch("twitch.models.DropCampaign.objects.get", return_value=campaign),
        patch("twitch.tasks._download_and_save", side_effect=error),
        patch.object(
            download_campaign_image,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        download_campaign_image.run(1)

    retry_mock.assert_called_once_with(exc=error)


def test_download_benefit_image_downloads_when_url_exists() -> None:
    """It should download and save benefit image when image_asset_url is present."""
    benefit = SimpleNamespace(
        image_asset_url="https://example.com/benefit.png",
        twitch_id="benefit123",
        image_file=MagicMock(),
    )

    with (
        patch("twitch.models.DropBenefit.objects.get", return_value=benefit),
        patch("twitch.tasks._download_and_save", return_value=True) as save_mock,
    ):
        download_benefit_image.run(1)

    save_mock.assert_called_once_with(
        url="https://example.com/benefit.png",
        name="benefit123",
        file_field=benefit.image_file,
    )


def test_download_benefit_image_skips_when_no_url() -> None:
    """It should skip when benefit has no image_asset_url."""
    benefit = SimpleNamespace(
        image_asset_url=None,
        twitch_id="benefit123",
        image_file=MagicMock(),
    )

    with (
        patch("twitch.models.DropBenefit.objects.get", return_value=benefit),
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_benefit_image.run(1)

    save_mock.assert_not_called()


def test_download_benefit_image_skips_when_not_found() -> None:
    """It should skip when benefit does not exist."""
    with (
        patch(
            "twitch.models.DropBenefit.objects.get",
            side_effect=DropBenefit.DoesNotExist(),
        ),
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_benefit_image.run(1)

    save_mock.assert_not_called()


def test_download_benefit_image_retries_on_error() -> None:
    """It should retry when image download fails unexpectedly."""
    benefit = SimpleNamespace(
        image_asset_url="https://example.com/benefit.png",
        twitch_id="benefit123",
        image_file=MagicMock(),
    )
    error = RuntimeError("boom")

    with (
        patch("twitch.models.DropBenefit.objects.get", return_value=benefit),
        patch("twitch.tasks._download_and_save", side_effect=error),
        patch.object(
            download_benefit_image,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        download_benefit_image.run(1)

    retry_mock.assert_called_once_with(exc=error)


def test_download_reward_campaign_image_downloads_when_url_exists() -> None:
    """It should download and save reward campaign image when image_url is present."""
    reward = SimpleNamespace(
        image_url="https://example.com/reward.jpg",
        twitch_id="reward123",
        image_file=MagicMock(),
    )

    with (
        patch("twitch.models.RewardCampaign.objects.get", return_value=reward),
        patch("twitch.tasks._download_and_save", return_value=True) as save_mock,
    ):
        download_reward_campaign_image.run(1)

    save_mock.assert_called_once_with(
        "https://example.com/reward.jpg",
        "reward123",
        reward.image_file,
    )


def test_download_reward_campaign_image_skips_when_no_url() -> None:
    """It should skip when reward has no image_url."""
    reward = SimpleNamespace(
        image_url=None,
        twitch_id="reward123",
        image_file=MagicMock(),
    )

    with (
        patch("twitch.models.RewardCampaign.objects.get", return_value=reward),
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_reward_campaign_image.run(1)

    save_mock.assert_not_called()


def test_download_reward_campaign_image_skips_when_not_found() -> None:
    """It should skip when reward does not exist."""
    with (
        patch(
            "twitch.models.RewardCampaign.objects.get",
            side_effect=RewardCampaign.DoesNotExist(),
        ),
        patch("twitch.tasks._download_and_save") as save_mock,
    ):
        download_reward_campaign_image.run(1)

    save_mock.assert_not_called()


def test_download_reward_campaign_image_retries_on_error() -> None:
    """It should retry when image download fails unexpectedly."""
    reward = SimpleNamespace(
        image_url="https://example.com/reward.jpg",
        twitch_id="reward123",
        image_file=MagicMock(),
    )
    error = RuntimeError("boom")

    with (
        patch("twitch.models.RewardCampaign.objects.get", return_value=reward),
        patch("twitch.tasks._download_and_save", side_effect=error),
        patch.object(
            download_reward_campaign_image,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        download_reward_campaign_image.run(1)

    retry_mock.assert_called_once_with(exc=error)
