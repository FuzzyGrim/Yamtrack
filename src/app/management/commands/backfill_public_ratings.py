from django.core.management.base import BaseCommand

from app.ratings import fill_tmdb_votes_from_cache, refresh_stale_ratings


class Command(BaseCommand):
    """Backfill public ratings for movies, TV, and anime already in the library."""

    help = (
        "Fetch public ratings for existing movies, TV shows, and anime. "
        "Runs in batches and stops calling OMDb once OMDB_DAILY_LIMIT is reached."
    )

    def add_arguments(self, parser):
        """Accept a batch size."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="How many items to load at a time.",
        )

    def handle(self, *_args, **options):
        """Refresh stale items until the library is caught up or the cap is hit."""
        batch_size = max(1, options["batch_size"])
        updated, deferred = refresh_stale_ratings(batch_size=batch_size)
        filled = fill_tmdb_votes_from_cache(batch_size=batch_size)
        self.stdout.write(f"Updated {updated} items.")
        if filled:
            self.stdout.write(
                f"Filled TMDB vote counts for {filled} items from cache.",
            )
        if deferred:
            self.stdout.write(
                f"Deferred {deferred} items because the OMDb daily limit was reached.",
            )
