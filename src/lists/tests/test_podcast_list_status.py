from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from api.helpers import paginate_list_items
from api.serializers import serialize_data
from app.models import (
    Item,
    MediaTypes,
    Podcast,
    PodcastShow,
    PodcastShowTracker,
    Status,
)
from app.tests.card_props import card_models
from lists import smart_rules
from lists.models import CustomList, CustomListItem


class PodcastListStatusTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="podcast-list", password="test")
        self.client.force_login(self.user)
        self.list = CustomList.objects.create(name="Podcasts", owner=self.user)
        self.show = PodcastShow.objects.create(podcast_uuid="show-uuid", title="Show")
        self.show_item = Item.objects.create(media_id=self.show.podcast_uuid, source=self.show.source, media_type=MediaTypes.PODCAST.value, title="Show")
        self.episode = Item.objects.create(media_id="episode-uuid", source=self.show.source, media_type=MediaTypes.PODCAST.value, title="Episode")
        self.untracked = Item.objects.create(media_id="untracked-uuid", source=self.show.source, media_type=MediaTypes.PODCAST.value, title="Untracked")
        for item in (self.show_item, self.episode, self.untracked):
            CustomListItem.objects.create(custom_list=self.list, item=item)
        self.show_tracker = PodcastShowTracker.objects.create(user=self.user, show=self.show, status=Status.IN_PROGRESS.value, score=8)
        self.play = Podcast.objects.create(user=self.user, item=self.episode, show=self.show, status=Status.COMPLETED.value)

    def test_web_status_filter_and_updates(self):
        url = reverse("list_detail", args=[self.list.pk])
        response = self.client.get(url, {"status": Status.IN_PROGRESS.value})
        self.assertEqual(response.status_code, 200)
        rows = list(response.context["items"])
        self.assertEqual([row.pk for row in rows], [self.show_item.pk])
        self.assertEqual(rows[0].media.score, 8)
        cards = card_models(response.content)
        self.assertEqual(cards[0]["statusChip"]["status"], Status.IN_PROGRESS.value)
        self.assertEqual(float(cards[0]["rating"]["value"]), 8)
        self.show_tracker.status = Status.COMPLETED.value
        self.show_tracker.save(update_fields=["status"])
        response = self.client.get(url, {"status": Status.COMPLETED.value, "sort": "status"})
        self.assertEqual({row.pk for row in response.context["items"]}, {self.show_item.pk, self.episode.pk})

    def test_api_serializes_show_and_episode(self):
        request = RequestFactory().get("/")
        page, error = paginate_list_items(request, self.user, self.list)
        self.assertIsNone(error)
        rows = serialize_data(page["results"], many=True, homogeneous=False, context={"serialize_items_as_media": True})
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[self.show_item.pk]["status"], 1)
        self.assertEqual(by_id[self.show_item.pk]["score"], 8)
        self.assertIsNone(by_id[self.show_item.pk]["consumption_id"])
        self.assertEqual(by_id[self.episode.pk]["status"], 3)
        self.assertIsNone(by_id[self.untracked.pk]["status"])
        self.play.status = Status.IN_PROGRESS.value
        self.play.save(update_fields=["status"])
        page, _ = paginate_list_items(request, self.user, self.list)
        rows = serialize_data(page["results"], many=True, homogeneous=False, context={"serialize_items_as_media": True})
        self.assertEqual(next(row for row in rows if row["id"] == self.episode.pk)["status"], 1)

    def test_table_and_public_owner_status(self):
        self.list.visibility = "public"
        self.list.save(update_fields=["visibility"])
        other = get_user_model().objects.create_user(username="other-podcast-list")
        PodcastShowTracker.objects.create(user=other, show=self.show, status=Status.DROPPED.value)
        self.client.force_login(other)
        response = self.client.get(reverse("list_detail", args=[self.list.pk]), {"layout": "table"})
        self.assertEqual(response.status_code, 200)
        rows = {row.item.pk: row for row in response.context["items"]}
        self.assertEqual(rows[self.show_item.pk].status, Status.IN_PROGRESS.value)
        response = self.client.get(reverse("api_list_detail", args=[self.list.pk]))
        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.json()["items"]["results"]}
        self.assertEqual(rows[self.show_item.pk]["status"], 1)

    def test_status_filter_preserves_any_repeat_and_blank(self):
        Podcast.objects.create(user=self.user, item=self.episode, show=self.show, status=Status.IN_PROGRESS.value)
        response = self.client.get(reverse("list_detail", args=[self.list.pk]), {"status": Status.COMPLETED.value, "sort_by": "status"})
        self.assertEqual({row.pk for row in response.context["items"]}, {self.episode.pk})
        response = self.client.get(reverse("list_detail", args=[self.list.pk]), {"status": "no_status"})
        self.assertEqual({row.pk for row in response.context["items"]}, {self.untracked.pk})

    def test_completed_summary_includes_shows(self):
        from lists.views_helpers import _get_list_completed_counts

        self.show_tracker.status = Status.COMPLETED.value
        self.show_tracker.save(update_fields=["status"])
        self.assertEqual(_get_list_completed_counts(self.user, [self.list.pk])[self.list.pk], 2)

    def test_smart_rules_match_show_status_and_follow_changes(self):
        rules = smart_rules.normalize_rule_payload(
            {"media_types": [MediaTypes.PODCAST.value], "status": [Status.IN_PROGRESS.value]}, self.user,
        )
        self.assertIn(self.show_item.pk, smart_rules.collect_matching_item_ids(self.user, rules))
        self.show_tracker.status = Status.COMPLETED.value
        self.show_tracker.save(update_fields=["status"])
        self.assertNotIn(self.show_item.pk, smart_rules.collect_matching_item_ids(self.user, rules))

    def test_smart_rules_match_show_rating_with_status(self):
        rules = smart_rules.normalize_rule_payload(
            {"media_types": [MediaTypes.PODCAST.value], "status": [Status.IN_PROGRESS.value], "rating": "rated", "rating_min": "7"}, self.user,
        )
        self.assertIn(self.show_item.pk, smart_rules.collect_matching_item_ids(self.user, rules))
