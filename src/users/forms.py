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
    """Override the default login form."""

    # Override the default error messages
    error_messages = {
        "invalid_login": "Please enter a correct username and password.",
        "inactive": "This account is inactive.",
    }

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(),
    )

    def __init__(self, *args, **kwargs):
        """Add crispy form helper to add submit button."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit(
                "submit",
                "Log In",
                css_class="btn btn-primary w-100 mt-3",
            ),
        )


class UserRegisterForm(UserCreationForm):
    """Override the default registration form."""

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(),
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(),
        label="Password",
    )

    password2 = forms.CharField(
        widget=forms.PasswordInput(),
        label="Password confirmation",
    )

    class Meta:
        """Only include the username and password fields."""

        model = User
        fields = ["username", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        """Add crispy form helper to add submit button."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit(
                "submit",
                "Sign Up",
                css_class="btn btn-primary w-100 mt-3",
            ),
        )


class UserUpdateForm(forms.ModelForm):
    """Custom form for updating username."""

    def clean(self):
        """Check if the user is demo before changing the password."""

        cleaned_data = super().clean()
        if self.instance.is_demo:
            msg = "Changing the username is not allowed for the demo account."
            self.add_error("username", msg)
        return cleaned_data

    def __init__(self, *args, **kwargs):
        """Add crispy form helper to add submit button."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit(
                "submit",
                "Update",
                css_class="btn btn-secondary",
            ),
        )
        self.fields["username"].help_text = None

    class Meta:
        """Only allow updating username."""

        model = User
        fields = ["username"]


class PasswordChangeForm(PasswordChangeForm):
    """Custom form for changing password."""

    def clean(self):
        """Check if the user is demo before changing the password."""

        cleaned_data = super().clean()
        if self.user.is_demo:
            msg = "Changing the password is not allowed for the demo account."
            self.add_error("new_password2", msg)
        return cleaned_data

    def __init__(self, *args, **kwargs):
        """Remove autofocus from password change form."""

        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit(
                "submit",
                "Update",
                css_class="btn btn-secondary",
            ),
        )
        self.fields["new_password1"].help_text = None
