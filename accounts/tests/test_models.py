from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

User: type[AbstractUser] = get_user_model()


@pytest.mark.django_db
class TestUserModel:
    """Test cases for the custom User model."""

    def test_create_user(self) -> None:
        """Test creating a regular user."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.check_password("testpass123")
        assert not user.is_staff
        assert not user.is_superuser
        assert user.is_active

    def test_create_superuser(self) -> None:
        """Test creating a superuser."""
        superuser: AbstractUser = User.objects.create_superuser(username="admin", email="admin@example.com", password="adminpass123")

        assert superuser.username == "admin"
        assert superuser.email == "admin@example.com"
        assert superuser.check_password("adminpass123")
        assert superuser.is_staff
        assert superuser.is_superuser
        assert superuser.is_active

    def test_user_str_representation(self) -> None:
        """Test the string representation of the user."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com")

        assert str(user) == "testuser"

    def test_user_email_field(self) -> None:
        """Test that email field works correctly."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        assert user.email == "test@example.com"

        # Test email update
        user.email = "newemail@example.com"
        user.save()
        user.refresh_from_db()

        assert user.email == "newemail@example.com"

    def test_user_unique_username(self) -> None:
        """Test that username must be unique."""
        User.objects.create_user(username="testuser", email="test1@example.com", password="testpass123")

        # Attempting to create another user with the same username should raise an error
        with pytest.raises(IntegrityError):
            User.objects.create_user(username="testuser", email="test2@example.com", password="testpass123")

    def test_user_password_hashing(self) -> None:
        """Test that passwords are properly hashed."""
        password = "testpass123"
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password=password)

        # Password should be hashed, not stored in plain text
        assert user.password != password
        assert user.check_password(password)
        assert not user.check_password("wrongpassword")

    def test_user_without_email(self) -> None:
        """Test creating a user without email."""
        user: AbstractUser = User.objects.create_user(username="testuser", password="testpass123")

        assert user.username == "testuser"
        assert not user.email
        assert user.check_password("testpass123")

    def test_user_model_meta_options(self) -> None:
        """Test the model meta options."""
        assert User._meta.db_table == "auth_user"  # noqa: SLF001
        assert User._meta.verbose_name == "User"  # noqa: SLF001
        assert User._meta.verbose_name_plural == "Users"  # noqa: SLF001

    def test_user_manager_methods(self) -> None:
        """Test User manager methods."""
        # Test create_user method
        user: AbstractUser = User.objects.create_user(username="regularuser", email="regular@example.com", password="pass123")
        assert not user.is_staff
        assert not user.is_superuser

        # Test create_superuser method
        superuser: AbstractUser = User.objects.create_superuser(username="superuser", email="super@example.com", password="superpass123")
        assert superuser.is_staff
        assert superuser.is_superuser

    def test_user_permissions(self) -> None:
        """Test user permissions and groups."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        # Initially user should have no permissions
        assert not user.user_permissions.exists()
        assert not user.groups.exists()

        # Test has_perm method (should be False for regular user)
        assert not user.has_perm("auth.add_user")

    def test_user_active_status(self) -> None:
        """Test user active status functionality."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        # User should be active by default
        assert user.is_active

        # Test deactivating user
        user.is_active = False
        user.save()
        user.refresh_from_db()

        assert not user.is_active

    def test_user_date_joined(self) -> None:
        """Test that date_joined is automatically set."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        assert user.date_joined is not None

    def test_user_last_login_initially_none(self) -> None:
        """Test that last_login is initially None."""
        user: AbstractUser = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        assert user.last_login is None


@pytest.mark.django_db
class TestUserModelEdgeCases:
    """Test edge cases and error conditions for the User model."""

    def test_create_user_without_username(self) -> None:
        """Test that creating a user without username raises an error."""
        with pytest.raises(ValueError, match="The given username must be set"):
            User.objects.create_user(username="", email="test@example.com", password="testpass123")

    def test_create_superuser_without_is_staff(self) -> None:
        """Test that create_superuser enforces is_staff=True."""
        with pytest.raises(ValueError, match=r"Superuser must have is_staff=True."):
            User.objects.create_superuser(username="admin", email="admin@example.com", password="adminpass123", is_staff=False)

    def test_create_superuser_without_is_superuser(self) -> None:
        """Test that create_superuser enforces is_superuser=True."""
        with pytest.raises(ValueError, match=r"Superuser must have is_superuser=True."):
            User.objects.create_superuser(username="admin", email="admin@example.com", password="adminpass123", is_superuser=False)

    def test_user_with_very_long_username(self) -> None:
        """Test username length validation."""
        # Django's default max_length for username is 150
        long_username: str = "a" * 151

        user = User(username=long_username, email="test@example.com")

        with pytest.raises(ValidationError):
            user.full_clean()

    def test_user_with_invalid_email_format(self) -> None:
        """Test email format validation."""
        user = User(username="testuser", email="invalid-email-format")

        with pytest.raises(ValidationError):
            user.full_clean()
