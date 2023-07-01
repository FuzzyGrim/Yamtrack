from django.test import TestCase

from app.forms import MangaForm, TVForm, MovieForm, SeasonForm
from app.models import User


class ValidForm(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test", password="12345")

    def test_valid_media_form(self):
        form_data = {
            'media_id': 1,
            'media_type': 'manga',
            'title': 'Sample',
            'image': 'sample.jpg',
            'score': 7.5,
            'progress': 25,
            'status': 'Paused',
            'start_date': '2023-02-01',
            'end_date': '2023-06-30',
            'user': self.user.id,
            'notes': 'New notes'
        }
        form = MangaForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_movie_form(self):
        form_data = {
            'media_id': 1,
            'media_type': 'movie',
            'title': 'Sample',
            'image': 'sample.jpg',
            'score': 7.5,
            'status': 'Paused',
            'start_date': '2023-02-01',
            'end_date': '2023-06-30',
            'user': self.user.id,
            'notes': 'New notes'
        }
        form = MovieForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_tv_form(self):
        form_data = {
            'media_id': 1,
            'media_type': 'tv',
            'title': 'Sample',
            'image': 'sample.jpg',
            'score': 7.5,
            'user': self.user.id,
            'notes': 'New notes'
        }
        form = TVForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_season_form(self):
        form_data = {
            'media_id': 1,
            'media_type': 'season',
            'title': 'Sample',
            'image': 'sample.jpg',
            'score': 7.5,
            "status": "Completed",
            'season_number': 1,
            'user': self.user.id,
            'notes': 'New notes'
        }
        form = SeasonForm(data=form_data)
        self.assertTrue(form.is_valid())
