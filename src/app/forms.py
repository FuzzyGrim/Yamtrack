from django import forms
from .models import User
from django.contrib.auth.forms import (
    UserCreationForm,
    PasswordChangeForm,
    AuthenticationForm,
)


class UserLoginForm(AuthenticationForm):
    """
    Subclass of Django ``AuthenticationForm`` which adds a remember me
    checkbox.
    """

    remember_me = forms.BooleanField(
        label="Remember Me", initial=False, required=False
    )


class UserRegisterForm(UserCreationForm):
    default_api = forms.ChoiceField(
        choices=[("mal", "MAL"), ("tmdb", "TMDB")],
        help_text="TMDB (The Movie Database) for TV Shows and Movies <br/> MAL (MyAnimeList) for Anime and Manga",
        label="Default API",
    )

    class Meta:
        model = User
        fields = ["username", "default_api", "password1", "password2"]


class UserUpdateForm(forms.ModelForm):
    default_api = forms.ChoiceField(
        choices=[("mal", "MAL"), ("tmdb", "TMDB")],
        help_text="TMDB (The Movie Database) for TV Shows and Movies <br/> MAL (MyAnimeList) for Anime and Manga",
        label="Default API",
    )

    class Meta:
        model = User
        fields = ["username", "default_api"]


class PasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
