from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.db.models import Field

from users.models import User


class CustomUserCreationForm(UserCreationForm):
    """A custom user creation form that only includes the username field."""

    class Meta:
        """Meta class for the custom user creation form."""

        model = User
        fields = ("username",)


class CustomUserAdmin(UserAdmin):
    """Custom admin interface for the User model."""

    add_form = CustomUserCreationForm
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "password1", "password2"),
            },
        ),
    )
    list_display = ("username", "is_staff", "is_active", "is_demo", "last_login")
    list_filter = ("is_staff", "is_active", "is_demo")

    def get_fieldsets(self, _, __=None):
        """Customize the fieldsets for the User model in the admin interface."""
        fieldsets = [
            (None, {"fields": ("username", "password")}),
            ("Permissions", {"fields": ("is_staff", "is_active")}),
        ]

        field_groups = {}
        for field in User._meta.get_fields():
            if not isinstance(field, Field):
                continue

            # Skip fields already included
            if field.name in {"username", "password", "is_staff", "is_active", "id"}:
                continue

            # Group fields by prefix (everything before first underscore)
            prefix = field.name.split("_")[0]
            field_groups.setdefault(prefix, []).append(field.name)

        # Add grouped fields to fieldsets
        for prefix, fields in field_groups.items():
            fieldsets.append((prefix.title(), {"fields": tuple(fields)}))

        return fieldsets

    search_fields = ("username",)
    ordering = ("username",)


admin.site.register(User, CustomUserAdmin)
admin.site.unregister(Group)
