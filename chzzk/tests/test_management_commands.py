from datetime import timedelta
from io import StringIO
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

import pytest
import requests
from django.core.management import CommandError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from chzzk.management.commands.import_chzzk_campaign import (
    Command as ImportChzzkCampaignCommand,
)
from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward
from chzzk.schemas import ChzzkCampaignV2
from chzzk.schemas import ChzzkRewardV2


class ImportChzzkCampaignRangeCommandTest(TestCase):
    """Tests for the import_chzzk_campaign_range management command."""

    def test_imports_campaigns_in_range_descending_by_default(self) -> None:
        """Test that campaigns are imported in descending order when start > end."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ) as call_command_mock:
            call_command(
                "import_chzzk_campaign_range",
                "5",
                "3",
                stdout=stdout,
            )

        # Verify call_command was called for each campaign in descending order
        expected_calls = [
            call("import_chzzk_campaign", "5"),
            call("import_chzzk_campaign", "4"),
            call("import_chzzk_campaign", "3"),
        ]
        call_command_mock.assert_has_calls(expected_calls)
        assert call_command_mock.call_count == 3

    def test_imports_campaigns_in_range_ascending_by_default(self) -> None:
        """Test that campaigns are imported in ascending order when start < end."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ) as call_command_mock:
            call_command(
                "import_chzzk_campaign_range",
                "3",
                "5",
                stdout=stdout,
            )

        expected_calls = [
            call("import_chzzk_campaign", "3"),
            call("import_chzzk_campaign", "4"),
            call("import_chzzk_campaign", "5"),
        ]
        call_command_mock.assert_has_calls(expected_calls)
        assert call_command_mock.call_count == 3

    def test_imports_single_campaign_when_start_equals_end(self) -> None:
        """Test that a single campaign is imported when start equals end."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ) as call_command_mock:
            call_command(
                "import_chzzk_campaign_range",
                "5",
                "5",
                stdout=stdout,
            )

        call_command_mock.assert_called_once_with("import_chzzk_campaign", "5")

    def test_respects_custom_step_parameter(self) -> None:
        """Test that custom step parameter is respected."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ) as call_command_mock:
            call_command(
                "import_chzzk_campaign_range",
                "1",
                "10",
                "--step",
                "2",
                stdout=stdout,
            )

        expected_calls = [
            call("import_chzzk_campaign", "1"),
            call("import_chzzk_campaign", "3"),
            call("import_chzzk_campaign", "5"),
            call("import_chzzk_campaign", "7"),
            call("import_chzzk_campaign", "9"),
        ]
        call_command_mock.assert_has_calls(expected_calls)
        assert call_command_mock.call_count == 5

    def test_respects_custom_negative_step(self) -> None:
        """Test that custom negative step parameter works correctly."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ) as call_command_mock:
            call_command(
                "import_chzzk_campaign_range",
                "10",
                "1",
                "--step",
                "-2",
                stdout=stdout,
            )

        expected_calls = [
            call("import_chzzk_campaign", "10"),
            call("import_chzzk_campaign", "8"),
            call("import_chzzk_campaign", "6"),
            call("import_chzzk_campaign", "4"),
            call("import_chzzk_campaign", "2"),
        ]
        call_command_mock.assert_has_calls(expected_calls)
        assert call_command_mock.call_count == 5

    def test_handles_command_error_gracefully(self) -> None:
        """Test that CommandError from import_chzzk_campaign is caught and reported."""
        stdout = StringIO()
        stderr = StringIO()

        def side_effect(command: str, *args: str, **kwargs: StringIO) -> None:
            if "4" in args:
                msg = "Campaign 4 not found"
                raise CommandError(msg)

        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
            side_effect=side_effect,
        ):
            call_command(
                "import_chzzk_campaign_range",
                "3",
                "5",
                stdout=stdout,
                stderr=stderr,
            )

        output = stdout.getvalue()
        assert "Importing campaign 3" in output
        assert "Importing campaign 4" in output
        assert "Importing campaign 5" in output
        assert "Failed campaign 4" in output
        assert "Campaign 4 not found" in output
        assert "Batch import complete" in output

    def test_continues_after_command_error(self) -> None:
        """Test that import continues after encountering a CommandError."""
        call_count = 0

        def side_effect(command: str, campaign_no: str) -> None:
            nonlocal call_count
            call_count += 1
            if campaign_no == "4":
                msg = "Campaign 4 error"
                raise CommandError(msg)

        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
            side_effect=side_effect,
        ):
            call_command(
                "import_chzzk_campaign_range",
                "3",
                "5",
                stdout=StringIO(),
            )

        # Verify all campaigns were attempted
        assert call_count == 3

    def test_outputs_success_messages(self) -> None:
        """Test that success messages are written to stdout."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ):
            call_command(
                "import_chzzk_campaign_range",
                "1",
                "2",
                stdout=stdout,
            )

        output: str = stdout.getvalue()
        assert "Importing campaigns from 1 to 2 with step 1" in output
        assert "Batch import complete" in output

    def test_raises_error_when_step_is_zero(self) -> None:
        """Test that ValueError is raised when step is 0."""
        with pytest.raises(ValueError, match="Step cannot be 0"):
            call_command(
                "import_chzzk_campaign_range",
                "1",
                "5",
                "--step",
                "0",
                stdout=StringIO(),
            )

    def test_handles_large_range(self) -> None:
        """Test that large ranges are handled correctly."""
        stdout = StringIO()
        with patch(
            "chzzk.management.commands.import_chzzk_campaign_range.call_command",
        ) as call_command_mock:
            call_command(
                "import_chzzk_campaign_range",
                "1",
                "100",
                "--step",
                "10",
                stdout=stdout,
            )

        assert call_command_mock.call_count == 10
        first_call = call_command_mock.call_args_list[0]
        assert first_call == call("import_chzzk_campaign", "1")
        last_call = call_command_mock.call_args_list[-1]
        assert last_call == call("import_chzzk_campaign", "91")


class ImportChzzkCampaignCommandTest(TestCase):
    """Tests for the import_chzzk_campaign management command."""

    def _create_campaign(self, campaign_no: int) -> ChzzkCampaign:
        now = timezone.now()
        return ChzzkCampaign.objects.create(
            campaign_no=campaign_no,
            title=f"Campaign {campaign_no}",
            description="Campaign description",
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

    def test_requires_campaign_no_when_latest_not_used(self) -> None:
        """Command should fail without campaign_no unless --latest is provided."""
        with pytest.raises(
            CommandError,
            match="campaign_no is required unless --latest is used",
        ):
            call_command("import_chzzk_campaign", stdout=StringIO())

    def test_imports_single_campaign_no(self) -> None:
        """Command should import the provided campaign number."""
        with patch(
            "chzzk.management.commands.import_chzzk_campaign.Command._import_campaign",
            autospec=True,
        ) as import_campaign_mock:
            call_command("import_chzzk_campaign", "42", stdout=StringIO())

        assert import_campaign_mock.call_count == 1
        assert import_campaign_mock.call_args.args[1] == 42

    def test_latest_imports_candidates(self) -> None:
        """Command should import all candidates returned by get_campaign_import_candidates."""
        with (
            patch(
                "chzzk.management.commands.import_chzzk_campaign.Command.get_campaign_import_candidates",
                autospec=True,
                return_value=[9, 10, 11],
            ),
            patch(
                "chzzk.management.commands.import_chzzk_campaign.Command._import_campaign",
                autospec=True,
            ) as import_campaign_mock,
        ):
            call_command("import_chzzk_campaign", "--latest", stdout=StringIO())

        called_campaigns = [
            call_.args[1] for call_ in import_campaign_mock.call_args_list
        ]
        assert called_campaigns == [9, 10, 11]

    def test_latest_with_no_candidates_exits_cleanly(self) -> None:
        """Command should not import anything when --latest has no candidates."""
        stdout = StringIO()

        with (
            patch(
                "chzzk.management.commands.import_chzzk_campaign.Command.get_campaign_import_candidates",
                autospec=True,
                return_value=[],
            ),
            patch(
                "chzzk.management.commands.import_chzzk_campaign.Command._import_campaign",
                autospec=True,
            ) as import_campaign_mock,
        ):
            call_command("import_chzzk_campaign", "--latest", stdout=stdout)

        assert import_campaign_mock.call_count == 0
        assert "Nothing to import with --latest at this time." in stdout.getvalue()

    def test_get_campaign_import_candidates_uses_initial_range_on_empty_db(
        self,
    ) -> None:
        """When there are no campaigns, candidates should be 1..5."""
        command: ImportChzzkCampaignCommand = ImportChzzkCampaignCommand()
        candidates: list[int] = command.get_campaign_import_candidates()
        assert candidates == [1, 2, 3, 4, 5]

    def test_get_campaign_import_candidates_adds_backfill_and_new_candidates(
        self,
    ) -> None:
        """Candidates should include missing IDs from latest-5..latest-1 plus latest+1..latest+5."""
        self._create_campaign(10)
        self._create_campaign(8)
        self._create_campaign(6)

        command: ImportChzzkCampaignCommand = ImportChzzkCampaignCommand()
        candidates: list[int] = command.get_campaign_import_candidates()

        assert candidates == [5, 7, 9, 11, 12, 13, 14, 15]

    def test_get_campaign_import_candidates_ignores_outlier_max_campaign_no(
        self,
    ) -> None:
        """If max campaign_no is an outlier, the second max should be used."""
        self._create_campaign(250)
        self._create_campaign(100_002_000)

        command: ImportChzzkCampaignCommand = ImportChzzkCampaignCommand()
        candidates: list[int] = command.get_campaign_import_candidates()

        assert candidates == [245, 246, 247, 248, 249, 251, 252, 253, 254, 255]

    def test_import_campaign_handles_http_error_with_json_message(self) -> None:
        """Import should fail gracefully on HTTP errors and include JSON message when present."""
        command = ImportChzzkCampaignCommand()

        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("404 Client Error")
        response.headers = {"Content-Type": "application/json; charset=utf-8"}
        response.json.return_value = {"message": "Campaign not found"}

        with (
            patch(
                "chzzk.management.commands.import_chzzk_campaign.requests.get",
                return_value=response,
            ),
            patch.object(command.stdout, "write") as stdout_write,
            patch.object(command, "import_campaign_data") as import_campaign_data_mock,
        ):
            command._import_campaign(12345)

        assert import_campaign_data_mock.call_count == 0
        assert stdout_write.call_count == 1
        msg = stdout_write.call_args.args[0]
        assert "Failed to fetch campaign 12345" in msg
        assert "Campaign not found" in msg

    def test_update_or_create_reward_updates_existing_reward(self) -> None:
        """Existing rewards should be updated when incoming reward data differs."""
        campaign = self._create_campaign(500)
        existing_reward = ChzzkReward.objects.create(
            campaign=campaign,
            reward_no=1,
            title="Old title",
            image_url="https://example.com/old.png",
            reward_type="OLD",
            campaign_reward_type="",
            condition_type="TIME",
            condition_for_minutes=10,
            ios_based_reward=False,
            code_remaining_count=5,
        )

        reward_data = ChzzkRewardV2.model_validate(
            {
                "title": "New title",
                "rewardNo": 1,
                "imageUrl": "https://example.com/new.png",
                "rewardType": "NEW",
                "conditionType": "WATCH",
                "conditionForMinutes": 20,
                "iosBasedReward": True,
                "codeRemainingCount": 99,
            },
        )

        command = ImportChzzkCampaignCommand()
        command.update_or_create_reward(500, campaign, reward_data)

        existing_reward.refresh_from_db()
        assert existing_reward.title == "New title"
        assert existing_reward.image_url == "https://example.com/new.png"
        assert existing_reward.reward_type == "NEW"
        assert not existing_reward.campaign_reward_type
        assert existing_reward.condition_type == "WATCH"
        assert existing_reward.condition_for_minutes == 20
        assert existing_reward.ios_based_reward is True
        assert existing_reward.code_remaining_count == 99

    def test_import_campaign_data_updates_existing_campaign(self) -> None:
        """Existing campaigns should be updated when imported fields have changed."""
        campaign = self._create_campaign(600)
        original_scraped_at = campaign.scraped_at

        command = ImportChzzkCampaignCommand()
        campaign_data = ChzzkCampaignV2.model_validate(
            {
                "campaignNo": 600,
                "title": "Updated title",
                "imageUrl": "https://example.com/new-campaign.png",
                "description": "Updated description",
                "categoryType": "game",
                "categoryId": "2",
                "categoryValue": "Game 2",
                "pcLinkUrl": "https://example.com/pc",
                "mobileLinkUrl": "https://example.com/mobile",
                "serviceId": "chzzk",
                "state": "ACTIVE",
                "startDate": campaign.start_date.isoformat(),
                "endDate": campaign.end_date.isoformat(),
                "rewardList": [],
                "hasIosBasedReward": True,
                "dropsCampaignNotStarted": False,
                "rewardType": "DROP",
                "accountLinkUrl": "https://example.com/account",
            },
        )

        updated_campaign = command.import_campaign_data(
            campaign_no=600,
            api_version="v2",
            data={"key": "value"},
            cd=campaign_data,
        )

        updated_campaign.refresh_from_db()
        assert updated_campaign.title == "Updated title"
        assert updated_campaign.description == "Updated description"
        assert updated_campaign.category_id == "2"
        assert updated_campaign.has_ios_based_reward is True
        assert not updated_campaign.campaign_reward_type
        assert updated_campaign.reward_type == "DROP"
        assert updated_campaign.account_link_url == "https://example.com/account"
        assert updated_campaign.raw_json_v2 == {"key": "value"}
        assert updated_campaign.scrape_status == "success"
        assert updated_campaign.scraped_at >= original_scraped_at

    def test_apply_updates_if_changed_returns_false_on_noop(self) -> None:
        """Helper should return False when values are unchanged."""
        campaign = self._create_campaign(700)
        command = ImportChzzkCampaignCommand()

        changed = command._apply_updates_if_changed(
            campaign,
            {
                "title": campaign.title,
                "description": campaign.description,
            },
        )

        assert changed is False
