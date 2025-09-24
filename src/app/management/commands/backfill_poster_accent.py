"""Management command to backfill poster accent colours."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from app.models import Item
from app.utils.color import compute_and_store_poster_accent


class Command(BaseCommand):
    """Populate missing poster accent colours for items."""

    help = "Downloads current poster images and stores dominant colour on Item.poster_accent_color"

    def add_arguments(self, parser):  # noqa: D401 - default message
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Optional limit for number of items to process.",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        queryset = Item.objects.filter(poster_accent_color="", image__isnull=False).exclude(image="")
        if limit is not None:
            queryset = queryset[:limit]

        processed = 0
        for item in queryset.iterator():
            try:
                color = compute_and_store_poster_accent(item, force=True)
                processed += 1
                self.stdout.write(self.style.SUCCESS(f"Updated accent for {item}"))
            except Exception as exc:  # pragma: no cover - best effort
                self.stderr.write(self.style.WARNING(f"Failed to process {item}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Processed {processed} items."))
