from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.templatetags import app_tags
from events.models import Event, SentinelDatetime
from users.forms import UserUpdateForm


class MediaListViewTests(TestCase):
    """Test the media list view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {
            "username": "test2",
            "password": "12345",
            "profile_private": True,
        }
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

        movies_id = ["278", "238", "129", "424", "680"]
        num_completed = 3
        for i in range(1, 6):
            item = Item.objects.create(
                media_id=movies_id[i - 1],
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Test Movie {i}",
                image="http://example.com/image.jpg",
            )
            status = (
                Status.COMPLETED.value
                if i < num_completed
                else Status.IN_PROGRESS.value
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=status,
                progress=1 if i < num_completed else 0,
                score=i,
            )

    def test_media_list_view(self):
        """Test the media list view displays media items."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
        )

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

    def test_media_list_with_filters(self):
        """Test the media list view with filters."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?status=Completed&sort=score&layout=table",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.context["current_status"],
            Status.COMPLETED.value,
        )
        self.assertEqual(response.context["current_sort"], "score")
        self.assertEqual(response.context["current_layout"], "table")

        self.assertEqual(response.context["media_list"].paginator.count, 2)

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_status, Status.COMPLETED.value)
        self.assertEqual(self.user.movie_sort, "score")
        self.assertEqual(self.user.movie_layout, "table")

    def test_media_list_htmx_request(self):
        """Test the media list view with HTMX request."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?layout=grid",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_grid_items.html")

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?layout=table",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_table_items.html")

    def test_media_list_soft_navigation_returns_full_page(self):
        """Soft-navigation body swaps (after an edit modal) get the full page."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
            headers={"hx-request": "true", "x-soft-navigation": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

    def test_public_media_list_ignores_invalid_filters(self):
        """Test invalid public filters fall back to the target user's preferences."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
            + "?status=invalid&sort=bad_field&layout=invalid",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["current_status"], self.external_user.movie_status
        )
        self.assertEqual(
            response.context["current_sort"], self.external_user.movie_sort
        )
        self.assertEqual(
            response.context["current_layout"], self.external_user.movie_layout
        )

    def test_anonymous_user_can_view_public_media_list(self):
        """Test anonymous users can view public media lists."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("media_list", response.context)

    def test_profile_private_defaults_to_true(self):
        """Test new users have private profiles by default."""
        user = get_user_model().objects.create_user(
            username="private-default",
        )

        self.assertTrue(user.profile_private)

    def test_private_media_list(self):
        """Test the private media list view."""
        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )
        self.assertEqual(response.status_code, 404)

        form = UserUpdateForm(
            data={"username": "test2", "profile_private": False},
            instance=self.external_user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        external_user = form.save()
        external_user.refresh_from_db()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("media_list", response.context)

    def test_media_list_hides_unreleased_movies_and_seasons(self):
        """Future titles are omitted. Released titles and active seasons stay."""
        self.user.movie_hide_unreleased = True
        self.user.tv_hide_unreleased = True
        self.user.save(
            update_fields=["movie_hide_unreleased", "tv_hide_unreleased"],
        )

        future_item = Item.objects.create(
            media_id="future-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Future Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=future_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        Event.objects.create(
            item=future_item,
            datetime=timezone.now() + timezone.timedelta(days=3),
        )

        released_item = Item.objects.create(
            media_id="released-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Released Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=released_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        Event.objects.create(
            item=released_item,
            datetime=timezone.now() - timezone.timedelta(days=3),
        )

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
        )
        movie_ids = [media.item.media_id for media in response.context["media_list"]]
        self.assertIn("released-movie", movie_ids)
        self.assertNotIn("future-movie", movie_ids)
        self.assertContains(response, "Hide unreleased")

        tv_item = Item.objects.create(
            media_id="show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Future Show",
            image="http://example.com/image.jpg",
        )
        tv = TV(
            item=tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        TV.save_base(tv)
        future_season_item = Item.objects.create(
            media_id="show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Future Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        future_season = Season(
            item=future_season_item,
            related_tv=tv,
            user=self.user,
            status=Status.PLANNING.value,
        )
        Season.save_base(future_season)
        Event.objects.create(
            item=future_season_item,
            content_number=1,
            datetime=SentinelDatetime.max_datetime(),
        )

        active_tv_item = Item.objects.create(
            media_id="show-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Airing Show",
            image="http://example.com/image.jpg",
        )
        active_tv = TV(
            item=active_tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        TV.save_base(active_tv)
        active_season_item = Item.objects.create(
            media_id="show-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Airing Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        active_season = Season(
            item=active_season_item,
            related_tv=active_tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        Season.save_base(active_season)
        Event.objects.create(
            item=active_season_item,
            content_number=2,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.TV.value]),
        )
        show_ids = [media.item.media_id for media in response.context["media_list"]]
        self.assertIn("show-2", show_ids)
        self.assertNotIn("show-1", show_ids)
        airing = next(
            media
            for media in response.context["media_list"]
            if media.item.media_id == "show-2"
        )
        self.assertEqual(airing.max_progress, 2)

    def test_hide_unreleased_stays_on_the_page_that_set_it(self):
        """A movie-list toggle does not change home or other media lists."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?hide_unreleased=true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["hide_unreleased"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.movie_hide_unreleased)
        self.assertFalse(self.user.tv_hide_unreleased)
        self.assertFalse(self.user.home_hide_unreleased)
        self.assertFalse(self.user.season_details_hide_unreleased)
