from __future__ import annotations

from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError

from accounts.models import User


class CustomUserCreationForm(UserCreationForm):
    """Custom user creation form for the custom User model."""

    class Meta:
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with Bootstrap classes."""
        super().__init__(*args, **kwargs)
        # Add Bootstrap classes to form fields
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})

    def clean_username(self) -> str:
        """Validate the username using the correct User model.

        Returns:
            str: The cleaned username.

        Raises:
            ValidationError: If the username already exists.
        """
        username = self.cleaned_data.get("username")
        if username and User.objects.filter(username=username).exists():
            msg = "A user with that username already exists."
            raise ValidationError(msg)
        return username or ""
