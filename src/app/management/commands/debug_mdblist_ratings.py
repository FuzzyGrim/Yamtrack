"""Management command to debug MDBList ratings issues."""

from __future__ import annotations

import json

from django.core.cache import cache
from django.core.management.base import BaseCommand

from app.providers import mdblist


class Command(BaseCommand):
    """Debug MDBList ratings for a specific movie/TV show."""

    help = "Debug MDBList ratings by checking cache and fetching fresh data"

    def add_arguments(self, parser):
        parser.add_argument(
            "tmdb_id",
            type=int,
            help="TMDB ID of the movie/TV show to debug",
        )
        parser.add_argument(
            "--media-type",
            type=str,
            default="movie",
            choices=["movie", "tv"],
            help="Media type (movie or tv)",
        )
        parser.add_argument(
            "--clear-cache",
            action="store_true",
            help="Clear the cache before fetching",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show full API response",
        )

    def handle(self, *args, **options):
        tmdb_id = options["tmdb_id"]
        media_type = options["media_type"]
        clear_cache = options["clear_cache"]
        verbose = options["verbose"]

        cache_key = f"mdblist_ratings_tmdb_{media_type}_{tmdb_id}"

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"Debugging MDBList ratings for {media_type} (TMDB ID: {tmdb_id})")
        self.stdout.write(f"{'='*60}\n")

        # Check cache
        cached_data = cache.get(cache_key)
        if cached_data:
            self.stdout.write(self.style.SUCCESS(f"✓ Found cached data:"))
            self.stdout.write(json.dumps(cached_data, indent=2))
            self.stdout.write(f"\nCache key: {cache_key}")
        else:
            self.stdout.write(self.style.WARNING("✗ No cached data found"))

        if clear_cache:
            cache.delete(cache_key)
            self.stdout.write(self.style.SUCCESS(f"\n✓ Cleared cache for {cache_key}"))

        # Fetch fresh data
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write("Fetching fresh data from MDBList API...")
        self.stdout.write(f"{'='*60}\n")

        try:
            ratings = mdblist.get_media_ratings(tmdb_id, media_type)
            if ratings:
                self.stdout.write(self.style.SUCCESS("✓ Successfully fetched ratings:"))
                self.stdout.write(json.dumps(ratings, indent=2))

                # Check for IMDb specifically
                if "imdb" in ratings:
                    imdb_data = ratings["imdb"]
                    self.stdout.write(self.style.SUCCESS(f"\n✓ IMDb rating found:"))
                    self.stdout.write(f"  Value: {imdb_data.get('value')}")
                    self.stdout.write(f"  Score: {imdb_data.get('score')}")
                    self.stdout.write(f"  Votes: {imdb_data.get('votes')}")
                    self.stdout.write(f"  URL: {imdb_data.get('url')}")
                else:
                    self.stdout.write(self.style.ERROR("\n✗ IMDb rating NOT found in response"))
                    self.stdout.write(f"Available ratings: {list(ratings.keys())}")
            else:
                self.stdout.write(self.style.ERROR("✗ No ratings returned (empty dict or None)"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Error fetching ratings: {e}"))
            import traceback
            if verbose:
                self.stdout.write(traceback.format_exc())

        self.stdout.write(f"\n{'='*60}\n")
