from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)

from .models import User


class UserLoginForm(AuthenticationForm):
    """Add a remember me checkbox and style the form."""

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "textinput textInput form-control first-input",
                "required": True,
                "id": "id_username",
                "placeholder": "Username",
            },
        ),
        label="Username",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control last-input",
                "required": True,
                "id": "id_password1",
                "placeholder": "Password",
            },
        ),
        label="Password",
    )

    remember_me = forms.BooleanField(
        label="Remember Me",
        initial=False,
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input"},
        ),
    )


class UserRegisterForm(UserCreationForm):
    """Style the form."""

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "textinput textInput form-control first-input",
                "required": True,
                "id": "id_username",
                "placeholder": "Username",
            },
        ),
        label="Username",
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control middle-input",
                "required": True,
                "id": "id_password1",
                "placeholder": "Password",
            },
        ),
        label="Password",
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control last-input",
                "required": True,
                "id": "id_password2",
                "placeholder": "Password confirmation",
            },
        ),
        label="Password confirmation",
    )

    class Meta:
        """Only include the username and password fields."""

        model = User
        fields = ["username", "password1", "password2"]


class UserUpdateForm(forms.ModelForm):
    """Custom form for updating username."""

    def __init__(self: "UserUpdateForm", *args: dict, **kwargs: dict) -> None:
        """Add crispy form helper to add submit button."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit("submit", "Update", css_class="btn btn-secondary rounded"),
        )

    class Meta:
        """Only allow updating username."""

        model = User
        fields = ["username"]


class PasswordChangeForm(PasswordChangeForm):
    """Custom form for changing password."""

    def __init__(self: "PasswordChangeForm", *args: dict, **kwargs: dict) -> None:
        """Remove autofocus from password change form."""

        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit("submit", "Update", css_class="btn btn-secondary rounded"),
        )
