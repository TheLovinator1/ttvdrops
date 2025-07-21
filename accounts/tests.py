from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class AuthenticationTestCase(TestCase):
    """Test authentication functionality."""

    def setUp(self) -> None:
        """Set up test data."""
        self.username = "testuser"
        self.password = "testpass123"
        self.user = User.objects.create_user(username=self.username, password=self.password)

    def test_login_view_get(self) -> None:
        """Test login view GET request."""
        response = self.client.get(reverse("accounts:login"))
        assert response.status_code == 200
        self.assertContains(response, "Login")

    def test_signup_view_get(self) -> None:
        """Test signup view GET request."""
        response = self.client.get(reverse("accounts:signup"))
        assert response.status_code == 200
        self.assertContains(response, "Sign Up")

    def test_login_valid_user(self) -> None:
        """Test login with valid credentials."""
        response = self.client.post(reverse("accounts:login"), {"username": self.username, "password": self.password})
        assert response.status_code == 302  # Redirect after login

    def test_login_invalid_user(self) -> None:
        """Test login with invalid credentials."""
        response = self.client.post(reverse("accounts:login"), {"username": self.username, "password": "wrongpassword"})
        assert response.status_code == 200  # Stay on login page
        self.assertContains(response, "Please enter a correct username and password")

    def test_profile_view_authenticated(self) -> None:
        """Test profile view for authenticated user."""
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(reverse("accounts:profile"))
        assert response.status_code == 200
        self.assertContains(response, self.username)

    def test_profile_view_unauthenticated(self) -> None:
        """Test profile view redirects unauthenticated user."""
        response = self.client.get(reverse("accounts:profile"))
        assert response.status_code == 302  # Redirect to login

    def test_user_signup(self) -> None:
        """Test user signup functionality."""
        response = self.client.post(
            reverse("accounts:signup"), {"username": "newuser", "password1": "complexpass123", "password2": "complexpass123"}
        )
        assert response.status_code == 302  # Redirect after signup
        assert User.objects.filter(username="newuser").exists()

    def test_logout(self) -> None:
        """Test user logout functionality."""
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(reverse("accounts:logout"))
        assert response.status_code == 302  # Redirect after logout
