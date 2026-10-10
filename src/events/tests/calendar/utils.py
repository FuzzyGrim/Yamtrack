from django.contrib.auth import get_user_model

from app.models import (
    TV,
    Anime,
    Book,
    Comic,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class CalendarFixturesMixin:
    """Shared media fixtures for calendar tests."""

    def setUp(self):
        """Create shared tracked-media fixtures used across calendar tests."""
        super().setUp()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.anime_item = Item.objects.create(
            media_id="437",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(
            item=self.anime_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="http://example.com/thegodfather.jpg",
        )
        Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.tv_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/breakingbad.jpg",
        )
        tv_object = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.season_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/breakingbad.jpg",
            season_number=1,
        )
        Season.objects.create(
            item=self.season_item,
            related_tv=tv_object,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.manga_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="http://example.com/berserk.jpg",
        )
        Manga.objects.create(
            item=self.manga_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.book_item = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="1984",
            image="http://example.com/1984.jpg",
        )
        Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.comic_item = Item.objects.create(
            media_id="60760",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Batman",
            image="http://example.com/batman.jpg",
        )
        Comic.objects.create(
            item=self.comic_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
