from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class MediaLikeMigrationTests(TransactionTestCase):
    """Test the one-time canonical media-like backfill."""

    migrate_from = [("app", "0065_historicalmovie_liked_movie_liked")]
    migrate_to = [("app", "0066_medialike")]

    def test_backfills_movie_and_diary_likes_deduped(self):
        executor = MigrationExecutor(connection)
        user = get_user_model().objects.create_user(username="migration-like", password="password")
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Item = old_apps.get_model("app", "Item")
        Movie = old_apps.get_model("app", "Movie")
        DiaryEntry = old_apps.get_model("app", "DiaryEntry")

        movie_item = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="movie-liked",
            title="Movie Liked",
            image="https://example.com/movie.jpg",
        )
        diary_item = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="diary-liked",
            title="Diary Liked",
            image="https://example.com/diary.jpg",
        )
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(days=1)
        movie = Movie.objects.create(user_id=user.id, item=movie_item, liked=True)
        Movie.objects.filter(id=movie.id).update(created_at=older)
        old_diary = DiaryEntry.objects.create(user_id=user.id, item=movie_item, consumed_at=older, liked=True)
        DiaryEntry.objects.filter(id=old_diary.id).update(created_at=older, updated_at=older)
        diary = DiaryEntry.objects.create(user_id=user.id, item=movie_item, consumed_at=newer, liked=True)
        DiaryEntry.objects.filter(id=diary.id).update(created_at=newer, updated_at=newer)
        other_diary = DiaryEntry.objects.create(user_id=user.id, item=diary_item, consumed_at=newer, liked=True)
        DiaryEntry.objects.filter(id=other_diary.id).update(created_at=newer, updated_at=newer)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        MediaLike = new_apps.get_model("app", "MediaLike")

        likes = MediaLike.objects.filter(user_id=user.id).order_by("item_id")
        self.assertEqual(likes.count(), 2)
        self.assertEqual(MediaLike.objects.filter(user_id=user.id, item_id=movie_item.id).count(), 1)
        self.assertEqual(
            MediaLike.objects.get(user_id=user.id, item_id=movie_item.id).created_at,
            newer,
        )
