from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import resolve_url
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes, PodcastEpisode, PodcastShow, Sources


class PodcastShowDetailWebsiteLinkTests(TestCase):
    """website_url renders as a link on the podcast show detail page."""

    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_anonymous_show_requests_require_login(self):
        show = PodcastShow.objects.create(
            podcast_uuid="private-tracker-show",
            title="Private Tracker Show",
        )
        self.client.logout()

        for show_id in (show.id, show.id + 1):
            with self.subTest(show_id=show_id):
                url = reverse("podcast_show_detail", args=[show_id])
                response = self.client.get(url)
                self.assertRedirects(
                    response,
                    f"{resolve_url(settings.LOGIN_URL)}?next={url}",
                    fetch_redirect_response=False,
                )

    def test_renders_show_and_episode_website_links(self):
        show = PodcastShow.objects.create(
            podcast_uuid="show-website-link",
            title="Website Link Podcast",
            website_url="https://example.com/show",
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="episode-website-link",
            title="Episode With A Link",
            website_url="https://example.com/show/episode-one",
        )

        response = self.client.get(
            reverse("podcast_show_detail", args=[show.id]),
            follow=True,
        )

        self.assertContains(response, "https://example.com/show")
        self.assertContains(response, "https://example.com/show/episode-one")

    def test_omits_links_when_website_url_is_blank(self):
        show = PodcastShow.objects.create(
            podcast_uuid="show-no-website-link",
            title="No Link Podcast",
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="episode-no-website-link",
            title="Episode Without A Link",
        )

        response = self.client.get(
            reverse("podcast_show_detail", args=[show.id]),
            follow=True,
        )

        self.assertNotContains(response, "Visit show website")
        self.assertNotContains(response, "Episode website")


class CompletedPlaysByPodcastIdTests(TestCase):
    """The shared history read can be bounded per podcast in SQL."""

    def test_limit_keeps_only_the_newest_plays_of_each_podcast(self):
        from datetime import UTC, datetime, timedelta

        from django.contrib.auth import get_user_model

        from app.models import Item, MediaTypes, Podcast, PodcastPlay, Sources, Status
        from app.podcast_views import completed_plays_by_podcast_id

        user = get_user_model().objects.create_user(username="plays", password="x")
        item = Item.objects.create(
            media_id="plays-episode",
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title="Episode",
            image="https://example.com/i.jpg",
        )
        podcast = Podcast.objects.create(
            item=item,
            user=user,
            status=Status.COMPLETED.value,
            progress=30,
            end_date=datetime(2024, 1, 1, tzinfo=UTC),
        )
        for day in range(2, 26):
            PodcastPlay.objects.create(
                podcast=podcast,
                end_date=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day),
            )

        everything = completed_plays_by_podcast_id({podcast.id})[podcast.id]
        newest = completed_plays_by_podcast_id({podcast.id}, limit=10)[podcast.id]

        self.assertGreater(len(everything), 10)
        self.assertEqual(len(newest), 10)
        self.assertEqual(
            [record.end_date for record in newest],
            [record.end_date for record in everything[:10]],
        )


class PodcastShowDetailRssRefreshTests(TestCase):
    """The feed refresh is queued, never run inside the render."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.credentials = {"username": "rss", "password": "12345"}
        get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.show = PodcastShow.objects.create(
            podcast_uuid="rss-queue-show",
            title="Queued Feed Show",
            rss_feed_url="https://example.com/feed.xml",
        )
        self.url = reverse(
            "media_details",
            kwargs={
                "source": Sources.POCKETCASTS.value,
                "media_type": MediaTypes.PODCAST.value,
                "media_id": self.show.podcast_uuid,
                "title": "queued-feed-show",
            },
        )

    def test_refresh_is_queued_once_per_window_without_fetching_inline(self):
        with (
            patch(
                "app.tasks_podcast.refresh_podcast_show_from_rss_task.delay"
            ) as delay,
            patch("integrations.podcast_rss.fetch_feed_from_rss") as fetch,
        ):
            first = self.client.get(self.url)
            second = self.client.get(self.url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        delay.assert_called_once_with(self.show.id)
        fetch.assert_not_called()

    def test_queue_failure_still_renders(self):
        with patch(
            "app.tasks_podcast.refresh_podcast_show_from_rss_task.delay",
            side_effect=OSError("broker down"),
        ):
            response = self.client.get(self.url)

        self.assertContains(response, "Queued Feed Show")

    def test_task_refreshes_the_show_from_its_feed(self):
        from app.tasks_podcast import refresh_podcast_show_from_rss_task

        with patch(
            "app.fork_services_podcast.refresh_show_from_rss"
        ) as refresh:
            refresh_podcast_show_from_rss_task(self.show.id)
            refresh_podcast_show_from_rss_task(self.show.id + 999)

        refresh.assert_called_once()


def build_long_show(user, size, *, prefix="long"):
    """Return a show with ``size`` episodes, every other one listened to.

    One listen in ten is a repeat. Shared with the detail-page census
    (assignment 49), so keep it cheap: bulk inserts, no signals.
    """
    from datetime import UTC, datetime, timedelta

    from app.models import Item, Podcast, Status

    show = PodcastShow.objects.create(
        podcast_uuid=f"{prefix}-{size}",
        source=Sources.POCKETCASTS.value,
        title=f"Long Show {size}",
        image="https://example.com/show.jpg",
    )
    base = datetime(2015, 1, 1, tzinfo=UTC)
    PodcastEpisode.objects.bulk_create(
        [
            PodcastEpisode(
                show=show,
                episode_uuid=f"{prefix}-{size}-ep-{index}",
                title=f"Episode {index}",
                published=None if index % 97 == 0 else base + timedelta(days=index),
                duration=1800 + index,
                episode_number=index,
            )
            for index in range(size)
        ],
    )
    listened = list(PodcastEpisode.objects.filter(show=show).order_by("id"))[::2]
    Item.objects.bulk_create(
        [
            Item(
                media_id=episode.episode_uuid,
                source=Sources.POCKETCASTS.value,
                media_type=MediaTypes.PODCAST.value,
                title=episode.title,
                image=show.image,
            )
            for episode in listened
        ],
    )
    items = {
        item.media_id: item
        for item in Item.objects.filter(
            media_id__in=[episode.episode_uuid for episode in listened],
        )
    }
    rows = []
    for index, episode in enumerate(listened):
        completed = bool(index % 3)
        rows.append(
            Podcast(
                item=items[episode.episode_uuid],
                user=user,
                show=show,
                episode=episode,
                status=Status.COMPLETED.value if completed else Status.IN_PROGRESS.value,
                progress=30,
                end_date=base + timedelta(days=index) if completed else None,
            ),
        )
        if index % 10 == 0:
            rows.append(
                Podcast(
                    item=items[episode.episode_uuid],
                    user=user,
                    show=show,
                    episode=episode,
                    status=Status.COMPLETED.value,
                    progress=45,
                    end_date=base + timedelta(days=index, hours=5),
                ),
            )
    Podcast.objects.bulk_create(rows)
    return show


class PodcastShowPageBoundedTests(TestCase):
    """A show's page costs the same however long its catalogue is.

    The old show page listed every episode and compared each with every
    listen, so a 2,000-episode show took 16 to 18 seconds.
    """

    PAGE_SIZE = 20

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="bounded")
        cls.short_show = build_long_show(cls.user, 50, prefix="short")
        cls.long_show = build_long_show(cls.user, 500)

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client.force_login(self.user)

    def _page(self, show):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        url = reverse("podcast_show_detail", args=[show.id])
        self.client.get(url, follow=True)  # first view creates the page's Items
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        return response, len(queries.captured_queries)

    def test_old_url_redirects_to_the_one_show_page(self):
        from app.podcast_views import podcast_show_url

        response = self.client.get(
            reverse("podcast_show_detail", args=[self.long_show.id]),
        )

        self.assertRedirects(
            response,
            podcast_show_url(self.long_show),
            fetch_redirect_response=False,
        )
        self.assertIn(self.long_show.podcast_uuid, response.url)

    def test_long_show_renders_one_page_of_episodes(self):
        response, _ = self._page(self.long_show)

        self.assertEqual(
            response.content.count(b"episode-card"),
            self.PAGE_SIZE,
        )
        self.assertContains(response, "page=2&page_size=20&format=html")

    def test_long_show_costs_the_same_queries_as_a_short_one(self):
        _, short_queries = self._page(self.short_show)
        _, long_queries = self._page(self.long_show)

        self.assertEqual(long_queries, short_queries)
        self.assertLessEqual(long_queries, 60)

    def test_totals_cover_the_whole_catalogue(self):
        from app.models import Podcast

        response, _ = self._page(self.long_show)
        listens = list(Podcast.objects.filter(user=self.user, show=self.long_show))

        self.assertEqual(response.context["total_episodes"], 500)
        self.assertEqual(response.context["total_listened"], len(listens))
        self.assertEqual(
            response.context["total_minutes"],
            sum(listen.progress for listen in listens),
        )

    def test_play_stats_match_the_listens(self):
        from app.models import Podcast, Status

        response, _ = self._page(self.long_show)
        listens = list(Podcast.objects.filter(user=self.user, show=self.long_show))
        stats = response.context["play_stats"]
        minutes = sum(listen.progress for listen in listens)

        self.assertEqual(
            stats["first_played"],
            min(
                listen.start_date or listen.end_date or listen.created_at
                for listen in listens
            ),
        )
        self.assertEqual(
            stats["last_played"],
            max(
                listen.end_date or listen.start_date or listen.created_at
                for listen in listens
            ),
        )
        self.assertEqual(stats["total_minutes"], minutes)
        self.assertEqual(stats["total_hours"], minutes // 60)
        self.assertEqual(stats["total_minutes_remainder"], minutes % 60)
        self.assertEqual(
            stats["total_plays"],
            sum(
                1
                for listen in listens
                if listen.end_date or listen.status == Status.COMPLETED.value
            ),
        )

    def test_first_page_is_newest_first_with_undated_last(self):
        from app.podcast_views import ordered_episodes

        response, _ = self._page(self.long_show)
        expected = [
            episode.title for episode in ordered_episodes(self.long_show)[: self.PAGE_SIZE]
        ]

        self.assertEqual(
            [episode["title"] for episode in response.context["episodes"]],
            expected,
        )
        self.assertEqual(expected[0], "Episode 499")
        last = list(ordered_episodes(self.long_show))[-1]
        self.assertIsNone(last.published)

    def test_unlistened_show_has_no_stats(self):
        other = get_user_model().objects.create_user(username="newcomer")
        self.client.force_login(other)

        response, _ = self._page(self.long_show)

        self.assertEqual(response.context["total_listened"], 0)
        self.assertEqual(response.context["total_minutes"], 0)
        self.assertIsNone(response.context["play_stats"])
        self.assertFalse(response.context["user_medias"])


class PodcastShowSaveRedirectTests(TestCase):
    """Saving or removing a show without a return URL lands on its page."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="saver")
        self.client.force_login(self.user)
        self.show = PodcastShow.objects.create(
            podcast_uuid="save-redirect-show",
            source=Sources.GPODDER.value,
            title="Save Redirect",
        )

    def test_save_and_delete_redirect_to_the_show_page(self):
        from app.models import PodcastShowTracker, Status
        from app.podcast_views import podcast_show_url

        saved = self.client.post(
            reverse("podcast_show_save"),
            {"show_id": self.show.id, "status": Status.IN_PROGRESS.value},
        )
        self.assertTrue(
            PodcastShowTracker.objects.filter(user=self.user, show=self.show).exists(),
        )
        removed = self.client.post(
            reverse("podcast_show_delete"),
            {"show_id": self.show.id},
        )

        for response in (saved, removed):
            self.assertRedirects(
                response,
                podcast_show_url(self.show),
                fetch_redirect_response=False,
            )
        self.assertIn("/gpodder/", podcast_show_url(self.show))

    def test_unrouted_source_opens_under_pocket_casts(self):
        from app.podcast_views import podcast_show_url

        self.show.source = Sources.PLEX.value

        self.assertIn("/pocketcasts/", podcast_show_url(self.show))
