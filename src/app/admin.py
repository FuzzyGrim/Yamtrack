from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User
from .forms import UserRegisterForm


class UserAdmin(UserAdmin):
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
