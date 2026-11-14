from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import cache_utils, media_list_entry_grouping
from app.media_list_entry_grouping import entry_grouping_mode
from app.models import (
    TV,
    BasicMedia,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from users.models import MediaStatusChoices


class MediaListEntryGroupingTests(TestCase):
    def setUp(self):
        self.credentials = {"username": "entry-grouping", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.movie_url = reverse("medialist", args=[MediaTypes.MOVIE.value])
        self.movie_item = Item.objects.create(
            media_id="entry-grouping-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Entry Grouping Movie",
            image="http://example.com/entry-grouping.jpg",
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=self.movie_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=timezone.now() - timedelta(days=2),
                ),
                Movie(
                    item=self.movie_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=2,
                    end_date=timezone.now() - timedelta(days=1),
                ),
            ],
        )
        cache.clear()

    def _manager_list(self, *, sort_filter="title", direction="asc"):
        return BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter=sort_filter,
            direction=direction,
        )

    def test_default_mode_groups_matching_entries(self):
        media_list = self._manager_list()

        self.assertEqual(len(media_list), 1)
        self.assertEqual(media_list[0].repeats, 2)

    def test_separate_mode_returns_raw_progress_rows_without_aggregate_values(self):
        with entry_grouping_mode(True):
            media_list = self._manager_list(sort_filter="progress", direction="asc")

        self.assertEqual([entry.progress for entry in media_list], [1, 2])
        self.assertFalse(
            any(hasattr(entry, "aggregated_progress") for entry in media_list)
        )

    def test_entry_modes_have_distinct_processed_list_cache_keys(self):
        key_args = {
            "user_id": self.user.id,
            "media_type": MediaTypes.MOVIE.value,
            "sort_filter": "title",
            "direction": "asc",
            "status_filter": "",
            "search_query": "",
            "rating_filter": "all",
        }

        with entry_grouping_mode(False):
            grouped_key = cache_utils.build_media_list_cache_key(**key_args)
        with entry_grouping_mode(True):
            separate_key = cache_utils.build_media_list_cache_key(**key_args)

        self.assertNotEqual(grouped_key, separate_key)
        self.assertTrue(grouped_key.endswith("_entry_grouping_grouped"))
        self.assertTrue(separate_key.endswith("_entry_grouping_separate"))

    def test_get_query_cannot_change_the_saved_preference(self):
        response = self.client.get(f"{self.movie_url}?separate_plays=1")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.movie_show_each_play)

    def test_post_updates_each_supported_preference_and_preserves_the_full_view(self):
        preference_fields = {
            MediaTypes.MOVIE.value: "movie_show_each_play",
            MediaTypes.ANIME.value: "anime_show_each_play",
            MediaTypes.MANGA.value: "manga_show_each_play",
            MediaTypes.GAME.value: "game_show_each_play",
            MediaTypes.BOOK.value: "book_show_each_play",
        }

        for media_type, field_name in preference_fields.items():
            with self.subTest(media_type=media_type):
                endpoint = reverse("medialist_entry_grouping", args=[media_type])
                target = (
                    reverse("medialist", args=[media_type])
                    + "?status=Completed&layout=table&tag=Favorite&tag_mode=not"
                )
                response = self.client.post(
                    endpoint,
                    {"show_separate_entries": "true", "next": target},
                )

                self.assertRedirects(response, target, fetch_redirect_response=False)
                self.user.refresh_from_db()
                self.assertTrue(getattr(self.user, field_name))

    def test_post_rejects_invalid_values_and_unsupported_media_types(self):
        movie_endpoint = reverse(
            "medialist_entry_grouping",
            args=[MediaTypes.MOVIE.value],
        )
        tv_endpoint = reverse(
            "medialist_entry_grouping",
            args=[MediaTypes.TV.value],
        )

        invalid_value = self.client.post(
            movie_endpoint,
            {"show_separate_entries": "yes"},
        )
        unsupported_type = self.client.post(
            tv_endpoint,
            {"show_separate_entries": "true"},
        )

        self.assertEqual(invalid_value.status_code, 400)
        self.assertEqual(unsupported_type.status_code, 400)

    def test_post_rejects_an_external_return_url(self):
        endpoint = reverse(
            "medialist_entry_grouping",
            args=[MediaTypes.MOVIE.value],
        )

        response = self.client.post(
            endpoint,
            {
                "show_separate_entries": "true",
                "next": "//example.invalid/account",
            },
        )

        self.assertRedirects(
            response,
            self.movie_url,
            fetch_redirect_response=False,
        )

    def test_separate_mode_does_not_reuse_a_grouped_cache_entry(self):
        list_url = f"{self.movie_url}?search=Entry+Grouping+Movie&sort=title"
        grouped_response = self.client.get(list_url)
        self.assertEqual(grouped_response.context["media_list"].paginator.count, 1)

        with mock.patch(
            "app.media_list_entry_grouping_views."
            "cache_utils.clear_media_list_cache_for_user"
        ):
            self.client.post(
                reverse(
                    "medialist_entry_grouping",
                    args=[MediaTypes.MOVIE.value],
                ),
                {"show_separate_entries": "true", "next": list_url},
            )

        separate_response = self.client.get(list_url)
        self.assertEqual(separate_response.context["media_list"].paginator.count, 2)

    def test_tv_backed_anime_uses_the_same_separate_entry_mode(self):
        anime_item = Item.objects.create(
            media_id="grouped-entry-anime",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Grouped Entry Anime",
            image="http://example.com/grouped-entry-anime.jpg",
        )
        TV.objects.bulk_create(
            [
                TV(
                    item=anime_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                ),
            ],
        )
        self.user.anime_library_mode = MediaTypes.ANIME.value
        self.user.anime_show_each_play = True
        self.user.save(
            update_fields=["anime_library_mode", "anime_show_each_play"]
        )
        cache.clear()

        with mock.patch.object(
            media_list_entry_grouping,
            "_get_separate_media_list",
            wraps=media_list_entry_grouping._get_separate_media_list,
        ) as separate_media_list:
            response = self.client.get(
                reverse("medialist", args=[MediaTypes.ANIME.value])
                + "?search=Grouped+Entry+Anime&sort=title",
            )

        called_media_types = {
            call.args[2] for call in separate_media_list.call_args_list
        }

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertIn(MediaTypes.ANIME.value, called_media_types)
        self.assertIn(MediaTypes.TV.value, called_media_types)

    def test_control_hands_the_settings_menu_its_entry_grouping_state(self):
        """The gear is a React island; the server supplies its labels and the grouping form."""
        response = self.client.get(self.movie_url)

        self.assertContains(response, "data-settings-menu-root")
        self.assertContains(response, "View settings")
        self.assertContains(response, "Show matching entries separately")
        self.assertContains(response, '"stateLabel": "Off"')
        self.assertContains(response, '"nextValue": "true"')
