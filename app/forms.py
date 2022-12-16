from django import forms
from .models import User
from django.contrib.auth.forms import UserCreationForm
    

class UserRegisterForm(UserCreationForm):
    default_api = forms.ChoiceField(choices=[("tmdb", "TMDB"), ("mal", "MAL")])

    class Meta:
        model = User
        fields = ['username', 'default_api', 'password1', 'password2']


class UserUpdateForm(forms.ModelForm):
    default_api = forms.ChoiceField(choices=[("tmdb", "TMDB"), ("mal", "MAL")])
    class Meta:
        model = User
        fields = ['username', 'default_api']