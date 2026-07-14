from __future__ import annotations

from datetime import UTC
from datetime import datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from kick.models import KickCategory
from kick.models import KickChannel
from kick.models import KickDropCampaign
from kick.models import KickOrganization
from kick.models import KickReward
from kick.models import KickUser


class KickCampaignApiTest(TestCase):
    """Tests for the public Kick campaign JSON API."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Create a fully populated campaign for API contract tests."""
        cls.organization = KickOrganization.objects.create(
            kick_id="org-api",
            name="API Organization",
            logo_url="https://example.com/org.png",
            url="https://example.com/org",
        )
        cls.category = KickCategory.objects.create(
            kick_id=123,
            name="API Game",
            slug="api-game",
            image_url="https://example.com/game.png",
        )
        cls.campaign = KickDropCampaign.objects.create(
            kick_id="campaign-api",
            name="API Campaign",
            status="expired",
            starts_at=datetime(2026, 7, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 1, tzinfo=UTC),
            connect_url="https://example.com/connect",
            url="https://example.com/campaign",
            organization=cls.organization,
            category=cls.category,
            rule_id=1,
            rule_name="Watch to earn",
            is_fully_imported=True,
        )
        KickDropCampaign.objects.create(
            kick_id="campaign-not-imported",
            name="Campaign Not Imported",
            starts_at=datetime(2026, 7, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 1, tzinfo=UTC),
            organization=cls.organization,
            category=cls.category,
        )
        user = KickUser.objects.create(
            kick_id=456,
            username="api-streamer",
            profile_picture="https://example.com/avatar.png",
        )
        channel = KickChannel.objects.create(
            kick_id=789,
            slug="api-streamer",
            user=user,
        )
        cls.campaign.channels.add(channel)
        KickReward.objects.create(
            kick_id="reward-api",
            name="API Reward",
            image_url="drops/reward-image/reward.png",
            required_units=30,
            campaign=cls.campaign,
            category=cls.category,
            organization=cls.organization,
        )

    def test_list_returns_paginated_campaigns_with_nested_data(self) -> None:
        """A campaign includes the fields needed by downstream importers."""
        with mock.patch(
            "kick.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            response = self.client.get(reverse("kick:campaign_api_list"))

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert response.json() == {
            "total": 1,
            "page": 1,
            "page_size": 100,
            "items": [
                {
                    "kick_id": "campaign-api",
                    "name": "API Campaign",
                    "status": "active",
                    "image_url": "https://ext.cdn.kick.com/drops/reward-image/reward.png",
                    "connect_url": "https://example.com/connect",
                    "url": "https://example.com/campaign",
                    "starts_at": "2026-07-01T00:00:00Z",
                    "ends_at": "2026-08-01T00:00:00Z",
                    "category": {
                        "kick_id": 123,
                        "name": "API Game",
                        "slug": "api-game",
                        "image_url": "https://example.com/game.png",
                    },
                    "organization": {
                        "kick_id": "org-api",
                        "name": "API Organization",
                        "logo_url": "https://example.com/org.png",
                        "url": "https://example.com/org",
                    },
                    "channels": [
                        {
                            "kick_id": 789,
                            "slug": "api-streamer",
                            "url": "https://kick.com/api-streamer",
                            "user": {
                                "kick_id": 456,
                                "username": "api-streamer",
                                "profile_picture": "https://example.com/avatar.png",
                            },
                        },
                    ],
                    "rewards": [
                        {
                            "kick_id": "reward-api",
                            "name": "API Reward",
                            "image_url": "https://ext.cdn.kick.com/drops/reward-image/reward.png",
                            "required_minutes_watched": 30,
                        },
                    ],
                    "is_fully_imported": True,
                    "added_at": self.campaign.added_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "updated_at": self.campaign.updated_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
            ],
        }

    def test_filters_campaigns_by_game(self) -> None:
        """A Kick category ID can select a downstream import subset."""
        other_category = KickCategory.objects.create(
            kick_id=124,
            name="Other Game",
        )
        KickDropCampaign.objects.create(
            kick_id="other-campaign",
            name="Other Campaign",
            starts_at=datetime(2026, 8, 2, tzinfo=UTC),
            ends_at=datetime(2026, 9, 1, tzinfo=UTC),
            organization=self.organization,
            category=other_category,
            is_fully_imported=True,
        )

        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"game": self.category.kick_id},
        )

        assert response.status_code == 200
        assert [item["kick_id"] for item in response.json()["items"]] == [
            self.campaign.kick_id,
        ]

    def test_filters_campaigns_by_computed_status(self) -> None:
        """All supported status filters are computed from campaign dates."""
        KickDropCampaign.objects.create(
            kick_id="upcoming-campaign",
            name="Upcoming Campaign",
            starts_at=datetime(2026, 8, 2, tzinfo=UTC),
            ends_at=datetime(2026, 9, 1, tzinfo=UTC),
            organization=self.organization,
            category=self.category,
            is_fully_imported=True,
        )
        KickDropCampaign.objects.create(
            kick_id="expired-campaign",
            name="Expired Campaign",
            starts_at=datetime(2026, 6, 1, tzinfo=UTC),
            ends_at=datetime(2026, 6, 2, tzinfo=UTC),
            organization=self.organization,
            category=self.category,
            is_fully_imported=True,
        )
        expected_ids = {
            "active": self.campaign.kick_id,
            "upcoming": "upcoming-campaign",
            "expired": "expired-campaign",
        }

        with mock.patch(
            "kick.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            for status, expected_id in expected_ids.items():
                with self.subTest(status=status):
                    response = self.client.get(
                        reverse("kick:campaign_api_list"),
                        {"status": status},
                    )
                    assert response.status_code == 200
                    assert [item["kick_id"] for item in response.json()["items"]] == [
                        expected_id
                    ]

    def test_status_filters_exclude_campaigns_with_partial_dates(self) -> None:
        """Filtered items never serialize with a contradictory unknown status."""
        KickDropCampaign.objects.create(
            kick_id="partial-upcoming-campaign",
            name="Partial Upcoming Campaign",
            starts_at=datetime(2026, 8, 2, tzinfo=UTC),
            ends_at=None,
            organization=self.organization,
            category=self.category,
            is_fully_imported=True,
        )
        KickDropCampaign.objects.create(
            kick_id="partial-expired-campaign",
            name="Partial Expired Campaign",
            starts_at=None,
            ends_at=datetime(2026, 6, 2, tzinfo=UTC),
            organization=self.organization,
            category=self.category,
            is_fully_imported=True,
        )

        with mock.patch(
            "kick.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            for status in ("upcoming", "expired"):
                with self.subTest(status=status):
                    response = self.client.get(
                        reverse("kick:campaign_api_list"),
                        {"status": status},
                    )
                    assert all(
                        item["status"] == status for item in response.json()["items"]
                    )

    def test_paginates_campaigns(self) -> None:
        """Page and page_size select a stable result slice."""
        for index in range(2):
            KickDropCampaign.objects.create(
                kick_id=f"pagination-campaign-{index}",
                name=f"Pagination Campaign {index}",
                starts_at=datetime(2026, 6, index + 1, tzinfo=UTC),
                ends_at=datetime(2026, 6, index + 2, tzinfo=UTC),
                organization=self.organization,
                category=self.category,
                is_fully_imported=True,
            )

        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"page": 2, "page_size": 1},
        )

        data = response.json()
        assert response.status_code == 200
        assert data["total"] == 3
        assert data["page"] == 2
        assert data["page_size"] == 1
        assert [item["kick_id"] for item in data["items"]] == [
            "pagination-campaign-1",
        ]

    def test_clamps_page_size(self) -> None:
        """A caller cannot request an unbounded response page."""
        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"page_size": 1000},
        )

        assert response.status_code == 200
        assert response.json()["page_size"] == 500

    def test_clamps_pagination_minimums(self) -> None:
        """Zero and negative pagination values use the first one-item page."""
        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"page": 0, "page_size": -1},
        )

        assert response.status_code == 200
        assert response.json()["page"] == 1
        assert response.json()["page_size"] == 1

    def test_rejects_non_integer_pagination_values(self) -> None:
        """Malformed pagination values return field-specific validation errors."""
        for field in ("page", "page_size"):
            with self.subTest(field=field):
                response = self.client.get(
                    reverse("kick:campaign_api_list"),
                    {field: "not-an-integer"},
                )
                assert response.status_code == 422
                assert response.json()["detail"][0]["loc"] == ["query", field]

    def test_page_beyond_result_set_returns_empty_items(self) -> None:
        """An arbitrarily large page does not become a pathological DB offset."""
        page = 10**100

        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"page": page},
        )

        assert response.status_code == 200
        assert response.json()["page"] == page
        assert response.json()["items"] == []

    def test_rejects_invalid_status(self) -> None:
        """Unknown status values return a client error instead of an empty result."""
        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"status": "running"},
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", "status"]

    def test_rejects_empty_status(self) -> None:
        """An explicitly empty status is invalid rather than an omitted filter."""
        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"status": ""},
        )

        assert response.status_code == 422

    def test_rejects_non_get_methods(self) -> None:
        """The campaign feed is a read-only endpoint."""
        response = self.client.post(reverse("kick:campaign_api_list"))

        assert response.status_code == 405

    def test_rejects_invalid_game_id(self) -> None:
        """A non-numeric Kick category ID returns a validation response."""
        response = self.client.get(
            reverse("kick:campaign_api_list"),
            {"game": "not-an-id"},
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", "game"]

    def test_returns_every_eligible_channel(self) -> None:
        """The JSON API does not apply the five-channel RSS display limit."""
        for index in range(6):
            channel = KickChannel.objects.create(
                kick_id=1000 + index,
                slug=f"extra-streamer-{index}",
            )
            self.campaign.channels.add(channel)

        response = self.client.get(reverse("kick:campaign_api_list"))

        channels = response.json()["items"][0]["channels"]
        assert len(channels) == 7

    def test_returns_every_raw_reward_in_stable_order(self) -> None:
        """Rewards are neither merged nor truncated for downstream importers."""
        KickReward.objects.create(
            kick_id="reward-api-con",
            name="API Reward (Con)",
            required_units=30,
            campaign=self.campaign,
        )
        KickReward.objects.create(
            kick_id="reward-api-beta",
            name="Beta Reward",
            required_units=60,
            campaign=self.campaign,
        )

        response = self.client.get(reverse("kick:campaign_api_list"))

        rewards = response.json()["items"][0]["rewards"]
        assert [reward["name"] for reward in rewards] == [
            "API Reward",
            "API Reward (Con)",
            "Beta Reward",
        ]

    def test_empty_channels_remain_an_empty_list(self) -> None:
        """Missing channel data is not presented as proof of global eligibility."""
        campaign = KickDropCampaign.objects.create(
            kick_id="campaign-without-channels",
            name="Campaign Without Channels",
            starts_at=datetime(2026, 5, 1, tzinfo=UTC),
            ends_at=datetime(2026, 5, 2, tzinfo=UTC),
            organization=self.organization,
            category=self.category,
            is_fully_imported=True,
        )

        response = self.client.get(reverse("kick:campaign_api_list"))

        item = next(
            item
            for item in response.json()["items"]
            if item["kick_id"] == campaign.kick_id
        )
        assert item["channels"] == []

    def test_query_count_does_not_grow_with_campaign_count(self) -> None:
        """Nested serialization avoids per-campaign database queries."""

        def select_count() -> int:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse("kick:campaign_api_list"))
                assert response.status_code == 200
            return sum(
                query["sql"].lstrip().upper().startswith("SELECT")
                for query in queries.captured_queries
            )

        baseline = select_count()

        for index in range(8):
            campaign = KickDropCampaign.objects.create(
                kick_id=f"query-campaign-{index}",
                name=f"Query Campaign {index}",
                starts_at=datetime(2026, 4, 1, tzinfo=UTC),
                ends_at=datetime(2026, 4, 2, tzinfo=UTC),
                organization=self.organization,
                category=self.category,
                is_fully_imported=True,
            )
            KickReward.objects.create(
                kick_id=f"query-reward-{index}",
                name=f"Query Reward {index}",
                required_units=15,
                campaign=campaign,
            )

        scaled = select_count()

        assert scaled == baseline
