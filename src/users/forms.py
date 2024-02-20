from crispy_forms.helper import FormHelper
from crispy_forms.layout import BaseInput
from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)

from .models import User


class UserLoginForm(AuthenticationForm):
    """Add a remember me checkbox and style the form."""

    # Override the default error messages
    error_messages = {
        "invalid_login": "Please enter a correct username and password.",
        "inactive": "This account is inactive.",
    }

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

    def clean(self: "PasswordChangeForm") -> dict:
        """Check if the user is editable before changing the password."""

        cleaned_data = super().clean()
        if not self.instance.editable:
            msg = "Changing the username is not allowed for this account."
            self.add_error("username", msg)
        return cleaned_data

    def __init__(self: "UserUpdateForm", *args: dict, **kwargs: dict) -> None:
        """Add crispy form helper to add submit button."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            CustomSubmit("submit", "Update"),
        )
        self.fields["username"].help_text = None

    class Meta:
        """Only allow updating username."""

        model = User
        fields = ["username"]


class PasswordChangeForm(PasswordChangeForm):
    """Custom form for changing password."""

    def clean(self: "PasswordChangeForm") -> dict:
        """Check if the user is editable before changing the password."""

        cleaned_data = super().clean()
        if not self.user.editable:
            msg = "Changing the password is not allowed for this account."
            self.add_error("new_password2", msg)
        return cleaned_data

    def __init__(self: "PasswordChangeForm", *args: dict, **kwargs: dict) -> None:
        """Remove autofocus from password change form."""

        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
        self.helper = FormHelper()
        self.helper.add_input(
            CustomSubmit("submit", "Update"),
        )
        self.fields["new_password1"].help_text = None


class CustomSubmit(BaseInput):
    """Custom submit button for crispy forms.

    Overrides button class btn-primary to btn-secondary and adds rounded corners.
    """

    input_type = "submit"
    field_classes = "btn btn-secondary rounded"
