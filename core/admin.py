from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.models import User

# Register your custom User model with the admin
admin.site.register(User, UserAdmin)
