from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class Organization(BaseModel):
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


class Game(BaseModel):
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


class DropCampaign(BaseModel):
    """Schema for Twitch DropCampaign objects."""

    twitch_id: str = Field(alias="id")
    name: str
    owner: Organization
    game: Game
    status: Literal["ACTIVE", "EXPIRED"]
    start_at: str = Field(alias="startAt")
    end_at: str = Field(alias="endAt")
    details_url: str = Field(alias="detailsURL")
    account_link_url: str = Field(alias="accountLinkURL")
    self: DropCampaignSelfEdge
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

    current_user: CurrentUser = Field(alias="currentUser")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class Extensions(BaseModel):
    """Schema for the extensions field in Twitch API responses."""

    duration_milliseconds: int = Field(alias="durationMilliseconds")
    operation_name: Literal["ViewerDropsDashboard"] = Field(alias="operationName")
    request_id: str = Field(alias="requestID")

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
        "populate_by_name": True,
    }


class ViewerDropsDashboardPayload(BaseModel):
    """Schema for the ViewerDropsDashboard response."""

    data: Data
    extensions: Extensions

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
        "strict": True,
    }
