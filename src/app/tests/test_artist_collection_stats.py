"""Artist collection counts: same numbers, same queries at any size (BENCH-12)."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.helpers import get_artist_collection_stats
from app.models import Album, CollectionEntry, Music
from app.tests.catalogue_fixtures import build_artist


class ArtistCollectionStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="fan", password="x")
        cls.artist = build_artist(cls.user, albums=6, prefix="stats")
        cls.entries = list(
            Music.objects.filter(user=cls.user, artist=cls.artist)
            .select_related("album")
            .order_by("album__title", "track__track_number")
        )

    def _collect(self, entries):
        CollectionEntry.objects.bulk_create(
            CollectionEntry(user=self.user, item_id=entry.item_id) for entry in entries
        )

    def _queries(self):
        with CaptureQueriesContext(connection) as queries:
            stats = get_artist_collection_stats(self.user, self.artist)
        return stats, len(queries.captured_queries)

    def test_nothing_collected(self):
        stats, _ = self._queries()
        self.assertEqual(
            stats,
            {
                "collected_albums": 0,
                "total_albums": 6,
                "collected_tracks": 0,
                "total_tracks": 30,
            },
        )

    def test_counts_entries_and_distinct_albums(self):
        # Three tracks of the first album, one of the second, and the first
        # track collected twice (two copies count as two).
        first, second = self.entries[:5], self.entries[5:10]
        self._collect([*first[:3], second[0], first[0]])
        stats, _ = self._queries()
        self.assertEqual(
            stats,
            {
                "collected_albums": 2,
                "total_albums": 6,
                "collected_tracks": 5,
                "total_tracks": 30,
            },
        )

    def test_a_track_on_two_albums_counts_its_latest_entry_album(self):
        entry = self.entries[0]
        other_album = Album.objects.filter(artist=self.artist).exclude(
            pk=entry.album_id
        )[0]
        # A later entry for the same recording on another album: the track is
        # counted on that album, as before.
        Music.objects.create(
            item_id=entry.item_id,
            user=self.user,
            artist=self.artist,
            album=other_album,
            status=entry.status,
        )
        on_other_album = next(e for e in self.entries if e.album_id == other_album.pk)
        self._collect([entry, on_other_album])
        stats, _ = self._queries()
        # Both collected tracks count on the other album: one album.
        self.assertEqual(stats["collected_albums"], 1)
        self.assertEqual(stats["collected_tracks"], 2)
        self.assertEqual(stats["total_tracks"], 31)

    def test_queries_do_not_grow_with_collected_tracks(self):
        self._collect(self.entries[:2])
        _, few = self._queries()
        CollectionEntry.objects.all().delete()
        self._collect(self.entries)
        stats, many = self._queries()
        self.assertEqual(stats["collected_tracks"], 30)
        self.assertEqual(stats["collected_albums"], 6)
        self.assertEqual(few, many)
