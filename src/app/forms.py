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

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_username",
                "placeholder": "Username",
            }
        ),
        label="Username",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_password1",
                "placeholder": "Password",
            }
        ),
        label="Password",
    )

    remember_me = forms.BooleanField(
        label="Remember Me",
        initial=False,
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "checkboxinput form-check-input", "id": "id_remember_me"}
        ),
    )


class UserRegisterForm(UserCreationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_username",
                "placeholder": "Username",
            }
        ),
        label="Username",
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_password1",
                "placeholder": "Password",
            }
        ),
        label="Password",
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_password2",
                "placeholder": "Password confirmation",
            }
        ),
        label="Password confirmation",
    )

    class Meta:
        model = User
        fields = ["username", "password1", "password2"]


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username"]


class PasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
