from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator


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

    twitch_id: str = Field(alias="id")  # Present in both ViewerDropsDashboard and Inventory formats
    display_name: str | None = Field(default=None, alias="displayName")  # Present in both formats
    box_art_url: str | None = Field(default=None, alias="boxArtURL")  # Present in both formats, made optional
    slug: str | None = None  # Present in Inventory format
    name: str | None = None  # Present in Inventory format (alternative to displayName)
    type_name: Literal["Game"] = Field(alias="__typename")  # Present in both formats

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

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


class DropCampaignSelfEdge(BaseModel):
    """Schema for the 'self' edge on DropCampaign objects."""

    is_account_connected: bool = Field(alias="isAccountConnected")
    type_name: Literal["DropCampaignSelfEdge"] = Field(alias="__typename")

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
    distribution_type: str | None = Field(default=None, alias="distributionType")  # Optional in some API responses
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
    type_name: Literal["DropBenefitEdge"] | None = Field(default=None, alias="__typename")

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
    benefit_edges: list[DropBenefitEdgeSchema] = Field(alias="benefitEdges")
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


class DropCampaign(BaseModel):
    """Schema for Twitch DropCampaign objects.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    """

    twitch_id: str = Field(alias="id")
    name: str
    owner: OrganizationSchema | None = None
    game: GameSchema
    status: Literal["ACTIVE", "EXPIRED", "UPCOMING"]
    start_at: str = Field(alias="startAt")
    end_at: str = Field(alias="endAt")
    details_url: str = Field(alias="detailsURL")
    account_link_url: str = Field(alias="accountLinkURL")
    description: str = Field(default="", alias="description")
    self: DropCampaignSelfEdge
    time_based_drops: list[TimeBasedDropSchema] = Field(default=[], alias="timeBasedDrops")
    type_name: Literal["DropCampaign"] = Field(alias="__typename")
    # Inventory-specific fields
    image_url: str | None = Field(default=None, alias="imageURL")
    allow: dict | None = None
    event_based_drops: list | None = Field(default=None, alias="eventBasedDrops")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class InventorySchema(BaseModel):
    """Schema for the inventory field in Inventory operation responses."""

    drop_campaigns_in_progress: list[DropCampaign] = Field(alias="dropCampaignsInProgress")
    type_name: Literal["Inventory"] = Field(alias="__typename")
    # gameEventDrops field is present in Inventory but we don't process it yet
    game_event_drops: list | None = Field(default=None, alias="gameEventDrops")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class CurrentUser(BaseModel):
    """Schema for Twitch User objects.

    Handles both ViewerDropsDashboard and Inventory operation formats.
    Also handles legacy format with dropCampaign (singular) field.
    """

    twitch_id: str = Field(alias="id")
    login: str | None = None
    drop_campaigns: list[DropCampaign] | None = Field(default=None, alias="dropCampaigns")
    drop_campaign: DropCampaign | None = Field(default=None, alias="dropCampaign")
    inventory: InventorySchema | None = None
    type_name: Literal["User"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }

    @model_validator(mode="after")
    def normalize_from_inventory(self) -> CurrentUser:
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


class Data(BaseModel):
    """Schema for the data field in Twitch API responses.

    Handles both currentUser (standard) and user (legacy) field names.
    """

    current_user: CurrentUser | None = Field(default=None, alias="currentUser")
    user: CurrentUser | None = Field(default=None, alias="user")

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
    def normalize_user_field(self) -> Data:
        """Normalize user field to current_user if needed.

        If current_user is None but user exists, use user as current_user.

        Returns:
            The Data instance with normalized current_user.
        """
        if self.current_user is None and self.user is not None:
            self.current_user = self.user

        return self


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

    data: Data
    extensions: Extensions | None = None
    errors: list[GraphQLError] | None = None

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }
