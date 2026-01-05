from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator


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
    """Schema for Twitch Game objects."""

    twitch_id: str = Field(alias="id")
    display_name: str = Field(alias="displayName")
    box_art_url: str = Field(alias="boxArtURL")
    type_name: Literal["Game"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


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
    """Schema for a benefit in a DropBenefitEdge."""

    twitch_id: str = Field(alias="id")
    name: str
    image_asset_url: str = Field(alias="imageAssetURL")
    created_at: str | None = Field(alias="createdAt")
    entitlement_limit: int = Field(alias="entitlementLimit")
    is_ios_available: bool = Field(alias="isIosAvailable")
    distribution_type: str = Field(alias="distributionType")
    type_name: Literal["Benefit"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class DropBenefitEdgeSchema(BaseModel):
    """Schema for a benefit edge in a TimeBasedDrop."""

    benefit: DropBenefitSchema
    entitlement_limit: int = Field(alias="entitlementLimit")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class TimeBasedDropSchema(BaseModel):
    """Schema for a TimeBasedDrop in a DropCampaign."""

    twitch_id: str = Field(alias="id")
    name: str
    required_minutes_watched: int | None = Field(alias="requiredMinutesWatched")
    required_subs: int = Field(alias="requiredSubs")
    start_at: str | None = Field(alias="startAt")
    end_at: str | None = Field(alias="endAt")
    benefit_edges: list[DropBenefitEdgeSchema] = Field(alias="benefitEdges")
    type_name: Literal["TimeBasedDrop"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class DropCampaign(BaseModel):
    """Schema for Twitch DropCampaign objects."""

    twitch_id: str = Field(alias="id")
    name: str
    owner: OrganizationSchema
    game: GameSchema
    status: Literal["ACTIVE", "EXPIRED", "UPCOMING"]
    start_at: str = Field(alias="startAt")
    end_at: str = Field(alias="endAt")
    details_url: str = Field(alias="detailsURL")
    account_link_url: str = Field(alias="accountLinkURL")
    self: DropCampaignSelfEdge
    time_based_drops: list[TimeBasedDropSchema] = Field(default=[], alias="timeBasedDrops")
    type_name: Literal["DropCampaign"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class CurrentUser(BaseModel):
    """Schema for Twitch User objects."""

    twitch_id: str = Field(alias="id")
    login: str
    drop_campaigns: list[DropCampaign] = Field(alias="dropCampaigns")
    type_name: Literal["User"] = Field(alias="__typename")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class Data(BaseModel):
    """Schema for the data field in Twitch API responses."""

    current_user: CurrentUser | None = Field(alias="currentUser")

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


class Extensions(BaseModel):
    """Schema for the extensions field in GraphQL responses."""

    operation_name: str | None = Field(default=None, alias="operationName")

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

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }
