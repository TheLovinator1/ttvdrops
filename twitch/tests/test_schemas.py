"""Tests for Pydantic schemas used in the import process."""

from __future__ import annotations

from twitch.schemas import DropBenefitSchema
from twitch.schemas import DropCampaignSchema
from twitch.schemas import GraphQLResponse
from twitch.schemas import TimeBasedDropSchema


def test_inventory_operation_validation() -> None:
    """Test that the Inventory operation format validates correctly.

    The Inventory operation has a different structure than ViewerDropsDashboard:
    - No 'login' field in currentUser
    - No 'dropCampaigns' field in currentUser
    - Has 'inventory.dropCampaignsInProgress' instead
    - Campaign data has extra fields that are now explicitly defined
    - Game uses 'name' instead of 'displayName'
    - Benefits have 'DropBenefit' as __typename instead of 'Benefit'
    """
    # Minimal valid Inventory operation structure
    payload: dict[str, object] = {
        "data": {
            "currentUser": {
                "id": "17658559",
                "inventory": {
                    "__typename": "Inventory",
                    "dropCampaignsInProgress": [
                        {
                            "id": "campaign-1",
                            "name": "Test Campaign",
                            "game": {
                                "id": "game-1",
                                "name": "Test Game",
                                "boxArtURL": "https://example.com/boxart.jpg",
                                "__typename": "Game",
                            },
                            "status": "ACTIVE",
                            "startAt": "2025-01-01T00:00:00Z",
                            "endAt": "2025-12-31T23:59:59Z",
                            "detailsURL": "https://example.com/details",
                            "accountLinkURL": "https://example.com/link",
                            "self": {
                                "isAccountConnected": True,
                                "__typename": "DropCampaignSelfEdge",
                            },
                            "imageURL": "https://example.com/image.png",
                            "allow": None,
                            "eventBasedDrops": [],
                            "timeBasedDrops": [
                                {
                                    "id": "drop-1",
                                    "name": "Test Drop",
                                    "requiredMinutesWatched": 60,
                                    "requiredSubs": 0,
                                    "startAt": "2025-01-01T00:00:00Z",
                                    "endAt": "2025-12-31T23:59:59Z",
                                    "benefitEdges": [
                                        {
                                            "benefit": {
                                                "id": "benefit-1",
                                                "name": "Test Benefit",
                                                "imageAssetURL": "https://example.com/benefit.png",
                                                "entitlementLimit": 1,
                                                "isIosAvailable": False,
                                                "distributionType": "DIRECT_ENTITLEMENT",
                                                "__typename": "DropBenefit",
                                            },
                                            "entitlementLimit": 1,
                                            "claimCount": 0,
                                            "__typename": "DropBenefitEdge",
                                        },
                                    ],
                                    "preconditionDrops": None,
                                    "self": {},
                                    "campaign": {},
                                    "localizedContent": {},
                                    "__typename": "TimeBasedDrop",
                                },
                            ],
                            "__typename": "DropCampaign",
                        },
                    ],
                    "gameEventDrops": [],
                },
                "__typename": "User",
            },
        },
        "extensions": {
            "operationName": "Inventory",
        },
    }

    # This should not raise ValidationError
    response: GraphQLResponse = GraphQLResponse.model_validate(payload)

    # Verify the structure was parsed correctly
    assert response.data.current_user is not None
    assert response.data.current_user.twitch_id == "17658559"

    # Verify drop_campaigns was normalized from inventory
    assert response.data.current_user.drop_campaigns is not None
    assert len(response.data.current_user.drop_campaigns) == 1

    campaign: DropCampaignSchema = response.data.current_user.drop_campaigns[0]
    assert campaign.name == "Test Campaign"
    assert campaign.game.display_name == "Test Game"
    assert len(campaign.time_based_drops) == 1

    # Verify time-based drops
    first_drop: TimeBasedDropSchema = campaign.time_based_drops[0]
    assert first_drop.name == "Test Drop"
    assert first_drop.required_minutes_watched == 60

    # Verify benefits
    assert len(first_drop.benefit_edges) == 1
    assert first_drop.benefit_edges[0].benefit.name == "Test Benefit"


def test_viewer_drops_dashboard_operation_still_works() -> None:
    """Test that the original ViewerDropsDashboard format still validates.

    This ensures backward compatibility with the existing import data.
    """
    # Minimal valid ViewerDropsDashboard structure
    payload: dict[str, object] = {
        "data": {
            "currentUser": {
                "id": "12345",
                "login": "testuser",
                "dropCampaigns": [
                    {
                        "id": "campaign-1",
                        "name": "Test Campaign",
                        "owner": {
                            "id": "org-1",
                            "name": "Test Org",
                            "__typename": "Organization",
                        },
                        "game": {
                            "id": "game-1",
                            "displayName": "Test Game",
                            "boxArtURL": "https://example.com/boxart.jpg",
                            "__typename": "Game",
                        },
                        "status": "ACTIVE",
                        "startAt": "2025-01-01T00:00:00Z",
                        "endAt": "2025-12-31T23:59:59Z",
                        "detailsURL": "https://example.com/details",
                        "accountLinkURL": "https://example.com/link",
                        "self": {
                            "isAccountConnected": True,
                            "__typename": "DropCampaignSelfEdge",
                        },
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                ],
                "__typename": "User",
            },
        },
        "extensions": {
            "operationName": "ViewerDropsDashboard",
        },
    }

    # This should not raise ValidationError
    response: GraphQLResponse = GraphQLResponse.model_validate(payload)

    assert response.data.current_user is not None
    assert response.data.current_user.login == "testuser"

    if response.data.current_user.drop_campaigns is not None:
        assert len(response.data.current_user.drop_campaigns) == 1


def test_graphql_response_with_errors() -> None:
    """Test that GraphQL responses with errors field validate correctly.

    Some API responses include partial data with errors (e.g., service timeouts).
    The schema should accept these responses so they can be processed.
    """
    # Real-world example: Inventory operation with service timeout errors
    payload: dict[str, object] = {
        "errors": [
            {
                "message": "service timeout",
                "path": ["currentUser", "inventory", "dropCampaignsInProgress", 7, "allow", "channels"],
            },
            {
                "message": "service timeout",
                "path": ["currentUser", "inventory", "dropCampaignsInProgress", 10, "allow", "channels"],
            },
        ],
        "data": {
            "currentUser": {
                "id": "17658559",
                "inventory": {
                    "__typename": "Inventory",
                    "dropCampaignsInProgress": [
                        {
                            "id": "campaign-1",
                            "name": "Test Campaign",
                            "game": {
                                "id": "game-1",
                                "name": "Test Game",
                                "boxArtURL": "https://example.com/boxart.jpg",
                                "__typename": "Game",
                            },
                            "status": "ACTIVE",
                            "startAt": "2025-01-01T00:00:00Z",
                            "endAt": "2025-12-31T23:59:59Z",
                            "detailsURL": "https://example.com/details",
                            "accountLinkURL": "https://example.com/link",
                            "self": {
                                "isAccountConnected": True,
                                "__typename": "DropCampaignSelfEdge",
                            },
                            "imageURL": "https://example.com/image.png",
                            "allow": None,
                            "eventBasedDrops": [],
                            "timeBasedDrops": [],
                            "__typename": "DropCampaign",
                        },
                    ],
                    "gameEventDrops": [],
                },
                "__typename": "User",
            },
        },
        "extensions": {
            "operationName": "Inventory",
        },
    }

    # This should not raise ValidationError even with errors field present
    response: GraphQLResponse = GraphQLResponse.model_validate(payload)

    # Verify the errors were captured
    assert response.errors is not None
    assert len(response.errors) == 2
    assert response.errors[0].message == "service timeout"
    assert response.errors[0].path == ["currentUser", "inventory", "dropCampaignsInProgress", 7, "allow", "channels"]

    # Verify the data is still accessible and valid
    assert response.data.current_user is not None
    assert response.data.current_user.twitch_id == "17658559"
    assert response.data.current_user.drop_campaigns is not None
    assert len(response.data.current_user.drop_campaigns) == 1


def test_drop_campaign_details_missing_distribution_type() -> None:
    """Test that DropCampaignDetails operation validates when distributionType is missing.

    Some API responses (e.g., from DropCampaignDetails operation) don't include
    the distributionType field in the benefit object. The schema should handle this
    gracefully by making the field optional.

    This is based on a real-world validation error from file 7720168310842708008.json.
    """
    payload: dict[str, object] = {
        "data": {
            "user": {
                "id": "58162970",
                "dropCampaign": {
                    "id": "1cf86f25-540f-11ef-90d0-0a58a9feac02",
                    "self": {
                        "isAccountConnected": False,
                        "__typename": "DropCampaignSelfEdge",
                    },
                    "allow": {
                        "channels": [
                            {
                                "id": "182565961",
                                "displayName": "WorldofWarships",
                                "name": "worldofwarships",
                                "__typename": "Channel",
                            },
                        ],
                        "isEnabled": True,
                        "__typename": "DropCampaignACL",
                    },
                    "accountLinkURL": "https://wargaming.net/personal/",
                    "description": "New Drops!",
                    "detailsURL": "https://worldofwarships.eu/news/general-news/stream-rewards-137/",
                    "endAt": "2024-08-21T21:59:59.996Z",
                    "eventBasedDrops": [],
                    "game": {
                        "id": "32502",
                        "slug": "world-of-warships",
                        "displayName": "World of Warships",
                        "__typename": "Game",
                    },
                    "imageURL": "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/51ec1931-6792-4b9f-850a-e6b9c454eefc.png",
                    "name": "Official Channel Weekly Drop",
                    "owner": {
                        "id": "6948a129-2c6d-4d88-9444-6b96918a19f8",
                        "name": "Wargaming",
                        "__typename": "Organization",
                    },
                    "startAt": "2024-08-14T22:00:00Z",
                    "status": "ACTIVE",
                    "timeBasedDrops": [
                        {
                            "id": "79a0a4db-5a4d-11ef-91b7-0a58a9feac02",
                            "requiredSubs": 0,
                            "benefitEdges": [
                                {
                                    "benefit": {
                                        "id": "6948a129-2c6d-4d88-9444-6b96918a19f8_CUSTOM_ID_WOWS_TwitchDrops_1307_250ct",  # noqa: E501
                                        "createdAt": "2024-08-06T16:03:15.89Z",
                                        "entitlementLimit": 1,
                                        "game": {
                                            "id": "32502",
                                            "name": "World of Warships",
                                            "__typename": "Game",
                                        },
                                        "imageAssetURL": "https://static-cdn.jtvnw.net/twitch-quests-assets/REWARD/a049fb1a-ab63-40e5-9a43-bd6b94e52f36.png",
                                        "isIosAvailable": False,
                                        "name": "13.7 Update: 250 CT",
                                        "ownerOrganization": {
                                            "id": "6948a129-2c6d-4d88-9444-6b96918a19f8",
                                            "name": "Wargaming",
                                            "__typename": "Organization",
                                        },
                                        "__typename": "DropBenefit",
                                    },
                                    "entitlementLimit": 1,
                                    "__typename": "DropBenefitEdge",
                                },
                            ],
                            "endAt": "2024-08-21T21:59:59.996Z",
                            "name": "Week 2: 250 Community Tokens",
                            "preconditionDrops": None,
                            "requiredMinutesWatched": 60,
                            "startAt": "2024-08-14T22:00:00Z",
                            "__typename": "TimeBasedDrop",
                        },
                    ],
                    "__typename": "DropCampaign",
                },
                "__typename": "User",
            },
        },
        "extensions": {
            "durationMilliseconds": 75,
            "operationName": "DropCampaignDetails",
            "requestID": "01J5RDFXF7VMMDWVBZKNZ52ED6",
        },
    }

    # This should not raise ValidationError despite missing distributionType
    response: GraphQLResponse = GraphQLResponse.model_validate(payload)

    # Verify the structure was parsed correctly
    assert response.data.current_user is not None
    assert response.data.current_user.twitch_id == "58162970"

    # Verify drop_campaigns was normalized from dropCampaign (singular)
    assert response.data.current_user.drop_campaigns is not None
    assert len(response.data.current_user.drop_campaigns) == 1

    campaign: DropCampaignSchema = response.data.current_user.drop_campaigns[0]
    assert campaign.name == "Official Channel Weekly Drop"
    assert campaign.game.display_name == "World of Warships"
    assert len(campaign.time_based_drops) == 1

    # Verify time-based drops
    first_drop: TimeBasedDropSchema = campaign.time_based_drops[0]
    assert first_drop.name == "Week 2: 250 Community Tokens"
    assert first_drop.required_minutes_watched == 60

    # Verify benefits - distributionType should be None since it was missing
    assert len(first_drop.benefit_edges) == 1
    benefit: DropBenefitSchema = first_drop.benefit_edges[0].benefit
    assert benefit.name == "13.7 Update: 250 CT"
    assert benefit.distribution_type is None  # This field was missing in the API response


def test_reward_campaigns_available_to_user() -> None:
    """Test that rewardCampaignsAvailableToUser field validates correctly.

    The ViewerDropsDashboard operation can include reward campaigns (Quest rewards)
    alongside drop campaigns. This test verifies that the schema properly handles
    this additional data.
    """
    payload: dict[str, object] = {
        "data": {
            "currentUser": {
                "id": "58162970",
                "login": "lovibot",
                "dropCampaigns": [],
                "__typename": "User",
            },
            "rewardCampaignsAvailableToUser": [
                {
                    "id": "dc4ff0b4-4de0-11ef-9ec3-621fb0811846",
                    "name": "Buy 1 new sub, get 3 months of Apple TV+",
                    "brand": "Apple TV+",
                    "startsAt": "2024-07-30T19:00:00Z",
                    "endsAt": "2024-08-19T19:00:00Z",
                    "status": "UNKNOWN",
                    "summary": "Get 3 months of Apple TV+",
                    "instructions": "",
                    "externalURL": "https://tv.apple.com/includes/commerce/redeem/code-entry",
                    "rewardValueURLParam": "",
                    "aboutURL": "https://blog.twitch.tv/2024/07/26/sub-and-get-apple-tv/",
                    "isSitewide": True,
                    "game": None,
                    "unlockRequirements": {
                        "subsGoal": 1,
                        "minuteWatchedGoal": 0,
                        "__typename": "QuestRewardUnlockRequirements",
                    },
                    "image": {
                        "image1xURL": "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/quests_appletv_q3_2024/apple_150x200.png",
                        "__typename": "RewardCampaignImageSet",
                    },
                    "rewards": [
                        {
                            "id": "dc2e9810-4de0-11ef-9ec3-621fb0811846",
                            "name": "3 months of Apple TV+",
                            "bannerImage": {
                                "image1xURL": "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/quests_appletv_q3_2024/apple_200x200.png",
                                "__typename": "RewardCampaignImageSet",
                            },
                            "thumbnailImage": {
                                "image1xURL": "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/quests_appletv_q3_2024/apple_200x200.png",
                                "__typename": "RewardCampaignImageSet",
                            },
                            "earnableUntil": "2024-08-19T19:00:00Z",
                            "redemptionInstructions": "",
                            "redemptionURL": "https://tv.apple.com/includes/commerce/redeem/code-entry",
                            "__typename": "Reward",
                        },
                    ],
                    "__typename": "RewardCampaign",
                },
            ],
        },
        "extensions": {
            "operationName": "ViewerDropsDashboard",
        },
    }

    # This should not raise ValidationError
    response: GraphQLResponse = GraphQLResponse.model_validate(payload)

    # Verify the reward campaigns were parsed correctly
    assert response.data.reward_campaigns_available_to_user is not None
    assert len(response.data.reward_campaigns_available_to_user) == 1

    reward_campaign = response.data.reward_campaigns_available_to_user[0]
    assert reward_campaign.twitch_id == "dc4ff0b4-4de0-11ef-9ec3-621fb0811846"
    assert reward_campaign.name == "Buy 1 new sub, get 3 months of Apple TV+"
    assert reward_campaign.brand == "Apple TV+"
    assert reward_campaign.is_sitewide is True
    assert reward_campaign.game is None

    # Verify unlock requirements
    assert reward_campaign.unlock_requirements is not None
    assert reward_campaign.unlock_requirements.subs_goal == 1
    assert reward_campaign.unlock_requirements.minute_watched_goal == 0

    # Verify image
    assert reward_campaign.image is not None
    assert (
        reward_campaign.image.image1x_url
        == "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/quests_appletv_q3_2024/apple_150x200.png"
    )

    # Verify rewards
    assert len(reward_campaign.rewards) == 1
    reward = reward_campaign.rewards[0]
    assert reward.twitch_id == "dc2e9810-4de0-11ef-9ec3-621fb0811846"
    assert reward.name == "3 months of Apple TV+"
    assert reward.banner_image is not None
    assert reward.thumbnail_image is not None
