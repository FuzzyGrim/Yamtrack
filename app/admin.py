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
                "fields": ("username", "password1", "password2", "default_api"),
            },
        ),
    )
    list_display = ('username', 'default_api')

admin.site.register(User, UserAdmin)