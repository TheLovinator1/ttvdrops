from typing import Any

from pydantic import BaseModel
from pydantic import Field


class ChzzkRewardV2(BaseModel):
    """Pydantic schema for api v2 reward object."""

    title: str
    reward_no: int = Field(..., alias="rewardNo")
    image_url: str = Field(..., alias="imageUrl")
    reward_type: str = Field(..., alias="rewardType")
    condition_type: str = Field(..., alias="conditionType")
    condition_for_minutes: int = Field(..., alias="conditionForMinutes")
    ios_based_reward: bool = Field(..., alias="iosBasedReward")
    code_remaining_count: int = Field(..., alias="codeRemainingCount")

    model_config = {"extra": "forbid"}


class ChzzkCampaignV2(BaseModel):
    """Pydantic schema for api v2 campaign object."""

    title: str
    state: str
    description: str
    campaign_no: int = Field(..., alias="campaignNo")
    image_url: str = Field(..., alias="imageUrl")
    category_type: str = Field(..., alias="categoryType")
    category_id: str = Field(..., alias="categoryId")
    category_value: str = Field(..., alias="categoryValue")
    pc_link_url: str = Field(..., alias="pcLinkUrl")
    mobile_link_url: str = Field(..., alias="mobileLinkUrl")
    service_id: str = Field(..., alias="serviceId")
    start_date: str = Field(..., alias="startDate")
    end_date: str = Field(..., alias="endDate")
    reward_list: list[ChzzkRewardV2] = Field(..., alias="rewardList")
    has_ios_based_reward: bool = Field(..., alias="hasIosBasedReward")
    drops_campaign_not_started: bool = Field(..., alias="dropsCampaignNotStarted")
    reward_type: str | None = Field(None, alias="rewardType")
    account_link_url: str = Field(..., alias="accountLinkUrl")

    model_config = {"extra": "forbid"}


class ChzzkApiResponseV2(BaseModel):
    """Pydantic schema for api v2 API response."""

    code: int
    message: Any | None
    content: ChzzkCampaignV2

    model_config = {"extra": "forbid"}
