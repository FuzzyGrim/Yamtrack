"""Cards load the provider's own smaller size; other pictures are untouched."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from api.serializers import ItemSerializer
from app.artwork_sizes import card_image_url, sized_image_url
from app.card_surfaces import card_context
from app.image_cache import rewrite_payload_images
from app.media_card_model import card_model
from app.models import Item, MediaTypes, Movie, Sources, Status

IGDB_ORIGINAL = "https://images.igdb.com/igdb/image/upload/t_original/co53e3.jpg"
IGDB_CARD = "https://images.igdb.com/igdb/image/upload/t_cover_big_2x/co53e3.jpg"
TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original/abc.jpg"
TMDB_CARD = "https://image.tmdb.org/t/p/w780/abc.jpg"


class SizedImageUrlTest(SimpleTestCase):
    """The size table: only known hosts, only ever smaller."""

    def test_card_sizes(self):
        """IGDB and TMDB originals become their card sizes."""
        self.assertEqual(card_image_url(IGDB_ORIGINAL), IGDB_CARD)
        self.assertEqual(card_image_url(TMDB_ORIGINAL), TMDB_CARD)

    def test_already_sized_urls_stay(self):
        """A poster stored at w500 or a cover already at card size is left alone."""
        for url in (
            "https://image.tmdb.org/t/p/w500/abc.jpg",
            "https://image.tmdb.org/t/p/w185/abc.jpg",
            IGDB_CARD,
            "https://images.igdb.com/igdb/image/upload/t_cover_big/co28ch.webp",
        ):
            self.assertEqual(card_image_url(url), url)

    def test_other_hosts_and_values_pass_through(self):
        """Unknown hosts, local covers, data URLs and empty values are unchanged."""
        for url in (
            "https://upload.wikimedia.org/wikipedia/commons/b/b0/x.jpg",
            "/covers/" + "a" * 64 + ".jpg",
            "data:image/svg+xml;base64,PHN2Zz4=",
            "http://image.tmdb.org/t/p/original/abc.jpg",
            "",
            None,
        ):
            self.assertEqual(card_image_url(url), url)

    def test_other_intents_keep_full_size(self):
        """Hero and backdrop pictures keep the stored size."""
        self.assertEqual(sized_image_url(TMDB_ORIGINAL, "hero"), TMDB_ORIGINAL)

    def test_proxy_rewrites_card_image_like_image(self):
        """With the image cache on, the API's card_image goes through the proxy too."""
        payload = {"image": "a", "card_image": "b"}
        rewritten = rewrite_payload_images(payload)
        self.assertEqual(set(rewritten), {"image", "card_image"})


class CardImageTest(TestCase):
    """Card props and the media API carry the card size; Detail keeps the original."""

    def setUp(self):
        """A tracked game with an IGDB original cover."""
        self.user = get_user_model().objects.create_user("art-user", password="x")
        self.item = Item.objects.create(
            media_id="art-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Art Game",
            image=IGDB_ORIGINAL,
        )

    def test_card_model_uses_card_size(self):
        """The card's image.src is the card size."""
        movie_item = Item.objects.create(
            media_id="art-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Art Movie",
            image=TMDB_ORIGINAL,
        )
        movie = Movie.objects.create(item=movie_item, user=self.user, status=Status.COMPLETED.value)
        ctx = card_context({"user": self.user}, "library", {"item": movie_item, "media": movie})
        self.assertEqual(card_model(ctx)["image"]["src"], TMDB_CARD)

    def test_api_item_has_card_image_and_keeps_image(self):
        """The API keeps `image` full size for Detail and adds `card_image`."""
        data = ItemSerializer(self.item).data
        self.assertEqual(data["image"], IGDB_ORIGINAL)
        self.assertEqual(data["card_image"], IGDB_CARD)
