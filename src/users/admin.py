from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .forms import UserRegisterForm
from .models import User


class UserAdmin(UserAdmin):
    """Custom user admin model.

    Allows creation of new users from the admin panel.
    """

    add_form = UserRegisterForm
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "last_search_type",
                ),
            },
        ),
    )
    list_display = ("username", "last_search_type")


admin.site.register(User, UserAdmin)
