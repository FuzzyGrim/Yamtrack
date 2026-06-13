import apprise
from allauth.account.forms import LoginForm, SignupForm
from django import forms
from django.contrib.auth.forms import (
    PasswordChangeForm,
)
from django.core.exceptions import ValidationError

from .models import User


class CustomLoginForm(LoginForm):
    """Custom login form for django-allauth."""

    def __init__(self, *args, **kwargs):
        """Remove email field and change password2 label."""
        super().__init__(*args, **kwargs)

        self.fields["login"].widget.attrs["placeholder"] = "Enter your username"

        self.fields["password"].widget.attrs["placeholder"] = "Enter your password"


class CustomSignupForm(SignupForm):
    """Custom signup form for django-allauth."""

    def __init__(self, *args, **kwargs):
        """Remove email field and change password2 label."""
        super().__init__(*args, **kwargs)

        del self.fields["email"]

        # Change label and placeholder for password2 field
        self.fields["password2"].label = "Confirm Password"
        self.fields["password2"].widget.attrs["placeholder"] = "Confirm your password"


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
        self.fields["username"].help_text = None

    class Meta:
        """Only allow updating username."""

        model = User
        fields = ["username", "profile_private"]


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
        self.fields["new_password1"].help_text = None


class NotificationSettingsForm(forms.ModelForm):
    """Form for notification settings."""

    class Meta:
        """Form fields for notification settings."""

        model = User
        fields = [
            "notification_urls",
            "daily_digest_enabled",
            "release_notifications_enabled",
        ]
        widgets = {
            "notification_urls": forms.Textarea(
                attrs={
                    "rows": 5,
                    "wrap": "off",
                    "placeholder": "discord://webhook_id/webhook_token\ntgram://bot_token/chat_id",
                },
            ),
        }

    def clean_notification_urls(self):
        """Validate that each URL is a valid Apprise URL."""
        notification_urls = self.cleaned_data.get("notification_urls", "")

        if not notification_urls.strip():
            return notification_urls

        # Create Apprise instance for validation
        apobj = apprise.Apprise()

        # Check each URL
        urls = [url.strip() for url in notification_urls.splitlines() if url.strip()]

        for url in urls:
            if not apobj.add(url):
                message = f"'{url}' is not a valid Apprise URL."
                raise ValidationError(message)

        return notification_urls
