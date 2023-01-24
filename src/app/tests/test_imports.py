from django.test import LiveServerTestCase
from django.test import override_settings

import csv
import shutil
import os

from app.utils import interactions
from app.models import User, Media


class Imports(LiveServerTestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)


    @override_settings(MEDIA_ROOT=("test_Imports/media"))
    def test_import_animelist(self):
        interactions.import_myanimelist("bloodthirstiness", self.user)
        self.assertEqual(Media.objects.filter(user=self.user).count(), 6)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="anime").count(), 4)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="manga").count(), 2)
        self.assertEqual(Media.objects.get(user=self.user, title="Ama Gli Animali").image=="images/none.svg", True)
        self.assertEqual(Media.objects.get(user=self.user, title="FLCL").status=="Paused", True)
        self.assertEqual(Media.objects.get(user=self.user, title="Fire Punch").score==7, True)


    @override_settings(MEDIA_ROOT=("test_Imports/media"))
    def test_import_anilist(self):
        interactions.import_anilist("bloodthirstiness", self.user)
        self.assertEqual(Media.objects.filter(user=self.user).count(), 6)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="anime").count(), 4)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="manga").count(), 2)
        self.assertEqual(Media.objects.get(user=self.user, title="FLCL").status=="Paused", True)
        self.assertEqual(Media.objects.get(user=self.user, title="One Punch-Man").score==9, True)


    @override_settings(MEDIA_ROOT=("test_Imports/media"))
    def test_import_tmdb(self):
        file_path = os.path.join("test_Imports/ratings.csv")
        fields = ["TMDb ID", "IMDb ID", "Type", "Name", "Release Date", "Season Number", "Episode Number", "Rating", "Your Rating", "Date Rated"]
        data = [
            ["634649", "tt10872600", "movie", "Spider-Man: No Way Home", "2021-12-15T00:00:00Z", "", "", "8.022", "7", "2022-12-17T15:50:35Z"],
            ["1668", "tt0108778", "tv", "Friends", "1994-09-22T00:00:00Z", "", "", "8.463", "10", "2022-12-17T16:23:01Z"],
        ]
        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fields)
            writer.writerows(data)

        with open(file_path, "rb") as file:
            interactions.import_tmdb(file, self.user)
            self.assertEqual(Media.objects.filter(user=self.user).count(), 2)
            self.assertEqual(Media.objects.filter(user=self.user, media_type="movie").count(), 1)
            self.assertEqual(Media.objects.filter(user=self.user, media_type="tv").count(), 1)
            self.assertEqual(Media.objects.get(user=self.user, media_id=634649).score==7, True)
            self.assertEqual(Media.objects.get(user=self.user, media_id=1668).score==10, True)


    def tearDownClass():
        try:
            shutil.rmtree("test_Imports")
        except OSError:
            pass