from datetime import datetime  # noqa: TC003

from pydantic import BaseModel
from pydantic import Field


class KickUserSchema(BaseModel):
    """Schema for the user nested inside a Kick channel."""

    id: int
    username: str
    profile_picture: str = ""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickChannelSchema(BaseModel):
    """Schema for a Kick channel participating in a drop campaign."""

    id: int
    slug: str = ""
    description: str = ""
    banner_picture_url: str = ""
    user: KickUserSchema

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickCategorySchema(BaseModel):
    """Schema for a Kick category (game)."""

    id: int
    name: str
    slug: str = ""
    image_url: str = ""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickOrganizationSchema(BaseModel):
    """Schema for a Kick organization that owns drop campaigns."""

    id: str
    name: str
    logo_url: str = ""
    url: str = ""
    restricted: bool = False
    email_notification: bool = False

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickRewardSchema(BaseModel):
    """Schema for a reward earned from a Kick drop campaign."""

    id: str
    name: str
    image_url: str = ""
    required_units: int
    category_id: int
    organization_id: str

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickRuleSchema(BaseModel):
    """Schema for the rule attached to a drop campaign."""

    id: int
    name: str

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickDropCampaignSchema(BaseModel):
    """Schema for a Kick drop campaign."""

    id: str
    name: str
    status: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    connect_url: str = ""
    url: str = ""
    category: KickCategorySchema
    organization: KickOrganizationSchema
    channels: list[KickChannelSchema] = Field(default_factory=list)
    rewards: list[KickRewardSchema] = Field(default_factory=list)
    rule: KickRuleSchema

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


class KickDropsResponseSchema(BaseModel):
    """Schema for the top-level response from the Kick drops API."""

    data: list[KickDropCampaignSchema]
    message: str = ""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }
