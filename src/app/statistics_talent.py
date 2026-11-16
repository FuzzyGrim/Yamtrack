"""Talent and credit aggregation for statistics: top cast, crew, studios, and per-person totals."""

import logging
from collections import Counter, defaultdict
from heapq import nsmallest
from types import SimpleNamespace

from django.conf import settings
from django.db import connection
from django.db.models import Q

from app import credits as credit_helpers
from app.models import (
    CreditRoleType,
    Episode,
    Game,
    Item,
    ItemPersonCredit,
    ItemStudioCredit,
    MediaTypes,
    Movie,
    Person,
    PersonGender,
    Studio,
)
from app.stats_utils import _format_hours_minutes

logger = logging.getLogger(__name__)

# Controls the maximum items shown in every top-N statistics card.
# Change this ONE value to resize all top cards simultaneously.
STATISTICS_TOP_N = 50
# Scan credits sequentially for dense SQLite watch histories.
DENSE_CREDIT_SCAN_MIN_ITEMS = 1_000
DENSE_CREDIT_SCAN_RATIO = 0.8
# Cross-type global top-rated heap — intentionally same value but a distinct card.
STATISTICS_TOP_RATED_OVERALL = 50

RUNTIME_UNKNOWN_AIRED = 999998  # aired but runtime unknown


def _safe_runtime_minutes(value):
    if not value:
        return 0
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    # Exclude fallback values: 999998 (aired but runtime unknown) and 999999 (unknown runtime)
    if minutes >= RUNTIME_UNKNOWN_AIRED:
        return 0
    return minutes


def _require_movie_or_game_date(qs, start_date, end_date, *, is_all_time=None):
    """Restrict a movie/game queryset to dated entries, unless this is an all-time query.

    "All Time" has no period an entry could fail to belong to, so entries
    with no recorded date are kept. Any concrete range still needs a date to
    place the entry within it. `is_all_time` defaults to inferring from
    `start_date`/`end_date` both being None, but callers that must pass
    concrete (e.g. day-list-derived) bounds for other reasons while still
    meaning "all time" for filtering purposes can pass it explicitly.
    """
    if is_all_time is None:
        is_all_time = start_date is None and end_date is None
    if is_all_time:
        return qs
    return qs.filter(Q(end_date__isnull=False) | Q(start_date__isnull=False))


def _tv_episode_play_rows(user, start_date, end_date, *, is_all_time=None):
    """Return watched TV episode rows and the season/show items they touch."""
    if is_all_time is None:
        is_all_time = start_date is None and end_date is None
    episodes_qs = Episode.objects.filter(Episode.watch_q(), related_season__user=user)
    if not is_all_time:
        if start_date or end_date:
            episodes_qs = episodes_qs.filter(end_date__isnull=False)
        if start_date:
            episodes_qs = episodes_qs.filter(end_date__gte=start_date)
        if end_date:
            episodes_qs = episodes_qs.filter(end_date__lte=end_date)

    episode_play_rows = []
    season_item_ids = set()
    tv_item_ids = set()
    for (
        episode_item_id,
        season_item_id,
        tv_item_id,
        runtime_minutes,
    ) in episodes_qs.values_list(
        "item_id",
        "related_season__item_id",
        "related_season__related_tv__item_id",
        "item__runtime_minutes",
    ).iterator():
        if not tv_item_id:
            continue
        episode_play_rows.append(
            (
                episode_item_id,
                season_item_id,
                tv_item_id,
                _safe_runtime_minutes(runtime_minutes),
            ),
        )
        if season_item_id:
            season_item_ids.add(season_item_id)
        if tv_item_id:
            tv_item_ids.add(tv_item_id)

    season_items_with_cast_credits = set()
    season_items_with_director_credits = set()
    season_items_with_writer_credits = set()
    season_items_with_usable_credits = credit_helpers.usable_credits_backfill_item_ids(
        season_item_ids,
    )
    if season_item_ids:
        for credit in (
            ItemPersonCredit.objects.filter(item_id__in=season_item_ids)
            .order_by()
            .values_list("item_id", "role_type", "department", "role", named=True)
            .iterator(chunk_size=500)
        ):
            if credit.role_type == CreditRoleType.CAST.value:
                season_items_with_cast_credits.add(credit.item_id)
                continue
            if credit.role_type == CreditRoleType.CREW.value:
                if _is_director_credit(credit):
                    season_items_with_director_credits.add(credit.item_id)
                if _is_writer_credit(credit):
                    season_items_with_writer_credits.add(credit.item_id)

    return SimpleNamespace(
        episode_play_rows=episode_play_rows,
        season_item_ids=season_item_ids,
        tv_item_ids=tv_item_ids,
        season_items_with_cast_credits=season_items_with_cast_credits,
        season_items_with_director_credits=season_items_with_director_credits,
        season_items_with_writer_credits=season_items_with_writer_credits,
        season_items_with_usable_credits=season_items_with_usable_credits,
    )


def _resolve_missing_credit_item_ids(item_ids):
    """Return TMDB movie/show/season/episode item IDs that still need credits backfill."""
    return credit_helpers.missing_credits_backfill_item_ids(item_ids)


def _is_director_credit(credit) -> bool:
    department = (credit.department or "").strip().lower()
    role = (credit.role or "").strip().lower()
    if department == "directing":
        return True
    return "director" in role


def _is_writer_credit(credit) -> bool:
    department = (credit.department or "").strip().lower()
    role = (credit.role or "").strip().lower()
    if department == "writing":
        return True
    return any(
        keyword in role
        for keyword in ("writer", "screenplay", "story", "teleplay", "script")
    )


def _cast_bucket_for_person(person) -> str:
    """Classify cast into actor/actress buckets, defaulting unknowns to actor."""
    if not person or person.gender != PersonGender.FEMALE.value:
        return "actor"
    return "actress"


def _build_person_talent_context(
    user,
    start_date=None,
    end_date=None,
    schedule_missing_backfill=True,
    *,
    is_all_time=None,
):
    """Build shared watched-item context for per-person talent computations."""
    if not user:
        return None
    if is_all_time is None:
        is_all_time = start_date is None and end_date is None

    movie_play_counts = Counter()
    movie_watch_minutes = Counter()
    game_play_counts = Counter()
    game_watch_minutes = Counter()
    tv_episode_rows = _tv_episode_play_rows(
        user,
        start_date,
        end_date,
        is_all_time=is_all_time,
    )
    episode_play_rows = tv_episode_rows.episode_play_rows
    season_item_ids = tv_episode_rows.season_item_ids
    tv_item_ids = tv_episode_rows.tv_item_ids
    season_items_with_cast_credits = tv_episode_rows.season_items_with_cast_credits
    season_items_with_director_credits = (
        tv_episode_rows.season_items_with_director_credits
    )
    season_items_with_writer_credits = tv_episode_rows.season_items_with_writer_credits
    season_items_with_usable_credits = tv_episode_rows.season_items_with_usable_credits

    movies_qs = _require_movie_or_game_date(
        Movie.objects.filter(user=user), start_date, end_date, is_all_time=is_all_time
    )
    if not is_all_time and start_date:
        movies_qs = movies_qs.filter(
            Q(end_date__gte=start_date)
            | (Q(end_date__isnull=True) & Q(start_date__gte=start_date)),
        )
    if not is_all_time and end_date:
        movies_qs = movies_qs.filter(
            Q(end_date__lte=end_date)
            | (Q(end_date__isnull=True) & Q(start_date__lte=end_date)),
        )
    for item_id, runtime_minutes in movies_qs.values_list(
        "item_id", "item__runtime_minutes"
    ).iterator():
        if item_id:
            movie_play_counts[item_id] += 1
            movie_watch_minutes[item_id] += _safe_runtime_minutes(runtime_minutes)

    from app.stats_time import _calculate_game_time_in_range

    games_qs = _require_movie_or_game_date(
        Game.objects.filter(user=user), start_date, end_date, is_all_time=is_all_time
    )
    if not is_all_time and start_date:
        games_qs = games_qs.filter(
            Q(end_date__gte=start_date)
            | (Q(end_date__isnull=True) & Q(start_date__gte=start_date)),
        )
    if not is_all_time and end_date:
        games_qs = games_qs.filter(
            Q(end_date__lte=end_date)
            | (Q(end_date__isnull=True) & Q(start_date__lte=end_date)),
        )
    for game in games_qs.select_related("item").iterator():
        if not game.item_id:
            continue
        game_play_counts[game.item_id] += 1
        game_watch_minutes[game.item_id] += int(
            _calculate_game_time_in_range(game, start_date, end_date) or 0
        )

    if not movie_play_counts and not game_play_counts and not episode_play_rows:
        return None

    movie_item_ids = set(movie_play_counts.keys())
    game_item_ids = set(game_play_counts.keys())
    show_item_ids = set(tv_item_ids)
    episode_item_ids = {
        episode_item_id
        for episode_item_id, _, _, _ in episode_play_rows
        if episode_item_id
    }
    played_item_ids = (
        movie_item_ids
        | game_item_ids
        | show_item_ids
        | season_item_ids
        | episode_item_ids
    )
    if not played_item_ids:
        return None
    item_rows = list(
        Item.objects.filter(
            id__in=played_item_ids,
        ).values_list("id", "media_type", "media_id", "source"),
    )
    item_media_type_by_id = {
        item_id: media_type for item_id, media_type, _media_id, _source in item_rows
    }
    item_media_key_by_id = {
        item_id: (media_type, str(media_id))
        for item_id, media_type, media_id, _source in item_rows
    }
    item_source_by_id = {
        item_id: source for item_id, _media_type, _media_id, source in item_rows
    }

    tv_items_with_usable_credits = credit_helpers.usable_credits_backfill_item_ids(
        tv_item_ids
    )

    missing_credit_item_ids = credit_helpers.missing_credits_backfill_item_ids(
        played_item_ids
    )
    if missing_credit_item_ids and schedule_missing_backfill:
        try:
            from app.tasks import enqueue_credits_backfill_items

            enqueue_credits_backfill_items(missing_credit_item_ids, countdown=3)
        except Exception as exc:  # pragma: no cover - best effort scheduling
            logger.debug(
                "person_talent_credits_backfill_schedule_failed user_id=%s item_count=%s error=%s",
                user.id,
                len(missing_credit_item_ids),
                exc,
            )

    return {
        "movie_play_counts": movie_play_counts,
        "movie_watch_minutes": movie_watch_minutes,
        "game_play_counts": game_play_counts,
        "game_watch_minutes": game_watch_minutes,
        "episode_play_rows": episode_play_rows,
        "season_items_with_cast_credits": season_items_with_cast_credits,
        "season_items_with_director_credits": season_items_with_director_credits,
        "season_items_with_writer_credits": season_items_with_writer_credits,
        "season_items_with_usable_credits": season_items_with_usable_credits,
        "item_media_type_by_id": item_media_type_by_id,
        "item_media_key_by_id": item_media_key_by_id,
        "item_source_by_id": item_source_by_id,
        "tv_items_with_usable_credits": tv_items_with_usable_credits,
        "played_item_ids": played_item_ids,
    }


def _get_person_talent_totals_from_context(user, person, context):
    """Reduce a shared watched-item context down to one person's totals."""
    if not context:
        return None

    movie_play_counts = context["movie_play_counts"]
    movie_watch_minutes = context["movie_watch_minutes"]
    game_play_counts = context["game_play_counts"]
    game_watch_minutes = context["game_watch_minutes"]
    episode_play_rows = context["episode_play_rows"]
    season_items_with_cast_credits = context["season_items_with_cast_credits"]
    season_items_with_director_credits = context["season_items_with_director_credits"]
    season_items_with_writer_credits = context["season_items_with_writer_credits"]
    season_items_with_usable_credits = context["season_items_with_usable_credits"]
    item_media_type_by_id = context["item_media_type_by_id"]
    item_media_key_by_id = context["item_media_key_by_id"]
    item_source_by_id = context["item_source_by_id"]
    tv_items_with_usable_credits = context["tv_items_with_usable_credits"]
    played_item_ids = context["played_item_ids"]

    actor_credit_item_ids = set()
    actress_credit_item_ids = set()
    director_credit_item_ids = set()
    writer_credit_item_ids = set()

    person_credits = (
        ItemPersonCredit.objects.filter(
            item_id__in=played_item_ids,
            person_id=person.id,
        )
        .order_by()
        .values_list(
            "item_id",
            "role_type",
            "department",
            "role",
            "sort_order",
            named=True,
        )
    )
    for credit in person_credits.iterator(chunk_size=500):
        item_media_type = item_media_type_by_id.get(credit.item_id)
        if not item_media_type:
            continue
        if credit.role_type == CreditRoleType.CAST.value:
            if (
                item_media_type == MediaTypes.TV.value
                and not credit_helpers.is_usable_tv_show_credit(
                    item_source_by_id.get(credit.item_id),
                    credit.role_type,
                    credit.sort_order,
                )
            ):
                continue
            cast_bucket = _cast_bucket_for_person(person)
            if cast_bucket == "actor":
                actor_credit_item_ids.add(credit.item_id)
            else:
                actress_credit_item_ids.add(credit.item_id)
            continue

        if credit.role_type == CreditRoleType.CREW.value:
            if _is_director_credit(credit):
                director_credit_item_ids.add(credit.item_id)
            if _is_writer_credit(credit):
                writer_credit_item_ids.add(credit.item_id)

    bucket_plays = Counter()
    bucket_minutes = Counter()
    bucket_movie_items = defaultdict(set)
    bucket_game_items = defaultdict(set)
    bucket_show_items = defaultdict(set)
    bucket_minutes_by_media_key = defaultdict(lambda: defaultdict(int))
    bucket_plays_by_media_key = defaultdict(lambda: defaultdict(int))

    role_sources = (
        (
            "actor",
            actor_credit_item_ids,
            season_items_with_cast_credits,
            CreditRoleType.CAST.value,
        ),
        (
            "actress",
            actress_credit_item_ids,
            season_items_with_cast_credits,
            CreditRoleType.CAST.value,
        ),
        (
            "director",
            director_credit_item_ids,
            season_items_with_director_credits,
            CreditRoleType.CREW.value,
        ),
        (
            "writer",
            writer_credit_item_ids,
            season_items_with_writer_credits,
            CreditRoleType.CREW.value,
        ),
    )

    for item_id, plays in movie_play_counts.items():
        if plays <= 0:
            continue
        watched_minutes = int(movie_watch_minutes.get(item_id, 0))
        media_key = item_media_key_by_id.get(item_id)
        for bucket, item_ids, _season_credit_item_ids, _role_type in role_sources:
            if item_id not in item_ids:
                continue
            bucket_plays[bucket] += plays
            bucket_minutes[bucket] += watched_minutes
            bucket_movie_items[bucket].add(item_id)
            if media_key:
                bucket_minutes_by_media_key[bucket][media_key] += watched_minutes
                bucket_plays_by_media_key[bucket][media_key] += plays

    for item_id, plays in game_play_counts.items():
        if plays <= 0:
            continue
        watched_minutes = int(game_watch_minutes.get(item_id, 0))
        media_key = item_media_key_by_id.get(item_id)
        for bucket, item_ids, _season_credit_item_ids, _role_type in role_sources:
            if item_id not in item_ids:
                continue
            bucket_plays[bucket] += plays
            bucket_minutes[bucket] += watched_minutes
            bucket_game_items[bucket].add(item_id)
            if media_key:
                bucket_minutes_by_media_key[bucket][media_key] += watched_minutes
                bucket_plays_by_media_key[bucket][media_key] += plays

    for (
        episode_item_id,
        season_item_id,
        tv_item_id,
        watched_minutes,
    ) in episode_play_rows:
        if not tv_item_id:
            continue
        media_key = item_media_key_by_id.get(tv_item_id)
        show_has_usable_credits = tv_item_id in tv_items_with_usable_credits
        for bucket, item_ids, season_credit_item_ids, role_type in role_sources:
            season_has_usable_credits = (
                season_item_id in season_credit_item_ids
                and season_item_id in season_items_with_usable_credits
            )
            is_match = (
                episode_item_id in item_ids
                or season_item_id in item_ids
                or (
                    tv_item_id in item_ids
                    and credit_helpers.should_count_tv_show_credit_for_episode(
                        item_source_by_id.get(tv_item_id),
                        role_type,
                        None,
                        season_has_usable_credits,
                        show_has_usable_credits,
                    )
                )
            )
            if not is_match:
                continue
            bucket_plays[bucket] += 1
            bucket_minutes[bucket] += watched_minutes
            bucket_show_items[bucket].add(tv_item_id)
            if media_key:
                bucket_minutes_by_media_key[bucket][media_key] += watched_minutes
                bucket_plays_by_media_key[bucket][media_key] += 1

    bucket_payloads = {}
    for bucket, _item_ids, _season_credit_item_ids, _role_type in role_sources:
        unique_movies = len(bucket_movie_items.get(bucket, set()))
        unique_games = len(bucket_game_items.get(bucket, set()))
        unique_shows = len(bucket_show_items.get(bucket, set()))
        watched_minutes = int(bucket_minutes.get(bucket, 0))
        bucket_payloads[bucket] = {
            "bucket": bucket,
            "plays": int(bucket_plays.get(bucket, 0)),
            "watched_minutes": watched_minutes,
            "watched_time": _format_hours_minutes(
                watched_minutes, user.duration_format
            ),
            "unique_movies": unique_movies,
            "unique_games": unique_games,
            "unique_shows": unique_shows,
            "unique_titles": unique_movies + unique_games + unique_shows,
            "minutes_by_media_key": dict(bucket_minutes_by_media_key.get(bucket, {})),
            "plays_by_media_key": dict(bucket_plays_by_media_key.get(bucket, {})),
        }

    nonzero_buckets = [
        bucket
        for bucket, payload in bucket_payloads.items()
        if payload["plays"] > 0 or payload["watched_minutes"] > 0
    ]
    if not nonzero_buckets:
        return None

    known_for_department = (person.known_for_department or "").strip().lower()
    preferred_order = []
    if known_for_department == "acting":
        if person.gender == PersonGender.MALE.value:
            preferred_order = ["actor"]
        elif person.gender == PersonGender.FEMALE.value:
            preferred_order = ["actress"]
        else:
            preferred_order = ["actor", "actress"]
    elif known_for_department == "directing":
        preferred_order = ["director"]
    elif known_for_department == "writing":
        preferred_order = ["writer"]

    selected_bucket = None
    for bucket in preferred_order:
        if bucket in nonzero_buckets:
            selected_bucket = bucket
            break

    if selected_bucket is None:
        selected_bucket = max(
            nonzero_buckets,
            key=lambda bucket: (
                bucket_payloads[bucket]["plays"],
                bucket_payloads[bucket]["watched_minutes"],
                bucket_payloads[bucket]["unique_titles"],
            ),
        )

    return bucket_payloads.get(selected_bucket)


def get_person_talent_totals(
    user,
    person_source,
    person_id,
    start_date=None,
    end_date=None,
    context=None,
):
    """Return stats-style totals for a single person's primary talent bucket."""
    if not user or not person_source or person_id is None:
        return None

    person = Person.objects.filter(
        source=person_source,
        source_person_id=str(person_id),
    ).first()
    if not person:
        return None

    if context is None:
        context = _build_person_talent_context(user, start_date, end_date)

    return _get_person_talent_totals_from_context(user, person, context)


TALENT_MEDIA_TYPES = frozenset(
    {
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.ANIME.value,
        MediaTypes.GAME.value,
    }
)


def _normalize_talent_media_types(media_type, allowed=TALENT_MEDIA_TYPES):
    """Turn "tv", "tv,movie" or a collection into a set of `allowed` types.

    Returns None (no filtering) for None/"all"/empty input. Types without
    cast/crew data (books, music, ...) are dropped, so a selection made only of
    them yields an empty set and therefore no talent.
    """
    if media_type in (None, "", "all"):
        return None
    if isinstance(media_type, str):
        media_type = media_type.split(",")
    selected = {str(value).strip() for value in media_type} & allowed
    return frozenset(selected)


def _aggregate_top_talent(
    user,
    start_date,
    end_date,
    limit=STATISTICS_TOP_N,
    schedule_missing_backfill=True,
    media_type=None,
    *,
    is_all_time=None,
):
    """Aggregate top cast/crew/studio rollups from watched movie and TV plays.

    media_type restricts the aggregation to "movie", "tv", "anime", and/or
    "game": one value, or several as a comma-separated string or a collection.
    None/"all" (the default) keeps the unfiltered cross-type rollup.
    `is_all_time` defaults to inferring "all time" from start_date/end_date
    both being None; callers that must pass concrete bounds for other
    reasons (e.g. day-list-derived aware datetimes) while still meaning "all
    time" for date filtering can pass it explicitly.
    """
    if is_all_time is None:
        is_all_time = start_date is None and end_date is None
    movie_play_counts = Counter()
    movie_watch_minutes = Counter()
    game_play_counts = Counter()
    game_watch_minutes = Counter()
    valid_sort_modes = ("plays", "time", "titles")
    sort_by = getattr(user, "top_talent_sort_by", "plays")
    if sort_by not in valid_sort_modes:
        sort_by = "plays"

    def _empty_talent_bucket():
        return {
            "top_actors": [],
            "top_actresses": [],
            "top_directors": [],
            "top_writers": [],
            "top_studios": [],
        }

    tv_episode_rows = _tv_episode_play_rows(
        user,
        start_date,
        end_date,
        is_all_time=is_all_time,
    )
    episode_play_rows = tv_episode_rows.episode_play_rows
    season_item_ids = tv_episode_rows.season_item_ids
    tv_item_ids = tv_episode_rows.tv_item_ids
    season_items_with_cast_credits = tv_episode_rows.season_items_with_cast_credits
    season_items_with_director_credits = (
        tv_episode_rows.season_items_with_director_credits
    )
    season_items_with_writer_credits = tv_episode_rows.season_items_with_writer_credits
    season_items_with_usable_credits = tv_episode_rows.season_items_with_usable_credits

    # Movie plays: count completed/dated movie entries (all-time includes
    # entries with no recorded date; a concrete range still requires one).
    movies_qs = _require_movie_or_game_date(
        Movie.objects.filter(user=user), start_date, end_date, is_all_time=is_all_time
    )
    if not is_all_time and start_date:
        movies_qs = movies_qs.filter(
            Q(end_date__gte=start_date)
            | (Q(end_date__isnull=True) & Q(start_date__gte=start_date)),
        )
    if not is_all_time and end_date:
        movies_qs = movies_qs.filter(
            Q(end_date__lte=end_date)
            | (Q(end_date__isnull=True) & Q(start_date__lte=end_date)),
        )
    for item_id, runtime_minutes in movies_qs.values_list(
        "item_id", "item__runtime_minutes"
    ).iterator():
        if item_id:
            movie_play_counts[item_id] += 1
            movie_watch_minutes[item_id] += _safe_runtime_minutes(runtime_minutes)

    # Game plays: same completed/dated filter as movies. Games only have
    # best-effort IMDB-sourced cast (see app.services.imdb_game_credits), so
    # this just needs their item ids folded into played_item_ids below.
    games_qs = _require_movie_or_game_date(
        Game.objects.filter(user=user), start_date, end_date, is_all_time=is_all_time
    )
    if not is_all_time and start_date:
        games_qs = games_qs.filter(
            Q(end_date__gte=start_date)
            | (Q(end_date__isnull=True) & Q(start_date__gte=start_date)),
        )
    if not is_all_time and end_date:
        games_qs = games_qs.filter(
            Q(end_date__lte=end_date)
            | (Q(end_date__isnull=True) & Q(start_date__lte=end_date)),
        )
    from app.stats_time import _calculate_game_time_in_range

    for game in games_qs.select_related("item").iterator():
        if not game.item_id:
            continue
        game_play_counts[game.item_id] += 1
        game_watch_minutes[game.item_id] += int(
            _calculate_game_time_in_range(game, start_date, end_date) or 0
        )

    selected_types = _normalize_talent_media_types(media_type)
    if selected_types is not None:
        if MediaTypes.MOVIE.value not in selected_types:
            movie_play_counts = Counter()
            movie_watch_minutes = Counter()
        if MediaTypes.GAME.value not in selected_types:
            game_play_counts = Counter()
            game_watch_minutes = Counter()
        selected_show_types = selected_types & {
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
        }
        if not selected_show_types:
            episode_play_rows = []
            season_item_ids = set()
            tv_item_ids = set()
        elif len(selected_show_types) == 1:
            # TV and Anime share the same related_tv FK chain in
            # _tv_episode_play_rows, so the split can only happen here once
            # each show's own Item.media_type is known.
            show_media_type_by_id = dict(
                Item.objects.filter(id__in=tv_item_ids).values_list("id", "media_type"),
            )
            tv_item_ids = {
                item_id
                for item_id in tv_item_ids
                if show_media_type_by_id.get(item_id) in selected_show_types
            }
            episode_play_rows = [
                row for row in episode_play_rows if row[2] in tv_item_ids
            ]
            season_item_ids = {row[1] for row in episode_play_rows if row[1]}

    if not movie_play_counts and not episode_play_rows and not game_play_counts:
        by_sort = {mode: _empty_talent_bucket() for mode in valid_sort_modes}
        selected_payload = by_sort.get(sort_by, _empty_talent_bucket())
        return {
            "sort_by": sort_by,
            "by_sort": by_sort,
            **selected_payload,
        }

    movie_item_ids = set(movie_play_counts.keys())
    game_item_ids = set(game_play_counts.keys())
    show_item_ids = set(tv_item_ids)
    episode_item_ids = {
        episode_item_id
        for episode_item_id, _, _, _ in episode_play_rows
        if episode_item_id
    }
    played_item_ids = (
        movie_item_ids
        | show_item_ids
        | season_item_ids
        | episode_item_ids
        | game_item_ids
    )
    item_media_type_by_id = {}
    item_source_by_id = {}
    for item_id, media_type_value, source in (
        Item.objects.filter(
            id__in=played_item_ids,
        )
        .values_list("id", "media_type", "source")
        .iterator(chunk_size=500)
    ):
        item_media_type_by_id[item_id] = media_type_value
        item_source_by_id[item_id] = source

    cast_actor_ids_by_item = defaultdict(set)
    cast_actress_ids_by_item = defaultdict(set)
    director_ids_by_item = defaultdict(set)
    writer_ids_by_item = defaultdict(set)
    studio_ids_by_item = defaultdict(set)
    person_names = {}
    studio_names = {}
    people_by_id = {}
    studios_by_id = {}

    # Project only aggregation fields, avoiding materialised Person objects.
    # Sparse histories use the indexed, batched query introduced upstream in
    # #1855. For dense SQLite histories, a sequential scan avoids repeated
    # index probes and expensive person-join plans.
    dense_credit_scan = (
        connection.vendor == "sqlite"
        and len(played_item_ids) >= DENSE_CREDIT_SCAN_MIN_ITEMS
        and len(played_item_ids) >= Item.objects.count() * DENSE_CREDIT_SCAN_RATIO
    )
    played_item_id_batches = list(played_item_ids)
    batch_size = 500
    if dense_credit_scan:
        credit_querysets = (ItemPersonCredit.objects.all().order_by(),)
    else:
        credit_querysets = (
            ItemPersonCredit.objects.filter(
                item_id__in=played_item_id_batches[offset : offset + batch_size]
            ).order_by()
            for offset in range(0, len(played_item_id_batches), batch_size)
        )

    for person_credit_qs in credit_querysets:
        person_credits = person_credit_qs.values_list(
            "item_id",
            "person_id",
            "role_type",
            "role",
            "department",
            "sort_order",
            "person__gender",
            "person__name",
            named=True,
        )
        chunk_size = 2000 if dense_credit_scan else batch_size
        for credit in person_credits.iterator(chunk_size=chunk_size):
            if dense_credit_scan and credit.item_id not in played_item_ids:
                continue
            person_names[credit.person_id] = credit.person__name.lower()
            if credit.role_type == CreditRoleType.CAST.value:
                item_media_type = item_media_type_by_id.get(credit.item_id)
                if (
                    item_media_type == MediaTypes.TV.value
                    and not credit_helpers.is_usable_tv_show_credit(
                        item_source_by_id.get(credit.item_id),
                        credit.role_type,
                        credit.sort_order,
                    )
                ):
                    continue
                if credit.person__gender == PersonGender.FEMALE.value:
                    cast_actress_ids_by_item[credit.item_id].add(credit.person_id)
                else:
                    cast_actor_ids_by_item[credit.item_id].add(credit.person_id)
                continue
            if credit.role_type == CreditRoleType.CREW.value:
                if _is_director_credit(credit):
                    director_ids_by_item[credit.item_id].add(credit.person_id)
                if _is_writer_credit(credit):
                    writer_ids_by_item[credit.item_id].add(credit.person_id)

    studio_item_ids = movie_item_ids | show_item_ids | game_item_ids
    studio_credits = (
        ItemStudioCredit.objects.filter(item_id__in=studio_item_ids)
        .order_by()
        .values_list("item_id", "studio_id", "studio__name")
    )
    for item_id, studio_id, name in studio_credits.iterator(chunk_size=500):
        studio_names[studio_id] = name.lower()
        studio_ids_by_item[item_id].add(studio_id)

    tv_items_with_usable_credits = credit_helpers.usable_credits_backfill_item_ids(
        tv_item_ids
    )
    missing_credit_item_ids = credit_helpers.missing_credits_backfill_item_ids(
        played_item_ids
    )

    if missing_credit_item_ids and schedule_missing_backfill:
        try:
            from app.tasks import enqueue_credits_backfill_items

            enqueue_credits_backfill_items(missing_credit_item_ids, countdown=3)
        except Exception as exc:  # pragma: no cover - best effort scheduling
            logger.debug(
                "top_talent_credits_backfill_schedule_failed user_id=%s items=%s error=%s",
                user.id,
                len(missing_credit_item_ids),
                exc,
            )

    actor_counts = Counter()
    actor_minutes = Counter()
    actress_counts = Counter()
    actress_minutes = Counter()
    director_counts = Counter()
    director_minutes = Counter()
    writer_counts = Counter()
    writer_minutes = Counter()
    studio_counts = Counter()
    studio_minutes = Counter()
    actor_movie_items = defaultdict(set)
    actor_game_items = defaultdict(set)
    actor_show_items = defaultdict(set)
    actress_movie_items = defaultdict(set)
    actress_game_items = defaultdict(set)
    actress_show_items = defaultdict(set)
    director_movie_items = defaultdict(set)
    director_game_items = defaultdict(set)
    director_show_items = defaultdict(set)
    writer_movie_items = defaultdict(set)
    writer_game_items = defaultdict(set)
    writer_show_items = defaultdict(set)
    studio_movie_items = defaultdict(set)
    studio_game_items = defaultdict(set)
    studio_show_items = defaultdict(set)

    for item_id, plays in movie_play_counts.items():
        if plays <= 0:
            continue
        watched_minutes = int(movie_watch_minutes.get(item_id, 0))
        for person_id in cast_actor_ids_by_item.get(item_id, ()):
            actor_counts[person_id] += plays
            actor_minutes[person_id] += watched_minutes
            actor_movie_items[person_id].add(item_id)
        for person_id in cast_actress_ids_by_item.get(item_id, ()):
            actress_counts[person_id] += plays
            actress_minutes[person_id] += watched_minutes
            actress_movie_items[person_id].add(item_id)
        for person_id in director_ids_by_item.get(item_id, ()):
            director_counts[person_id] += plays
            director_minutes[person_id] += watched_minutes
            director_movie_items[person_id].add(item_id)
        for person_id in writer_ids_by_item.get(item_id, ()):
            writer_counts[person_id] += plays
            writer_minutes[person_id] += watched_minutes
            writer_movie_items[person_id].add(item_id)
        for studio_id in studio_ids_by_item.get(item_id, ()):
            studio_counts[studio_id] += plays
            studio_minutes[studio_id] += watched_minutes
            studio_movie_items[studio_id].add(item_id)

    # Games are single-title credits like movies, but keep their title counts
    # separate so Featured Repeat Player can show Movies, Games, and Shows.
    for item_id, plays in game_play_counts.items():
        if plays <= 0:
            continue
        watched_minutes = int(game_watch_minutes.get(item_id, 0))
        for person_id in cast_actor_ids_by_item.get(item_id, ()):
            actor_counts[person_id] += plays
            actor_minutes[person_id] += watched_minutes
            actor_game_items[person_id].add(item_id)
        for person_id in cast_actress_ids_by_item.get(item_id, ()):
            actress_counts[person_id] += plays
            actress_minutes[person_id] += watched_minutes
            actress_game_items[person_id].add(item_id)
        for person_id in director_ids_by_item.get(item_id, ()):
            director_counts[person_id] += plays
            director_minutes[person_id] += watched_minutes
            director_game_items[person_id].add(item_id)
        for person_id in writer_ids_by_item.get(item_id, ()):
            writer_counts[person_id] += plays
            writer_minutes[person_id] += watched_minutes
            writer_game_items[person_id].add(item_id)
        for studio_id in studio_ids_by_item.get(item_id, ()):
            studio_counts[studio_id] += plays
            studio_minutes[studio_id] += watched_minutes
            studio_game_items[studio_id].add(item_id)

    for (
        episode_item_id,
        season_item_id,
        tv_item_id,
        watched_minutes,
    ) in episode_play_rows:
        if not tv_item_id:
            continue

        season_has_cast_credits = (
            season_item_id in season_items_with_cast_credits
            and season_item_id in season_items_with_usable_credits
        )
        season_has_director_credits = (
            season_item_id in season_items_with_director_credits
            and season_item_id in season_items_with_usable_credits
        )
        season_has_writer_credits = (
            season_item_id in season_items_with_writer_credits
            and season_item_id in season_items_with_usable_credits
        )
        show_has_usable_credits = tv_item_id in tv_items_with_usable_credits
        actor_ids = cast_actor_ids_by_item.get(
            episode_item_id, set()
        ) | cast_actor_ids_by_item.get(
            season_item_id,
            set(),
        )
        if credit_helpers.should_count_tv_show_credit_for_episode(
            item_source_by_id.get(tv_item_id),
            CreditRoleType.CAST.value,
            None,
            season_has_cast_credits,
            show_has_usable_credits,
        ):
            actor_ids |= cast_actor_ids_by_item.get(tv_item_id, set())
        for person_id in actor_ids:
            actor_counts[person_id] += 1
            actor_minutes[person_id] += watched_minutes
            actor_show_items[person_id].add(tv_item_id)

        actress_ids = cast_actress_ids_by_item.get(
            episode_item_id, set()
        ) | cast_actress_ids_by_item.get(
            season_item_id,
            set(),
        )
        if credit_helpers.should_count_tv_show_credit_for_episode(
            item_source_by_id.get(tv_item_id),
            CreditRoleType.CAST.value,
            None,
            season_has_cast_credits,
            show_has_usable_credits,
        ):
            actress_ids |= cast_actress_ids_by_item.get(tv_item_id, set())
        for person_id in actress_ids:
            actress_counts[person_id] += 1
            actress_minutes[person_id] += watched_minutes
            actress_show_items[person_id].add(tv_item_id)

        director_ids = director_ids_by_item.get(
            episode_item_id, set()
        ) | director_ids_by_item.get(
            season_item_id,
            set(),
        )
        if credit_helpers.should_count_tv_show_credit_for_episode(
            item_source_by_id.get(tv_item_id),
            CreditRoleType.CREW.value,
            None,
            season_has_director_credits,
            show_has_usable_credits,
        ):
            director_ids |= director_ids_by_item.get(tv_item_id, set())
        for person_id in director_ids:
            director_counts[person_id] += 1
            director_minutes[person_id] += watched_minutes
            director_show_items[person_id].add(tv_item_id)

        writer_ids = writer_ids_by_item.get(
            episode_item_id, set()
        ) | writer_ids_by_item.get(
            season_item_id,
            set(),
        )
        if credit_helpers.should_count_tv_show_credit_for_episode(
            item_source_by_id.get(tv_item_id),
            CreditRoleType.CREW.value,
            None,
            season_has_writer_credits,
            show_has_usable_credits,
        ):
            writer_ids |= writer_ids_by_item.get(tv_item_id, set())
        for person_id in writer_ids:
            writer_counts[person_id] += 1
            writer_minutes[person_id] += watched_minutes
            writer_show_items[person_id].add(tv_item_id)

        for studio_id in studio_ids_by_item.get(tv_item_id, ()):
            studio_counts[studio_id] += 1
            studio_minutes[studio_id] += watched_minutes
            studio_show_items[studio_id].add(tv_item_id)

    def _person_sort_key(
        person_id,
        plays,
        minutes,
        movie_items_by_person,
        game_items_by_person,
        show_items_by_person,
        mode,
    ):
        unique_movies = len(movie_items_by_person.get(person_id, set()))
        unique_games = len(game_items_by_person.get(person_id, set()))
        unique_shows = len(show_items_by_person.get(person_id, set()))
        unique_titles = unique_movies + unique_games + unique_shows
        name_key = person_names.get(person_id, "")
        if mode == "time":
            return (-minutes, -plays, -unique_titles, name_key)
        if mode == "titles":
            return (-unique_titles, -plays, -minutes, name_key)
        return (-plays, -minutes, -unique_titles, name_key)

    def _studio_sort_key(
        studio_id,
        plays,
        minutes,
        movie_items_by_studio,
        game_items_by_studio,
        show_items_by_studio,
        mode,
    ):
        unique_movies = len(movie_items_by_studio.get(studio_id, set()))
        unique_games = len(game_items_by_studio.get(studio_id, set()))
        unique_shows = len(show_items_by_studio.get(studio_id, set()))
        unique_titles = unique_movies + unique_games + unique_shows
        name_key = studio_names.get(studio_id, "")
        if mode == "time":
            return (-minutes, -plays, -unique_titles, name_key)
        if mode == "titles":
            return (-unique_titles, -plays, -minutes, name_key)
        return (-plays, -minutes, -unique_titles, name_key)

    def _sorted_people(
        counter_obj,
        minute_counter,
        movie_items_by_person,
        game_items_by_person,
        show_items_by_person,
        mode,
    ):
        ranked = nsmallest(
            limit,
            counter_obj.items(),
            key=lambda row: _person_sort_key(
                row[0],
                row[1],
                int(minute_counter.get(row[0], 0)),
                movie_items_by_person,
                game_items_by_person,
                show_items_by_person,
                mode,
            ),
        )
        payload = []
        missing_ids = {person_id for person_id, _ in ranked} - people_by_id.keys()
        people_by_id.update(
            Person.objects.filter(pk__in=missing_ids)
            .only("name", "image", "source", "source_person_id")
            .in_bulk()
        )
        for person_id, plays in ranked:
            person = people_by_id.get(person_id)
            if not person:
                continue
            watched_minutes = int(minute_counter.get(person_id, 0))
            unique_movies = len(movie_items_by_person.get(person_id, set()))
            unique_games = len(game_items_by_person.get(person_id, set()))
            unique_shows = len(show_items_by_person.get(person_id, set()))
            payload.append(
                {
                    "name": person.name,
                    "image": person.image or settings.IMG_NONE,
                    "source": person.source,
                    "person_id": person.source_person_id,
                    "plays": int(plays),
                    "watched_minutes": watched_minutes,
                    "watched_time": _format_hours_minutes(
                        watched_minutes, user.duration_format
                    ),
                    "unique_movies": unique_movies,
                    "unique_games": unique_games,
                    "unique_shows": unique_shows,
                    "unique_titles": unique_movies + unique_games + unique_shows,
                },
            )
        return payload

    def _sorted_studios(
        counter_obj,
        minute_counter,
        movie_items_by_studio,
        game_items_by_studio,
        show_items_by_studio,
        mode,
    ):
        ranked = nsmallest(
            limit,
            counter_obj.items(),
            key=lambda row: _studio_sort_key(
                row[0],
                row[1],
                int(minute_counter.get(row[0], 0)),
                movie_items_by_studio,
                game_items_by_studio,
                show_items_by_studio,
                mode,
            ),
        )
        payload = []
        missing_ids = {studio_id for studio_id, _ in ranked} - studios_by_id.keys()
        studios_by_id.update(
            Studio.objects.filter(pk__in=missing_ids)
            .only("name", "logo", "source", "source_studio_id")
            .in_bulk()
        )
        for studio_id, plays in ranked:
            studio = studios_by_id.get(studio_id)
            if not studio:
                continue
            watched_minutes = int(minute_counter.get(studio_id, 0))
            unique_movies = len(movie_items_by_studio.get(studio_id, set()))
            unique_games = len(game_items_by_studio.get(studio_id, set()))
            unique_shows = len(show_items_by_studio.get(studio_id, set()))
            payload.append(
                {
                    "name": studio.name,
                    "logo": studio.logo or settings.IMG_NONE,
                    "source": studio.source,
                    "studio_id": studio.source_studio_id,
                    "plays": int(plays),
                    "watched_minutes": watched_minutes,
                    "watched_time": _format_hours_minutes(
                        watched_minutes, user.duration_format
                    ),
                    "unique_movies": unique_movies,
                    "unique_games": unique_games,
                    "unique_shows": unique_shows,
                    "unique_titles": unique_movies + unique_games + unique_shows,
                },
            )
        return payload

    by_sort = {}
    for mode in valid_sort_modes:
        by_sort[mode] = {
            "top_actors": _sorted_people(
                actor_counts,
                actor_minutes,
                actor_movie_items,
                actor_game_items,
                actor_show_items,
                mode,
            ),
            "top_actresses": _sorted_people(
                actress_counts,
                actress_minutes,
                actress_movie_items,
                actress_game_items,
                actress_show_items,
                mode,
            ),
            "top_directors": _sorted_people(
                director_counts,
                director_minutes,
                director_movie_items,
                director_game_items,
                director_show_items,
                mode,
            ),
            "top_writers": _sorted_people(
                writer_counts,
                writer_minutes,
                writer_movie_items,
                writer_game_items,
                writer_show_items,
                mode,
            ),
            "top_studios": _sorted_studios(
                studio_counts,
                studio_minutes,
                studio_movie_items,
                studio_game_items,
                studio_show_items,
                mode,
            ),
        }

    selected_payload = by_sort.get(sort_by, _empty_talent_bucket())
    return {
        "sort_by": sort_by,
        "by_sort": by_sort,
        **selected_payload,
    }
