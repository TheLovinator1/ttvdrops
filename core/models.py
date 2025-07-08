from __future__ import annotations

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom User model extending Django's AbstractUser.

    This allows for future customization of the User model
    while maintaining all the default Django User functionality.
    """

    # Add any custom fields here in the future
    # For example:
    # bio = models.TextField(max_length=500, blank=True)
    # avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)

    class Meta:
        db_table = "auth_user"  # Keep the same table name as Django's default User
        verbose_name = "User"
        verbose_name_plural = "Users"
