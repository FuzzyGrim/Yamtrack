import csv
from datetime import UTC, datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem


class ExportListsCSVTest(TestCase):
    """Test exporting custom lists to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.credentials_other = {"username": "other", "password": "12345"}
        self.other_user = get_user_model().objects.create_user(
            **self.credentials_other,
        )

        self.item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )

    def get_rows(self):
        """Return the rows of the exported CSV."""
        response = self.client.get(reverse("export_lists_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = b"".join(response.streaming_content).decode("utf-8")
        return list(csv.DictReader(StringIO(content, newline="")))

    def test_export_list_and_item_rows(self):
        """Test a list is exported as a list row followed by its items."""
        custom_list = CustomList.objects.create(
            name="Favorites",
            description="The best",
            owner=self.user,
        )
        CustomListItem.objects.create(item=self.item, custom_list=custom_list)

        list_row, item_row = self.get_rows()

        self.assertEqual(list_row["media_type"], "list")
        self.assertEqual(list_row["list_name"], "Favorites")
        self.assertEqual(list_row["list_description"], "The best")
        self.assertEqual(list_row["title"], "")
        self.assertEqual(item_row["list_name"], "Favorites")
        self.assertEqual(item_row["list_description"], "")
        self.assertEqual(item_row["media_id"], "10494")
        self.assertEqual(item_row["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(item_row["title"], "Perfect Blue")
        self.assertNotEqual(item_row["date_added"], "")

    def test_export_empty_list(self):
        """Test a list without items is exported as a single row."""
        CustomList.objects.create(name="Empty", owner=self.user)

        rows = self.get_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["list_name"], "Empty")

    def test_export_page_has_lists_button(self):
        """Test the export page links to the lists export."""
        response = self.client.get(reverse("export_data"))

        self.assertContains(response, reverse("export_lists_csv"))

    def test_export_only_owned_lists(self):
        """Test the export only has the lists owned by the user."""
        CustomList.objects.create(name="Owned", owner=self.user)
        shared = CustomList.objects.create(name="Shared", owner=self.other_user)
        shared.collaborators.add(self.user)

        names = [row["list_name"] for row in self.get_rows()]

        self.assertEqual(names, ["Owned"])


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            notes="Favorite episode",
        )

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://image.url",
        )
        Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_manga = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="https://image.url",
        )
        Manga.objects.create(
            item=item_manga,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        Game.objects.create(
            item=item_game,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        Book.objects.create(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the streaming content and decode it
        content = b"".join(response.streaming_content).decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(content))

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(movie__user=self.user)
                | Q(season__user=self.user)
                | Q(episode__related_season__user=self.user)
                | Q(anime__user=self.user)
                | Q(manga__user=self.user)
                | Q(game__user=self.user)
                | Q(book__user=self.user),
            ).values_list("media_id", flat=True),
        )

        # Verify each row in the CSV exists in the database
        for row in reader:
            media_id = row["media_id"]
            self.assertIn(media_id, db_media_ids)
            if row["media_type"] == MediaTypes.EPISODE.value:
                self.assertEqual(row["notes"], "Favorite episode")
