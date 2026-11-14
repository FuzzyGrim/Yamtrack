import json
import re
from datetime import UTC, datetime, time, timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import cache_utils
from app.models import (
    TV,
    Album,
    Anime,
    Artist,
    ArtistTracker,
    BasicMedia,
    Book,
    CollectionEntry,
    Comic,
    ComicIssue,
    Episode,
    Game,
    Item,
    ItemTag,
    Manga,
    MediaTypes,
    Movie,
    Music,
    Season,
    Sources,
    Status,
    Tag,
    Video,
)
from app.templatetags import app_tags
from app.tests.card_props import card_models
from events.models import Event


class MediaListViewTests(TestCase):
    """Test the media list view."""

    def setUp(self):
        """Create a user and log in."""
        # Media-list cache keys are (user_id, media_type, filters) with no data
        # version, and these fixtures use bulk_create, which skips the
        # invalidation signals. Every test reuses user id 1, so a list cached by
        # one test would otherwise be served to the next.
        cache.clear()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        movies_id = ["278", "238", "129", "424", "680"]
        num_completed = 3
        Item.objects.bulk_create(
            [
                Item(
                    media_id=movies_id[i - 1],
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title=f"Test Movie {i}",
                    image="http://example.com/image.jpg",
                )
                for i in range(1, 6)
            ],
        )
        created_items = {
            item.media_id: item
            for item in Item.objects.filter(
                media_id__in=movies_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
            )
        }

        Movie.objects.bulk_create(
            [
                Movie(
                    item=created_items[movies_id[i - 1]],
                    user=self.user,
                    status=(
                        Status.COMPLETED.value
                        if i < num_completed
                        else Status.IN_PROGRESS.value
                    ),
                    progress=1 if i < num_completed else 0,
                    score=i,
                )
                for i in range(1, 6)
            ],
        )

    def test_infinite_scroll_has_no_global_pagination_guard(self):
        """The htmx per-element `once` trigger is the only pagination gate.

        A page-wide in-progress flag swallowed the next trigger when its rows
        arrived inside the re-arm window, stalling the scroll for good.
        """
        response = self.client.get(reverse("medialist", args=["movie"]))
        self.assertNotContains(response, "paginationInProgress")
        self.assertNotContains(response, "PAGINATION_")

    def _create_game_entry(
        self,
        media_id,
        title,
        *,
        hltb_minutes=None,
        igdb_seconds=None,
    ):
        provider_game_lengths = {}
        provider_game_lengths_source = ""
        provider_game_lengths_match = ""

        if hltb_minutes:
            provider_game_lengths = {
                "active_source": "hltb",
                "hltb": {
                    "game_id": int(media_id) if str(media_id).isdigit() else 0,
                    "summary": {
                        "all_styles_minutes": hltb_minutes,
                    },
                    "counts": {
                        "all_styles": 12,
                    },
                },
            }
            provider_game_lengths_source = "hltb"
            provider_game_lengths_match = "exact_title_year"
        elif igdb_seconds:
            provider_game_lengths = {
                "active_source": "igdb",
                "igdb": {
                    "game_id": int(media_id) if str(media_id).isdigit() else 0,
                    "summary": {
                        "normally_seconds": igdb_seconds,
                        "count": 4,
                    },
                    "raw": [],
                },
            }
            provider_game_lengths_source = "igdb"
            provider_game_lengths_match = "igdb_fallback"

        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title=title,
            image="http://example.com/game.jpg",
            provider_game_lengths=provider_game_lengths,
            provider_game_lengths_source=provider_game_lengths_source,
            provider_game_lengths_match=provider_game_lengths_match,
        )
        Game.objects.bulk_create(
            [
                Game(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=60,
                ),
            ],
        )
        return item

    def _create_movie_runtime_entry(self, media_id, title, runtime_minutes=None):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/movie-runtime.jpg",
            runtime_minutes=runtime_minutes,
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=0,
                ),
            ],
        )
        return item

    def _create_movie_popularity_entry(self, media_id, title, rank):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/movie-popularity.jpg",
            trakt_rating=8.0,
            trakt_rating_count=1000,
            trakt_popularity_score=1000.0 / rank,
            trakt_popularity_rank=rank,
            trakt_popularity_fetched_at=timezone.now() - timedelta(days=1),
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=0,
                ),
            ],
        )
        return item

    def _create_movie_critic_rating_entry(self, media_id, title, provider_rating=None):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/movie-critic-rating.jpg",
            provider_rating=provider_rating,
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=0,
                ),
            ],
        )
        return item

    def _create_tv_runtime_entry(
        self, media_id, title, episode_runtimes, *, progress=0
    ):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=title,
            image="http://example.com/tv-runtime.jpg",
        )
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title=f"{title} Season 1",
            image="http://example.com/tv-season.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )

        released_at = timezone.now() - timedelta(days=30)
        for episode_number, runtime_minutes in enumerate(episode_runtimes, start=1):
            episode_item = Item.objects.create(
                media_id=str(media_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"{title} Episode {episode_number}",
                image="http://example.com/tv-episode.jpg",
                season_number=1,
                episode_number=episode_number,
                runtime_minutes=runtime_minutes,
                release_datetime=released_at + timedelta(days=episode_number),
            )
            if episode_number <= progress:
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=timezone.now() - timedelta(days=episode_number),
                )

        return item

    def _create_tv_seasonal_entry(self, media_id, title, season_configs):
        """Create a TV show with explicit per-season status and release counts."""
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=title,
            image="http://example.com/tv-seasonal.jpg",
        )
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        now = timezone.now()
        for season_config in season_configs:
            season_number = season_config["season_number"]
            season_item = Item.objects.create(
                media_id=str(media_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=f"{title} Season {season_number}",
                image="http://example.com/tv-seasonal-season.jpg",
                season_number=season_number,
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                related_tv=tv,
                status=season_config["status"],
            )

            released_episodes = season_config["released_episodes"]
            watched_episodes = season_config.get("watched_episodes", 0)

            for episode_number in range(1, released_episodes + 1):
                episode_item = Item.objects.create(
                    media_id=str(media_id),
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title=f"{title} S{season_number:02d}E{episode_number:02d}",
                    image="http://example.com/tv-seasonal-episode.jpg",
                    season_number=season_number,
                    episode_number=episode_number,
                    release_datetime=now - timedelta(days=episode_number),
                )
                if episode_number <= watched_episodes:
                    Episode.objects.create(
                        item=episode_item,
                        related_season=season,
                        end_date=now - timedelta(days=episode_number),
                    )

        return tv

    def _create_anime_runtime_entry(
        self,
        media_id,
        title,
        *,
        runtime_minutes,
        episode_count,
        progress=0,
    ):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title=title,
            image="http://example.com/anime-runtime.jpg",
            runtime_minutes=runtime_minutes,
        )
        Anime.objects.bulk_create(
            [
                Anime(
                    item=item,
                    user=self.user,
                    status=Status.PLANNING.value,
                    progress=progress,
                ),
            ],
        )
        Event.objects.create(
            item=item,
            content_number=episode_count,
            datetime=timezone.now() - timedelta(days=1),
        )
        return item

    def _create_tv_next_episode_air_date_entry(
        self,
        media_id,
        title,
        episode_release_datetimes,
        *,
        progress=0,
        library_media_type=None,
    ):
        item_kwargs = {
            "media_id": str(media_id),
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": title,
            "image": "http://example.com/tv-next-episode.jpg",
        }
        if library_media_type is not None:
            item_kwargs["library_media_type"] = library_media_type

        item = Item.objects.create(**item_kwargs)
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item_kwargs = {
            "media_id": str(media_id),
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.SEASON.value,
            "title": f"{title} Season 1",
            "image": "http://example.com/tv-next-episode-season.jpg",
            "season_number": 1,
        }
        if library_media_type is not None:
            season_item_kwargs["library_media_type"] = library_media_type

        season_item = Item.objects.create(**season_item_kwargs)
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )

        watched_at = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        for episode_number, release_datetime in enumerate(
            episode_release_datetimes, start=1
        ):
            event_datetime = release_datetime
            if event_datetime is None:
                event_datetime = timezone.datetime.min.replace(
                    tzinfo=timezone.get_current_timezone(),
                )

            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=event_datetime,
                notification_sent=False,
            )

            episode_item_kwargs = {
                "media_id": str(media_id),
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "title": f"{title} Episode {episode_number}",
                "image": "http://example.com/tv-next-episode-episode.jpg",
                "season_number": 1,
                "episode_number": episode_number,
            }
            if library_media_type is not None:
                episode_item_kwargs["library_media_type"] = library_media_type
            if release_datetime is not None:
                episode_item_kwargs["release_datetime"] = release_datetime

            episode_item = Item.objects.create(**episode_item_kwargs)
            if episode_number <= progress:
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=watched_at - timedelta(days=episode_number),
                )

        return tv, season

    def _create_anime_next_episode_air_date_entry(
        self,
        media_id,
        title,
        episode_air_dates,
        *,
        progress=0,
        status=Status.IN_PROGRESS.value,
    ):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title=title,
            image="http://example.com/anime-next-episode.jpg",
        )
        Anime.objects.create(
            item=item,
            user=self.user,
            status=status,
            progress=progress,
        )

        for episode_number, air_datetime in episode_air_dates:
            Event.objects.create(
                item=item,
                content_number=episode_number,
                datetime=air_datetime,
                notification_sent=False,
            )

        return item

    def test_media_list_view(self):
        """Test the media list view displays media items."""
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

        self.assertIn("media_list", response.context)
        self.assertEqual(response.context["media_list"].paginator.count, 5)

        self.assertIn("sort_choices", response.context)
        self.assertIn("status_choices", response.context)
        self.assertEqual(response.context["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(
            response.context["media_type_plural"],
            app_tags.media_type_readable_plural(MediaTypes.MOVIE.value).lower(),
        )

    def test_video_media_list_shows_channel_and_length(self):
        """The Videos list renders and each card shows its channel and length."""
        item = Item.objects.create(
            media_id="dQw4w9WgXcQ",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            title="Never Gonna Give You Up",
            image=settings.IMG_NONE,
        )
        Video.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            channel="Rick Astley",
            length_seconds=213,
        )

        response = self.client.get(reverse("medialist", args=[MediaTypes.VIDEO.value]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        lines = [
            " · ".join(part["text"] for part in line["parts"])
            for model in card_models(response.content)
            for line in model["lines"]
        ]
        self.assertIn("Rick Astley · 3:33", lines)
        self.assertContains(response, 'class="media-grid media-grid-video"')

    def test_season_media_list_with_status_filter_does_not_500(self):
        """Season rows derive end_date from episodes, so the SQL latest-status
        subquery must not be used for them (#1222).
        """
        self._create_tv_runtime_entry("season-status-filter", "Season Status TV", [24])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.SEASON.value]),
            {"status": Status.IN_PROGRESS.value, "sort": "title"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Season Status TV Season 1")

    def test_default_media_list_excludes_untracked_collection_entries(self):
        item = Item.objects.create(
            media_id="untracked-manga-default",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Untracked Manga Default",
            image="http://example.com/untracked-manga.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=item,
            media_type="paperback",
        )

        response = self.client.get(reverse("medialist", args=[MediaTypes.MANGA.value]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 0)
        self.assertNotContains(response, "Untracked Manga Default")

    def test_no_status_media_list_includes_untracked_collection_entries(self):
        item = Item.objects.create(
            media_id="untracked-manga-no-status",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Untracked Manga No Status",
            image="http://example.com/untracked-manga.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=item,
            media_type="paperback",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MANGA.value]) + "?status=no_status",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertContains(response, "Untracked Manga No Status")

    def _statusless_manga(self, score=None):
        """A rating-only media row: a score with no tracking status."""
        item = Item.objects.create(
            media_id="statusless-manga",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Statusless Manga",
            image="http://example.com/statusless-manga.jpg",
        )
        return Manga.objects.create(
            item=item,
            user=self.user,
            status=None,
            score=score,
        )

    def test_statusless_media_is_hidden_from_every_status_view(self):
        self._statusless_manga()

        for status_filter in ("All", *Status.values):
            response = self.client.get(
                reverse("medialist", args=[MediaTypes.MANGA.value]),
                {"status": status_filter},
            )

            self.assertEqual(response.status_code, 200)
            self.assertNotContains(
                response,
                "Statusless Manga",
                msg_prefix=f"status={status_filter}",
            )

    def test_statusless_media_appears_under_no_status_with_its_score(self):
        self._statusless_manga(score=8)

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MANGA.value]) + "?status=no_status",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Statusless Manga")

        entry = response.context["media_list"].object_list[0]
        # The tracker row is attached, so the score renders, but the entry still
        # reads as statusless for the "No Status" chip.
        self.assertIsNotNone(entry.media)
        self.assertEqual(entry.score, 8)
        self.assertTrue(entry.is_statusless)

    def test_music_media_list_uses_canonical_artist_links(self):
        """Music list should render artist cells with canonical shared-detail URLs."""
        artist = Artist.objects.create(name="List Artist")
        album = Album.objects.create(title="List Album", artist=artist)
        item = Item.objects.create(
            media_id="list-track-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="List Track",
            image="http://example.com/list-track.jpg",
        )
        media = Music.objects.create(
            item=item,
            user=self.user,
            artist=artist,
            album=album,
            status=Status.COMPLETED.value,
            progress=1,
        )

        response = self.client.get(reverse("medialist", args=[MediaTypes.MUSIC.value]))

        self.assertEqual(response.status_code, 200)
        rendered_cell = render_to_string(
            "app/components/cells/artist_name_cell.html",
            {"media": media},
        )
        self.assertIn(
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "list-artist",
                },
            ),
            rendered_cell,
        )

    def test_music_tracks_subview_falls_back_to_album_cover_for_stale_item_image(self):
        """Issue #974: a track's stale/missing Item.image should fall back to the
        album's cover art on the tracks grid, mirroring what History already does.
        """
        artist = Artist.objects.create(name="Fallback Artist")
        album_image = "http://example.com/fallback-album.jpg"
        album = Album.objects.create(
            title="Fallback Album", artist=artist, image=album_image
        )
        item = Item.objects.create(
            media_id="fallback-track-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Fallback Track",
            image=settings.IMG_NONE,
        )
        Music.objects.create(
            item=item,
            user=self.user,
            artist=artist,
            album=album,
            status=Status.COMPLETED.value,
            progress=1,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MUSIC.value]),
            {"subview": "tracks"},
        )

        self.assertEqual(response.status_code, 200)
        entry = response.context["media_list"].object_list[0]
        self.assertEqual(entry.media.card_image_override, album_image)
        self.assertEqual(entry.media.card_image_source, "fallback")
        self.assertContains(response, album_image)

    def test_music_media_list_resolves_artist_country_code_to_name(self):
        """Origin filter options should show country names, not raw ISO codes."""
        artist = Artist.objects.create(
            name="Origin Artist",
            genres=[],
            country="jp",
        )
        ArtistTracker.objects.create(
            user=self.user,
            artist=artist,
            status=Status.COMPLETED.value,
        )

        response = self.client.get(reverse("medialist", args=[MediaTypes.MUSIC.value]))

        self.assertEqual(response.status_code, 200)
        origins = response.context["filter_data"]["origins"]
        self.assertEqual(origins, [{"value": "jp", "label": "Japan"}])

    def test_music_media_list_keeps_artist_genres_as_primary_filter_data(self):
        artist = Artist.objects.create(
            name="Genre Artist",
            genres=["Art Rock"],
            country="gb",
        )
        ArtistTracker.objects.create(
            user=self.user,
            artist=artist,
            status=Status.COMPLETED.value,
        )

        response = self.client.get(reverse("medialist", args=[MediaTypes.MUSIC.value]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filter_data"]["genres"], ["Art Rock"])
        self.assertNotIn("implied_genres", response.context["filter_data"])

    def test_music_media_list_queues_background_artist_image_backfill(self):
        """Missing artist images should be backfilled via a queued Celery task,
        not a synchronous write during the GET request (avoids DB lock 503s
        when this races with a concurrent importer).
        """
        artist = Artist.objects.create(name="No Image Artist", image="")
        ArtistTracker.objects.create(
            user=self.user,
            artist=artist,
            status=Status.COMPLETED.value,
        )

        with mock.patch("app.tasks.prefetch_album_covers_batch.delay") as mock_delay:
            response = self.client.get(
                reverse("medialist", args=[MediaTypes.MUSIC.value])
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with([artist.id], limit_per_artist=5)

        artist.refresh_from_db()
        self.assertEqual(artist.image, "")

    def test_artist_list_filters_keep_facets_before_genre_and_origin_selection(self):
        today = timezone.localdate()
        for name, genre, country, score, status, released in [
            ("Match A", "Rock", "gb", 8, Status.COMPLETED.value, today),
            ("Match B", "Jazz", "jp", 7, Status.COMPLETED.value, today),
            ("Match C", "Rock", "gb", None, Status.COMPLETED.value, today),
            ("Match D", "Rock", "gb", 9, Status.PLANNING.value, today),
            ("Other", "Pop", "us", 8, Status.COMPLETED.value, today),
            (
                "Match Future",
                "Pop",
                "us",
                8,
                Status.COMPLETED.value,
                today + timedelta(days=30),
            ),
        ]:
            artist = Artist.objects.create(
                name=name, genres=[genre], country=country, image="/artist.jpg"
            )
            Album.objects.create(title=name, artist=artist, release_date=released)
            ArtistTracker.objects.create(
                user=self.user, artist=artist, score=score, status=status
            )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MUSIC.value]),
            {
                "status": Status.COMPLETED.value,
                "search": "Match",
                "rating": "rated",
                "release": "released",
                "genre": " rock ",
                "origin": "GB",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [tracker.artist.name for tracker in response.context["media_list"]],
            ["Match A"],
        )
        self.assertEqual(response.context["filter_data"]["genres"], ["Jazz", "Rock"])
        self.assertEqual(
            response.context["filter_data"]["origins"],
            [
                {
                    "value": "gb",
                    "label": "United Kingdom of Great Britain and Northern Ireland",
                },
                {"value": "jp", "label": "Japan"},
            ],
        )

    def test_artist_list_release_sort_uses_earliest_album_in_table(self):
        today = timezone.localdate()
        for name, release_dates in [
            ("Later", [today]),
            ("Earlier", [today, today - timedelta(days=30)]),
            ("Unknown", []),
        ]:
            artist = Artist.objects.create(name=name, image="/artist.jpg")
            ArtistTracker.objects.create(user=self.user, artist=artist)
            for release_date in release_dates:
                Album.objects.create(
                    title=name, artist=artist, release_date=release_date
                )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MUSIC.value]),
            {"layout": "table", "sort": "release_date", "direction": "asc"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["table_type"], "artist")
        self.assertEqual(
            [tracker.artist.name for tracker in response.context["media_list"]],
            ["Earlier", "Later", "Unknown"],
        )
        self.assertEqual(
            response.context["media_list"][0].first_release_date,
            today - timedelta(days=30),
        )

    def test_artist_list_htmx_pagination_only_backfills_visible_artists(self):
        artists = [
            Artist.objects.create(name=f"Artist {i:02d}", image="") for i in range(33)
        ]
        ArtistTracker.objects.bulk_create(
            [ArtistTracker(user=self.user, artist=artist) for artist in artists]
        )
        url = reverse("medialist", args=[MediaTypes.MUSIC.value])
        params = {"sort": "title", "direction": "asc", "page": 2, "layout": "grid"}
        with mock.patch("app.tasks.prefetch_album_covers_batch.delay") as delay:
            response = self.client.get(url, params, HTTP_HX_REQUEST="true")
            repeated = self.client.get(url, params, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertTemplateUsed(response, "app/components/artist_grid_items.html")
        self.assertEqual(response.context["media_list"].number, 2)
        self.assertEqual(response.context["media_list"].paginator.count, 33)
        self.assertEqual(
            [tracker.artist.id for tracker in response.context["media_list"]],
            [artists[-1].id],
        )
        delay.assert_called_once_with([artists[-1].id], limit_per_artist=5)
        self.assertEqual(
            json.loads(response["HX-Trigger"]), {"resultCountUpdated": {"count": 33}}
        )
        boosted = self.client.get(
            url, params, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true"
        )
        self.assertTemplateUsed(boosted, "app/media_list.html")

    def test_artist_list_empty_htmx_target_preserves_redirect_contract(self):
        url = reverse("medialist", args=[MediaTypes.MUSIC.value])
        headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "empty_list"}
        self.assertEqual(self.client.get(url, **headers).status_code, 204)
        artist = Artist.objects.create(name="New Artist", image="/artist.jpg")
        ArtistTracker.objects.create(user=self.user, artist=artist)
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], url)

    def test_movie_grid_aggregates_duplicate_completed_plays(self):
        """Grid cards should show total plays across duplicate completed movie entries."""
        item = Item.objects.get(
            title="Test Movie 1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        existing_play = Movie.objects.get(item=item, user=self.user)
        existing_play.score = 9
        existing_play.save(update_fields=["score"])

        second_date = timezone.now() - timedelta(days=7)
        third_date = timezone.now() - timedelta(days=1)
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    score=None,
                    start_date=second_date,
                    end_date=second_date,
                ),
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    score=9,
                    start_date=third_date,
                    end_date=third_date,
                ),
            ],
        )

        latest = Movie.objects.filter(item=item, user=self.user).order_by("-id").first()
        latest.score = 10
        latest.save(update_fields=["score"])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=grid&search=Test+Movie+1&sort=title&direction=asc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertContains(response, "Test Movie 1")
        self.assertContains(response, "3 plays")

    def test_grid_layout_wires_select_mode_click_guard(self):
        """Grid cards must toggle selection, not navigate, once bulk-select was wired (issue #972)."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=grid",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toggleItemSelected(")

    def test_table_layout_title_link_respects_select_mode(self):
        """Table view's title link must also guard against navigating during select mode (issue #972)."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "if (selectMode) { $event.preventDefault(); toggleItemSelected("
        )

    def test_table_layout_shows_notes_on_main_media_list(self):
        """Notes must render in Table View on the main media list (issue #1010)."""
        movie = Movie.objects.get(item__title="Test Movie 1", user=self.user)
        movie.notes = "My private note"
        movie.save(update_fields=["notes"])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My private note")

    def test_table_layout_shows_capitalized_entry_source(self):
        """The Source column shows each entry's source with display casing (issue #1258)."""
        movie = Movie.objects.get(item__title="Test Movie 1", user=self.user)
        movie.entry_source = "plex"
        movie.save(update_fields=["entry_source"])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "entry_source", [c.key for c in response.context["resolved_columns"]]
        )
        self.assertContains(response, ">Plex</div>")
        self.assertNotContains(response, ">plex</div>")

    def test_movie_grid_counts_completed_plays_when_progress_is_zero(self):
        """Completed movie duplicates should count as plays even when progress is zero."""
        item = Item.objects.get(
            title="Test Movie 1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )

        first_play = Movie.objects.get(item=item, user=self.user)
        first_play.status = Status.COMPLETED.value
        first_play.progress = 1
        first_play.end_date = timezone.now() - timedelta(days=220)
        first_play.save()

        second_date = timezone.now() - timedelta(days=126)
        third_date = timezone.now() - timedelta(days=90)
        fourth_date = timezone.now() - timedelta(days=9)
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=second_date,
                ),
                # Simulate legacy/completed rows where progress was never normalized to 1.
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=0,
                    end_date=third_date,
                    score=9,
                ),
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=0,
                    end_date=fourth_date,
                    score=10,
                ),
            ],
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=grid&search=Test+Movie+1&sort=title&direction=asc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertContains(response, "Test Movie 1")
        self.assertContains(response, "4 plays")

    def test_supported_media_sort_shows_plays_option(self):
        """Movies, TV, and Anime should expose the plays sort option."""
        movie_response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
        )
        tv_response = self.client.get(reverse("medialist", args=[MediaTypes.TV.value]))
        anime_response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
        )

        self.assertContains(movie_response, "toggleSort('plays')")
        self.assertContains(tv_response, "toggleSort('plays')")
        self.assertContains(anime_response, "toggleSort('plays')")
        self.assertContains(movie_response, "toggleSort('release_date')")
        self.assertContains(movie_response, "toggleSort('date_added')")

    def test_non_supported_media_sort_hides_plays_option_and_falls_back(self):
        """Non movie/tv/anime media types should hide plays sort and fallback to title."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value]) + "?sort=plays",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('plays')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.book_sort, "title")

    def test_supported_media_sort_shows_time_watched_option(self):
        """Movies, TV, and Anime should expose the time watched sort option."""
        movie_response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
        )
        tv_response = self.client.get(reverse("medialist", args=[MediaTypes.TV.value]))
        anime_response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
        )

        self.assertContains(movie_response, "toggleSort('time_watched')")
        self.assertContains(tv_response, "toggleSort('time_watched')")
        self.assertContains(anime_response, "toggleSort('time_watched')")

    def test_non_supported_media_sort_hides_time_watched_option_and_falls_back(self):
        """Non movie/tv/anime media types should hide time watched sort and fallback."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value]) + "?sort=time_watched",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('time_watched')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.book_sort, "title")

    def test_supported_media_sort_shows_next_episode_air_date_option(self):
        """TV, Season, and Anime should expose the next episode air date sort option."""
        tv_response = self.client.get(reverse("medialist", args=[MediaTypes.TV.value]))
        season_response = self.client.get(
            reverse("medialist", args=[MediaTypes.SEASON.value])
        )
        anime_response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
        )

        self.assertContains(tv_response, "toggleSort('next_episode_air_date')")
        self.assertContains(season_response, "toggleSort('next_episode_air_date')")
        self.assertContains(anime_response, "toggleSort('next_episode_air_date')")

    def test_non_supported_media_sort_hides_next_episode_air_date_option_and_falls_back(
        self,
    ):
        """Non show-like media types should hide next-episode air-date sort and fallback."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value])
            + "?sort=next_episode_air_date",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('next_episode_air_date')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.book_sort, "title")

    def test_game_sort_dropdown_includes_time_to_beat_option(self):
        self._create_game_entry("325609", "Dispatch", hltb_minutes=555)

        response = self.client.get(reverse("medialist", args=[MediaTypes.GAME.value]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toggleSort('time_to_beat')")

    def test_game_sort_dropdown_includes_platform_option(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.GAME.value]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toggleSort('platform')")

    def test_movie_sort_dropdown_includes_runtime_option(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toggleSort('runtime')")

    def test_supported_media_sort_dropdown_includes_popularity_option(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toggleSort('popularity')")

    def test_media_sort_dropdown_is_alphabetized(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        labels = [str(label) for _value, label in response.context["sort_choices"]]
        self.assertEqual(labels, sorted(labels, key=str.lower))

    def test_supported_media_sort_dropdown_includes_critic_rating_option(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toggleSort('critic_rating')")

    def test_non_game_sort_hides_time_to_beat_option_and_falls_back(self):
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?sort=time_to_beat",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('time_to_beat')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_sort, "title")

    def test_non_runtime_media_sort_hides_runtime_option_and_falls_back(self):
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value]) + "?sort=runtime",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('runtime')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.book_sort, "title")

    def test_non_popularity_media_sort_hides_popularity_option_and_falls_back(self):
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value]) + "?sort=popularity",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('popularity')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.book_sort, "title")

    def test_non_supported_critic_rating_sort_hides_option_and_falls_back(self):
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.PODCAST.value])
            + "?sort=critic_rating",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('critic_rating')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.podcast_sort, "title")

    def test_movie_sort_by_plays_orders_by_aggregated_completed_plays(self):
        """Movie plays sort should use aggregated completed play totals."""
        item = Item.objects.get(
            title="Test Movie 1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        older = timezone.now() - timedelta(days=30)
        newer = timezone.now() - timedelta(days=3)
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=0,
                    end_date=older,
                ),
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=newer,
                ),
            ],
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?sort=plays&direction=desc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "plays")
        self.assertEqual(
            response.context["media_list"].object_list[0].item.title, "Test Movie 1"
        )

    def test_movie_sort_by_time_watched_orders_by_total_minutes(self):
        """Movie time watched sort should prioritize plays multiplied by runtime."""
        item_one = Item.objects.get(
            title="Test Movie 1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        item_two = Item.objects.get(
            title="Test Movie 2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )

        Item.objects.filter(id=item_one.id).update(runtime_minutes=30)
        Item.objects.filter(id=item_two.id).update(runtime_minutes=100)

        Movie.objects.bulk_create(
            [
                Movie(
                    item=item_one,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=timezone.now() - timedelta(days=7),
                ),
                Movie(
                    item=item_one,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=timezone.now() - timedelta(days=6),
                ),
                Movie(
                    item=item_one,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=timezone.now() - timedelta(days=5),
                ),
            ],
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=grid&sort=time_watched&direction=desc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "time_watched")
        self.assertEqual(
            response.context["media_list"].object_list[0].item.title, "Test Movie 1"
        )
        self.assertContains(response, "2h 00min")

    def test_tv_sort_by_plays_orders_by_episode_progress(self):
        """TV plays sort should use total watched episode progress."""
        first_item = Item.objects.create(
            media_id="tv-plays-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="TV Plays First",
            image="http://example.com/tv1.jpg",
        )
        second_item = Item.objects.create(
            media_id="tv-plays-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="TV Plays Second",
            image="http://example.com/tv2.jpg",
        )
        first_tv = TV.objects.create(
            item=first_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        second_tv = TV.objects.create(
            item=second_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        first_season_item = Item.objects.create(
            media_id="tv-plays-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Plays First",
            image="http://example.com/tv1-season.jpg",
            season_number=1,
        )
        second_season_item = Item.objects.create(
            media_id="tv-plays-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Plays Second",
            image="http://example.com/tv2-season.jpg",
            season_number=1,
        )
        first_season = Season.objects.create(
            item=first_season_item,
            user=self.user,
            related_tv=first_tv,
            status=Status.IN_PROGRESS.value,
        )
        second_season = Season.objects.create(
            item=second_season_item,
            user=self.user,
            related_tv=second_tv,
            status=Status.IN_PROGRESS.value,
        )

        first_episode_rows = []
        for episode_number in (1, 2):
            episode_item = Item.objects.create(
                media_id="tv-plays-1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"TV Plays First Episode {episode_number}",
                image="http://example.com/tv1-episode.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            first_episode_rows.append(
                Episode(
                    item=episode_item,
                    related_season=first_season,
                    end_date=timezone.now() - timedelta(days=episode_number),
                ),
            )
        Episode.objects.bulk_create(first_episode_rows)

        episode_item = Item.objects.create(
            media_id="tv-plays-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="TV Plays Second Episode 1",
            image="http://example.com/tv2-episode.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=episode_item,
                    related_season=second_season,
                    end_date=timezone.now() - timedelta(days=1),
                ),
            ],
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.TV.value])
            + "?sort=plays&direction=desc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "plays")
        self.assertEqual(
            response.context["media_list"].object_list[0].item.title, "TV Plays First"
        )

    def test_movie_sort_by_release_date_orders_items(self):
        """Release date sort should order by item.release_datetime."""
        now = timezone.now()
        item1 = Item.objects.get(
            title="Test Movie 1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        item2 = Item.objects.get(
            title="Test Movie 2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        item3 = Item.objects.get(
            title="Test Movie 3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        Item.objects.filter(id=item1.id).update(
            release_datetime=now - timedelta(days=90)
        )
        Item.objects.filter(id=item2.id).update(
            release_datetime=now - timedelta(days=15)
        )
        Item.objects.filter(id=item3.id).update(
            release_datetime=now - timedelta(days=45)
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?sort=release_date&direction=asc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "release_date")
        self.assertEqual(
            response.context["media_list"].object_list[0].item.title, "Test Movie 1"
        )

    def test_movie_sort_by_date_added_orders_items(self):
        """Date added sort should order by media.created_at."""
        oldest = timezone.now() - timedelta(days=120)
        newest = timezone.now() - timedelta(days=3)
        middle = timezone.now() - timedelta(days=40)

        movie1 = Movie.objects.get(item__title="Test Movie 1", user=self.user)
        movie2 = Movie.objects.get(item__title="Test Movie 2", user=self.user)
        movie3 = Movie.objects.get(item__title="Test Movie 3", user=self.user)
        Movie.objects.filter(id=movie1.id).update(created_at=oldest)
        Movie.objects.filter(id=movie2.id).update(created_at=newest)
        Movie.objects.filter(id=movie3.id).update(created_at=middle)

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?sort=date_added&direction=asc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "date_added")
        self.assertEqual(
            response.context["media_list"].object_list[0].item.title, "Test Movie 1"
        )

    def test_media_list_with_filters(self):
        """Test the media list view with filters."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?status=Completed&sort=score&layout=table",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.context["current_status"],
            [Status.COMPLETED.value],
        )
        self.assertEqual(response.context["current_sort"], "score")
        self.assertEqual(response.context["current_layout"], "table")

        self.assertEqual(response.context["media_list"].paginator.count, 2)

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_status, Status.COMPLETED.value)
        self.assertEqual(self.user.movie_sort, "score")
        self.assertEqual(self.user.movie_layout, "table")

    def test_media_list_accepts_multiple_status_filters(self):
        """Multiple statuses are combined with OR semantics."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
            {"status": [Status.COMPLETED.value, Status.IN_PROGRESS.value]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["current_status"],
            [Status.COMPLETED.value, Status.IN_PROGRESS.value],
        )
        self.assertEqual(response.context["media_list"].paginator.count, 5)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.movie_status,
            f"{Status.COMPLETED.value},{Status.IN_PROGRESS.value}",
        )

    def test_status_all_excludes_collected_untracked_items(self):
        tracked_item = Item.objects.create(
            media_id="status-all-collected-tracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Status All Collected Tracked",
            image="http://example.com/status-all-tracked.jpg",
        )
        Movie.objects.create(
            item=tracked_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        untracked_item = Item.objects.create(
            media_id="status-all-collected-untracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Status All Collected Untracked",
            image="http://example.com/status-all-untracked.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=untracked_item,
            media_type="digital",
        )

        url = reverse("medialist", args=[MediaTypes.MOVIE.value])
        response = self.client.get(url, {"search": "Status All Collected"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry.item.title for entry in response.context["media_list"].object_list],
            ["Status All Collected Tracked"],
        )

        in_progress_response = self.client.get(
            url,
            {
                "search": "Status All Collected",
                "status": Status.IN_PROGRESS.value,
            },
        )
        self.assertEqual(in_progress_response.status_code, 200)
        self.assertEqual(
            [
                entry.item.title
                for entry in in_progress_response.context["media_list"].object_list
            ],
            ["Status All Collected Tracked"],
        )

    def test_no_status_filter_shows_only_collected_untracked_without_persisting_preference(
        self,
    ):
        self.user.movie_status = Status.COMPLETED.value
        self.user.save(update_fields=["movie_status"])

        tracked_item = Item.objects.create(
            media_id="no-status-tracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="No Status Filter Tracked",
            image="http://example.com/no-status-tracked.jpg",
        )
        Movie.objects.create(
            item=tracked_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        untracked_item = Item.objects.create(
            media_id="no-status-untracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="No Status Filter Untracked",
            image="http://example.com/no-status-untracked.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=untracked_item,
            media_type="digital",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
            {
                "search": "No Status Filter",
                "status": "no_status",
                "layout": "table",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_status"], ["no_status"])
        self.assertEqual(
            [entry.item.title for entry in response.context["media_list"].object_list],
            ["No Status Filter Untracked"],
        )
        self.assertContains(response, "No Status")

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_status, Status.COMPLETED.value)

    def test_grid_layout_renders_no_status_chip_for_untracked_entries(self):
        untracked_item = Item.objects.create(
            media_id="grid-no-status-untracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Grid No Status Untracked",
            image="http://example.com/grid-no-status-untracked.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=untracked_item,
            media_type="digital",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
            {
                "search": "Grid No Status",
                "layout": "grid",
                "status": "no_status",
            },
        )

        self.assertEqual(response.status_code, 200)
        chips = [m["statusChip"] for m in card_models(response.content)]
        self.assertIn("no-status", [chip and chip["kind"] for chip in chips])

    def test_no_status_card_add_to_tracker_opens_modal(self):
        """Regression for #1376: the card sent instance_id="None" and the modal 500ed."""
        untracked_item = Item.objects.create(
            media_id="21510",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Grid No Status Modal",
            image="http://example.com/grid-no-status-modal.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=untracked_item,
            media_type="digital",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
            {"search": "Grid No Status Modal", "layout": "grid", "status": "no_status"},
        )
        self.assertContains(response, "Grid No Status Modal")
        self.assertNotContains(response, '"instance_id": "None"')

        with (
            mock.patch(
                "app.providers.services.get_media_metadata",
                return_value={
                    "media_id": "21510",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "title": "Grid No Status Modal",
                    "image": "http://example.com/grid-no-status-modal.jpg",
                    "max_progress": 1,
                },
            ),
            mock.patch("app.models.Item.fetch_releases"),
        ):
            modal = self.client.get(
                reverse(
                    "track_modal",
                    args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "21510"],
                ),
                {"return_url": "/medialist/movie"},
            )
        self.assertEqual(modal.status_code, 200)

    def test_not_rated_filter_excludes_collected_untracked_items(self):
        rated_item = Item.objects.create(
            media_id="rating-split-tracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Rating Split Tracked",
            image="http://example.com/rating-split-tracked.jpg",
        )
        Movie.objects.create(
            item=rated_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
            score=9,
        )

        untracked_item = Item.objects.create(
            media_id="rating-split-untracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Rating Split Untracked",
            image="http://example.com/rating-split-untracked.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=untracked_item,
            media_type="digital",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
            {
                "search": "Rating Split",
                "rating": "not_rated",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_rating"], "not_rated")
        self.assertEqual(
            [entry.item.title for entry in response.context["media_list"].object_list],
            [],
        )

    def test_tv_episode_collection_items_appear_for_no_status_only(self):
        show_item = Item.objects.create(
            media_id="tv-episode-collected-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Episode Collected Library Show",
            image="http://example.com/episode-collected-show.jpg",
        )
        episode_item = Item.objects.create(
            media_id="tv-episode-collected-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode Collected Library Show Episode 1",
            image="http://example.com/episode-collected-ep.jpg",
            season_number=1,
            episode_number=1,
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=episode_item,
            media_type="digital",
        )

        url = reverse("medialist", args=[MediaTypes.TV.value])
        all_response = self.client.get(url, {"search": "Episode Collected Library"})
        no_status_response = self.client.get(
            url,
            {"search": "Episode Collected Library", "status": "no_status"},
        )

        self.assertEqual(
            show_item.id,
            no_status_response.context["media_list"].object_list[0].item.id,
        )
        self.assertEqual(
            [
                entry.item.title
                for entry in all_response.context["media_list"].object_list
            ],
            [],
        )
        self.assertEqual(
            [
                entry.item.title
                for entry in no_status_response.context["media_list"].object_list
            ],
            ["Episode Collected Library Show"],
        )

    def test_progress_filters_exclude_collected_untracked_items(self):
        self._create_tv_runtime_entry(
            "progress-filter-tracked-tv",
            "Progress Filter Tracked TV",
            [24, 24, 24],
            progress=1,
        )
        episode_item = Item.objects.create(
            media_id="progress-filter-untracked-tv",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Progress Filter Untracked TV Episode 1",
            image="http://example.com/progress-filter-untracked-ep.jpg",
            season_number=1,
            episode_number=1,
        )
        Item.objects.create(
            media_id="progress-filter-untracked-tv",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Progress Filter Untracked TV",
            image="http://example.com/progress-filter-untracked-show.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=episode_item,
            media_type="digital",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.TV.value]),
            {
                "search": "Progress Filter",
                "status": "All",
                "progress": "not_caught_up",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry.item.title for entry in response.context["media_list"].object_list],
            ["Progress Filter Tracked TV"],
        )

    def test_progress_filter_is_visible_only_for_tv_and_anime(self):
        """Progress filtering should render only where caught-up semantics are supported."""
        movie_response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
        )
        tv_response = self.client.get(reverse("medialist", args=[MediaTypes.TV.value]))
        anime_response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
        )

        self.assertFalse(movie_response.context["filter_data"]["show_progress"])
        self.assertTrue(tv_response.context["filter_data"]["show_progress"])
        self.assertTrue(anime_response.context["filter_data"]["show_progress"])

        self.assertNotContains(movie_response, "@click=\"view = 'progress'\"")
        self.assertContains(tv_response, "@click=\"view = 'progress'\"")
        self.assertContains(anime_response, "@click=\"view = 'progress'\"")

    def test_tv_progress_filter_hides_caught_up_shows(self):
        """TV caught-up filtering should split shows by watched-vs-released progress."""
        self._create_tv_runtime_entry(
            "tv-progress-caught-up",
            "Caught Up TV",
            [24, 24, 24],
            progress=3,
        )
        self._create_tv_runtime_entry(
            "tv-progress-not-caught-up",
            "Not Caught Up TV",
            [24, 24, 24],
            progress=1,
        )

        url = reverse("medialist", args=[MediaTypes.TV.value])

        not_caught_up_response = self.client.get(f"{url}?progress=not_caught_up")
        self.assertEqual(not_caught_up_response.status_code, 200)
        self.assertTrue(not_caught_up_response.context["filter_data"]["show_progress"])
        self.assertEqual(
            not_caught_up_response.context["current_progress"], "not_caught_up"
        )
        self.assertEqual(
            not_caught_up_response.context["media_list"].paginator.count, 1
        )
        self.assertEqual(
            [
                media.item.title
                for media in not_caught_up_response.context["media_list"].object_list
            ],
            ["Not Caught Up TV"],
        )

        caught_up_response = self.client.get(f"{url}?progress=caught_up")
        self.assertEqual(caught_up_response.status_code, 200)
        self.assertEqual(caught_up_response.context["current_progress"], "caught_up")
        self.assertEqual(caught_up_response.context["media_list"].paginator.count, 1)
        self.assertEqual(
            [
                media.item.title
                for media in caught_up_response.context["media_list"].object_list
            ],
            ["Caught Up TV"],
        )

    def test_tv_progress_ignores_date_only_episode_on_its_release_date(self):
        """An episode with only a release date is not released that morning."""
        from events.calendar.helpers import date_parser

        self._create_tv_seasonal_entry(
            "tv-unknown-time-release",
            "Unknown Time Release TV",
            [
                {
                    "season_number": 1,
                    "status": Status.IN_PROGRESS.value,
                    "released_episodes": 2,
                    "watched_episodes": 1,
                },
            ],
        )
        today = timezone.localdate()
        episode = Item.objects.get(
            media_id="tv-unknown-time-release",
            media_type=MediaTypes.EPISODE.value,
            episode_number=2,
        )
        episode.release_datetime = date_parser(today)
        episode.save(update_fields=["release_datetime"])

        now = datetime.combine(today, time(14, 0), tzinfo=UTC)
        with mock.patch("django.utils.timezone.now", return_value=now):
            response = self.client.get(
                f"{reverse('medialist', args=[MediaTypes.TV.value])}"
                "?progress=not_caught_up",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 0)

    def test_tv_progress_filter_ignores_dropped_seasons(self):
        """Dropped seasons should not keep a fully released show out of caught-up."""
        self._create_tv_seasonal_entry(
            "tv-progress-dropped-seasons",
            "Dropped Seasons Caught Up",
            [
                {
                    "season_number": 1,
                    "status": Status.DROPPED.value,
                    "released_episodes": 2,
                    "watched_episodes": 0,
                },
                {
                    "season_number": 2,
                    "status": Status.DROPPED.value,
                    "released_episodes": 2,
                    "watched_episodes": 0,
                },
                {
                    "season_number": 3,
                    "status": Status.COMPLETED.value,
                    "released_episodes": 3,
                    "watched_episodes": 3,
                },
                {
                    "season_number": 4,
                    "status": Status.IN_PROGRESS.value,
                    "released_episodes": 2,
                    "watched_episodes": 2,
                },
            ],
        )
        self._create_tv_runtime_entry(
            "tv-progress-not-caught-up-2",
            "Still In Progress TV",
            [24, 24, 24],
            progress=1,
        )

        url = reverse("medialist", args=[MediaTypes.TV.value])

        caught_up_response = self.client.get(f"{url}?progress=caught_up")
        self.assertEqual(caught_up_response.status_code, 200)
        self.assertEqual(caught_up_response.context["current_progress"], "caught_up")
        self.assertEqual(caught_up_response.context["media_list"].paginator.count, 1)
        self.assertEqual(
            [
                media.item.title
                for media in caught_up_response.context["media_list"].object_list
            ],
            ["Dropped Seasons Caught Up"],
        )

        not_caught_up_response = self.client.get(f"{url}?progress=not_caught_up")
        self.assertEqual(not_caught_up_response.status_code, 200)
        self.assertEqual(
            not_caught_up_response.context["current_progress"], "not_caught_up"
        )
        self.assertEqual(
            not_caught_up_response.context["media_list"].paginator.count, 1
        )
        self.assertEqual(
            [
                media.item.title
                for media in not_caught_up_response.context["media_list"].object_list
            ],
            ["Still In Progress TV"],
        )

    def test_anime_progress_filter_hides_caught_up_shows(self):
        """Anime caught-up filtering should split shows by watched-vs-released progress."""
        self._create_anime_runtime_entry(
            "anime-progress-caught-up",
            "Caught Up Anime",
            runtime_minutes=24,
            episode_count=12,
            progress=12,
        )
        self._create_anime_runtime_entry(
            "anime-progress-not-caught-up",
            "Not Caught Up Anime",
            runtime_minutes=24,
            episode_count=12,
            progress=5,
        )

        url = reverse("medialist", args=[MediaTypes.ANIME.value])

        not_caught_up_response = self.client.get(f"{url}?progress=not_caught_up")
        self.assertEqual(not_caught_up_response.status_code, 200)
        self.assertTrue(not_caught_up_response.context["filter_data"]["show_progress"])
        self.assertEqual(
            not_caught_up_response.context["current_progress"], "not_caught_up"
        )
        self.assertEqual(
            not_caught_up_response.context["media_list"].paginator.count, 1
        )
        self.assertEqual(
            [
                media.item.title
                for media in not_caught_up_response.context["media_list"].object_list
            ],
            ["Not Caught Up Anime"],
        )

        caught_up_response = self.client.get(f"{url}?progress=caught_up")
        self.assertEqual(caught_up_response.status_code, 200)
        self.assertEqual(caught_up_response.context["current_progress"], "caught_up")
        self.assertEqual(caught_up_response.context["media_list"].paginator.count, 1)
        self.assertEqual(
            [
                media.item.title
                for media in caught_up_response.context["media_list"].object_list
            ],
            ["Caught Up Anime"],
        )

    def test_time_left_cache_key_separates_progress_filters(self):
        """Time-left caching should not bleed across different progress filter states."""
        cache_utils.clear_time_left_cache_for_user(self.user.id)

        self._create_tv_runtime_entry(
            "tv-time-left-caught-up",
            "Caught Up Time Left",
            [24, 24, 24],
            progress=3,
        )
        self._create_tv_runtime_entry(
            "tv-time-left-not-caught-up",
            "Not Caught Up Time Left",
            [24, 24, 24],
            progress=1,
        )

        url = reverse("medialist", args=[MediaTypes.TV.value])

        caught_up_response = self.client.get(
            f"{url}?sort=time_left&direction=asc&progress=caught_up",
        )
        self.assertEqual(caught_up_response.status_code, 200)
        self.assertEqual(caught_up_response.context["current_progress"], "caught_up")
        self.assertEqual(caught_up_response.context["media_list"].paginator.count, 1)
        self.assertEqual(
            caught_up_response.context["media_list"].object_list[0].item.title,
            "Caught Up Time Left",
        )

        not_caught_up_response = self.client.get(
            f"{url}?sort=time_left&direction=asc&progress=not_caught_up",
        )
        self.assertEqual(not_caught_up_response.status_code, 200)
        self.assertEqual(
            not_caught_up_response.context["current_progress"], "not_caught_up"
        )
        self.assertEqual(
            not_caught_up_response.context["media_list"].paginator.count, 1
        )
        self.assertEqual(
            not_caught_up_response.context["media_list"].object_list[0].item.title,
            "Not Caught Up Time Left",
        )

    def test_media_list_with_release_filters(self):
        """Release filter should split tracked media by today."""
        now = timezone.now()
        released_item = (
            Item.objects.filter(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Test Movie 1",
            )
            .only("id")
            .first()
        )
        upcoming_item = (
            Item.objects.filter(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Test Movie 2",
            )
            .only("id")
            .first()
        )
        self.assertIsNotNone(released_item)
        self.assertIsNotNone(upcoming_item)
        Item.objects.filter(id=released_item.id).update(
            release_datetime=now - timedelta(days=30),
        )
        Item.objects.filter(id=upcoming_item.id).update(
            release_datetime=now + timedelta(days=30),
        )

        url = reverse("medialist", args=[MediaTypes.MOVIE.value])

        released_response = self.client.get(f"{url}?release=released")
        self.assertEqual(released_response.status_code, 200)
        self.assertEqual(released_response.context["current_release"], "released")
        self.assertEqual(released_response.context["media_list"].paginator.count, 1)
        self.assertContains(released_response, "Test Movie 1")
        self.assertNotContains(released_response, "Test Movie 2")

        not_released_response = self.client.get(f"{url}?release=not_released")
        self.assertEqual(not_released_response.status_code, 200)
        self.assertEqual(
            not_released_response.context["current_release"],
            "not_released",
        )
        self.assertEqual(not_released_response.context["media_list"].paginator.count, 4)
        self.assertContains(not_released_response, "Test Movie 2")
        self.assertNotContains(not_released_response, "Test Movie 1")

    def test_media_list_with_media_status_filter(self):
        """Media status filter should match Item.status exactly and exclude blanks."""
        ended_item = (
            Item.objects.filter(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Test Movie 1",
            )
            .only("id")
            .first()
        )
        self.assertIsNotNone(ended_item)
        Item.objects.filter(id=ended_item.id).update(status="Ended")

        url = reverse("medialist", args=[MediaTypes.MOVIE.value])

        response = self.client.get(f"{url}?media_status=Ended")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_media_status"], "Ended")
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertContains(response, "Test Movie 1")
        self.assertNotContains(response, "Test Movie 2")

        all_response = self.client.get(url)
        self.assertEqual(all_response.context["media_list"].paginator.count, 5)
        media_statuses = [
            option["value"]
            for option in all_response.context["filter_data"]["media_statuses"]
        ]
        self.assertEqual(media_statuses, ["Ended"])

    def test_game_platform_filter_prefers_collection_resolution(self):
        """Game platform filtering should prefer collection platform over metadata platforms."""
        switch_override_item = Item.objects.create(
            media_id="game-platform-filter-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Multiplatform Game",
            image="http://example.com/game1.jpg",
            platforms=["PlayStation 5"],
        )
        ps5_item = Item.objects.create(
            media_id="game-platform-filter-2",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="PS5 Exclusive Game",
            image="http://example.com/game2.jpg",
            platforms=["PlayStation 5"],
        )

        Game.objects.bulk_create(
            [
                Game(
                    item=switch_override_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=60,
                ),
                Game(
                    item=ps5_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=60,
                ),
            ],
        )

        CollectionEntry.objects.create(
            user=self.user,
            item=switch_override_item,
            resolution="Nintendo Switch",
        )

        url = reverse("medialist", args=[MediaTypes.GAME.value])
        response = self.client.get(url, {"platform": "PlayStation 5", "status": "All"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertContains(response, "PS5 Exclusive Game")
        self.assertNotContains(response, "Multiplatform Game")

        platform_values = {
            option["value"] for option in response.context["filter_data"]["platforms"]
        }
        self.assertIn("Nintendo Switch", platform_values)
        self.assertIn("PlayStation 5", platform_values)

    def test_game_platform_filter_multi_select_or_and_not(self):
        """Game platform filtering supports OR/AND/NOT across multiple platforms."""
        switch_item = Item.objects.create(
            media_id="game-platform-multi-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Switch Only Game",
            image="http://example.com/game3.jpg",
            platforms=["Nintendo Switch"],
        )
        ps5_item = Item.objects.create(
            media_id="game-platform-multi-2",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="PS5 Only Game",
            image="http://example.com/game4.jpg",
            platforms=["PlayStation 5"],
        )
        both_item = Item.objects.create(
            media_id="game-platform-multi-3",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Switch and PS5 Game",
            image="http://example.com/game5.jpg",
            platforms=["Nintendo Switch", "PlayStation 5"],
        )

        Game.objects.bulk_create(
            [
                Game(
                    item=switch_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=1,
                ),
                Game(
                    item=ps5_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=1,
                ),
                Game(
                    item=both_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=1,
                ),
            ],
        )

        url = reverse("medialist", args=[MediaTypes.GAME.value])

        or_response = self.client.get(
            url,
            {
                "platform": ["Nintendo Switch", "PlayStation 5"],
                "platform_mode": "or",
                "status": "All",
            },
        )
        self.assertEqual(or_response.context["media_list"].paginator.count, 3)

        and_response = self.client.get(
            url,
            {
                "platform": ["Nintendo Switch", "PlayStation 5"],
                "platform_mode": "and",
                "status": "All",
            },
        )
        self.assertEqual(and_response.context["media_list"].paginator.count, 1)
        self.assertContains(and_response, "Switch and PS5 Game")

        not_response = self.client.get(
            url,
            {
                "platform": ["Nintendo Switch"],
                "platform_mode": "not",
                "status": "All",
            },
        )
        self.assertEqual(not_response.context["media_list"].paginator.count, 1)
        self.assertContains(not_response, "PS5 Only Game")

    def test_game_table_renders_time_to_beat_column(self):
        self._create_game_entry("325609", "Dispatch", hltb_minutes=555)

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.GAME.value]) + "?layout=table",
        )

        self.assertEqual(response.status_code, 200)
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertIn("time_to_beat", column_keys)
        self.assertContains(response, "Time to Beat")
        self.assertContains(response, "9h 15min")

    def test_movie_table_renders_runtime_column(self):
        self._create_movie_runtime_entry(
            "runtime-column-movie", "Runtime Column Movie", 142
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=table&search=Runtime+Column+Movie",
        )

        self.assertEqual(response.status_code, 200)
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertIn("runtime", column_keys)
        self.assertContains(response, "Runtime")
        self.assertContains(response, "2h 22min")

    def test_movie_table_renders_time_watched_column(self):
        item = Item.objects.create(
            media_id="time-watched-column-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Time Watched Column Movie",
            image="http://example.com/time-watched-column.jpg",
            runtime_minutes=142,
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=timezone.now() - timedelta(days=2),
                ),
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=timezone.now() - timedelta(days=1),
                ),
            ],
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=table&search=Time+Watched+Column+Movie",
        )

        self.assertEqual(response.status_code, 200)
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertIn("time_watched", column_keys)
        self.assertContains(response, "Time Watched")
        self.assertContains(response, "4h 44min")

    def test_movie_table_renders_popularity_column(self):
        self._create_movie_popularity_entry(
            "popularity-column-movie", "Popularity Column Movie", 7
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=table&search=Popularity+Column+Movie",
        )

        self.assertEqual(response.status_code, 200)
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertIn("popularity", column_keys)
        self.assertContains(response, "Popularity")
        self.assertContains(response, "#7")

    def test_movie_table_renders_critic_rating_column(self):
        self._create_movie_critic_rating_entry(
            "critic-column-movie",
            "Critic Column Movie",
            8.7,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=table&search=Critic+Column+Movie",
        )

        self.assertEqual(response.status_code, 200)
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertIn("critic_rating", column_keys)
        self.assertContains(response, "Critic Rating")
        self.assertContains(response, "8.7")

    def test_movie_sort_by_runtime_orders_shortest_first(self):
        self._create_movie_runtime_entry(
            "runtime-sort-movie-1", "Runtime Sort Long", 160
        )
        self._create_movie_runtime_entry(
            "runtime-sort-movie-2", "Runtime Sort Short", 95
        )
        self._create_movie_runtime_entry("runtime-sort-movie-3", "Runtime Sort Unknown")

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=grid&search=Runtime+Sort&sort=runtime",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "runtime")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:3]
            ],
            ["Runtime Sort Short", "Runtime Sort Long", "Runtime Sort Unknown"],
        )
        self.assertContains(response, "1h 35min")
        self.assertContains(response, "2h 40min")

    def test_movie_sort_by_popularity_orders_lowest_rank_first(self):
        self._create_movie_popularity_entry(
            "popularity-sort-movie-1", "Popularity Rank Two", 2
        )
        self._create_movie_popularity_entry(
            "popularity-sort-movie-2", "Popularity Rank One", 1
        )
        self._create_movie_popularity_entry(
            "popularity-sort-movie-3", "Popularity Rank Missing", 50
        )
        Item.objects.filter(media_id="popularity-sort-movie-3").update(
            trakt_popularity_rank=None,
            trakt_popularity_score=None,
            trakt_rating=None,
            trakt_rating_count=None,
            trakt_popularity_fetched_at=None,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=grid&search=Popularity+Rank&sort=popularity",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "popularity")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:3]
            ],
            ["Popularity Rank One", "Popularity Rank Two", "Popularity Rank Missing"],
        )

    def test_movie_sort_by_critic_rating_orders_highest_first(self):
        self._create_movie_critic_rating_entry(
            "critic-sort-movie-1",
            "Critic Rating Low",
            7.2,
        )
        self._create_movie_critic_rating_entry(
            "critic-sort-movie-2",
            "Critic Rating High",
            9.1,
        )
        self._create_movie_critic_rating_entry(
            "critic-sort-movie-3",
            "Critic Rating Missing",
            None,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=grid&search=Critic+Rating&sort=critic_rating",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "critic_rating")
        self.assertEqual(response.context["current_direction"], "desc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:3]
            ],
            ["Critic Rating High", "Critic Rating Low", "Critic Rating Missing"],
        )
        self.assertContains(response, "9.1/10")
        self.assertContains(response, "7.2/10")

    def test_tv_sort_by_runtime_uses_total_show_runtime(self):
        self._create_tv_runtime_entry("tv-runtime-1", "TV Runtime Short", [24, 24, 24])
        self._create_tv_runtime_entry("tv-runtime-2", "TV Runtime Long", [48, 48, 48])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.TV.value])
            + "?layout=grid&search=TV+Runtime&sort=runtime",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "runtime")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:2]
            ],
            ["TV Runtime Short", "TV Runtime Long"],
        )
        self.assertContains(response, "1h 12min")
        self.assertContains(response, "2h 24min")

    def test_anime_sort_by_runtime_uses_episode_count_times_runtime(self):
        self._create_anime_runtime_entry(
            "anime-runtime-1",
            "Anime Runtime Short",
            runtime_minutes=24,
            episode_count=12,
        )
        self._create_anime_runtime_entry(
            "anime-runtime-2",
            "Anime Runtime Long",
            runtime_minutes=24,
            episode_count=26,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
            + "?layout=grid&search=Anime+Runtime&sort=runtime",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "runtime")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:2]
            ],
            ["Anime Runtime Short", "Anime Runtime Long"],
        )
        self.assertContains(response, "4h 48min")
        self.assertContains(response, "10h 24min")

    def test_tv_and_season_sort_by_next_episode_air_date_orders_items(self):
        self.user.date_format = "iso_8601"
        self.user.save(update_fields=["date_format"])

        base_now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        backlog_release = base_now - timedelta(days=10)
        future_release = base_now + timedelta(days=10)

        self._create_tv_next_episode_air_date_entry(
            "tv-next-episode-1",
            "Zulu Backlog TV",
            [backlog_release, backlog_release + timedelta(days=7)],
            progress=0,
        )
        self._create_tv_next_episode_air_date_entry(
            "tv-next-episode-2",
            "Alpha Future TV",
            [backlog_release, future_release],
            progress=1,
        )
        self._create_tv_next_episode_air_date_entry(
            "tv-next-episode-3",
            "Mike Missing TV",
            [None, future_release + timedelta(days=7)],
            progress=0,
        )

        tv_response = self.client.get(
            reverse("medialist", args=[MediaTypes.TV.value])
            + "?layout=grid&sort=next_episode_air_date",
        )

        self.assertEqual(tv_response.status_code, 200)
        self.assertEqual(tv_response.context["current_sort"], "next_episode_air_date")
        self.assertEqual(tv_response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in tv_response.context["media_list"].object_list[:3]
            ],
            ["Zulu Backlog TV", "Alpha Future TV", "Mike Missing TV"],
        )
        self.assertContains(
            tv_response, timezone.localtime(backlog_release).date().isoformat()
        )
        self.assertContains(
            tv_response, timezone.localtime(future_release).date().isoformat()
        )

        season_response = self.client.get(
            reverse("medialist", args=[MediaTypes.SEASON.value])
            + "?layout=table&sort=next_episode_air_date",
        )

        self.assertEqual(season_response.status_code, 200)
        self.assertEqual(
            season_response.context["current_sort"], "next_episode_air_date"
        )
        self.assertEqual(season_response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in season_response.context["media_list"].object_list[:3]
            ],
            [
                "Zulu Backlog TV Season 1",
                "Alpha Future TV Season 1",
                "Mike Missing TV Season 1",
            ],
        )
        self.assertIn(
            "next_episode_air_date",
            [column.key for column in season_response.context["resolved_columns"]],
        )
        self.assertContains(season_response, "Episode Air Date")
        self.assertContains(
            season_response,
            timezone.localtime(backlog_release).date().isoformat(),
        )
        self.assertContains(
            season_response,
            timezone.localtime(future_release).date().isoformat(),
        )

    def test_tv_next_episode_air_date_uses_untracked_season_events(self):
        """Fall back to untracked-season events when tracked seasons are exhausted."""
        base_now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        season1_release = base_now - timedelta(days=30)
        season2_release = base_now - timedelta(days=3)

        tv, _season = self._create_tv_next_episode_air_date_entry(
            "tv-next-episode-untracked",
            "Untracked Season TV",
            [season1_release, season1_release + timedelta(days=7)],
            progress=2,
        )

        untracked_season_item = Item.objects.create(
            media_id="tv-next-episode-untracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Untracked Season TV Season 2",
            image="http://example.com/tv-next-episode-season2.jpg",
            season_number=2,
        )
        Event.objects.create(
            item=untracked_season_item,
            content_number=1,
            datetime=season2_release,
            notification_sent=False,
        )

        air_date = BasicMedia.objects._next_episode_air_date_value(tv)
        self.assertEqual(air_date, season2_release)

    def test_tv_next_episode_air_date_uses_events_on_a_duplicate_season_item(self):
        """Events written to another Item for the same season still date the show.

        The calendar attaches events to whichever Item row it resolves for a
        show/season identity, and that can differ from the row a tracked
        Season row points at (the library can hold the same identity in more
        than one bucket). Reading only the tracked row's own events leaves the
        TV page blank while the calendar still lists the episodes (#1531).
        """
        base_now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        season1_release = base_now - timedelta(days=30)
        season2_premiere = base_now + timedelta(days=3)

        tv, _season1 = self._create_tv_next_episode_air_date_entry(
            "tv-next-episode-duplicate-row",
            "Duplicate Row TV",
            [season1_release, season1_release + timedelta(days=7)],
            progress=2,
        )
        tracked_season2_item = Item.objects.create(
            media_id="tv-next-episode-duplicate-row",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            library_media_type=MediaTypes.TV.value,
            title="Duplicate Row TV Season 2",
            image="http://example.com/tv-next-episode-season2.jpg",
            season_number=2,
        )
        Season.objects.create(
            item=tracked_season2_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        event_season2_item = Item.objects.create(
            media_id="tv-next-episode-duplicate-row",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            library_media_type=MediaTypes.SEASON.value,
            title="Duplicate Row TV Season 2",
            image="http://example.com/tv-next-episode-season2.jpg",
            season_number=2,
        )
        Event.objects.create(
            item=event_season2_item,
            content_number=1,
            datetime=season2_premiere,
            notification_sent=False,
        )

        tv = BasicMedia.objects._apply_prefetch_related(
            TV.objects.filter(pk=tv.pk),
            MediaTypes.TV.value,
            list_mode=True,
        ).get()
        self.assertEqual(
            BasicMedia.objects._next_episode_air_date_value(tv),
            season2_premiere,
        )

    def test_tv_next_episode_air_date_ignores_a_dropped_season(self):
        """A dropped season is outside the show's progress, so outside its events too (#1531)."""
        base_now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        old_release = base_now - timedelta(days=60)
        premiere = base_now + timedelta(days=3)

        tv, season = self._create_tv_next_episode_air_date_entry(
            "tv-next-episode-dropped",
            "Dropped Season TV",
            [old_release, old_release + timedelta(days=7)],
            progress=2,
        )
        Season.objects.filter(pk=season.pk).update(status=Status.DROPPED.value)
        season2_item = Item.objects.create(
            media_id="tv-next-episode-dropped",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Dropped Season TV Season 2",
            image="http://example.com/tv-next-episode-season2.jpg",
            season_number=2,
        )
        Event.objects.create(
            item=season2_item,
            content_number=1,
            datetime=premiere,
            notification_sent=False,
        )

        tv = TV.objects.get(pk=tv.pk)
        self.assertEqual(
            BasicMedia.objects._next_episode_air_date_value(tv),
            premiere,
        )

    def test_tv_page_annotation_skips_a_season_row_instead_of_failing(self):
        """A season row on a TV page is named in the log; the page still loads."""
        _tv, season = self._create_tv_next_episode_air_date_entry(
            "tv-stray-season",
            "Stray Season TV",
            [timezone.now() - timedelta(days=5)],
        )

        with self.assertLogs("app.models.manager", level="WARNING") as logs:
            BasicMedia.objects.annotate_max_progress([season], MediaTypes.TV.value)

        self.assertIsNone(season.max_progress)
        self.assertIn("TV list holds a non-show row: model=Season", logs.output[0])
        self.assertIn("item_media_type=season", logs.output[0])

    def test_anime_sort_by_next_episode_air_date_orders_grouped_and_flat_rows(self):
        self.user.date_format = "iso_8601"
        self.user.anime_library_mode = MediaTypes.ANIME.value
        self.user.save(update_fields=["date_format", "anime_library_mode"])

        base_now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        past_release = base_now - timedelta(days=14)
        future_release = base_now + timedelta(days=14)

        self._create_anime_next_episode_air_date_entry(
            "anime-next-episode-1",
            "Flat Past Anime",
            [(1, past_release)],
            progress=0,
        )
        self._create_tv_next_episode_air_date_entry(
            "anime-next-episode-2",
            "Grouped Future Anime",
            [past_release, future_release],
            progress=1,
            library_media_type=MediaTypes.ANIME.value,
        )
        missing_item = Item.objects.create(
            media_id="anime-next-episode-3",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Flat Missing Anime",
            image="http://example.com/anime-missing.jpg",
        )
        Anime.objects.create(
            item=missing_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
            + "?layout=grid&sort=next_episode_air_date",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "next_episode_air_date")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:3]
            ],
            ["Flat Past Anime", "Grouped Future Anime", "Flat Missing Anime"],
        )
        self.assertContains(
            response, timezone.localtime(past_release).date().isoformat()
        )
        self.assertContains(
            response, timezone.localtime(future_release).date().isoformat()
        )

    def test_game_sort_by_time_to_beat_orders_by_best_available_value(self):
        self._create_game_entry("910001", "Longest Run", hltb_minutes=555)
        self._create_game_entry("910002", "Quick Quest", hltb_minutes=120)
        self._create_game_entry("910003", "Fallback Route", igdb_seconds=10800)
        self._create_game_entry("910004", "Unknown Length")

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.GAME.value])
            + "?layout=grid&sort=time_to_beat&direction=asc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "time_to_beat")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:4]
            ],
            ["Quick Quest", "Fallback Route", "Longest Run", "Unknown Length"],
        )
        self.assertContains(response, "2h 00min")
        self.assertContains(response, "3h 00min")

    def test_game_sort_by_platform_prefers_collection_platform_over_item_platform(self):
        collection_item = Item.objects.create(
            media_id="game-platform-sort-collection",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Collection Platform",
            image="http://example.com/game.jpg",
            platforms=["Xbox Series X|S", "PlayStation 5"],
        )
        ambiguous_item = Item.objects.create(
            media_id="game-platform-sort-ambiguous",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Ambiguous Platform",
            image="http://example.com/game.jpg",
            platforms=["Nintendo Switch", "PlayStation 5"],
        )
        Game.objects.create(
            item=collection_item, user=self.user, status=Status.PLANNING.value
        )
        Game.objects.create(
            item=ambiguous_item, user=self.user, status=Status.PLANNING.value
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=collection_item,
            resolution="PlayStation 5",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.GAME.value])
            + "?layout=grid&sort=platform&direction=asc",
        )

        self.assertEqual(response.context["current_sort"], "platform")
        self.assertEqual(response.context["current_direction"], "asc")
        # The collection-resolved platform sorts first; the multi-platform
        # game with no collection entry has no unambiguous platform to show,
        # so it sorts last instead of guessing an arbitrary IGDB platform.
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:2]
            ],
            ["Collection Platform", "Ambiguous Platform"],
        )
        self.assertContains(response, "Nintendo Switch")
        self.assertContains(response, "PlayStation 5")

    def test_game_last_played_sort_includes_in_progress_items_by_activity(self):
        """The Last Played sort follows progress, not only completion dates."""
        finished_item = Item.objects.create(
            media_id="game-last-played-finished",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Finished Long Ago",
            image="http://example.com/game.jpg",
        )
        playing_item = Item.objects.create(
            media_id="game-last-played-playing",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Playing Now",
            image="http://example.com/game.jpg",
        )
        Game.objects.bulk_create(
            [
                Game(
                    item=finished_item,
                    user=self.user,
                    status=Status.DROPPED.value,
                    progress=60,
                    end_date=timezone.now() - timedelta(days=10),
                ),
                Game(
                    item=playing_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=60,
                ),
            ],
        )
        Game.objects.filter(item=playing_item, user=self.user).update(
            progressed_at=timezone.now(),
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.GAME.value])
            + "?layout=grid&sort=end_date&direction=desc",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list
            ],
            ["Playing Now", "Finished Long Ago"],
        )

    def test_game_platform_filter_uses_latest_aggregated_status(self):
        """Status filtering should honor latest aggregated status for duplicate sessions."""
        stale_item = Item.objects.create(
            media_id="game-platform-filter-latest-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Completed Now, Was In Progress",
            image="http://example.com/game-latest-1.jpg",
            platforms=["PlayStation 5"],
        )
        active_item = Item.objects.create(
            media_id="game-platform-filter-latest-2",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Still In Progress",
            image="http://example.com/game-latest-2.jpg",
            platforms=["PlayStation 5"],
        )

        Game.objects.bulk_create(
            [
                Game(
                    item=stale_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=12,
                ),
                Game(
                    item=stale_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=30,
                    end_date=timezone.now() - timedelta(days=1),
                ),
                Game(
                    item=active_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=20,
                ),
            ],
        )

        old_in_progress = Game.objects.filter(
            item=stale_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        ).get()
        old_activity = timezone.now() - timedelta(days=3)
        Game.objects.filter(id=old_in_progress.id).update(
            created_at=old_activity,
            progressed_at=old_activity,
        )

        url = reverse("medialist", args=[MediaTypes.GAME.value])
        response = self.client.get(
            url,
            {"platform": "PlayStation 5", "status": Status.IN_PROGRESS.value},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertContains(response, "Still In Progress")
        self.assertNotContains(response, "Completed Now, Was In Progress")

    def test_book_format_filter_uses_collection_entry_media_type(self):
        """Book format options should include collection-only formats like Audiobook."""
        book_item = Item.objects.create(
            media_id="book-audiobook-filter",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Audiobook Filter Book",
            image="http://example.com/book.jpg",
            format="",
        )
        Book.objects.bulk_create(
            [
                Book(
                    item=book_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=0,
                ),
            ],
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=book_item,
            media_type="Audiobook",
        )

        url = reverse("medialist", args=[MediaTypes.BOOK.value])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_data"]["show_formats"])
        self.assertTrue(
            any(
                option["value"] == "audiobook" and option["label"] == "Audiobook"
                for option in response.context["filter_data"]["formats"]
            ),
        )

        filtered_response = self.client.get(f"{url}?format=audiobook")
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(filtered_response.context["current_format"], "audiobook")
        self.assertEqual(filtered_response.context["media_list"].paginator.count, 1)
        self.assertContains(filtered_response, "Audiobook Filter Book")

    def test_book_author_filter_shows_and_filters_tracked_books(self):
        book_with_author = Item.objects.create(
            media_id="book-author-filter-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="Author Filter Book One",
            image="http://example.com/book1.jpg",
            authors=["Author One"],
        )
        other_book = Item.objects.create(
            media_id="book-author-filter-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="Author Filter Book Two",
            image="http://example.com/book2.jpg",
            authors=["Author Two"],
        )
        Book.objects.create(
            item=book_with_author,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        Book.objects.create(
            item=other_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        url = reverse("medialist", args=[MediaTypes.BOOK.value])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_data"]["show_authors"])
        self.assertTrue(
            any(
                option["value"] == "Author One"
                for option in response.context["filter_data"]["authors"]
            ),
        )

        filtered_response = self.client.get(f"{url}?author=Author One")
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(filtered_response.context["current_author"], "Author One")
        self.assertEqual(filtered_response.context["media_list"].paginator.count, 1)
        self.assertContains(filtered_response, "Author Filter Book One")
        self.assertNotContains(filtered_response, "Author Filter Book Two")

    def test_book_sort_by_author_orders_alphabetically(self):
        first_book = Item.objects.create(
            media_id="book-author-sort-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="Zulu Title",
            image="http://example.com/book-sort-1.jpg",
            authors=["Bravo Writer"],
        )
        second_book = Item.objects.create(
            media_id="book-author-sort-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="Alpha Title",
            image="http://example.com/book-sort-2.jpg",
            authors=["Alpha Writer"],
        )
        Book.objects.create(
            item=first_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        Book.objects.create(
            item=second_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value]) + "?sort=author",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "author")
        self.assertEqual(response.context["current_direction"], "asc")
        self.assertContains(response, "toggleSort('author')")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:2]
            ],
            ["Alpha Title", "Zulu Title"],
        )

    def test_book_table_renders_author_column(self):
        book_item = Item.objects.create(
            media_id="book-author-column-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="Author Column Book",
            image="http://example.com/book-column.jpg",
            authors=["Author One", "Author Two"],
        )
        Book.objects.create(
            item=book_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.BOOK.value]) + "?layout=table",
        )

        self.assertEqual(response.status_code, 200)
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertIn("author", column_keys)
        self.assertContains(response, "Author One, Author Two")

    def test_comic_and_manga_author_filter_work(self):
        comic_item = Item.objects.create(
            media_id="comic-author-filter-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.COMIC.value,
            title="Author Filter Comic",
            image="http://example.com/comic.jpg",
            authors=["Writer Alpha"],
        )
        manga_item = Item.objects.create(
            media_id="manga-author-filter-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Author Filter Manga",
            image="http://example.com/manga.jpg",
            authors=["Mangaka Beta"],
        )
        Comic.objects.create(
            item=comic_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        Manga.objects.create(
            item=manga_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        comic_url = reverse("medialist", args=[MediaTypes.COMIC.value])
        comic_response = self.client.get(f"{comic_url}?author=Writer Alpha")
        self.assertEqual(comic_response.status_code, 200)
        self.assertTrue(comic_response.context["filter_data"]["show_authors"])
        self.assertEqual(comic_response.context["media_list"].paginator.count, 1)
        self.assertContains(comic_response, "Author Filter Comic")

        manga_url = reverse("medialist", args=[MediaTypes.MANGA.value])
        manga_response = self.client.get(f"{manga_url}?author=Mangaka Beta")
        self.assertEqual(manga_response.status_code, 200)
        self.assertTrue(manga_response.context["filter_data"]["show_authors"])
        self.assertEqual(manga_response.context["media_list"].paginator.count, 1)
        self.assertContains(manga_response, "Author Filter Manga")

    def test_movie_provider_filter_shows_and_filters_when_region_configured(self):
        netflix_movie = Item.objects.create(
            media_id="provider-filter-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Provider Filter Movie One",
            image="http://example.com/pf1.jpg",
            watch_providers={
                "US": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]},
            },
        )
        other_movie = Item.objects.create(
            media_id="provider-filter-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Provider Filter Movie Two",
            image="http://example.com/pf2.jpg",
            watch_providers={
                "US": {"flatrate": [{"provider_id": 384, "provider_name": "HBO Max"}]},
            },
        )
        Movie.objects.create(
            item=netflix_movie,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        Movie.objects.create(
            item=other_movie,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        self.user.watch_provider_region = "US"
        self.user.save(update_fields=["watch_provider_region"])

        url = reverse("medialist", args=[MediaTypes.MOVIE.value])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_data"]["show_providers"])
        self.assertTrue(
            any(
                option["value"] == "Netflix"
                for option in response.context["filter_data"]["providers"]
            ),
        )

        filtered_response = self.client.get(f"{url}?provider=Netflix")
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(filtered_response.context["current_provider"], "Netflix")
        self.assertContains(filtered_response, "Provider Filter Movie One")
        self.assertNotContains(filtered_response, "Provider Filter Movie Two")

    def test_movie_provider_filter_hidden_without_region_configured(self):
        url = reverse("medialist", args=[MediaTypes.MOVIE.value])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["filter_data"]["show_providers"])

    def test_pinned_provider_shown_and_filters_outside_home_region(self):
        """A pinned provider from a non-home region still filters the library (#519)."""
        uk_only_movie = Item.objects.create(
            media_id="provider-filter-pinned-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Provider Filter Pinned Movie",
            image="http://example.com/pfp1.jpg",
            watch_providers={
                "GB": {"flatrate": [{"provider_id": 39, "provider_name": "Now TV"}]},
            },
        )
        other_movie = Item.objects.create(
            media_id="provider-filter-pinned-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Provider Filter Other Movie",
            image="http://example.com/pfp2.jpg",
            watch_providers={
                "US": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]},
            },
        )
        Movie.objects.create(
            item=uk_only_movie,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        Movie.objects.create(
            item=other_movie,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        # No home region configured, but "Now TV" (GB-only) is pinned.
        self.user.pinned_watch_providers = ["Now TV"]
        self.user.save(update_fields=["pinned_watch_providers"])

        url = reverse("medialist", args=[MediaTypes.MOVIE.value])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_data"]["show_providers"])
        self.assertTrue(
            any(
                option["value"] == "Now TV"
                for option in response.context["filter_data"]["providers"]
            ),
        )

        filtered_response = self.client.get(f"{url}?provider=Now TV")
        self.assertEqual(filtered_response.status_code, 200)
        self.assertContains(filtered_response, "Provider Filter Pinned Movie")
        self.assertNotContains(filtered_response, "Provider Filter Other Movie")

    @mock.patch("app.media_list_views.tmdb.global_watch_provider_catalog")
    def test_global_provider_choices_deduplicate_names(self, mock_catalog):
        """Provider-name keys must remain unique for Alpine's x-for rendering."""
        mock_catalog.return_value = [
            {
                "provider_id": 1,
                "provider_name": "Amazon MX Player",
                "image": "https://example.com/movie-logo.jpg",
            },
            {
                "provider_id": 2,
                "provider_name": "Amazon MX Player",
                "image": "https://example.com/tv-logo.jpg",
            },
        ]

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["filter_data"]["all_providers"],
            [
                {
                    "value": "Amazon MX Player",
                    "label": "Amazon MX Player",
                    "image": "https://example.com/tv-logo.jpg",
                },
            ],
        )

    def test_toggle_pinned_provider_requires_authentication(self):
        self.client.logout()
        response = self.client.post(
            reverse("toggle_pinned_provider"), {"provider_name": "Netflix"}
        )
        self.assertEqual(response.status_code, 302)

    def test_toggle_pinned_provider_pins_and_unpins(self):
        url = reverse("toggle_pinned_provider")

        response = self.client.post(url, {"provider_name": "Netflix"})
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pinned_watch_providers, ["Netflix"])

        response = self.client.post(url, {"provider_name": "Netflix"})
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pinned_watch_providers, [])

    def test_movie_filter_data_hides_author_filter(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["filter_data"]["show_authors"])

    def test_non_bookish_sort_hides_author_option_and_falls_back(self):
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?sort=author",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")
        self.assertNotContains(response, "toggleSort('author')")

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_sort, "title")

    def test_media_list_filter_persistence_serializes_filter_form_fields(self):
        """Filter persistence should derive keys from the hidden filter form."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?tag=Favorite&tag_mode=not&layout=grid",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_tag"], ["Favorite"])
        self.assertEqual(response.context["current_tag_mode"], "not")
        self.assertContains(
            response, "function buildMediaListFilterParams(form, overrides = {})"
        )
        self.assertContains(response, "Array.from(form.elements)")
        self.assertContains(response, "const persistedKeys = Array.from(")
        self.assertNotContains(response, "const persistedKeys = [")

    def test_saved_tv_tag_exclusion_tracks_tags_added_after_save(self):
        """A saved NOT-tag filter re-evaluates TV associations on every request."""
        tagged_before_save = self._create_tv_runtime_entry(
            "saved-tag-tv-1", "Tagged before save", [24]
        )
        tagged_later_one = self._create_tv_runtime_entry(
            "saved-tag-tv-2", "Tagged later one", [24]
        )
        tagged_later_two = self._create_tv_runtime_entry(
            "saved-tag-tv-3", "Tagged later two", [24]
        )
        tag = Tag.objects.create(user=self.user, name="BunnyEarsTV")
        self.client.post(
            reverse("tag_item_toggle"),
            {"tag_id": tag.id, "item_id": tagged_before_save.id},
        )

        saved_response = self.client.post(
            reverse("saved_view_create"),
            {
                "media_type": MediaTypes.TV.value,
                "name": "Un-tagged in-progress TV",
                "status": Status.IN_PROGRESS.value,
                "tag": tag.name,
                "tag_mode": "not",
            },
        )
        self.assertEqual(saved_response.status_code, 200)
        saved_url = saved_response.json()["url"]

        initial = self.client.get(saved_url)
        initial_ids = {
            entry.item.id for entry in initial.context["media_list"].object_list
        }
        self.assertNotIn(tagged_before_save.id, initial_ids)
        self.assertIn(tagged_later_one.id, initial_ids)
        self.assertIn(tagged_later_two.id, initial_ids)

        for item in (tagged_later_one, tagged_later_two):
            toggle_response = self.client.post(
                reverse("tag_item_toggle"),
                {"tag_id": tag.id, "item_id": item.id},
            )
            self.assertEqual(toggle_response.status_code, 200)
            self.assertTrue(ItemTag.objects.filter(tag=tag, item=item).exists())

        refreshed = self.client.get(saved_url)
        refreshed_ids = {
            entry.item.id for entry in refreshed.context["media_list"].object_list
        }
        self.assertNotIn(tagged_before_save.id, refreshed_ids)
        self.assertNotIn(tagged_later_one.id, refreshed_ids)
        self.assertNotIn(tagged_later_two.id, refreshed_ids)

        # The same exclusion remains active after returning through the saved URL.
        reopened = self.client.get(saved_url)
        reopened_ids = {
            entry.item.id for entry in reopened.context["media_list"].object_list
        }
        self.assertEqual(reopened_ids, refreshed_ids)

    def test_tag_exclusion_handles_retag_multiple_tags_and_other_media(self):
        """NOT excludes any matching tag while preserving other media types."""
        tv_item = self._create_tv_runtime_entry(
            "saved-tag-tv-retag", "Retagged TV", [24]
        )
        movie_item = Item.objects.create(
            media_id="saved-tag-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Tagged Movie",
            image="http://example.com/tagged-movie.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        excluded = Tag.objects.create(user=self.user, name="BunnyEarsTV")
        other = Tag.objects.create(user=self.user, name="AlsoExcluded")

        toggle_url = reverse("tag_item_toggle")
        for _ in range(3):
            self.client.post(
                toggle_url, {"tag_id": excluded.id, "item_id": tv_item.id}
            )
        self.client.post(toggle_url, {"tag_id": other.id, "item_id": tv_item.id})
        self.client.post(toggle_url, {"tag_id": excluded.id, "item_id": movie_item.id})
        self.assertTrue(ItemTag.objects.filter(tag=excluded, item=tv_item).exists())
        self.assertTrue(ItemTag.objects.filter(tag=other, item=tv_item).exists())
        self.assertTrue(ItemTag.objects.filter(tag=excluded, item=movie_item).exists())

        tv_response = self.client.get(
            reverse("medialist", args=[MediaTypes.TV.value]),
            {
                "status": Status.IN_PROGRESS.value,
                "tag": [excluded.name, other.name],
                "tag_mode": "not",
            },
        )
        tv_ids = {
            entry.item.id for entry in tv_response.context["media_list"].object_list
        }
        self.assertNotIn(tv_item.id, tv_ids)

        movie_response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]),
            {"status": Status.IN_PROGRESS.value, "tag": excluded.name, "tag_mode": "not"},
        )
        movie_ids = {
            entry.item.id for entry in movie_response.context["media_list"].object_list
        }
        self.assertNotIn(movie_item.id, movie_ids)

    def test_media_list_layout_toggle_uses_shared_filter_serializer(self):
        """The settings menu island switches layout through the page's shared serializer, not a query string of its own."""
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-settings-menu-root")
        self.assertContains(
            response,
            "layoutHref(nextLayout) { return buildMediaListHref(this.mediaListUrl, document.getElementById('filter-form'), { layout: nextLayout }); }",
        )
        self.assertNotContains(
            response, "layout = layout === 'grid' ? 'table' : 'grid'"
        )

    def test_filters_status_and_sort_are_the_shared_filter_bar_island(self):
        """The three toolbar dropdowns mount the shared FilterBar, which groups the date options under Date."""
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertContains(response, "data-filter-bar-root")
        self.assertContains(response, '"date": "Date"')
        self.assertContains(response, '"releaseDate": "Release date"')
        self.assertContains(response, '"addedDate": "Added date"')
        self.assertContains(response, '"completedDate": "Completed date"')
        self.assertContains(response, '"releaseStatus": "Release status"')
        for key in ("year", "release"):
            self.assertContains(response, f'"key": "{key}"')
        for key in ("releaseDate", "dateAdded", "completedDate"):
            self.assertContains(response, f'"key": "{key}"')

    def test_comic_media_list_can_switch_to_issue_subview(self):
        """Comic media list should reuse the music-style subview switch for issues."""
        comic_item = Item.objects.create(
            media_id="comic-1",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Volume One",
            image="http://example.com/comic.jpg",
        )
        Comic.objects.create(
            item=comic_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=3,
        )

        issue_item = Item.objects.create(
            media_id="issue-1",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Issue One",
            image="http://example.com/issue.jpg",
        )
        ComicIssue.objects.create(
            item=issue_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.COMIC.value]) + "?subview=issues",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_subview"], "issues")
        self.assertEqual(response.context["media_type"], MediaTypes.COMIC.value)
        self.assertEqual(response.context["media_type_plural"], "comic issues")
        self.assertContains(response, "?subview=comics")
        self.assertContains(response, "?subview=issues")
        self.assertContains(response, "Issue One")
        self.assertNotContains(response, "Volume One")
        self.assertEqual(
            response.context["media_list"].object_list[0].item.media_type,
            MediaTypes.COMIC_ISSUE.value,
        )

    def test_media_list_htmx_request(self):
        """Test the media list view with HTMX request."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=grid",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_grid_items.html")

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/table_items.html")

    def test_table_column_refresh_wiring_is_present(self):
        """Table layout should render deterministic column refresh wiring."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
        )

        self.assertContains(response, 'id="column-pref-form"')
        self.assertContains(
            response, "this.$refs.form.addEventListener('htmx:afterRequest'"
        )
        self.assertContains(response, "htmx.ajax('GET'")
        self.assertContains(response, "column_refresh_nonce")
        self.assertContains(response, "save_after_request successful=")
        self.assertContains(response, "refresh_dispatch source=save_after_request")
        self.assertNotContains(response, 'id="column-refresh-runner"')
        self.assertNotContains(response, 'hx-trigger="runColumnRefresh"')

    def test_paginated_media_layouts_render_correctly(self):
        """Grid and table pagination retain their layout-specific safeguards."""
        extra_ids = [f"extra-{i}" for i in range(6, 41)]
        Item.objects.bulk_create(
            [
                Item(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title=f"Extra Movie {media_id}",
                    image="http://example.com/image.jpg",
                )
                for media_id in extra_ids
            ],
        )
        extra_items = {
            item.media_id: item
            for item in Item.objects.filter(
                media_id__in=extra_ids,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
            )
        }
        Movie.objects.bulk_create(
            [
                Movie(
                    item=extra_items[media_id],
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=0,
                    score=5,
                )
                for media_id in extra_ids
            ],
        )

        headers = {"HTTP_HX_REQUEST": "true"}
        grid_page = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=grid&page=1",
            **headers,
        )
        self.assertContains(
            grid_page,
            'hx-trigger="revealed threshold:200px once"',
            html=False,
        )
        grid_html = grid_page.content.decode()
        # Grid cards open their track and lists popups from the shared React
        # host, so the page no longer carries a hidden popup per card.
        self.assertContains(grid_page, "data-media-card-root")
        self.assertNotContains(grid_page, 'x-show="trackOpen"')
        self.assertNotContains(grid_page, 'x-show="listsOpen"')

        first_page = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=table&page=1",
            **headers,
        )
        first_html = first_page.content.decode()
        header_count = first_html.count("<th ")
        self.assertGreater(header_count, 0)

        first_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", first_html, flags=re.DOTALL)
        self.assertGreater(len(first_rows), 0)
        for row_html in first_rows:
            if "<td " not in row_html:
                continue
            self.assertEqual(row_html.count("<td "), header_count)

        second_page = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?layout=table&page=2",
            **headers,
        )
        second_html = second_page.content.decode()
        self.assertNotIn("<thead", second_html)
        self.assertRegex(second_html, r'x-show="trackOpen"[^>]*\sx-cloak\b')

        second_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", second_html, flags=re.DOTALL)
        self.assertGreater(len(second_rows), 0)
        for row_html in second_rows:
            self.assertEqual(row_html.count("<td "), header_count)

    def test_column_preferences_endpoint_updates_user_prefs(self):
        """Column preference updates should persist and trigger table refresh."""
        response = self.client.post(
            reverse("medialist_columns", args=[MediaTypes.MOVIE.value]),
            {
                "table_type": "media",
                "sort": "score",
                "order": json.dumps(["status"]),
                "hidden": json.dumps(["status"]),
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertIn("HX-Trigger", response)
        self.assertIn("refreshTableColumns", response["HX-Trigger"])

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.table_column_prefs[MediaTypes.MOVIE.value],
            {
                "order": [
                    "status",
                    "score",
                    "critic_rating",
                    "runtime",
                    "time_watched",
                    "popularity",
                    "genres",
                    "tags",
                    "release_date",
                    "date_added",
                    "start_date",
                    "end_date",
                    "entry_source",
                    "notes",
                    "synopsis",
                ],
                "hidden": ["status"],
            },
        )

    def test_table_columns_keep_fixed_columns_at_front_after_save(self):
        """Saving prefs without fixed columns in order keeps them anchored first."""
        self.client.post(
            reverse("medialist_columns", args=[MediaTypes.MOVIE.value]),
            {
                "table_type": "media",
                "sort": "score",
                "order": json.dumps(["end_date", "status"]),
                "hidden": json.dumps([]),
            },
            HTTP_HX_REQUEST="true",
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
        )
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertEqual(column_keys[:2], ["image", "title"])
        self.assertEqual(column_keys[2:4], ["end_date", "status"])

    def test_column_preferences_second_save_wins(self):
        """Consecutive saves should persist and render the latest submitted order."""
        url = reverse("medialist_columns", args=[MediaTypes.MOVIE.value])
        first = self.client.post(
            url,
            {
                "table_type": "media",
                "sort": "score",
                "order": json.dumps(["status", "end_date"]),
                "hidden": json.dumps([]),
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(first.status_code, 204)

        second = self.client.post(
            url,
            {
                "table_type": "media",
                "sort": "score",
                "order": json.dumps(["score", "start_date"]),
                "hidden": json.dumps([]),
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(second.status_code, 204)

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.table_column_prefs[MediaTypes.MOVIE.value]["order"],
            [
                "score",
                "start_date",
                "critic_rating",
                "runtime",
                "time_watched",
                "popularity",
                "genres",
                "tags",
                "status",
                "release_date",
                "date_added",
                "end_date",
                "entry_source",
                "notes",
                "synopsis",
            ],
        )

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
        )
        column_keys = [column.key for column in response.context["resolved_columns"]]
        self.assertEqual(
            column_keys,
            [
                "image",
                "title",
                "score",
                "start_date",
                "critic_rating",
                "runtime",
                "time_watched",
                "popularity",
                "genres",
                "tags",
                "status",
                "release_date",
                "date_added",
                "end_date",
                "entry_source",
                "notes",
                "synopsis",
            ],
        )

    def test_consecutive_column_reorder_full_round_trip(self):
        """Two consecutive column saves should each render after HTMX refresh."""
        columns_url = reverse("medialist_columns", args=[MediaTypes.MOVIE.value])
        list_url = reverse("medialist", args=[MediaTypes.MOVIE.value])
        list_query = "?layout=table&sort=score&direction=desc"
        htmx_headers = {"HTTP_HX_REQUEST": "true"}

        def assert_partial_table_refresh(response, expected_labels):
            self.assertEqual(response.status_code, 200)
            self.assertIn("HX-Trigger", response)
            trigger_payload = json.loads(response["HX-Trigger"])
            self.assertIn("resultCountUpdated", trigger_payload)

            html = response.content.decode()
            labels = [
                re.sub(r"<[^>]+>", "", label).strip()
                for label in re.findall(
                    r"<th\s[^>]*>(.*?)</th>",
                    html,
                    flags=re.DOTALL,
                )
            ]
            self.assertEqual(labels, expected_labels)

        first_order = ["end_date", "status", "score", "start_date"]
        first_save = self.client.post(
            columns_url,
            {
                "table_type": "media",
                "sort": "score",
                "order": json.dumps(first_order),
                "hidden": json.dumps([]),
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(first_save.status_code, 204)
        self.assertIn("HX-Trigger", first_save)
        self.assertEqual(
            json.loads(first_save["HX-Trigger"]),
            {"refreshTableColumns": True},
        )

        first_refresh = self.client.get(f"{list_url}{list_query}", **htmx_headers)
        assert_partial_table_refresh(
            first_refresh,
            [
                "",
                "",
                "Title",
                "End Date",
                "Status",
                "Score",
                "Start Date",
                "Critic Rating",
                "Runtime",
                "Time Watched",
                "Popularity",
                "Genres",
                "Tags",
                "Release Date",
                "Date Added",
                "Source",
                "Notes",
                "Description",
            ],
        )

        second_order = ["score", "start_date", "end_date", "status"]
        second_save = self.client.post(
            columns_url,
            {
                "table_type": "media",
                "sort": "score",
                "order": json.dumps(second_order),
                "hidden": json.dumps([]),
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(second_save.status_code, 204)
        self.assertIn("HX-Trigger", second_save)
        self.assertEqual(
            json.loads(second_save["HX-Trigger"]),
            {"refreshTableColumns": True},
        )

        second_refresh = self.client.get(f"{list_url}{list_query}", **htmx_headers)
        assert_partial_table_refresh(
            second_refresh,
            [
                "",
                "",
                "Title",
                "Score",
                "Start Date",
                "End Date",
                "Status",
                "Critic Rating",
                "Runtime",
                "Time Watched",
                "Popularity",
                "Genres",
                "Tags",
                "Release Date",
                "Date Added",
                "Source",
                "Notes",
                "Description",
            ],
        )

        full_page = self.client.get(f"{list_url}{list_query}")
        self.assertEqual(full_page.status_code, 200)
        self.assertContains(full_page, 'id="column-pref-form"')
        self.assertContains(full_page, f'hx-post="{columns_url}"')
        self.assertContains(full_page, 'hx-swap="none"')
        self.assertContains(full_page, 'x-data="columnConfigMenu({')
        self.assertContains(full_page, "refreshTableAfterColumnSave(options)")

        full_html = full_page.content.decode()
        config_match = re.search(
            r'<script id="media-column-config-data" type="application/json">'
            r"(.*?)</script>",
            full_html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(config_match)
        if config_match is not None:
            column_config = json.loads(config_match.group(1))
            self.assertEqual(
                [column["key"] for column in column_config],
                second_order
                + [
                    "critic_rating",
                    "runtime",
                    "time_watched",
                    "popularity",
                    "genres",
                    "tags",
                    "release_date",
                    "date_added",
                    "entry_source",
                    "notes",
                    "synopsis",
                ],
            )

        resolved_keys = [column.key for column in full_page.context["resolved_columns"]]
        self.assertEqual(
            resolved_keys,
            [
                "image",
                "title",
                "score",
                "start_date",
                "end_date",
                "status",
                "critic_rating",
                "runtime",
                "time_watched",
                "popularity",
                "genres",
                "tags",
                "release_date",
                "date_added",
                "entry_source",
                "notes",
                "synopsis",
            ],
        )

    def test_grouped_anime_library_mode_routes_grouped_titles(self):
        """Grouped anime TV rows should follow the user's anime library mode."""
        grouped_item = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Frieren: Beyond Journey's End",
            image="https://example.com/grouped-anime.jpg",
        )
        TV.objects.create(
            item=grouped_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.user.anime_library_mode = MediaTypes.ANIME.value
        self.user.save(update_fields=["anime_library_mode"])

        anime_response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
        )
        tv_response = self.client.get(reverse("medialist", args=[MediaTypes.TV.value]))

        anime_titles = [
            media.item.title
            for media in anime_response.context["media_list"].object_list
        ]
        tv_titles = [
            media.item.title for media in tv_response.context["media_list"].object_list
        ]

        self.assertIn("Frieren: Beyond Journey's End", anime_titles)
        self.assertNotIn("Frieren: Beyond Journey's End", tv_titles)

        self.user.anime_library_mode = MediaTypes.TV.value
        self.user.save(update_fields=["anime_library_mode"])

        anime_response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
        )
        tv_response = self.client.get(reverse("medialist", args=[MediaTypes.TV.value]))

        anime_titles = [
            media.item.title
            for media in anime_response.context["media_list"].object_list
        ]
        tv_titles = [
            media.item.title for media in tv_response.context["media_list"].object_list
        ]

        self.assertNotIn("Frieren: Beyond Journey's End", anime_titles)
        self.assertIn("Frieren: Beyond Journey's End", tv_titles)

    def test_grouped_anime_sort_by_popularity_uses_shared_rank_field(self):
        grouped_low = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Grouped Anime Low Rank",
            image="https://example.com/grouped-anime-low.jpg",
            trakt_rating=8.0,
            trakt_rating_count=1000,
            trakt_popularity_score=5000,
            trakt_popularity_rank=2,
            trakt_popularity_fetched_at=timezone.now() - timedelta(days=1),
        )
        grouped_high = Item.objects.create(
            media_id="9350139",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Grouped Anime High Rank",
            image="https://example.com/grouped-anime-high.jpg",
            trakt_rating=8.0,
            trakt_rating_count=1000,
            trakt_popularity_score=2500,
            trakt_popularity_rank=8,
            trakt_popularity_fetched_at=timezone.now() - timedelta(days=1),
        )
        TV.objects.create(
            item=grouped_low,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.create(
            item=grouped_high,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.user.anime_library_mode = MediaTypes.ANIME.value
        self.user.save(update_fields=["anime_library_mode"])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
            + "?search=Grouped+Anime&sort=popularity",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "popularity")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:2]
            ],
            ["Grouped Anime Low Rank", "Grouped Anime High Rank"],
        )

    def test_grouped_anime_sort_by_critic_rating_uses_provider_rating(self):
        grouped_low = Item.objects.create(
            media_id="9350140",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Grouped Anime Low Critic",
            image="https://example.com/grouped-anime-low-critic.jpg",
            provider_rating=7.4,
        )
        grouped_high = Item.objects.create(
            media_id="9350141",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Grouped Anime High Critic",
            image="https://example.com/grouped-anime-high-critic.jpg",
            provider_rating=8.9,
        )
        TV.objects.create(
            item=grouped_low,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.create(
            item=grouped_high,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.user.anime_library_mode = MediaTypes.ANIME.value
        self.user.save(update_fields=["anime_library_mode"])

        response = self.client.get(
            reverse("medialist", args=[MediaTypes.ANIME.value])
            + "?search=Grouped+Anime&sort=critic_rating",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "critic_rating")
        self.assertEqual(
            [
                media.item.title
                for media in response.context["media_list"].object_list[:2]
            ],
            ["Grouped Anime High Critic", "Grouped Anime Low Critic"],
        )


class MediaListRelativeCompletedDateTests(TestCase):
    """ "Completed in the last N units" on the library filter toolbar."""

    def setUp(self):
        """Log in with two movies completed at known distances from today."""
        cache.clear()
        self.credentials = {"username": "relative", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.recent = Item.objects.create(
            media_id="rel-recent",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Recent Movie",
            image="http://example.com/image.jpg",
        )
        self.old = Item.objects.create(
            media_id="rel-old",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Old Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=self.recent,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now() - timedelta(days=3),
        )
        Movie.objects.create(
            item=self.old,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now() - timedelta(days=200),
        )

    def _titles(self, **params):
        response = self.client.get(
            reverse("medialist", kwargs={"media_type": MediaTypes.MOVIE.value}),
            params,
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        return [title for title in ("Recent Movie", "Old Movie") if title in content]

    def test_window_narrows_the_list(self):
        """A 7-day window keeps only the recently completed movie."""
        self.assertEqual(sorted(self._titles()), ["Old Movie", "Recent Movie"])

        self.assertEqual(
            self._titles(completed_date_within="7", completed_date_within_unit="days"),
            ["Recent Movie"],
        )

    def test_wider_window_keeps_both(self):
        """A one-year window covers both."""
        self.assertEqual(
            sorted(
                self._titles(
                    completed_date_within="1",
                    completed_date_within_unit="years",
                ),
            ),
            ["Old Movie", "Recent Movie"],
        )

    def test_filter_form_carries_the_window_fields(self):
        """The toolbar must post the window, or the choice never reaches here.

        The form is included separately from the toolbar on this page, so a
        missing flag silently drops these inputs (and the multi-select platform
        fields) without breaking any other assertion.
        """
        response = self.client.get(
            reverse("medialist", kwargs={"media_type": MediaTypes.MOVIE.value}),
        )
        content = response.content.decode()

        for name in (
            "completed_date_from",
            "completed_date_within",
            "completed_date_within_unit",
            "platform_mode",
        ):
            self.assertIn(f'name="{name}"', content, name)


class GameGroupingViewTests(TestCase):
    """The Games library can roll up tracked games by catalog relationship."""

    def setUp(self):
        cache.clear()
        self.credentials = {"username": "grouping", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.url = reverse("medialist", kwargs={"media_type": MediaTypes.GAME.value})

    def _game(
        self,
        media_id,
        title,
        *,
        status=Status.IN_PROGRESS.value,
        progress=0,
        score=None,
        release_datetime=None,
        collection=("", ""),
        franchise=("", ""),
        image="",
    ):
        item = Item.objects.create(
            media_id=str(media_id),
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title=title,
            image=image,
            release_datetime=release_datetime,
            provider_collection_id=collection[0],
            provider_collection_name=collection[1],
            provider_franchise_id=franchise[0],
            provider_franchise_name=franchise[1],
        )
        Game.objects.create(
            item=item,
            user=self.user,
            status=status,
            progress=progress,
            score=score,
            end_date=timezone.now() if status == Status.COMPLETED.value else None,
        )
        return item

    def test_series_groups_use_igdb_collections(self):
        self._game(
            1,
            "Super Mario Bros.",
            progress=120,
            collection=("1", "Super Mario"),
            franchise=("10", "Mario"),
        )
        self._game(
            2,
            "Mario Kart",
            status=Status.COMPLETED.value,
            progress=300,
            collection=("2", "Mario Kart"),
            franchise=("10", "Mario"),
        )

        response = self.client.get(self.url, {"group_by": "series"})

        self.assertEqual(response.status_code, 200)
        groups = response.context["media_groups"]
        self.assertEqual(
            [group["name"] for group in groups],
            ["Mario Kart", "Super Mario"],
        )
        mario_kart = next(group for group in groups if group["name"] == "Mario Kart")
        self.assertEqual(mario_kart["game_count"], 1)
        self.assertEqual(mario_kart["played_count"], 1)
        self.assertEqual(mario_kart["completed_count"], 1)
        self.assertEqual(mario_kart["playtime_minutes"], 300)

    def test_franchise_groups_merge_their_series(self):
        self._game(
            1,
            "Super Mario Bros.",
            collection=("1", "Super Mario"),
            franchise=("10", "Mario"),
        )
        self._game(
            2,
            "Mario Kart",
            collection=("2", "Mario Kart"),
            franchise=("10", "Mario"),
        )

        response = self.client.get(self.url, {"group_by": "franchise"})

        groups = response.context["media_groups"]
        self.assertEqual([group["name"] for group in groups], ["Mario"])
        self.assertEqual(groups[0]["game_count"], 2)

    def test_missing_provider_groups_render_as_ungrouped(self):
        self._game(1, "Local Game")

        response = self.client.get(self.url, {"group_by": "franchise"})

        groups = response.context["media_groups"]
        self.assertEqual([group["name"] for group in groups], ["Ungrouped"])

    def test_ungrouped_games_are_paginated(self):
        for index in range(40):
            self._game(index + 1, f"Local Game {index + 1}")

        response = self.client.get(self.url, {"group_by": "franchise"})

        ungrouped_page = response.context["ungrouped_page"]
        self.assertEqual(len(ungrouped_page.object_list), 32)
        self.assertTrue(ungrouped_page.has_next())
        self.assertIn("ug_page=2", response.context["ungrouped_next_url"])

    def test_ungrouped_pagination_loads_more_cards(self):
        for index in range(40):
            self._game(index + 1, f"Local Game {index + 1}")

        response = self.client.get(
            self.url,
            {"group_by": "franchise", "ug_page": "2"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("media-card "), 8)
        self.assertNotIn('id="grouped-ungrouped', response.content.decode())

    def test_group_cards_sort_by_total_playtime(self):
        self._game(
            1,
            "Short Game",
            progress=60,
            franchise=("10", "Small Franchise"),
        )
        self._game(
            2,
            "Long Game",
            progress=600,
            franchise=("20", "Big Franchise"),
        )

        response = self.client.get(
            self.url,
            {"group_by": "franchise", "sort": "progress", "direction": "desc"},
        )

        self.assertEqual(
            [group["name"] for group in response.context["media_groups"]],
            ["Big Franchise", "Small Franchise"],
        )

    def test_group_rating_is_the_average_of_rated_games(self):
        self._game(1, "Mario 1", score=8, franchise=("10", "Mario"))
        self._game(2, "Mario 2", score=6, franchise=("10", "Mario"))
        self._game(3, "Unrated", franchise=("10", "Mario"))

        response = self.client.get(self.url, {"group_by": "franchise"})

        group = response.context["media_groups"][0]
        self.assertEqual(group["average_score"], 7.0)
        self.assertNotIn("media-card-rate-button", response.content.decode())

    def test_group_release_subtitle_is_a_year_range(self):
        self._game(
            1,
            "Old Game",
            release_datetime=datetime(2007, 1, 1, tzinfo=UTC),
            franchise=("10", "Mario"),
        )
        self._game(
            2,
            "New Game",
            release_datetime=datetime(2024, 1, 1, tzinfo=UTC),
            franchise=("10", "Mario"),
        )

        response = self.client.get(self.url, {"group_by": "franchise"})

        group = response.context["media_groups"][0]
        self.assertEqual(group["release_range"], "2007-2024")
        self.assertEqual(group["display_item"].display_release_year, "2007-2024")

    def test_grouped_count_is_groups_plus_ungrouped_games(self):
        self._game(1, "Mario 1", franchise=("10", "Mario"))
        self._game(2, "Mario 2", franchise=("10", "Mario"))
        self._game(3, "Zelda", franchise=("20", "Zelda"))
        self._game(4, "Local Game")

        response = self.client.get(self.url, {"group_by": "franchise"})

        self.assertEqual(response.context["result_count"], 3)

    def test_group_drilldown_count_is_games_in_that_group(self):
        self._game(1, "Mario 1", franchise=("10", "Mario"))
        self._game(2, "Mario 2", franchise=("10", "Mario"))
        self._game(3, "Zelda", franchise=("20", "Zelda"))

        response = self.client.get(
            self.url,
            {"group_by": "franchise", "group": "id:10"},
        )

        self.assertEqual(response.context["result_count"], 2)

    def test_group_card_uses_a_member_game_poster(self):
        self._game(
            1,
            "Super Mario Bros.",
            franchise=("10", "Mario"),
            image="https://example.com/mario.jpg",
        )

        response = self.client.get(self.url, {"group_by": "franchise"})

        group = response.context["media_groups"][0]
        self.assertEqual(group["image"], "https://example.com/mario.jpg")
        self.assertContains(response, "https://example.com/mario.jpg")

    def test_group_cards_link_to_that_group(self):
        self._game(
            1,
            "Super Mario Bros.",
            collection=("1", "Super Mario"),
            franchise=("10", "Mario"),
        )

        response = self.client.get(self.url, {"group_by": "franchise"})

        group = response.context["media_groups"][0]
        self.assertIn("group_by=franchise", group["href"])
        self.assertIn("group=id%3A10", group["href"])

    def test_opening_a_group_shows_its_games_as_regular_cards(self):
        self._game(
            1,
            "Super Mario Bros.",
            collection=("1", "Super Mario"),
            franchise=("10", "Mario"),
        )
        self._game(
            2,
            "Mario Kart",
            collection=("2", "Mario Kart"),
            franchise=("10", "Mario"),
        )
        self._game(
            3,
            "Donkey Kong Country",
            collection=("3", "Donkey Kong Country"),
            franchise=("20", "Donkey Kong"),
        )

        response = self.client.get(
            self.url,
            {"group_by": "franchise", "group": "id:10"},
        )

        self.assertEqual(response.context["current_game_group_name"], "Mario")
        self.assertCountEqual(
            [entry.item.title for entry in response.context["media_list"]],
            ["Super Mario Bros.", "Mario Kart"],
        )
        self.assertEqual(response.context["media_groups"], [])
        self.assertContains(response, "All Franchise")

    def test_group_by_does_not_change_a_flat_library(self):
        self._game(1, "Super Mario Bros.", collection=("1", "Super Mario"))

        response = self.client.get(self.url)

        self.assertEqual(response.context["current_group_by"], "item")
        self.assertEqual(response.context["media_groups"], [])

    def test_group_drilldown_keeps_the_library_alphabet_symbol(self):
        """The alphabet rail samples the library, including games outside a group."""
        self._game(1, "Éclair", franchise=("10", "First"))
        self._game(2, "Ωmega", franchise=("20", "Second"))

        response = self.client.get(
            self.url, {"group_by": "franchise", "group": "id:20"},
        )

        self.assertEqual(response.context["alphabet_other_initial"], "É")
        self.assertEqual(
            [entry.item.title for entry in response.context["media_list"]],
            ["Ωmega"],
        )
