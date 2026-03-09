"""Tests for chat badge models and functionality."""

import pytest
from django.db import IntegrityError
from pydantic import ValidationError

from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.schemas import ChatBadgeSetSchema
from twitch.schemas import ChatBadgeVersionSchema
from twitch.schemas import GlobalChatBadgesResponse


@pytest.mark.django_db
class TestChatBadgeSetModel:
    """Tests for the ChatBadgeSet model."""

    def test_create_badge_set(self) -> None:
        """Test creating a new badge set."""
        badge_set = ChatBadgeSet.objects.create(set_id="vip")
        assert badge_set.set_id == "vip"
        assert badge_set.added_at is not None
        assert badge_set.updated_at is not None
        assert str(badge_set) == "vip"

    def test_unique_set_id(self) -> None:
        """Test that set_id must be unique."""
        ChatBadgeSet.objects.create(set_id="vip")
        with pytest.raises(IntegrityError):
            ChatBadgeSet.objects.create(set_id="vip")

    def test_badge_set_ordering(self) -> None:
        """Test that badge sets are ordered by set_id."""
        ChatBadgeSet.objects.create(set_id="subscriber")
        ChatBadgeSet.objects.create(set_id="bits")
        ChatBadgeSet.objects.create(set_id="vip")

        badge_sets = list(ChatBadgeSet.objects.all())
        assert badge_sets[0].set_id == "bits"
        assert badge_sets[1].set_id == "subscriber"
        assert badge_sets[2].set_id == "vip"


@pytest.mark.django_db
class TestChatBadgeModel:
    """Tests for the ChatBadge model."""

    def test_create_badge(self) -> None:
        """Test creating a new badge."""
        badge_set = ChatBadgeSet.objects.create(set_id="vip")
        badge = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="VIP",
            description="VIP Badge",
            click_action="visit_url",
            click_url="https://help.twitch.tv",
        )

        assert badge.badge_set == badge_set
        assert badge.badge_id == "1"
        assert badge.title == "VIP"
        assert badge.description == "VIP Badge"
        assert badge.click_action == "visit_url"
        assert badge.click_url == "https://help.twitch.tv"
        assert str(badge) == "vip/1: VIP"

    def test_unique_badge_set_and_id(self) -> None:
        """Test that badge_set and badge_id combination must be unique."""
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

        with pytest.raises(IntegrityError):
            ChatBadge.objects.create(
                badge_set=badge_set,
                badge_id="1",
                image_url_1x="https://example.com/1x.png",
                image_url_2x="https://example.com/2x.png",
                image_url_4x="https://example.com/4x.png",
                title="VIP Duplicate",
                description="Duplicate Badge",
            )

    def test_different_badge_ids_same_set(self) -> None:
        """Test that different badge_ids can exist in the same set."""
        badge_set = ChatBadgeSet.objects.create(set_id="bits")
        badge1 = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Bits 1",
            description="1 Bit",
        )
        badge2 = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="100",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Bits 100",
            description="100 Bits",
        )

        assert badge1.badge_id == "1"
        assert badge2.badge_id == "100"
        assert badge1.badge_set == badge2.badge_set

    def test_nullable_click_fields(self) -> None:
        """Test that click_action and click_url can be null."""
        badge_set = ChatBadgeSet.objects.create(set_id="moderator")
        badge = ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Moderator",
            description="Channel Moderator",
            click_action=None,
            click_url=None,
        )

        assert badge.click_action is None
        assert badge.click_url is None

    def test_badge_cascade_delete(self) -> None:
        """Test that badges are deleted when their badge set is deleted."""
        badge_set = ChatBadgeSet.objects.create(set_id="test_set")
        ChatBadge.objects.create(
            badge_set=badge_set,
            badge_id="1",
            image_url_1x="https://example.com/1x.png",
            image_url_2x="https://example.com/2x.png",
            image_url_4x="https://example.com/4x.png",
            title="Test Badge",
            description="Test Description",
        )

        assert ChatBadge.objects.filter(badge_set=badge_set).count() == 1
        badge_set.delete()
        assert ChatBadge.objects.filter(badge_set__set_id="test_set").count() == 0


class TestChatBadgeSchemas:
    """Tests for chat badge Pydantic schemas."""

    def test_chat_badge_version_schema_valid(self) -> None:
        """Test that ChatBadgeVersionSchema validates correct data."""
        data = {
            "id": "1",
            "image_url_1x": "https://static-cdn.jtvnw.net/badges/v1/example/1",
            "image_url_2x": "https://static-cdn.jtvnw.net/badges/v1/example/2",
            "image_url_4x": "https://static-cdn.jtvnw.net/badges/v1/example/3",
            "title": "VIP",
            "description": "VIP Badge",
            "click_action": "visit_url",
            "click_url": "https://help.twitch.tv",
        }

        schema = ChatBadgeVersionSchema.model_validate(data)
        assert schema.badge_id == "1"
        assert schema.title == "VIP"
        assert schema.click_action == "visit_url"

    def test_chat_badge_version_schema_nullable_fields(self) -> None:
        """Test that nullable fields in ChatBadgeVersionSchema work correctly."""
        data = {
            "id": "1",
            "image_url_1x": "https://static-cdn.jtvnw.net/badges/v1/example/1",
            "image_url_2x": "https://static-cdn.jtvnw.net/badges/v1/example/2",
            "image_url_4x": "https://static-cdn.jtvnw.net/badges/v1/example/3",
            "title": "VIP",
            "description": "VIP Badge",
            "click_action": None,
            "click_url": None,
        }

        schema = ChatBadgeVersionSchema.model_validate(data)
        assert schema.click_action is None
        assert schema.click_url is None

    def test_chat_badge_version_schema_missing_required(self) -> None:
        """Test that ChatBadgeVersionSchema raises error on missing required fields."""
        data = {
            "id": "1",
            "title": "VIP",
            # Missing required image URLs and description
        }

        with pytest.raises(ValidationError):
            ChatBadgeVersionSchema.model_validate(data)

    def test_chat_badge_set_schema_valid(self) -> None:
        """Test that ChatBadgeSetSchema validates correct data."""
        data = {
            "set_id": "vip",
            "versions": [
                {
                    "id": "1",
                    "image_url_1x": "https://static-cdn.jtvnw.net/badges/v1/example/1",
                    "image_url_2x": "https://static-cdn.jtvnw.net/badges/v1/example/2",
                    "image_url_4x": "https://static-cdn.jtvnw.net/badges/v1/example/3",
                    "title": "VIP",
                    "description": "VIP Badge",
                    "click_action": "visit_url",
                    "click_url": "https://help.twitch.tv",
                },
            ],
        }

        schema = ChatBadgeSetSchema.model_validate(data)
        assert schema.set_id == "vip"
        assert len(schema.versions) == 1
        assert schema.versions[0].badge_id == "1"

    def test_chat_badge_set_schema_multiple_versions(self) -> None:
        """Test that ChatBadgeSetSchema handles multiple badge versions."""
        data = {
            "set_id": "bits",
            "versions": [
                {
                    "id": "1",
                    "image_url_1x": "https://example.com/1/1x.png",
                    "image_url_2x": "https://example.com/1/2x.png",
                    "image_url_4x": "https://example.com/1/4x.png",
                    "title": "Bits 1",
                    "description": "1 Bit",
                    "click_action": None,
                    "click_url": None,
                },
                {
                    "id": "100",
                    "image_url_1x": "https://example.com/100/1x.png",
                    "image_url_2x": "https://example.com/100/2x.png",
                    "image_url_4x": "https://example.com/100/4x.png",
                    "title": "Bits 100",
                    "description": "100 Bits",
                    "click_action": None,
                    "click_url": None,
                },
            ],
        }

        schema = ChatBadgeSetSchema.model_validate(data)
        assert schema.set_id == "bits"
        assert len(schema.versions) == 2
        assert schema.versions[0].badge_id == "1"
        assert schema.versions[1].badge_id == "100"

    def test_global_chat_badges_response_valid(self) -> None:
        """Test that GlobalChatBadgesResponse validates correct API response."""
        data = {
            "data": [
                {
                    "set_id": "vip",
                    "versions": [
                        {
                            "id": "1",
                            "image_url_1x": "https://static-cdn.jtvnw.net/badges/v1/example/1",
                            "image_url_2x": "https://static-cdn.jtvnw.net/badges/v1/example/2",
                            "image_url_4x": "https://static-cdn.jtvnw.net/badges/v1/example/3",
                            "title": "VIP",
                            "description": "VIP Badge",
                            "click_action": "visit_url",
                            "click_url": "https://help.twitch.tv",
                        },
                    ],
                },
            ],
        }

        response = GlobalChatBadgesResponse.model_validate(data)
        assert len(response.data) == 1
        assert response.data[0].set_id == "vip"

    def test_global_chat_badges_response_empty(self) -> None:
        """Test that GlobalChatBadgesResponse validates empty response."""
        data = {"data": []}

        response = GlobalChatBadgesResponse.model_validate(data)
        assert len(response.data) == 0

    def test_chat_badge_schema_extra_forbidden(self) -> None:
        """Test that extra fields are forbidden in schemas."""
        data = {
            "id": "1",
            "image_url_1x": "https://example.com/1x.png",
            "image_url_2x": "https://example.com/2x.png",
            "image_url_4x": "https://example.com/4x.png",
            "title": "VIP",
            "description": "VIP Badge",
            "extra_field": "should_fail",  # This should cause validation to fail
        }

        with pytest.raises(ValidationError) as exc_info:
            ChatBadgeVersionSchema.model_validate(data)

        # Check that the error is about the extra field
        assert "extra_field" in str(exc_info.value)
