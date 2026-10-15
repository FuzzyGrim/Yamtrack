from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from app.models import (
    TV,
    Anime,
    BoardGame,
    Book,
    Comic,
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
from events.models import Event
from lists.models import CustomList, CustomListItem


class YamtrackApiTestCase(APITestCase):
    """Shared setup and request helpers for API endpoint tests."""

    def setUp(self):
        """Create auth fixtures and patch noisy side effects."""
        self.user1 = get_user_model().objects.create_user(
            username="api-test-user1",
        )
        self.user2 = get_user_model().objects.create_user(
            username="api-test-user2",
        )
        self.auth_headers = {"HTTP_X_API_KEY": self.user1.token}
        self.auth_headers2 = {"HTTP_X_API_KEY": self.user2.token}
        self.invalid_auth_headers = {"HTTP_X_API_KEY": "invalid-token"}

        # Disable external side effects while creating many media fixtures.
        self._fetch_releases_patcher = patch(
            "app.models.Item.fetch_releases",
            return_value=None,
        )
        self._fetch_releases_patcher.start()
        self.addCleanup(self._fetch_releases_patcher.stop)

        self._metadata_patcher = patch(
            "api.views.services.get_media_metadata",
            return_value={
                "max_progress": None,
                "related": {"seasons": [], "recommendations": []},
                "episodes": [],
            },
        )
        self._metadata_patcher.start()
        self.addCleanup(self._metadata_patcher.stop)

        self.items_by_type = {
            MediaTypes.MOVIE.value: [
                Item.objects.create(
                    media_id="701",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Movie 1",
                    image="https://example.com/movie-1.jpg",
                ),
                Item.objects.create(
                    media_id="702",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Movie 2",
                    image="https://example.com/movie-2.jpg",
                ),
                Item.objects.create(
                    media_id="703",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Movie 3",
                    image="https://example.com/movie-3.jpg",
                ),
            ],
            MediaTypes.TV.value: [
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title="TV Show 1",
                    image="https://example.com/tv-1.jpg",
                ),
                Item.objects.create(
                    media_id="1002",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title="TV Show 2",
                    image="https://example.com/tv-2.jpg",
                ),
                Item.objects.create(
                    media_id="1003",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title="TV Show 3",
                    image="https://example.com/tv-3.jpg",
                ),
            ],
            MediaTypes.SEASON.value: [
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    title="TV Show 1",
                    image="https://example.com/season-1.jpg",
                    season_number=1,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    title="TV Show 1",
                    image="https://example.com/season-2.jpg",
                    season_number=2,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    title="TV Show 1",
                    image="https://example.com/season-3.jpg",
                    season_number=3,
                ),
            ],
            MediaTypes.EPISODE.value: [
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="TV Show 1",
                    image="https://example.com/episode-1.jpg",
                    season_number=1,
                    episode_number=1,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="TV Show 1",
                    image="https://example.com/episode-2.jpg",
                    season_number=1,
                    episode_number=2,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="TV Show 1",
                    image="https://example.com/episode-3.jpg",
                    season_number=1,
                    episode_number=3,
                ),
            ],
            MediaTypes.ANIME.value: [
                Item.objects.create(
                    media_id="2001",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title="Anime 1",
                    image="https://example.com/anime-1.jpg",
                ),
                Item.objects.create(
                    media_id="2002",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title="Anime 2",
                    image="https://example.com/anime-2.jpg",
                ),
                Item.objects.create(
                    media_id="2003",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title="Anime 3",
                    image="https://example.com/anime-3.jpg",
                ),
            ],
            MediaTypes.MANGA.value: [
                Item.objects.create(
                    media_id="3001",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.MANGA.value,
                    title="Manga 1",
                    image="https://example.com/manga-1.jpg",
                ),
                Item.objects.create(
                    media_id="3002",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.MANGA.value,
                    title="Manga 2",
                    image="https://example.com/manga-2.jpg",
                ),
                Item.objects.create(
                    media_id="3003",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.MANGA.value,
                    title="Manga 3",
                    image="https://example.com/manga-3.jpg",
                ),
            ],
            MediaTypes.GAME.value: [
                Item.objects.create(
                    media_id="4001",
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    title="Game 1",
                    image="https://example.com/game-1.jpg",
                ),
                Item.objects.create(
                    media_id="4002",
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    title="Game 2",
                    image="https://example.com/game-2.jpg",
                ),
                Item.objects.create(
                    media_id="4003",
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    title="Game 3",
                    image="https://example.com/game-3.jpg",
                ),
            ],
            MediaTypes.BOOK.value: [
                Item.objects.create(
                    media_id="5001",
                    source=Sources.OPENLIBRARY.value,
                    media_type=MediaTypes.BOOK.value,
                    title="Book 1",
                    image="https://example.com/book-1.jpg",
                ),
                Item.objects.create(
                    media_id="5002",
                    source=Sources.OPENLIBRARY.value,
                    media_type=MediaTypes.BOOK.value,
                    title="Book 2",
                    image="https://example.com/book-2.jpg",
                ),
                Item.objects.create(
                    media_id="5003",
                    source=Sources.OPENLIBRARY.value,
                    media_type=MediaTypes.BOOK.value,
                    title="Book 3",
                    image="https://example.com/book-3.jpg",
                ),
            ],
            MediaTypes.COMIC.value: [
                Item.objects.create(
                    media_id="6001",
                    source=Sources.COMICVINE.value,
                    media_type=MediaTypes.COMIC.value,
                    title="Comic 1",
                    image="https://example.com/comic-1.jpg",
                ),
                Item.objects.create(
                    media_id="6002",
                    source=Sources.COMICVINE.value,
                    media_type=MediaTypes.COMIC.value,
                    title="Comic 2",
                    image="https://example.com/comic-2.jpg",
                ),
                Item.objects.create(
                    media_id="6003",
                    source=Sources.COMICVINE.value,
                    media_type=MediaTypes.COMIC.value,
                    title="Comic 3",
                    image="https://example.com/comic-3.jpg",
                ),
            ],
            MediaTypes.BOARDGAME.value: [
                Item.objects.create(
                    media_id="7001",
                    source=Sources.BGG.value,
                    media_type=MediaTypes.BOARDGAME.value,
                    title="Board Game 1",
                    image="https://example.com/boardgame-1.jpg",
                ),
                Item.objects.create(
                    media_id="7002",
                    source=Sources.BGG.value,
                    media_type=MediaTypes.BOARDGAME.value,
                    title="Board Game 2",
                    image="https://example.com/boardgame-2.jpg",
                ),
                Item.objects.create(
                    media_id="7003",
                    source=Sources.BGG.value,
                    media_type=MediaTypes.BOARDGAME.value,
                    title="Board Game 3",
                    image="https://example.com/boardgame-3.jpg",
                ),
            ],
        }

        # Various tracked medias for user1.
        self.tv_medias = [
            TV.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.TV.value]
        ]
        self.movie_medias = [
            Movie.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.MOVIE.value]
        ]
        self.anime_medias = [
            Anime.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.ANIME.value]
        ]
        self.manga_medias = [
            Manga.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.MANGA.value]
        ]
        self.game_medias = [
            Game.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.GAME.value]
        ]
        self.book_medias = [
            Book.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.BOOK.value]
        ]
        self.comic_medias = [
            Comic.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.COMIC.value]
        ]
        self.boardgame_medias = [
            BoardGame.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.BOARDGAME.value]
        ]

        self.season_medias = [
            Season.objects.create(
                item=item,
                user=self.user1,
                related_tv=self.tv_medias[0],
                status=Status.IN_PROGRESS.value,
            )
            for item in self.items_by_type[MediaTypes.SEASON.value]
        ]

        self.episode_medias = Episode.objects.bulk_create(
            [
                Episode(
                    item=item,
                    related_season=self.season_medias[0],
                )
                for item in self.items_by_type[MediaTypes.EPISODE.value]
            ],
        )

        self.calendar_events = [
            Event.objects.create(
                item=item,
                content_number=None,
                datetime=timezone.now(),
            )
            for item in self.items_by_type["movie"]
        ]
        self.calendar_events += [
            Event.objects.create(
                item=self.items_by_type["manga"][0],
                content_number=None,
                datetime=timezone.now() - timezone.timedelta(days=10),
            )
        ]
        self.calendar_events += [
            Event.objects.create(
                item=self.items_by_type["comic"][0],
                content_number=None,
                datetime=timezone.now() + timezone.timedelta(days=10),
            )
        ]

        self._build_lists_fixtures()

        self._build_changes_history_fixtures()

    def _build_lists_fixtures(self):
        """Create reusable lists with linked media items for endpoint tests."""
        self.lists_by_name = {
            "favorites": CustomList.objects.create(
                name="Favorites",
                description="Primary seeded list",
                owner=self.user1,
            ),
            "watching": CustomList.objects.create(
                name="Watching Now",
                description="Secondary seeded list",
                owner=self.user1,
            ),
            "shared": CustomList.objects.create(
                name="Shared Picks",
                description="Shared list between fixtures users",
                owner=self.user1,
            ),
            "user2_private": CustomList.objects.create(
                name="User2 Private",
                description="Private list owned by user2",
                owner=self.user2,
            ),
        }
        self.lists_by_name["shared"].collaborators.add(self.user2)

        seeded_items = [
            (
                self.lists_by_name["favorites"],
                self.items_by_type[MediaTypes.MOVIE.value][0],
            ),
            (
                self.lists_by_name["favorites"],
                self.items_by_type[MediaTypes.TV.value][0],
            ),
            (
                self.lists_by_name["favorites"],
                self.items_by_type[MediaTypes.ANIME.value][0],
            ),
            (
                self.lists_by_name["watching"],
                self.items_by_type[MediaTypes.TV.value][1],
            ),
            (
                self.lists_by_name["watching"],
                self.items_by_type[MediaTypes.SEASON.value][0],
            ),
            (
                self.lists_by_name["shared"],
                self.items_by_type[MediaTypes.MANGA.value][0],
            ),
            (
                self.lists_by_name["shared"],
                self.items_by_type[MediaTypes.BOOK.value][0],
            ),
            (
                self.lists_by_name["shared"],
                self.items_by_type[MediaTypes.COMIC.value][1],
            ),
        ]

        self.list_items = CustomListItem.objects.bulk_create(
            [
                CustomListItem(custom_list=custom_list, item=item)
                for custom_list, item in seeded_items
            ],
        )

    def _build_changes_history_fixtures(self):
        """Create history rows linked to user1 for changes-history endpoint tests."""
        media_for_history = [
            self.movie_medias[0],
            self.tv_medias[0],
            self.season_medias[0],
            self.anime_medias[0],
            self.manga_medias[0],
            self.game_medias[0],
            self.book_medias[0],
            self.comic_medias[0],
            self.boardgame_medias[0],
        ]

        for index, media in enumerate(media_for_history, start=1):
            media._history_user = self.user1
            media.notes = f"history-seed-{index}"
            media.save()

        # Add one extra change on movie to ensure at least two snapshots exist.
        movie = self.movie_medias[0]
        movie._history_user = self.user1
        movie.score = 8
        movie.save()

        self.changes_history_entries = {
            "movie": movie.history.filter(history_user=self.user1).first(),
            "tv": self.tv_medias[0].history.filter(history_user=self.user1).first(),
            "season": self.season_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "anime": self.anime_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "manga": self.manga_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "game": self.game_medias[0].history.filter(history_user=self.user1).first(),
            "book": self.book_medias[0].history.filter(history_user=self.user1).first(),
            "comic": self.comic_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "boardgame": self.boardgame_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
        }

    def build_episode_metadata(
        self,
        tv_item,
        season_number,
        episode_number,
        title,
        image,
        synopsis="episode synopsis",
        score=8.5,
        score_count=50,
    ):
        """Build season metadata payload containing one episode entry."""
        return {
            "media_id": tv_item.media_id,
            "source": tv_item.source,
            "media_type": MediaTypes.TV.value,
            "season_number": season_number,
            "source_url": f"https://example.com/season/{season_number}",
            "title": title,
            "image": image,
            "max_progress": 1,
            "synopsis": synopsis,
            "genres": [],
            "score": score,
            "score_count": score_count,
            "details": {},
            "related": {},
            "episodes": [
                {
                    "episode_number": int(episode_number),
                    "season_number": int(season_number),
                    "name": title,
                    "overview": synopsis,
                    "vote_average": score,
                    "vote_count": score_count,
                    "air_date": None,
                    "runtime": None,
                    "episode_type": None,
                    "crew": [],
                    "guest_stars": [],
                    "still_path": None,
                },
            ],
        }

    def call_api(
        self, method, url_name, args=(), params=None, payload=None, headers=None
    ):
        """Call an API endpoint using a named URL and optional payload."""
        request_method = getattr(self.client, method.lower())
        request_kwargs = dict(headers or {})

        url = reverse(url_name, args=args)
        if params:
            request_kwargs["QUERY_STRING"] = urlencode(params, doseq=True)

        if payload is not None:
            request_kwargs["data"] = payload
            request_kwargs["content_type"] = "application/json"

        return request_method(url, **request_kwargs)
