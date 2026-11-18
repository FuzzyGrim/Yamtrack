from django import forms
from django.core.exceptions import ValidationError
from django.db import models


class ImportFrequency(models.TextChoices):
    """Choices for import frequency."""

    ONCE = "once", "Once"
    DAILY = "daily", "Daily"
    TWO_DAYS = "2days", "Every 2 days"


class ImportMode(models.TextChoices):
    """Choices for import mode."""

    NEW = "new", "New"
    OVERWRITE = "overwrite", "Overwrite"


class CalibreWebNextGenImportDataForm(forms.Form):
    """Class for Calibre-Web-NextGen import form validation."""

    url = forms.URLField(assume_scheme="http")
    username = forms.CharField()
    password = forms.CharField()
    frequency = forms.ChoiceField(choices=ImportFrequency)
    mode = forms.ChoiceField(choices=ImportMode)
    time = forms.TimeField(required=False)

    def clean(self):
        """Make time required if frequency is not 'once'."""
        frequency = self.cleaned_data.get("frequency")
        time = self.cleaned_data.get("time")

        if frequency != ImportFrequency.ONCE.value and time is None:
            self.add_error(
                "time",
                ValidationError(
                    "This field is required.",
                    code="required",
                ),
            )

        return self.cleaned_data
