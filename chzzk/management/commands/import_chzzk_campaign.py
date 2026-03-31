from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    import argparse

    from chzzk.schemas import ChzzkCampaignV1
    from chzzk.schemas import ChzzkCampaignV2


from typing import TYPE_CHECKING

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from chzzk.models import ChzzkCampaign
from chzzk.models import ChzzkReward
from chzzk.schemas import ChzzkApiResponseV1
from chzzk.schemas import ChzzkApiResponseV2

if TYPE_CHECKING:
    import argparse

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
        parser.add_argument("campaign_no", type=int, help="Campaign number to fetch")

    def handle(self, **options) -> None:
        """Main handler for the management command. Fetches campaign data from both API versions, validates, and stores them."""
        campaign_no: int = int(options["campaign_no"])
        for api_version, url_template in CHZZK_API_URLS:
            url: str = url_template.format(campaign_no=campaign_no)
            resp: requests.Response = requests.get(
                url,
                timeout=2,
                headers={
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            campaign_data: ChzzkCampaignV1 | ChzzkCampaignV2
            if api_version == "v1":
                campaign_data = ChzzkApiResponseV1.model_validate(data).content
            elif api_version == "v2":
                campaign_data = ChzzkApiResponseV2.model_validate(data).content
            else:
                msg: str = f"Unknown API version: {api_version}"
                self.stdout.write(self.style.ERROR(msg))
                continue

            # Save campaign
            campaign_obj, created = ChzzkCampaign.objects.update_or_create(
                campaign_no=campaign_data.campaign_no,
                source_api=api_version,
                defaults={
                    "title": campaign_data.title,
                    "image_url": campaign_data.image_url,
                    "description": campaign_data.description,
                    "category_type": campaign_data.category_type,
                    "category_id": campaign_data.category_id,
                    "category_value": campaign_data.category_value,
                    "pc_link_url": campaign_data.pc_link_url,
                    "mobile_link_url": campaign_data.mobile_link_url,
                    "service_id": campaign_data.service_id,
                    "state": campaign_data.state,
                    "start_date": campaign_data.start_date,
                    "end_date": campaign_data.end_date,
                    "has_ios_based_reward": campaign_data.has_ios_based_reward,
                    "drops_campaign_not_started": campaign_data.drops_campaign_not_started,
                    "campaign_reward_type": getattr(
                        campaign_data,
                        "campaign_reward_type",
                        "",
                    ),
                    "reward_type": getattr(campaign_data, "reward_type", ""),
                    "account_link_url": campaign_data.account_link_url,
                    "scraped_at": timezone.now(),
                    "scrape_status": "success",
                    "raw_json": data,
                },
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created campaign {campaign_no} from {api_version}",
                    ),
                )
            for reward in campaign_data.reward_list:
                reward_, created = ChzzkReward.objects.update_or_create(
                    campaign=campaign_obj,
                    reward_no=reward.reward_no,
                    defaults={
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
                    },
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Created reward {reward_.reward_no} for campaign {campaign_no}",
                        ),
                    )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported campaign {campaign_no} from {api_version}",
                ),
            )
