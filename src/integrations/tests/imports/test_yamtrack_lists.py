import csv
from io import BytesIO, StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Item, MediaTypes, Movie, Sources
from app.providers import services
from integrations.exports import generate_list_rows
from integrations.imports import helpers, yamtrack_lists
from lists.models import CustomList, CustomListItem

HEADER = [
    "list_name",
    "list_description",
    "date_added",
    "media_id",
    "source",
    "media_type",
    "title",
    "image",
    "season_number",
    "episode_number",
]


def list_row(name, description=""):
    """Return the values of a list row."""
    return [name, description, "", "", "", "list", "", "", "", ""]


def movie_row(name, media_id="1", title="Movie", **columns):
    """Return the values of an item row with a movie."""
    values = {
        "list_name": name,
        "media_id": media_id,
        "source": Sources.TMDB.value,
        "media_type": MediaTypes.MOVIE.value,
        "title": title,
        "image": "https://image.url",
        **columns,
    }
    return [values.get(column, "") for column in HEADER]


def make_file(*rows, header=HEADER):
    """Return a CSV file with the rows."""
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return BytesIO(buffer.getvalue().encode("utf-8"))


class ImportYamtrackLists(TestCase):
    """Test importing custom lists from Yamtrack lists CSV."""

    def setUp(self):
        """Create users for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.other_user = get_user_model().objects.create_user(
            **{**self.credentials, "username": "other"},
        )

    def add_movie(self, custom_list, media_id="1"):
        """Add a movie to the list."""
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={"title": "Movie", "image": "https://image.url"},
        )
        return CustomListItem.objects.create(item=item, custom_list=custom_list)

    def test_import_creates_lists_and_items(self):
        """Test the lists and items in the file are created for the user."""
        counts, warnings = yamtrack_lists.importer(
            make_file(
                list_row("Favorites", "The best"),
                movie_row("Favorites"),
                list_row("Empty"),
            ),
            self.user,
            "new",
        )

        self.assertEqual(warnings, "")
        self.assertEqual(counts, {MediaTypes.MOVIE.value: 1})
        favorites = CustomList.objects.get(name="Favorites", owner=self.user)
        self.assertEqual(favorites.description, "The best")
        self.assertEqual(favorites.items.get().title, "Movie")
        self.assertEqual(CustomList.objects.get(name="Empty").items.count(), 0)
        self.assertFalse(Movie.objects.filter(user=self.user).exists())

    def test_import_uses_row_order_and_ignores_date_added(self):
        """Test items are added in the order of the rows at the import time."""
        media_ids = ["3", "1", "2"]

        yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                *[
                    movie_row("Favorites", media_id=media_id, date_added="2023-01-16")
                    for media_id in media_ids
                ],
            ),
            self.user,
            "new",
        )

        list_items = CustomListItem.objects.order_by("date_added")
        self.assertEqual(
            [list_item.item.media_id for list_item in list_items],
            media_ids,
        )
        self.assertGreater(list_items[0].date_added.year, 2023)

    def test_import_requires_list_row_before_items(self):
        """Test items without a list row before them are skipped with a warning."""
        existing = CustomList.objects.create(name="Existing", owner=self.user)

        counts, warnings = yamtrack_lists.importer(
            make_file(
                movie_row("Late", title="Before its list"),
                list_row("Late"),
                movie_row("Missing", title="No list"),
                movie_row("Existing", title="Only in Yamtrack"),
            ),
            self.user,
            "new",
        )

        self.assertEqual(counts, {})
        self.assertIn("Missing list row for Late", warnings)
        self.assertIn("Missing list row for Missing", warnings)
        self.assertIn("Missing list row for Existing", warnings)
        self.assertFalse(Item.objects.exists())
        self.assertEqual(CustomList.objects.get(name="Late").items.count(), 0)
        self.assertEqual(existing.items.count(), 0)
        self.assertFalse(CustomList.objects.filter(name="Missing").exists())

    def test_import_duplicate_item_in_file(self):
        """Test an item repeated in a list is only added once."""
        counts, _ = yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                movie_row("Favorites"),
                movie_row("Favorites"),
            ),
            self.user,
            "new",
        )

        self.assertEqual(counts, {MediaTypes.MOVIE.value: 1})
        self.assertEqual(CustomListItem.objects.count(), 1)

    def test_new_mode_keeps_existing_list(self):
        """Test "new" mode only adds the missing items to an existing list."""
        custom_list = CustomList.objects.create(
            name="Favorites",
            description="Mine",
            owner=self.user,
        )
        self.add_movie(custom_list, media_id="1")
        self.add_movie(custom_list, media_id="2")

        counts, _ = yamtrack_lists.importer(
            make_file(
                list_row("Favorites", "From file"),
                movie_row("Favorites", media_id="2"),
                movie_row("Favorites", media_id="3"),
            ),
            self.user,
            "new",
        )

        custom_list.refresh_from_db()
        self.assertEqual(counts, {MediaTypes.MOVIE.value: 1})
        self.assertEqual(custom_list.description, "Mine")
        self.assertEqual(
            set(custom_list.items.values_list("media_id", flat=True)),
            {"1", "2", "3"},
        )

    def test_overwrite_mode_replaces_existing_list(self):
        """Test "overwrite" mode replaces the items and description of a list."""
        custom_list = CustomList.objects.create(
            name="Favorites",
            description="Mine",
            owner=self.user,
        )
        self.add_movie(custom_list, media_id="1")
        other_list = CustomList.objects.create(name="Other", owner=self.user)
        self.add_movie(other_list, media_id="1")

        yamtrack_lists.importer(
            make_file(
                list_row("Favorites", "From file"),
                movie_row("Favorites", media_id="2"),
            ),
            self.user,
            "overwrite",
        )

        custom_list.refresh_from_db()
        self.assertEqual(custom_list.description, "From file")
        self.assertEqual(custom_list.items.get().media_id, "2")
        self.assertEqual(other_list.items.count(), 1)

    def test_overwrite_mode_empties_list_without_items(self):
        """Test a list row with no item rows empties the list."""
        custom_list = CustomList.objects.create(name="Favorites", owner=self.user)
        self.add_movie(custom_list)

        yamtrack_lists.importer(
            make_file(list_row("Favorites")),
            self.user,
            "overwrite",
        )

        self.assertEqual(custom_list.items.count(), 0)

    def test_import_only_changes_owned_lists(self):
        """Test lists of other users with the same name are left alone."""
        shared = CustomList.objects.create(name="Favorites", owner=self.other_user)
        shared.collaborators.add(self.user)
        self.add_movie(shared, media_id="1")

        yamtrack_lists.importer(
            make_file(list_row("Favorites"), movie_row("Favorites", media_id="2")),
            self.user,
            "overwrite",
        )

        self.assertEqual(shared.items.get().media_id, "1")
        owned = CustomList.objects.get(owner=self.user)
        self.assertEqual(owned.items.get().media_id, "2")

    def test_import_existing_item_metadata(self):
        """Test only "overwrite" mode replaces the metadata of an existing item."""
        Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Original",
            image="https://image.url",
        )
        rows = (list_row("Favorites"), movie_row("Favorites", title="Changed"))

        yamtrack_lists.importer(make_file(*rows), self.user, "new")
        self.assertEqual(Item.objects.get().title, "Original")

        yamtrack_lists.importer(make_file(*rows), self.user, "overwrite")
        self.assertEqual(Item.objects.get().title, "Changed")

    @patch("app.providers.services.get_media_metadata")
    def test_import_fetches_missing_metadata(self, mock_metadata):
        """Test a new item without a title or image gets them from the provider."""
        mock_metadata.return_value = {
            "title": "Fetched",
            "image": "https://fetched.url",
        }

        _, warnings = yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                movie_row("Favorites", title="", image=""),
            ),
            self.user,
            "new",
        )

        self.assertEqual(warnings, "")
        mock_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "1",
            Sources.TMDB.value,
            [None],
            None,
        )
        item = Item.objects.get()
        self.assertEqual(item.title, "Fetched")
        self.assertEqual(item.image, "https://fetched.url")

    @patch("app.providers.services.search")
    def test_import_searches_by_title_without_media_id(self, mock_search):
        """Test an item without an ID gets it from the first search result."""
        mock_search.return_value = {
            "results": [
                {
                    "title": "Found",
                    "source": Sources.TMDB.value,
                    "media_id": "99",
                    "image": "https://found.url",
                },
            ],
        }

        _, warnings = yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                movie_row("Favorites", media_id="", title="Search", image=""),
            ),
            self.user,
            "new",
        )

        self.assertEqual(warnings, "")
        item = CustomList.objects.get().items.get()
        self.assertEqual(item.media_id, "99")
        self.assertEqual(item.title, "Found")
        self.assertEqual(item.image, "https://found.url")

    def test_import_without_media_id_and_title_is_rejected(self):
        """Test a row with nothing to find its metadata with raises an error."""
        with self.assertRaises(helpers.MediaImportUnexpectedError):
            yamtrack_lists.importer(
                make_file(
                    list_row("Favorites"),
                    movie_row("Favorites", media_id="", title=""),
                ),
                self.user,
                "new",
            )

    @patch("app.providers.services.get_media_metadata")
    def test_import_provider_error_becomes_warning(self, mock_metadata):
        """Test a failed lookup skips the row while the others are imported."""
        mock_metadata.side_effect = services.ProviderAPIError(
            Sources.TMDB.value,
            Exception("Not found"),
        )

        counts, warnings = yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                movie_row("Favorites", media_id="404", title=""),
                movie_row("Favorites", media_id="2"),
            ),
            self.user,
            "new",
        )

        self.assertEqual(counts, {MediaTypes.MOVIE.value: 1})
        self.assertIn("Error processing entry with ID 404", warnings)
        self.assertEqual(Item.objects.get().media_id, "2")

    @patch("app.providers.services.get_media_metadata")
    def test_import_manual_item_uses_placeholder(self, mock_metadata):
        """Test a manual item without an image gets the placeholder image."""
        yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                movie_row("Favorites", source=Sources.MANUAL.value, image=""),
            ),
            self.user,
            "new",
        )

        mock_metadata.assert_not_called()
        self.assertEqual(Item.objects.get().image, settings.IMG_NONE)

    def test_invalid_rows_become_warnings(self):
        """Test invalid item rows are skipped while the valid ones are imported."""
        counts, warnings = yamtrack_lists.importer(
            make_file(
                list_row("Favorites"),
                movie_row("Favorites"),
                movie_row("Favorites", media_id="3", title="Source", source="bad"),
                movie_row("Favorites", media_id="4", title="Type", media_type="bad"),
                movie_row("Favorites", media_id="5", title="Image", image="bad"),
                movie_row("Favorites", media_id="6", title="Season", season_number="1"),
                list_row(""),
            ),
            self.user,
            "new",
        )

        self.assertEqual(counts, {MediaTypes.MOVIE.value: 1})
        self.assertEqual(Item.objects.count(), 1)
        for text in ("Source", "Type", "Image", "Season", "Missing list name"):
            self.assertIn(text, warnings)

    def test_import_media_csv_is_rejected(self):
        """Test a file without the list columns raises an error."""
        file = make_file(
            ["1", "tmdb", "movie"],
            header=["media_id", "source", "media_type"],
        )

        with self.assertRaises(helpers.MediaImportUnexpectedError):
            yamtrack_lists.importer(file, self.user, "new")

    def test_import_binary_file_is_rejected(self):
        """Test a file that isn't text raises an error."""
        with self.assertRaises(helpers.MediaImportError):
            yamtrack_lists.importer(BytesIO(b"\xff\xfe\x00"), self.user, "new")

    def test_round_trip(self):
        """Test importing an export recreates the lists on another account."""
        custom_list = CustomList.objects.create(
            name='Quotes, "and" commas',
            description="Line one\nLine two",
            owner=self.other_user,
        )
        self.add_movie(custom_list)
        season = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Show",
            image="https://image.url",
            season_number=1,
        )
        CustomListItem.objects.create(item=season, custom_list=custom_list)
        CustomList.objects.create(name="Empty", owner=self.other_user)

        exported = "".join(generate_list_rows(self.other_user)).encode("utf-8")
        counts, warnings = yamtrack_lists.importer(
            BytesIO(exported),
            self.user,
            "new",
        )

        self.assertEqual(warnings, "")
        self.assertEqual(
            counts,
            {MediaTypes.MOVIE.value: 1, MediaTypes.SEASON.value: 1},
        )
        imported = CustomList.objects.get(owner=self.user, name=custom_list.name)
        self.assertEqual(imported.description, "Line one\nLine two")
        self.assertEqual(imported.items.get(season_number=1), season)
        self.assertTrue(
            CustomList.objects.filter(owner=self.user, name="Empty").exists(),
        )
