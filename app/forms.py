from django import forms
from .models import User
from django.contrib.auth.forms import UserCreationForm
    

class UserRegisterForm(UserCreationForm):
    default_api = forms.ChoiceField(choices=[("mal", "MAL"), ("tmdb", "TMDB")], help_text="TMDB (The Movie Database) for TV Shows and Movies <br/> MAL (MyAnimeList) for Anime and Manga", label="Default API")

    class Meta:
        model = User
        fields = ['username', 'default_api', 'password1', 'password2']


class UserUpdateForm(forms.ModelForm):
    default_api = forms.ChoiceField(choices=[("mal", "MAL"), ("tmdb", "TMDB")], help_text="TMDB (The Movie Database) for TV Shows and Movies <br/> MAL (MyAnimeList) for Anime and Manga", label="Default API")
    class Meta:
        model = User
        fields = ['username', 'default_api']