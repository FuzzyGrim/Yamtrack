from django import forms
from django.contrib.auth.forms import (
    UserCreationForm,
    PasswordChangeForm,
    AuthenticationForm,
)
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column
from .models import TV, Season, Manga, Anime, Movie, User

import datetime


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


class MediaForm(forms.ModelForm):

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if status == 'Completed' and not end_date:
            cleaned_data['end_date'] = datetime.date.today()
        elif status == "Watching" and not start_date:
            cleaned_data['start_date'] = datetime.date.today()

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            Row(
                Column("score", css_class="form-group col-md-6 pe-1"),
                Column("progress", css_class="form-group col-md-6 ps-1"),
                css_class="form-row"
            ),
            "status",
            Row(
                Column("start_date", css_class="form-group col-md-6 pe-1"),
                Column("end_date", css_class="form-group col-md-6 ps-1"),
                css_class="form-row"
            ),
            "notes",
        )

    class Meta:
        fields = ["media_id", "score", "progress", "status", "start_date", "end_date", "notes"]
        widgets = {
            "media_id": forms.HiddenInput(),
            "score": forms.NumberInput(attrs={"min": 0, "max": 10, "step": 0.1}),
            "progress": forms.NumberInput(attrs={"min": 0}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class MangaForm(MediaForm):

    class Meta(MediaForm.Meta):
        model = Manga


class AnimeForm(MediaForm):
    class Meta(MediaForm.Meta):
        model = Anime


class MovieForm(MediaForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        # movies don"t have progress, score will fill whole row
        self.helper.layout = Layout(
            "media_id",
            "score",
            "status",
            Row(
                Column("start_date", css_class="form-group col-md-6 pe-1"),
                Column("end_date", css_class="form-group col-md-6 ps-1"),
                css_class="form-row"
            ),
            "notes",
        )

    class Meta(MediaForm.Meta):
        model = Movie
        exclude = ("progress",)


class TVForm(MediaForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "score",
            "notes",
        )

    class Meta(MediaForm.Meta):
        model = TV
        exclude = ("progress", "status", "start_date", "end_date")


class SeasonForm(MediaForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "score",
            "status",
            "notes",
        )

    class Meta(MediaForm.Meta):
        model = Season
        exclude = ("progress", "start_date", "end_date")
