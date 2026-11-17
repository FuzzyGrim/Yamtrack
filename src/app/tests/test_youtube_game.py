"""The game YouTube tags on a video, read from its watch page."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from app.models import Item, MediaTypes, Sources, VideoGameTag
from app.services import youtube_game


def _page(*games, icon="GAMING_LOGO", heading="Games"):
    """The slice of ytInitialData that carries the game section."""
    return {
        "engagementPanels": [
            {
                "items": [
                    {
                        "videoAttributesSectionViewModel": {
                            "headerTitle": heading,
                            "videoAttributeViewModels": [
                                {
                                    "videoAttributeViewModel": {
                                        "title": title,
                                        "subtitle": year,
                                        "onTap": {
                                            "innertubeCommand": {
                                                "browseEndpoint": {"browseId": game_id},
                                            },
                                        },
                                    },
                                }
                                for title, year, game_id in games
                            ],
                            "footerButton": {"buttonViewModel": {"iconName": icon}},
                        },
                    },
                ],
            },
        ],
    }


MINECRAFT = ("Minecraft", "2009", "UCQvWX73GQygcwXOTSf_VDVg")


class ExtractGamesTests(TestCase):
    def test_reads_title_year_and_game_id(self):
        self.assertEqual(
            youtube_game.extract_games(_page(MINECRAFT)),
            [{"title": "Minecraft", "game_id": MINECRAFT[2], "year": "2009"}],
        )

    def test_translated_heading_still_matches(self):
        self.assertEqual(
            len(youtube_game.extract_games(_page(MINECRAFT, heading="Spiele"))), 1
        )

    def test_a_section_without_the_gaming_icon_is_not_a_game(self):
        self.assertEqual(
            youtube_game.extract_games(_page(MINECRAFT, icon="MUSIC_LOGO")), []
        )

    def test_a_page_without_a_section_has_no_game(self):
        self.assertEqual(youtube_game.extract_games({"contents": []}), [])


class RefreshGameTagTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(
            media_id="HQp_b8VHtgI",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            title="A video",
        )

    def _refresh(self, data=None, error=None):
        target = "app.services.youtube_game.fetch_initial_data"
        with patch(target, side_effect=error, return_value=data):
            return youtube_game.refresh_game_tag(self.item.pk)

    def test_found(self):
        self.assertEqual(self._refresh(_page(MINECRAFT)), "found")
        tag_row = VideoGameTag.objects.get(item=self.item)
        self.assertEqual((tag_row.game_title, tag_row.game_id), ("Minecraft", MINECRAFT[2]))

    def test_untagged_video_is_not_found_not_guessed(self):
        self.assertEqual(self._refresh({"contents": []}), "not_found")
        self.assertEqual(VideoGameTag.objects.get(item=self.item).game_title, "")

    def test_fetch_failure_is_unavailable_and_counts(self):
        err = youtube_game.WatchPageUnavailableError("boom")
        self._refresh(error=err)
        self._refresh(error=err)
        row = VideoGameTag.objects.get(item=self.item)
        self.assertEqual((row.status, row.failures), ("unavailable", 2))

    def test_recovery_after_failure_clears_the_count(self):
        self._refresh(error=youtube_game.WatchPageUnavailableError("boom"))
        self._refresh(_page(MINECRAFT))
        self.assertEqual(VideoGameTag.objects.get(item=self.item).failures, 0)

    def test_other_sources_are_ignored(self):
        self.item.source = Sources.MANUAL.value
        self.item.save()
        self.assertIsNone(self._refresh(_page(MINECRAFT)))
        self.assertFalse(VideoGameTag.objects.exists())

    def test_bad_video_id_never_reaches_the_network(self):
        with self.assertRaises(youtube_game.WatchPageUnavailableError):
            youtube_game.fetch_initial_data("../etc/passwd")


class LookupDueTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(
            media_id="HQp_b8VHtgI",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            title="A video",
        )

    def _tag(self, status, age, failures=0):
        VideoGameTag.objects.create(
            item=self.item,
            status=status,
            checked_at=timezone.now() - age,
            failures=failures,
        )

    def test_new_item_is_due(self):
        self.assertTrue(youtube_game.lookup_due(self.item))

    def test_found_is_never_due_again(self):
        self._tag("found", timedelta(days=400))
        self.assertFalse(youtube_game.lookup_due(self.item))

    def _play(self, ago):
        from django.contrib.auth import get_user_model

        from app.models import Video, VideoPlay

        user = get_user_model().objects.create_user("viewer", password="x")
        video = Video.objects.create(item=self.item, user=user)
        VideoPlay.objects.create(
            video=video, external_id="p1", end_date=timezone.now() - ago
        )

    def test_not_found_waits_a_week(self):
        self._play(timedelta(days=2))
        self._tag("not_found", timedelta(days=1))
        self.assertFalse(youtube_game.lookup_due(self.item))
        VideoGameTag.objects.update(checked_at=timezone.now() - timedelta(days=8))
        self.assertTrue(youtube_game.lookup_due(self.item))

    def test_not_found_is_not_rechecked_once_nobody_watches_it(self):
        """A tag added long after anyone watched is not worth a weekly fetch, forever."""
        self._play(timedelta(days=45))
        self._tag("not_found", timedelta(days=8))
        self.assertFalse(youtube_game.lookup_due(self.item))
        with patch("app.services.youtube_game.fetch_initial_data") as fetch:
            self.assertEqual(youtube_game.backfill_due_items(), 0)
        fetch.assert_not_called()

    def test_recently_watched_not_found_is_rechecked_by_the_backfill(self):
        self._play(timedelta(days=3))
        self._tag("not_found", timedelta(days=8))
        with patch(
            "app.services.youtube_game.fetch_initial_data", return_value={}
        ) as fetch:
            self.assertEqual(youtube_game.backfill_due_items(), 1)
        fetch.assert_called_once_with(self.item.media_id)

    def test_unavailable_backs_off_then_gives_up(self):
        self._tag("unavailable", timedelta(minutes=5), failures=1)
        self.assertFalse(youtube_game.lookup_due(self.item))
        VideoGameTag.objects.update(checked_at=timezone.now() - timedelta(hours=7))
        self.assertTrue(youtube_game.lookup_due(self.item))
        VideoGameTag.objects.update(failures=youtube_game.MAX_FAILURES)
        self.assertFalse(youtube_game.lookup_due(self.item))


@tag("network")
class LiveWatchPageTests(TestCase):
    """Reads YouTube for real: run with `scripts/test.sh --network`."""

    def test_sample_video_is_tagged_minecraft(self):
        games = youtube_game.extract_games(
            youtube_game.fetch_initial_data("HQp_b8VHtgI")
        )
        self.assertEqual(games[0]["title"], "Minecraft")
        self.assertEqual(games[0]["game_id"], MINECRAFT[2])

    def test_untagged_video_has_no_game(self):
        self.assertEqual(
            youtube_game.extract_games(youtube_game.fetch_initial_data("dQw4w9WgXcQ")),
            [],
        )


class WatchedForGameTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user("watcher", password="x")
        self.other = get_user_model().objects.create_user("other", password="x")
        self.game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Legend of Zelda: Tears of the Kingdom",
            release_datetime=timezone.now().replace(year=2023),
        )

    def _video(self, user, vid, title, year, progress, length, status="found"):
        from app.models import Status, Video

        item, _ = Item.objects.get_or_create(
            media_id=vid,
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            defaults={"title": vid},
        )
        VideoGameTag.objects.get_or_create(
            item=item,
            defaults={
                "status": status,
                "game_title": title,
                "release_year": year,
                "checked_at": timezone.now(),
            },
        )
        return Video.objects.create(
            item=item,
            user=user,
            progress=progress,
            length_seconds=length,
            status=Status.IN_PROGRESS.value,
        )

    def test_sums_matching_videos_for_one_user_and_caps_at_runtime(self):
        from app.services import youtube_game_watch as watch

        self._video(self.user, "v1", self.game.title, "2023", 600, 1000)
        self._video(self.user, "v2", self.game.title, "2023", 5000, 1000)
        self._video(self.user, "v3", "Minecraft", "2009", 900, 900)
        self._video(self.other, "v4", self.game.title, "2023", 900, 900)
        self.assertEqual(
            watch.watched_for_game(self.user, self.game),
            {"videos": 2, "seconds": 1600, "hours_label": "26m", "estimated": True},
        )

    def test_wrong_year_and_missing_tags_do_not_match(self):
        from app.services import youtube_game_watch as watch

        self._video(self.user, "v1", self.game.title, "2017", 600, 1000)
        self._video(self.user, "v2", self.game.title, "", 600, 1000, "not_found")
        self.assertIsNone(watch.watched_for_game(self.user, self.game))

    def test_punctuation_and_accents_are_folded(self):
        from app.services import youtube_game_watch as watch

        self.assertEqual(
            watch.normalize_title("Pokémon: Legends Z-A"),
            watch.normalize_title("pokemon legends z a"),
        )


class BackfillTests(TestCase):
    def test_looks_up_untagged_videos_within_the_limit(self):
        for n in range(3):
            Item.objects.create(
                media_id=f"vid{n:08d}",
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.VIDEO.value,
                title=f"v{n}",
            )
        target = "app.services.youtube_game.fetch_initial_data"
        with patch(target, return_value=_page(MINECRAFT)):
            self.assertEqual(youtube_game.backfill_due_items(limit=2), 2)
            self.assertEqual(youtube_game.backfill_due_items(limit=2), 1)
            self.assertEqual(youtube_game.backfill_due_items(limit=2), 0)


class ManualLinkWatchTests(TestCase):
    """A viewer's link overrides YouTube's tag in their own watch time only."""

    def setUp(self):
        """One tagged video watched by two users; one game."""
        from django.contrib.auth import get_user_model

        from app.models import Video, VideoGameLink
        from app.services.youtube_game_watch import watched_for_game

        self.watched = watched_for_game
        self.link_model = VideoGameLink
        users = get_user_model()
        self.me = users.objects.create_user(username="me", password="x")
        self.other = users.objects.create_user(username="other", password="x")
        self.game = Item.objects.create(
            media_id="1", source="igdb", media_type="game", title="Minecraft"
        )
        self.elsewhere = Item.objects.create(
            media_id="2", source="igdb", media_type="game", title="Pong"
        )
        item = Item.objects.create(
            media_id="aaaaaaaaaaa",
            source="youtube",
            media_type="video",
            library_media_type="video",
            title="Let's Play",
        )
        VideoGameTag.objects.create(
            item=item,
            status=VideoGameTag.Status.FOUND,
            game_title="Minecraft",
            checked_at=timezone.now(),
        )
        self.mine = Video.objects.create(
            item=item,
            user=self.me,
            status="In progress",
            progress=600,
            length_seconds=1000,
        )
        Video.objects.create(
            item=item,
            user=self.other,
            status="In progress",
            progress=100,
            length_seconds=1000,
        )

    def test_tag_counts_until_unlinked(self):
        """The tag matches by title; unlinking removes it for that user only."""
        self.assertEqual(self.watched(self.me, self.game)["seconds"], 600)
        self.link_model.objects.create(video=self.mine, game_item=None)
        self.assertIsNone(self.watched(self.me, self.game))
        self.assertEqual(self.watched(self.other, self.game)["seconds"], 100)

    def test_relink_moves_the_time_to_the_other_game(self):
        """A manual link counts for its game and the tag stops counting."""
        self.link_model.objects.create(video=self.mine, game_item=self.elsewhere)
        self.assertIsNone(self.watched(self.me, self.game))
        self.assertEqual(self.watched(self.me, self.elsewhere)["seconds"], 600)
