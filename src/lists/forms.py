from django import forms
from django_select2 import forms as s2forms
from django.utils.translation import gettext_lazy as _

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
        labels = {
            "name": _("Name"),
            "description": _("Description"),
            "collaborators": _("Collaborators"),
        }
        widgets = {
            "collaborators": CollaboratorsWidget(
                attrs={
                    "data-minimum-input-length": 1,
                    "data-placeholder": _("Search users to add..."),
                    "data-allow-clear": "false",
                },
            ),
        }
