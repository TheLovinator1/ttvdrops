"""Tests for Pydantic schemas used in the import process."""

from __future__ import annotations

from twitch.schemas import GraphQLResponse


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
    payload = {
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
    response = GraphQLResponse.model_validate(payload)

    # Verify the structure was parsed correctly
    assert response.data.current_user is not None
    assert response.data.current_user.twitch_id == "17658559"

    # Verify drop_campaigns was normalized from inventory
    assert response.data.current_user.drop_campaigns is not None
    assert len(response.data.current_user.drop_campaigns) == 1

    campaign = response.data.current_user.drop_campaigns[0]
    assert campaign.name == "Test Campaign"
    assert campaign.game.display_name == "Test Game"
    assert len(campaign.time_based_drops) == 1

    # Verify time-based drops
    first_drop = campaign.time_based_drops[0]
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
    payload = {
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
    response = GraphQLResponse.model_validate(payload)

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
    payload = {
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
    response = GraphQLResponse.model_validate(payload)

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
