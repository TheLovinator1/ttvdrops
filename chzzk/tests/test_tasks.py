from unittest.mock import patch

import pytest

from chzzk.tasks import discover_chzzk_campaigns
from chzzk.tasks import import_chzzk_campaign_task


def test_import_chzzk_campaign_task_calls_command_with_campaign_no() -> None:
    """It should invoke import_chzzk_campaign for the given campaign number."""
    with patch("chzzk.tasks.call_command") as call_command_mock:
        import_chzzk_campaign_task.run(905)

    call_command_mock.assert_called_once_with("import_chzzk_campaign", "905")


def test_import_chzzk_campaign_task_retries_on_command_error() -> None:
    """It should retry when import_chzzk_campaign raises an exception."""
    error = RuntimeError("boom")

    with (
        patch("chzzk.tasks.call_command", side_effect=error),
        patch.object(
            import_chzzk_campaign_task,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        import_chzzk_campaign_task.run(905)

    retry_mock.assert_called_once_with(exc=error)


def test_discover_chzzk_campaigns_calls_command_with_latest_flag() -> None:
    """It should invoke import_chzzk_campaign with latest=True."""
    with patch("chzzk.tasks.call_command") as call_command_mock:
        discover_chzzk_campaigns.run()

    call_command_mock.assert_called_once_with("import_chzzk_campaign", latest=True)


def test_discover_chzzk_campaigns_retries_on_command_error() -> None:
    """It should retry when import_chzzk_campaign raises an exception."""
    error = RuntimeError("boom")

    with (
        patch("chzzk.tasks.call_command", side_effect=error),
        patch.object(
            discover_chzzk_campaigns,
            "retry",
            side_effect=RuntimeError("retried"),
        ) as retry_mock,
        pytest.raises(RuntimeError, match="retried"),
    ):
        discover_chzzk_campaigns.run()

    retry_mock.assert_called_once_with(exc=error)
