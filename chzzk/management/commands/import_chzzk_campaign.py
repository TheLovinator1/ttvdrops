from typing import TYPE_CHECKING
from typing import Any

import requests
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward
from chzzk.schemas import ChzzkApiResponseV2

if TYPE_CHECKING:
    import argparse

    from chzzk.schemas import ChzzkCampaignV2
    from chzzk.schemas import ChzzkRewardV2

MAX_CAMPAIGN_OUTLIER_THRESHOLD: int = 100_000_000
MAX_CAMPAIGN_OUTLIER_GAP: int = 1_000

CHZZK_API_URLS: list[tuple[str, str]] = [
    ("v1", "https://api.chzzk.naver.com/service/v1/drops/campaigns/{campaign_no}"),
    ("v2", "https://api.chzzk.naver.com/service/v2/drops/campaigns/{campaign_no}"),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"
)


class Command(BaseCommand):
    """Django management command to scrape Chzzk drops campaigns from both v1 and v2 APIs and store them in the database."""

    help = "Scrape Chzzk drops campaigns from both v1 and v2 APIs and store them."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add command-line arguments for the management command."""
        parser.add_argument(
            "campaign_no",
            nargs="?",
            type=int,
            help="Campaign number to fetch (required unless --latest is used)",
        )
        parser.add_argument(
            "--latest",
            action="store_true",
            help=(
                "Fetches the highest existing campaign_no and imports "
                "missing IDs from latest-5..latest-1 plus latest+1..latest+5."
            ),
        )

    def handle(self, **options) -> None:
        """Main handler for the management command.

        Raises:
            CommandError: If campaign_no is missing when --latest is not used.
        """
        latest: bool = bool(options.get("latest"))
        campaign_no: int | None = options.get("campaign_no")

        if latest:
            to_import: list[int] = self.get_campaign_import_candidates()

            if not to_import:
                msg: str = "Nothing to import with --latest at this time."
                self.stdout.write(self.style.SUCCESS(msg))
                return

            for target_no in to_import:
                self.stdout.write(f"Importing campaign {target_no}...")
                self._import_campaign(target_no)

            self.stdout.write(self.style.SUCCESS("--latest import completed."))
            return

        if campaign_no is None:
            err_msg: str = "campaign_no is required unless --latest is used"
            raise CommandError(err_msg)

        self._import_campaign(int(campaign_no))

    def get_campaign_import_candidates(self) -> list[int]:
        """Determine which campaign numbers to import when --latest is used.

        Returns:
            list[int]: A list of campaign numbers that should be imported.
        """
        # Handle potential outliers by checking the top two campaign IDs.
        campaign_ids = list(
            ChzzkCampaign.objects.order_by("-campaign_no").values_list(
                "campaign_no",
                flat=True,
            )[:2],
        )

        max_campaign_no: int = campaign_ids[0] if campaign_ids else 0
        second_max_campaign_no: int = campaign_ids[1] if len(campaign_ids) > 1 else 0

        if (
            max_campaign_no > MAX_CAMPAIGN_OUTLIER_THRESHOLD
            and max_campaign_no - second_max_campaign_no > MAX_CAMPAIGN_OUTLIER_GAP
        ):
            self.stdout.write(
                self.style.WARNING(
                    f"Detected an outlier max campaign_no {max_campaign_no}; "
                    f"using second max {second_max_campaign_no} instead.",
                ),
            )
            max_campaign_no = second_max_campaign_no

        msg: str = f"Max campaign_no in database: {max_campaign_no}"
        self.stdout.write(self.style.SUCCESS(msg))

        if max_campaign_no <= 0:
            backfill_candidates: list[int] = []
        else:
            backfill_start: int = max(1, max_campaign_no - 5)
            existing_lower_ids: set[int] = set(
                ChzzkCampaign.objects.filter(
                    campaign_no__gte=backfill_start,
                    campaign_no__lt=max_campaign_no,
                ).values_list("campaign_no", flat=True),
            )
            backfill_candidates: list[int] = [
                idx
                for idx in range(backfill_start, max_campaign_no)
                if idx not in existing_lower_ids
            ]

        new_candidates: list[int] = list(
            range(
                max_campaign_no + 1,
                max_campaign_no + 6,
            ),
        )
        to_import: list[int] = backfill_candidates + new_candidates
        return to_import

    def _import_campaign(self, campaign_no: int) -> None:
        """Import a single campaign by its campaign number.

        Args:
            campaign_no (int): The campaign number to import.
        """
        api_version: str = "v2"  # TODO(TheLovinator): Add support for v1 API  # ruff:ignore[missing-todo-link]
        url: str = f"https://api.chzzk.naver.com/service/{api_version}/drops/campaigns/{campaign_no}"
        resp: requests.Response = requests.get(
            url,
            timeout=2,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            json_msg: str = ""
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                error_data: dict[str, Any] = resp.json()
                json_msg = error_data.get("message", "")

            msg: str = f"Failed to fetch campaign {campaign_no}: {e} - {json_msg}"
            self.stdout.write(self.style.ERROR(msg))
            return

        data: dict[str, Any] = resp.json()
        cd: ChzzkCampaignV2 = ChzzkApiResponseV2.model_validate(data).content

        campaign_obj: ChzzkCampaign = self.import_campaign_data(
            campaign_no=campaign_no,
            api_version=api_version,
            data=data,
            cd=cd,
        )

        cd_reward_list: list[ChzzkRewardV2] = cd.reward_list
        for reward in cd_reward_list:
            self.update_or_create_reward(campaign_no, campaign_obj, reward)

        self.stdout.write(self.style.SUCCESS(f"Imported campaign {campaign_no}"))

    def update_or_create_reward(
        self,
        campaign_no: int,
        campaign_obj: ChzzkCampaign,
        reward: ChzzkRewardV2,
    ) -> None:
        """Update or create a reward for a given campaign.

        Args:
            campaign_no (int): The campaign number the reward belongs to.
            campaign_obj (ChzzkCampaign): The campaign database object the reward belongs to.
            reward (ChzzkRewardV2): The reward data parsed from the API response.
        """
        reward_defaults: dict[str, Any] = {
            "image_url": reward.image_url,
            "title": reward.title,
            "reward_type": reward.reward_type,
            "campaign_reward_type": getattr(
                reward,
                "campaign_reward_type",
                "",
            ),
            "condition_type": reward.condition_type,
            "condition_for_minutes": reward.condition_for_minutes,
            "ios_based_reward": reward.ios_based_reward,
            "code_remaining_count": reward.code_remaining_count,
        }

        reward_, created = ChzzkReward.objects.get_or_create(
            campaign=campaign_obj,
            reward_no=reward.reward_no,
            defaults=reward_defaults,
        )

        if created:
            msg: str = f"Created reward {reward_.reward_no} for campaign {campaign_no}"
            self.stdout.write(self.style.SUCCESS(msg))
            return

        updated_reward: bool = self._apply_updates_if_changed(reward_, reward_defaults)
        if updated_reward:
            msg: str = f"  Updated reward {reward_.reward_no} for campaign {campaign_no} (changes detected)"
            self.stdout.write(self.style.SUCCESS(msg))

    def _apply_updates_if_changed(
        self,
        instance: ChzzkCampaign | ChzzkReward,
        changes: dict[str, Any],
    ) -> bool:
        """Update a model instance only if values have changed.

        Returns:
            bool: True if an update occurred, False if no changes were needed.
        """
        fields_to_update: list[str] = []

        for field_name, new_value in changes.items():
            if getattr(instance, field_name) != new_value:
                setattr(instance, field_name, new_value)
                fields_to_update.append(field_name)

        if fields_to_update:
            instance.save(update_fields=fields_to_update)
            return True

        return False

    def import_campaign_data(
        self,
        campaign_no: int,
        api_version: str,
        data: dict[str, Any],
        cd: ChzzkCampaignV2,
    ) -> ChzzkCampaign:
        """Import campaign data into the database.

        Args:
            campaign_no (int): The campaign number being imported.
            api_version (str): The API version used to fetch the data ("v1" or "v2").
            data (dict[str, Any]): The raw JSON data returned from the API.
            cd (ChzzkCampaignV2): The parsed campaign data from the API response.

        Returns:
            ChzzkCampaign: The imported or updated campaign database object.
        """
        raw_json_v1_val: dict[str, Any] = data if api_version == "v1" else {}
        raw_json_v2_val: dict[str, Any] = data if api_version == "v2" else {}

        defaults: dict[str, Any] = {
            "title": cd.title,
            "image_url": cd.image_url,
            "description": cd.description,
            "category_type": cd.category_type,
            "category_id": cd.category_id,
            "category_value": cd.category_value,
            "pc_link_url": cd.pc_link_url,
            "mobile_link_url": cd.mobile_link_url,
            "service_id": cd.service_id,
            "state": cd.state,
            "start_date": cd.start_date,
            "end_date": cd.end_date,
            "has_ios_based_reward": cd.has_ios_based_reward,
            "drops_campaign_not_started": cd.drops_campaign_not_started,
            "campaign_reward_type": getattr(cd, "campaign_reward_type", ""),
            "reward_type": getattr(cd, "reward_type", ""),
            "account_link_url": cd.account_link_url,
            "raw_json_v1": raw_json_v1_val,
            "raw_json_v2": raw_json_v2_val,
        }

        campaign_obj, created = ChzzkCampaign.objects.get_or_create(
            campaign_no=cd.campaign_no,
            defaults={
                **defaults,
                "scraped_at": timezone.now(),
                "scrape_status": "success",
            },
        )

        if created:
            msg: str = f"Created campaign {campaign_no}"
            self.stdout.write(self.style.SUCCESS(msg))
            return campaign_obj

        updated: bool = self._apply_updates_if_changed(campaign_obj, defaults)

        if updated:
            campaign_obj.scraped_at = timezone.now()
            campaign_obj.scrape_status = "success"
            campaign_obj.save(update_fields=["scraped_at", "scrape_status"])
            msg: str = f"Updated campaign {campaign_no} (changes detected)"
            self.stdout.write(self.style.SUCCESS(msg))

        return campaign_obj
