import logging

from celery import states
from celery.signals import before_task_publish
from django.db.backends.signals import connection_created
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django_celery_results.models import TaskResult

from app.models import DiaryEntry
from app.tasks import update_daily_statistics

logger = logging.getLogger(__name__)


@receiver(connection_created)
def setup_sqlite_pragmas(sender, connection, **kwargs):  # noqa: ARG001
    """Set up SQLite pragmas for WAL mode and busy timeout on connection creation."""
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=wal;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()


@before_task_publish.connect
def create_task_result_on_publish(sender=None, headers=None, body=None, **kwargs):  # noqa: ARG001
    """Create a TaskResult object with PENDING status on task publish.

    https://github.com/celery/django-celery-results/issues/286#issuecomment-1279161047
    """
    if "task" not in headers:
        return

    TaskResult.objects.store_result(
        content_type="application/json",
        content_encoding="utf-8",
        task_id=headers["id"],
        result=None,
        status=states.PENDING,
        task_name=headers["task"],
        task_args=headers.get("argsrepr", ""),
        task_kwargs=headers.get("kwargsrepr", ""),
    )


@receiver(post_save, sender=DiaryEntry)
def handle_diary_entry_save(sender, instance, created, **kwargs):
    """Queue statistics update when a diary entry is saved."""
    update_daily_statistics.delay(
        user_id=instance.user.id,
        date_str=instance.consumed_at.isoformat(),
    )
