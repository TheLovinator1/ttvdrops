"""Tests for chat badge views."""

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet

if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.django_db
class TestBadgeListView:
    """Tests for the badge list view."""

    def test_badge_list_empty(self, client: Client) -> None:
        """Test badge list view with no badges."""
        response = client.get(reverse("twitch:badge_list"))
        assert response.status_code == 200
        assert "No badge sets found" in response.content.decode()

    def test_badge_list_displays_sets(self, client: Client) -> None:
        """Test that badge sets are displayed."""
        badge_set1 = ChatBadgeSet.objects.create(set_id="vip")
        badge_set2 = ChatBadgeSet.objects.create(set_id="subscriber")

        response = client.get(reverse("twitch:badge_list"))
        assert response.status_code == 200
        content = response.content.decode()

        assert badge_set1.set_id in content
        assert badge_set2.set_id in content

    def test_badge_list_displays_badge_count(self, client: Client) -> None:
        """Test that badge version count is displayed."""
        badge_set = ChatBadgeSet.objects.create(set_id="bits")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Bits 1",
            description="1 Bit",
        )
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="100",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Bits 100",
            description="100 Bits",
        )

        response = client.get(reverse("twitch:badge_list"))
        assert response.status_code == 200
        content = response.content.decode()

        # Should show version count (the template uses "versions" not "version")
        assert "2" in content
        assert "versions" in content


@pytest.mark.django_db
class TestBadgeSetDetailView:
    """Tests for the badge set detail view."""

    def test_badge_set_detail_not_found(self, client: Client) -> None:
        """Test 404 when badge set doesn't exist."""
        response = client.get(
            reverse("twitch:badge_set_detail", kwargs={"set_id": "nonexistent"}),
        )
        assert response.status_code == 404

    def test_badge_set_detail_displays_badges(self, client: Client) -> None:
        """Test that badge versions are displayed."""
        badge_set = ChatBadgeSet.objects.create(set_id="moderator")
        badge = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Moderator",
            description="Channel Moderator",
            click_action="visit_url",
            click_url="https://help.twitch.tv",
        )

        response = client.get(
            reverse("twitch:badge_set_detail", kwargs={"set_id": "moderator"}),
        )
        assert response.status_code == 200
        content = response.content.decode()

        assert badge.title in content
        assert badge.description in content
        assert badge.badge_id in content
        assert badge.image_url_4x in content

    def test_badge_set_detail_displays_metadata(self, client: Client) -> None:
        """Test that badge set metadata is displayed."""
        badge_set = ChatBadgeSet.objects.create(set_id="vip")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="VIP",
            description="VIP Badge",
        )

        response = client.get(
            reverse("twitch:badge_set_detail", kwargs={"set_id": "vip"}),
        )
        assert response.status_code == 200
        content = response.content.decode()

        assert "vip" in content
        assert "1" in content

    def test_badge_set_detail_json_data(self, client: Client) -> None:
        """Test that JSON data is displayed."""
        badge_set = ChatBadgeSet.objects.create(set_id="test_set")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Test",
            description="Test Badge",
        )

        response = client.get(
            reverse("twitch:badge_set_detail", kwargs={"set_id": "test_set"}),
        )
        assert response.status_code == 200
        content = response.content.decode()

        assert "test_set" in content


@pytest.mark.django_db
class TestBadgeSearch:
    """Tests for badge search functionality."""

    def test_search_finds_badge_sets(self, client: Client) -> None:
        """Test that search finds badge sets."""
        ChatBadgeSet.objects.create(set_id="vip")
        ChatBadgeSet.objects.create(set_id="subscriber")

        response = client.get(reverse("twitch:search"), {"q": "vip"})
        assert response.status_code == 200
        content = response.content.decode()

        assert "Badge Sets" in content
        assert "vip" in content

    def test_search_finds_badges_by_title(self, client: Client) -> None:
        """Test that search finds badges by title."""
        badge_set = ChatBadgeSet.objects.create(set_id="test")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Moderator Badge",
            description="Test description",
        )

        response = client.get(reverse("twitch:search"), {"q": "Moderator"})
        assert response.status_code == 200
        content = response.content.decode()

        assert "Chat Badges" in content
        assert "Moderator Badge" in content

    def test_search_finds_badges_by_description(self, client: Client) -> None:
        """Test that search finds badges by description."""
        badge_set = ChatBadgeSet.objects.create(set_id="test")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Test Badge",
            description="Unique description text",
        )

        response = client.get(reverse("twitch:search"), {"q": "Unique description"})
        assert response.status_code == 200
        content = response.content.decode()

        assert "Chat Badges" in content or "Test Badge" in content
