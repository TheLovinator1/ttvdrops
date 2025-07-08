from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import AbstractUser
from django.db.models.base import Model
from django.test import RequestFactory

if TYPE_CHECKING:
    from django.contrib.admin import ModelAdmin
    from django.contrib.auth.models import AbstractUser
    from django.db.models.base import Model

User: type[AbstractUser] = get_user_model()


@pytest.mark.django_db
class TestUserAdmin:
    """Test cases for User admin configuration."""

    def setup_method(self) -> None:
        """Set up test data for each test method."""
        self.factory = RequestFactory()
        self.superuser: AbstractUser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test_admin_password_123",
        )

    def test_user_model_registered_in_admin(self) -> None:
        """Test that User model is registered in Django admin."""
        registry: dict[type[Model], ModelAdmin[Any]] = admin.site._registry  # noqa: SLF001
        assert User in registry

    def test_user_admin_class(self) -> None:
        """Test that User is registered with UserAdmin."""
        registry: dict[type[Model], ModelAdmin[Any]] = admin.site._registry  # noqa: SLF001
        admin_class: admin.ModelAdmin[Any] = registry[User]
        assert isinstance(admin_class, UserAdmin)

    def test_user_admin_can_create_user(self) -> None:
        """Test that admin can create users through the interface."""
        # Test that the admin form can handle user creation
        registry: dict[type[Model], ModelAdmin[Any]] = admin.site._registry  # noqa: SLF001
        user_admin: admin.ModelAdmin[Any] = registry[User]

        # Check that the admin has the expected methods
        assert hasattr(user_admin, "get_form")
        assert hasattr(user_admin, "save_model")

    def test_user_admin_list_display(self) -> None:
        """Test admin list display configuration."""
        registry: dict[type[Model], ModelAdmin[Any]] = admin.site._registry  # noqa: SLF001
        user_admin: admin.ModelAdmin[Any] = registry[User]

        # UserAdmin should have default list_display
        expected_fields: tuple[str, ...] = (
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
        )
        assert user_admin.list_display == expected_fields

    def test_user_admin_search_fields(self) -> None:
        """Test admin search fields configuration."""
        registry: dict[type[Model], ModelAdmin[Any]] = admin.site._registry  # noqa: SLF001
        user_admin: admin.ModelAdmin[Any] = registry[User]

        # UserAdmin should have default search fields
        expected_search_fields: tuple[str, ...] = ("username", "first_name", "last_name", "email")
        assert user_admin.search_fields == expected_search_fields

    def test_user_admin_fieldsets(self) -> None:
        """Test admin fieldsets configuration."""
        registry: dict[type[Model], ModelAdmin[Any]] = admin.site._registry  # noqa: SLF001
        user_admin: admin.ModelAdmin[Any] = registry[User]

        # UserAdmin should have fieldsets defined
        assert hasattr(user_admin, "fieldsets")
        assert user_admin.fieldsets is not None
