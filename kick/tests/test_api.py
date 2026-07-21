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
            response = self.client.get(reverse("api-v1:kick-api-v1_list_campaigns"))

        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 100

        item = data["items"][0]
        assert item["kick_id"] == "campaign-api"
        assert item["name"] == "API Campaign"
        assert item["status"] == "active"
        assert (
            item["image_url"]
            == "https://ext.cdn.kick.com/drops/reward-image/reward.png"
        )
        assert item["connect_url"] == "https://example.com/connect"
        assert item["url"] == "https://example.com/campaign"
        assert item["starts_at"] == "2026-07-01T00:00:00Z"
        assert item["ends_at"] == "2026-08-01T00:00:00Z"
        assert item["category"] == {
            "kick_id": 123,
            "name": "API Game",
            "slug": "api-game",
            "image_url": "https://example.com/game.png",
        }
        assert item["organization"] == {
            "kick_id": "org-api",
            "name": "API Organization",
            "logo_url": "https://example.com/org.png",
            "url": "https://example.com/org",
        }
        assert item["reward_count"] == 1
        assert item["channels"] == [
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
        ]
        assert item["rewards"] == [
            {
                "kick_id": "reward-api",
                "name": "API Reward",
                "image_url": "https://ext.cdn.kick.com/drops/reward-image/reward.png",
                "required_minutes_watched": 30,
            },
        ]
        assert item["is_fully_imported"] is True
        assert "added_at" in item
        assert "updated_at" in item

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
            reverse("api-v1:kick-api-v1_list_campaigns"),
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
                        reverse("api-v1:kick-api-v1_list_campaigns"),
                        {"status": status},
                    )
                    assert response.status_code == 200
                    assert [item["kick_id"] for item in response.json()["items"]] == [
                        expected_id,
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
                        reverse("api-v1:kick-api-v1_list_campaigns"),
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
            reverse("api-v1:kick-api-v1_list_campaigns"),
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
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"page_size": 1000},
        )

        assert response.status_code == 200
        assert response.json()["page_size"] == 500

    def test_clamps_pagination_minimums(self) -> None:
        """Zero and negative pagination values use the first one-item page."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
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
                    reverse("api-v1:kick-api-v1_list_campaigns"),
                    {field: "not-an-integer"},
                )
                assert response.status_code == 422
                assert response.json()["detail"][0]["loc"] == ["query", field]

    def test_page_beyond_result_set_returns_empty_items(self) -> None:
        """An arbitrarily large page does not become a pathological DB offset."""
        page = 10**100

        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"page": page},
        )

        assert response.status_code == 200
        assert response.json()["page"] == page
        assert response.json()["items"] == []

    def test_rejects_invalid_status(self) -> None:
        """Unknown status values return a client error instead of an empty result."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"status": "running"},
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", "status"]

    def test_rejects_empty_status(self) -> None:
        """An explicitly empty status is invalid rather than an omitted filter."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"status": ""},
        )

        assert response.status_code == 422

    def test_rejects_non_get_methods(self) -> None:
        """The campaign feed is a read-only endpoint."""
        response = self.client.post(reverse("api-v1:kick-api-v1_list_campaigns"))

        assert response.status_code == 405

    def test_rejects_invalid_game_id(self) -> None:
        """A non-numeric Kick category ID returns a validation response."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
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

        response = self.client.get(reverse("api-v1:kick-api-v1_list_campaigns"))

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

        response = self.client.get(reverse("api-v1:kick-api-v1_list_campaigns"))

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

        response = self.client.get(reverse("api-v1:kick-api-v1_list_campaigns"))

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
                response = self.client.get(reverse("api-v1:kick-api-v1_list_campaigns"))
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


class KickCampaignDetailApiTest(TestCase):
    """Tests for the campaign detail endpoint."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.organization = KickOrganization.objects.create(
            kick_id="org-detail",
            name="Detail Org",
        )
        cls.category = KickCategory.objects.create(
            kick_id=200,
            name="Detail Game",
            slug="detail-game",
        )
        cls.campaign = KickDropCampaign.objects.create(
            kick_id="campaign-detail",
            name="Detail Campaign",
            starts_at=datetime(2026, 7, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 1, tzinfo=UTC),
            organization=cls.organization,
            category=cls.category,
            rule_id=1,
            rule_name="Watch to earn",
            is_fully_imported=True,
        )
        cls.user = KickUser.objects.create(
            kick_id=457,
            username="detail-streamer",
            profile_picture="https://example.com/detail.png",
        )
        cls.channel = KickChannel.objects.create(
            kick_id=790,
            slug="detail-streamer",
            user=cls.user,
        )
        cls.campaign.channels.add(cls.channel)
        cls.reward = KickReward.objects.create(
            kick_id="reward-detail",
            name="Detail Reward",
            required_units=45,
            campaign=cls.campaign,
            category=cls.category,
            organization=cls.organization,
        )

    def test_detail_returns_campaign(self) -> None:
        """A fully imported campaign returns its full serialized payload."""
        with mock.patch(
            "kick.api.timezone.now",
            return_value=datetime(2026, 7, 15, tzinfo=UTC),
        ):
            response = self.client.get(
                reverse(
                    "api-v1:kick-api-v1_get_campaign",
                    args=[self.campaign.kick_id],
                ),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["kick_id"] == "campaign-detail"
        assert data["status"] == "active"
        assert data["name"] == "Detail Campaign"
        assert len(data["channels"]) == 1
        assert len(data["rewards"]) == 1

    def test_detail_not_found(self) -> None:
        """A non-existent campaign ID returns 404."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_get_campaign", args=["nonexistent-id"]),
        )
        assert response.status_code == 404

    def test_detail_not_fully_imported(self) -> None:
        """A campaign that is not fully imported returns 404."""
        KickDropCampaign.objects.create(
            kick_id="not-imported-detail",
            name="Not Imported",
            organization=self.organization,
            category=self.category,
            starts_at=datetime(2026, 7, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 1, tzinfo=UTC),
            is_fully_imported=False,
        )
        response = self.client.get(
            reverse("api-v1:kick-api-v1_get_campaign", args=["not-imported-detail"]),
        )
        assert response.status_code == 404


class KickCampaignSearchFilterTest(TestCase):
    """Tests for the search and organization filter on the campaign list."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org_a = KickOrganization.objects.create(
            kick_id="org-a",
            name="Studio Alpha",
        )
        cls.org_b = KickOrganization.objects.create(kick_id="org-b", name="Beta Corp")
        cls.cat = KickCategory.objects.create(kick_id=300, name="Test Game")

        cls.camp_alpha = KickDropCampaign.objects.create(
            kick_id="alpha-camp",
            name="Alpha Drop Event",
            starts_at=datetime(2026, 6, 1, tzinfo=UTC),
            ends_at=datetime(2026, 7, 1, tzinfo=UTC),
            organization=cls.org_a,
            category=cls.cat,
            is_fully_imported=True,
        )
        cls.camp_beta = KickDropCampaign.objects.create(
            kick_id="beta-camp",
            name="Beta Giveaway",
            starts_at=datetime(2026, 7, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 1, tzinfo=UTC),
            organization=cls.org_b,
            category=cls.cat,
            is_fully_imported=True,
        )
        cls.camp_alpha2 = KickDropCampaign.objects.create(
            kick_id="alpha-camp-2",
            name="Alpha Second Wave",
            starts_at=datetime(2026, 8, 1, tzinfo=UTC),
            ends_at=datetime(2026, 9, 1, tzinfo=UTC),
            organization=cls.org_a,
            category=cls.cat,
            is_fully_imported=True,
        )

    def test_filters_by_organization(self) -> None:
        """An organization kick_id returns only that org's campaigns."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"organization": "org-a"},
        )
        assert response.status_code == 200
        ids = [item["kick_id"] for item in response.json()["items"]]
        assert sorted(ids) == ["alpha-camp", "alpha-camp-2"]

    def test_organization_filter_with_no_matches(self) -> None:
        """An org with no campaigns returns an empty list."""
        KickOrganization.objects.create(kick_id="org-empty", name="Empty Org")
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"organization": "org-empty"},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_searches_by_name(self) -> None:
        """A search term filters campaigns by case-insensitive name match."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"search": "alpha"},
        )
        assert response.status_code == 200
        ids = [item["kick_id"] for item in response.json()["items"]]
        assert sorted(ids) == ["alpha-camp", "alpha-camp-2"]

    def test_search_with_no_matches(self) -> None:
        """A search term with no matches returns an empty list."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"search": "zzzzz"},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_search_case_insensitive(self) -> None:
        """Search is case-insensitive."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"search": "ALPHA"},
        )
        assert response.status_code == 200
        ids = [item["kick_id"] for item in response.json()["items"]]
        assert sorted(ids) == ["alpha-camp", "alpha-camp-2"]

    def test_organization_filter_with_search(self) -> None:
        """Organization filter and search can be combined."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_campaigns"),
            {"organization": "org-a", "search": "Second"},
        )
        assert response.status_code == 200
        ids = [item["kick_id"] for item in response.json()["items"]]
        assert ids == ["alpha-camp-2"]


class KickOrganizationApiTest(TestCase):
    """Tests for the organization API endpoints."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org_a = KickOrganization.objects.create(
            kick_id="org-list-a",
            name="Alpha Studios",
            logo_url="https://example.com/alpha.png",
            url="https://example.com/alpha",
        )
        cls.org_b = KickOrganization.objects.create(
            kick_id="org-list-b",
            name="Beta Games",
            logo_url="https://example.com/beta.png",
            url="https://example.com/beta",
        )
        cat = KickCategory.objects.create(kick_id=400, name="Game")
        for org in (cls.org_a, cls.org_b):
            KickDropCampaign.objects.create(
                kick_id=f"camp-{org.kick_id}",
                name=f"{org.name} Campaign",
                starts_at=datetime(2026, 7, 1, tzinfo=UTC),
                ends_at=datetime(2026, 8, 1, tzinfo=UTC),
                organization=org,
                category=cat,
                is_fully_imported=True,
            )

    def test_list_returns_organizations(self) -> None:
        """Organization list returns all orgs with campaign counts."""
        response = self.client.get(reverse("api-v1:kick-api-v1_list_organizations"))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        names = [item["name"] for item in data["items"]]
        assert names == ["Alpha Studios", "Beta Games"]
        assert all(item["campaign_count"] == 1 for item in data["items"])

    def test_detail_returns_organization_with_campaigns(self) -> None:
        """Organization detail returns org metadata and its campaigns."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_get_organization", args=["org-list-a"]),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Alpha Studios"
        assert data["campaign_count"] == 1
        assert len(data["campaigns"]) == 1
        assert data["campaigns"][0]["name"] == "Alpha Studios Campaign"

    def test_detail_not_found(self) -> None:
        """A non-existent org returns 404."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_get_organization", args=["nonexistent"]),
        )
        assert response.status_code == 404

    def test_list_pagination(self) -> None:
        """Organization list respects pagination."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_organizations"),
            {"page": 1, "page_size": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["total"] == 2

    def test_list_search(self) -> None:
        """Organization list supports search by name."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_organizations"),
            {"search": "alpha"},
        )
        assert response.status_code == 200
        names = [item["name"] for item in response.json()["items"]]
        assert names == ["Alpha Studios"]


class KickCategoryApiTest(TestCase):
    """Tests for the category (game) API endpoints."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.cat_a = KickCategory.objects.create(
            kick_id=500,
            name="Action Game",
            slug="action-game",
            image_url="https://example.com/action.png",
        )
        cls.cat_b = KickCategory.objects.create(
            kick_id=501,
            name="Racing Game",
            slug="racing-game",
            image_url="https://example.com/racing.png",
        )
        org = KickOrganization.objects.create(
            kick_id="cat-org",
            name="Category Org",
        )
        for cat in (cls.cat_a, cls.cat_b):
            KickDropCampaign.objects.create(
                kick_id=f"camp-{cat.kick_id}",
                name=f"{cat.name} Campaign",
                starts_at=datetime(2026, 7, 1, tzinfo=UTC),
                ends_at=datetime(2026, 8, 1, tzinfo=UTC),
                organization=org,
                category=cat,
                is_fully_imported=True,
            )

    def test_list_returns_categories(self) -> None:
        """Category list returns all categories with campaign counts."""
        response = self.client.get(reverse("api-v1:kick-api-v1_list_games"))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        names = [item["name"] for item in data["items"]]
        assert names == ["Action Game", "Racing Game"]
        assert all(item["campaign_count"] == 1 for item in data["items"])

    def test_detail_returns_category_with_campaigns(self) -> None:
        """Category detail returns metadata and its campaigns."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_get_game", args=[500]),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Action Game"
        assert data["campaign_count"] == 1
        assert len(data["campaigns"]) == 1
        assert data["campaigns"][0]["name"] == "Action Game Campaign"

    def test_detail_not_found(self) -> None:
        """A non-existent category returns 404."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_get_game", args=[99999]),
        )
        assert response.status_code == 404

    def test_list_search(self) -> None:
        """Category list supports search by name."""
        response = self.client.get(
            reverse("api-v1:kick-api-v1_list_games"),
            {"search": "action"},
        )
        assert response.status_code == 200
        names = [item["name"] for item in response.json()["items"]]
        assert names == ["Action Game"]


class KickStatsApiTest(TestCase):
    """Tests for the stats endpoint."""

    @classmethod
    def setUpTestData(cls) -> None:
        KickOrganization.objects.create(kick_id="stats-org", name="Stats Org")
        KickCategory.objects.create(kick_id=600, name="Stats Game")
        KickChannel.objects.create(kick_id=800, slug="stats-channel")

    def test_stats_returns_aggregate_counts(self) -> None:
        """Stats endpoint returns counts for all entity types."""
        response = self.client.get(reverse("api-v1:kick-api-v1_stats"))
        assert response.status_code == 200
        data = response.json()
        assert data["total_campaigns"] == 0
        assert data["total_organizations"] == 1
        assert data["total_categories"] == 1
        assert data["total_channels"] == 1
        for key in ("active", "upcoming", "expired", "partial_dates"):
            assert key in data
