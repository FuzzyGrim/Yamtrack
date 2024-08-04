from app.models import MEDIA_TYPES, READABLE_MEDIA_TYPES
from django import forms
from django_select2 import forms as s2forms

from lists.models import CustomList


class CollaboratorsWidget(s2forms.ModelSelect2MultipleWidget):
    """Custom widget for selecting multiple users."""

    search_fields = ["username__icontains"]


class CustomListForm(forms.ModelForm):
    """Form for creating new custom lists."""

    class Meta:
        """Bind form to model."""

        model = CustomList
        fields = ["name", "description", "collaborators"]
        widgets = {
            "collaborators": CollaboratorsWidget(
                attrs={
                    "data-minimum-input-length": 1,
                    "data-placeholder": "Add users",
                    "data-allow-clear": "false",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form with the owner."""
        self.owner = kwargs.pop("owner", None)
        super().__init__(*args, **kwargs)

    def clean(self):
        """Check if a list with the same name already exists for the user."""
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        if (
            name
            and self.owner
            and CustomList.objects.filter(name=name, owner=self.owner).exists()
        ):
            msg = "A list with this name already exists."
            raise forms.ValidationError(msg)
        return cleaned_data


class FilterListItemsForm(forms.Form):
    """Form for filtering media on media list view."""

    media_type = forms.ChoiceField(
        choices=[("all", "All")]
        + [
            (media_type, READABLE_MEDIA_TYPES[media_type]) for media_type in MEDIA_TYPES
        ],
        initial="all",
    )

    sort = forms.ChoiceField(
        choices=[
            ("title", "Title"),
            ("date_added", "Date Added"),
        ],
        initial="date_added",
    )
