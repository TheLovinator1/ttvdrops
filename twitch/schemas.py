from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from twitch.utils import normalize_twitch_box_art_url


class OrganizationSchema(BaseModel):
    """Schema for Twitch Organization objects."""

    twitch_id: str = Field(alias="id")
    name: str
    type_name: Literal["Organization"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class GameSchema(BaseModel):
    """Schema for Twitch Game objects.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    """

    # Present in both ViewerDropsDashboard and Inventory formats
    twitch_id: str = Field(alias="id")

    # Present in both formats
    display_name: str | None = Field(default=None, alias="displayName")

    # Present in both formats, made optional
    box_art_url: str | None = Field(default=None, alias="boxArtURL")

    # Present in Inventory format
    slug: str | None = None

    # Present in Inventory format (alternative to displayName)
    name: str | None = None

    # Present in both formats
    type_name: Literal["Game"] = Field(alias="__typename")

    owner_organization: dict | None = Field(default=None, alias="ownerOrganization")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @field_validator("box_art_url", mode="before")
    @classmethod
    def normalize_box_art_url(cls, v: str | None) -> str | None:
        """Normalize Twitch box art URLs to higher quality variants.

        Twitch's box art URLs often include size suffixes (e.g. -120x160)
        that point to lower quality images. This validator removes those
        suffixes to get the original higher quality image.

        Args:
            v: The raw box_art_url value (str or None).

        Returns:
            The normalized box_art_url string, or None if input was None.
        """
        if v:
            return normalize_twitch_box_art_url(v)
        return v

    @model_validator(mode="before")
    @classmethod
    def normalize_display_name(cls, data: dict | object) -> dict | object:
        """Normalize display_name from 'name' field if needed.

        Inventory format uses 'name' instead of 'displayName'.

        Args:
            data: The raw input data dictionary.

        Returns:
            Normalized data dictionary.
        """
        if isinstance(data, dict) and "displayName" not in data and "name" in data:
            data["displayName"] = data["name"]
        return data


class DropCampaignSelfEdgeSchema(BaseModel):
    """Schema for the 'self' edge on DropCampaign objects."""

    is_account_connected: bool = Field(alias="isAccountConnected")
    type_name: Literal["DropCampaignSelfEdge"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class ChannelInfoSchema(BaseModel):
    """Schema for allowed channel info in DropCampaignACL.

    Represents individual channels that are allowed to participate in a campaign.
    """

    twitch_id: str = Field(alias="id")
    display_name: str | None = Field(default=None, alias="displayName")
    name: str  # Channel login name
    url: str | None = None
    type_name: Literal["Channel"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class DropCampaignACLSchema(BaseModel):
    """Schema for DropCampaign ACL (Access Control List).

    Defines which channels are allowed to participate in a campaign.
    """

    channels: list[ChannelInfoSchema] | None = None
    is_enabled: bool | None = Field(default=None, alias="isEnabled")
    type_name: Literal["DropCampaignACL"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class DropBenefitSchema(BaseModel):
    """Schema for a benefit in a DropBenefitEdge.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    """

    twitch_id: str = Field(alias="id")
    name: str
    image_asset_url: str = Field(alias="imageAssetURL")
    created_at: str | None = Field(default=None, alias="createdAt")
    entitlement_limit: int = Field(default=1, alias="entitlementLimit")
    is_ios_available: bool = Field(default=False, alias="isIosAvailable")

    # Optional in some API responses
    distribution_type: str | None = Field(default=None, alias="distributionType")
    type_name: Literal["Benefit", "DropBenefit"] = Field(alias="__typename")

    # API response fields that should be ignored
    game: dict | None = None
    owner_organization: dict | None = Field(default=None, alias="ownerOrganization")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class DropBenefitEdgeSchema(BaseModel):
    """Schema for a benefit edge in a TimeBasedDrop.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    """

    benefit: DropBenefitSchema
    entitlement_limit: int = Field(alias="entitlementLimit")
    claim_count: int | None = Field(default=None, alias="claimCount")
    type_name: Literal["DropBenefitEdge"] | None = Field(
        default=None,
        alias="__typename",
    )

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class TimeBasedDropSchema(BaseModel):
    """Schema for a TimeBasedDrop in a DropCampaign.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    """

    twitch_id: str = Field(alias="id")
    name: str
    required_minutes_watched: int | None = Field(alias="requiredMinutesWatched")
    required_subs: int = Field(alias="requiredSubs")
    start_at: str | None = Field(alias="startAt")
    end_at: str | None = Field(alias="endAt")
    benefit_edges: list[DropBenefitEdgeSchema] = Field(default=[], alias="benefitEdges")
    type_name: Literal["TimeBasedDrop"] = Field(alias="__typename")

    # Inventory-specific fields
    precondition_drops: None = Field(default=None, alias="preconditionDrops")
    self_edge: dict | None = Field(default=None, alias="self")
    campaign: dict | None = None
    localized_content: dict | None = Field(default=None, alias="localizedContent")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @field_validator("benefit_edges", mode="before")
    @classmethod
    def handle_null_benefit_edges(cls, v: list | None) -> list:
        """Convert null benefitEdges to empty list.

        Args:
            v: The raw benefit_edges value (list or None).

        Returns:
            Empty list if None, otherwise the list itself.
        """
        return v or []


class DropCampaignSchema(BaseModel):
    """Schema for Twitch DropCampaign objects.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    """

    account_link_url: str = Field(alias="accountLinkURL")
    description: str = Field(default="", alias="description")
    details_url: str = Field(alias="detailsURL")
    end_at: str = Field(alias="endAt")
    game: GameSchema
    image_url: str = Field(default="", alias="imageURL")
    name: str
    owner: OrganizationSchema | None = None
    self: DropCampaignSelfEdgeSchema
    start_at: str = Field(alias="startAt")
    status: Literal["ACTIVE", "EXPIRED", "UPCOMING"]
    time_based_drops: list[TimeBasedDropSchema] = Field(
        default=[],
        alias="timeBasedDrops",
    )
    twitch_id: str = Field(alias="id")
    type_name: Literal["DropCampaign"] = Field(alias="__typename")

    # Campaign access control list - defines which channels can participate
    allow: DropCampaignACLSchema | None = None

    # Inventory-specific fields
    event_based_drops: list | None = Field(default=None, alias="eventBasedDrops")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @field_validator("account_link_url", "details_url", "image_url", mode="before")
    @classmethod
    def normalize_nullable_urls(cls, v: str | None) -> str:
        """Normalize nullable URL-ish fields to empty strings.

        Twitch sometimes returns `null` for URL fields. With `strict=True`,
        we normalize these to "" to keep imports resilient.

        Args:
            v: The raw URL field value (str or None).

        Returns:
            The URL string, or empty string if None.
        """
        return v or ""


class InventorySchema(BaseModel):
    """Schema for the inventory field in Inventory operation responses."""

    drop_campaigns_in_progress: list[DropCampaignSchema] = Field(
        default=[],
        alias="dropCampaignsInProgress",
    )
    type_name: Literal["Inventory"] = Field(alias="__typename")

    # gameEventDrops field is present in Inventory but we don't process it yet
    game_event_drops: list | None = Field(default=None, alias="gameEventDrops")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @field_validator("drop_campaigns_in_progress", mode="before")
    @classmethod
    def handle_null_campaigns(cls, v: list | None) -> list:
        """Convert null dropCampaignsInProgress to empty list.

        Args:
            v: The raw drop_campaigns_in_progress value (list or None).

        Returns:
            Empty list if None, otherwise the list itself.
        """
        return v or []


class CurrentUserSchema(BaseModel):
    """Schema for Twitch User objects.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    Also handles legacy format with dropCampaign (singular) field.
    """

    twitch_id: str = Field(alias="id")
    login: str | None = None
    drop_campaigns: list[DropCampaignSchema] | None = Field(
        default=None,
        alias="dropCampaigns",
    )
    drop_campaign: DropCampaignSchema | None = Field(default=None, alias="dropCampaign")
    inventory: InventorySchema | None = None
    type_name: Literal["User"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @model_validator(mode="after")
    def normalize_from_inventory(self) -> CurrentUserSchema:
        """Normalize drop_campaigns from inventory or singular dropCampaign.

        Handles three sources in order of priority:
        1. dropCampaigns (plural) - standard format
        2. inventory.dropCampaignsInProgress - Inventory operation format
        3. dropCampaign (singular) - legacy format

        Returns:
            The CurrentUser instance with normalized drop_campaigns.
        """
        if self.drop_campaigns is None:
            if self.inventory is not None:
                self.drop_campaigns = self.inventory.drop_campaigns_in_progress
            elif self.drop_campaign is not None:
                self.drop_campaigns = [self.drop_campaign]

        return self


class ChannelSchema(BaseModel):
    """Schema for Twitch Channel objects with viewer drop campaigns.

    Handles the channel.viewerDropCampaigns structure from viewer-focused API responses.
    """

    twitch_id: str = Field(alias="id")
    login: str | None = None
    viewer_drop_campaigns: list[DropCampaignSchema] | DropCampaignSchema | None = Field(
        default=None,
        alias="viewerDropCampaigns",
    )
    type_name: Literal["Channel"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @field_validator("viewer_drop_campaigns", mode="before")
    @classmethod
    def normalize_viewer_campaigns(cls, v: list | dict | None) -> list[dict] | None:
        """Normalize viewer_drop_campaigns to a list.

        Handles both list and dict formats from API responses.

        Args:
            v: The raw viewer_drop_campaigns value (list, dict, or None).

        Returns:
            Normalized list of campaign dicts, or None if empty.
        """
        if isinstance(v, dict):
            return [v]
        if isinstance(v, list):
            return v or None
        return None


class DataSchema(BaseModel):
    """Schema for the data field in Twitch API responses.

    Handles both currentUser (standard) and user (legacy) field names,
    as well as channel-based campaign structures and reward campaigns.
    """

    current_user: CurrentUserSchema | None = Field(default=None, alias="currentUser")
    user: CurrentUserSchema | None = Field(default=None, alias="user")
    channel: ChannelSchema | None = Field(default=None, alias="channel")
    reward_campaigns_available_to_user: list[RewardCampaign] | None = Field(
        default=None,
        alias="rewardCampaignsAvailableToUser",
    )

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @field_validator("current_user", mode="before")
    @classmethod
    def empty_dict_to_none(cls, v: dict) -> dict | None:
        """Convert empty dicts to None for current_user field.

        Args:
            v (dict): The value to validate.

        Returns:
            dict | None: None when input is an empty dict; otherwise the value.
        """
        if v == {}:
            return None
        return v

    @model_validator(mode="after")
    def normalize_user_field(self) -> DataSchema:
        """Normalize user field to current_user if needed.

        If current_user is None but user exists, use user as current_user.

        Returns:
            The Data instance with normalized current_user.
        """
        if self.current_user is None and self.user is not None:
            self.current_user = self.user

        return self


class QuestRewardUnlockRequirements(BaseModel):
    """Schema for quest reward unlock requirements."""

    subs_goal: int | None = Field(default=None, alias="subsGoal")
    minute_watched_goal: int | None = Field(default=None, alias="minuteWatchedGoal")
    type_name: Literal["QuestRewardUnlockRequirements"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class RewardCampaignImageSet(BaseModel):
    """Schema for reward campaign image sets."""

    image1x_url: str | None = Field(default=None, alias="image1xURL")
    type_name: Literal["RewardCampaignImageSet"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class Reward(BaseModel):
    """Schema for a reward in a RewardCampaign."""

    twitch_id: str = Field(alias="id")
    name: str
    banner_image: RewardCampaignImageSet | None = Field(
        default=None,
        alias="bannerImage",
    )
    thumbnail_image: RewardCampaignImageSet | None = Field(
        default=None,
        alias="thumbnailImage",
    )
    earnable_until: str | None = Field(default=None, alias="earnableUntil")
    redemption_instructions: str = Field(default="", alias="redemptionInstructions")
    redemption_url: str = Field(default="", alias="redemptionURL")
    type_name: Literal["Reward"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class RewardCampaign(BaseModel):
    """Schema for a RewardCampaign from rewardCampaignsAvailableToUser."""

    twitch_id: str = Field(alias="id")
    name: str
    brand: str
    starts_at: str = Field(alias="startsAt")
    ends_at: str = Field(alias="endsAt")
    status: str
    summary: str = Field(default="")
    instructions: str = Field(default="")
    external_url: str = Field(default="", alias="externalURL")
    reward_value_url_param: str = Field(default="", alias="rewardValueURLParam")
    about_url: str = Field(default="", alias="aboutURL")
    is_sitewide: bool = Field(default=False, alias="isSitewide")
    game: dict | None = None
    unlock_requirements: QuestRewardUnlockRequirements | None = Field(
        default=None,
        alias="unlockRequirements",
    )
    image: RewardCampaignImageSet | None = None
    rewards: list[Reward] = Field(default=[])
    type_name: Literal["RewardCampaign"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class Extensions(BaseModel):
    """Schema for the extensions field in GraphQL responses."""

    operation_name: str | None = Field(default=None, alias="operationName")

    model_config = {
        "extra": "ignore",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class GraphQLError(BaseModel):
    """Schema for GraphQL error objects."""

    message: str
    path: list[str | int] | None = None

    model_config = {
        "extra": "ignore",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class GraphQLResponse(BaseModel):
    """Schema for the complete GraphQL response from Twitch API."""

    data: DataSchema
    extensions: Extensions | None = None
    errors: list[GraphQLError] | None = None

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class BatchedGraphQLResponse(BaseModel):
    """Schema for batched GraphQL responses wrapped in a 'responses' array.

    Handles cases where multiple GraphQL responses are collected and wrapped
    in an outer object with a 'responses' field.
    """

    responses: list[GraphQLResponse]

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


# MARK: ChatBadge Schemas
class ChatBadgeVersionSchema(BaseModel):
    """Schema for a single chat badge version."""

    badge_id: str = Field(alias="id")
    image_url_1x: str = Field(alias="image_url_1x")
    image_url_2x: str = Field(alias="image_url_2x")
    image_url_4x: str = Field(alias="image_url_4x")
    title: str
    description: str
    click_action: str | None = Field(default=None, alias="click_action")
    click_url: str | None = Field(default=None, alias="click_url")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class ChatBadgeSetSchema(BaseModel):
    """Schema for a chat badge set containing multiple versions."""

    set_id: str = Field(alias="set_id")
    versions: list[ChatBadgeVersionSchema]

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class GlobalChatBadgesResponse(BaseModel):
    """Schema for the Twitch global chat badges API response."""

    data: list[ChatBadgeSetSchema]


# MARK: SunkwiBOT /twitch-drops-api Schemas
#
# The SunkwiBOT API serves simplified JSON without GraphQL metadata (no
# __typename, etc.).  These schemas are relaxed to match that format.


class SunkwiBotChannelInfoSchema(BaseModel):
    """Schema for a channel inside a drop's allow.channels from the SunkwiBOT API."""

    twitch_id: str = Field(alias="id")
    display_name: str | None = Field(default=None, alias="displayName")
    name: str

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotDropCampaignACLSchema(BaseModel):
    """Schema for the allow field within a SunkwiBOT reward (drop campaign)."""

    channels: list[SunkwiBotChannelInfoSchema] | None = None
    is_enabled: bool = Field(alias="isEnabled")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotGameRefSchema(BaseModel):
    """Schema for a game object inside a SunkwiBOT reward."""

    twitch_id: str = Field(alias="id")
    slug: str
    display_name: str = Field(alias="displayName")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotOrganizationSchema(BaseModel):
    """Schema for an owner organization inside a SunkwiBOT reward."""

    twitch_id: str = Field(alias="id")
    name: str

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotBenefitSchema(BaseModel):
    """Schema for a benefit inside a benefitEdge from the SunkwiBOT API."""

    twitch_id: str = Field(alias="id")
    created_at: str | None = Field(default=None, alias="createdAt")
    entitlement_limit: int = Field(alias="entitlementLimit")
    game: dict | None = None
    image_asset_url: str = Field(alias="imageAssetURL")
    is_ios_available: bool = Field(alias="isIosAvailable")
    name: str
    owner_organization: dict | None = Field(default=None, alias="ownerOrganization")
    distribution_type: str | None = Field(default=None, alias="distributionType")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotBenefitEdgeSchema(BaseModel):
    """Schema for a benefitEdge within a timeBasedDrop from the SunkwiBOT API."""

    benefit: SunkwiBotBenefitSchema
    entitlement_limit: int = Field(alias="entitlementLimit")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotTimeBasedDropSchema(BaseModel):
    """Schema for a timeBasedDrop within a SunkwiBOT reward (drop campaign)."""

    twitch_id: str = Field(alias="id")
    required_subs: int = Field(alias="requiredSubs")
    benefit_edges: list[SunkwiBotBenefitEdgeSchema] = Field(
        default=[],
        alias="benefitEdges",
    )
    end_at: str = Field(alias="endAt")
    name: str
    precondition_drops: None = Field(default=None, alias="preconditionDrops")
    required_minutes_watched: int | None = Field(
        default=None,
        alias="requiredMinutesWatched",
    )
    start_at: str = Field(alias="startAt")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }

    @field_validator("benefit_edges", mode="before")
    @classmethod
    def handle_null_benefit_edges(cls, v: list | None) -> list:
        """Convert null benefitEdges to empty list.

        Args:
            v: The raw benefit_edges value (list or None).

        Returns:
            Empty list if None, otherwise the list itself.
        """
        return v or []


class SunkwiBotRewardSchema(BaseModel):
    """Schema for a single reward item inside the drops.json rewards array.

    This maps to a DropCampaign in our database.
    """

    twitch_id: str = Field(alias="id")
    self_field: dict | None = Field(default=None, alias="self")
    allow: SunkwiBotDropCampaignACLSchema | None = None
    account_link_url: str = Field(alias="accountLinkURL")
    description: str
    details_url: str = Field(alias="detailsURL")
    end_at: str = Field(alias="endAt")
    event_based_drops: list | None = Field(default=None, alias="eventBasedDrops")
    game: SunkwiBotGameRefSchema
    image_url: str = Field(alias="imageURL")
    name: str
    owner: SunkwiBotOrganizationSchema
    start_at: str = Field(alias="startAt")
    status: str
    time_based_drops: list[SunkwiBotTimeBasedDropSchema] = Field(
        default=[],
        alias="timeBasedDrops",
    )

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotDropGroupSchema(BaseModel):
    """Schema for a single top-level item in the drops.json array.

    Each item groups multiple drop campaigns under a shared game.
    """

    end_at: str = Field(alias="endAt")
    game_box_art_url: str = Field(alias="gameBoxArtURL")
    game_display_name: str = Field(alias="gameDisplayName")
    game_id: str = Field(alias="gameId")
    rewards: list[SunkwiBotRewardSchema]
    start_at: str = Field(alias="startAt")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotRewardImageSetSchema(BaseModel):
    """Schema for an image set inside a rewards.json reward."""

    image1x_url: str = Field(alias="image1xURL")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotIndividualRewardSchema(BaseModel):
    """Schema for a single reward inside a rewards.json reward campaign."""

    twitch_id: str = Field(alias="id")
    name: str
    banner_image: SunkwiBotRewardImageSetSchema | None = Field(
        default=None,
        alias="bannerImage",
    )
    thumbnail_image: SunkwiBotRewardImageSetSchema | None = Field(
        default=None,
        alias="thumbnailImage",
    )
    earnable_until: str | None = Field(default=None, alias="earnableUntil")
    redemption_instructions: str = Field(default="", alias="redemptionInstructions")
    redemption_url: str = Field(default="", alias="redemptionURL")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotRewardUnlockRequirementsSchema(BaseModel):
    """Schema for the unlockRequirements inside a rewards.json campaign."""

    minute_watched_goal: int = Field(alias="minuteWatchedGoal")
    subs_goal: int = Field(alias="subsGoal")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }


class SunkwiBotRewardCampaignSchema(BaseModel):
    """Schema for a single top-level item in the rewards.json array.

    This maps to a RewardCampaign in our database.
    """

    about_url: str = Field(alias="aboutURL")
    brand: str
    ends_at: str = Field(alias="endsAt")
    external_url: str = Field(alias="externalURL")
    game: dict | None = None
    twitch_id: str = Field(alias="id")
    image: SunkwiBotRewardImageSetSchema | None = None
    instructions: str
    is_sitewide: bool = Field(alias="isSitewide")
    name: str
    rewards: list[SunkwiBotIndividualRewardSchema] = Field(default=[])
    reward_value_url_param: str = Field(default="", alias="rewardValueURLParam")
    starts_at: str = Field(alias="startsAt")
    status: str
    summary: str
    unlock_requirements: SunkwiBotRewardUnlockRequirementsSchema | None = Field(
        default=None,
        alias="unlockRequirements",
    )

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "populate_by_name": True,
    }
