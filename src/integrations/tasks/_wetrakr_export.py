"""Send a library's existing plays, statuses and ratings to WeTrakr, on request.

Plays are WeTrakr's activity feed, so history never reaches it by itself
(``ACCEPTS_IMPORTED_CHANGES`` is off). This is the one way it does, and only
when the user asks for it on the settings card. Everything goes through the same
outbox as a live play, so batching, rate limits, retries and per-item results
behave identically.

Plays without a date are left out rather than given an invented one: WeTrakr
would show them as watched today.
"""

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db.models import Exists, Max, OuterRef, Q

from app.models import TV, Episode, Item, Movie, MoviePlay, Status
from integrations.models import (
    CAPABILITY_PLAY_PUSH,
    CAPABILITY_RATING_PUSH,
    CAPABILITY_STATUS_PUSH,
    OutboundDeliveryStatus,
    OutboundStateDelivery,
    RatingSyncEdit,
    SyncBinding,
    SyncClientKind,
    SyncDirection,
    WeTrakrAccount,
)
from integrations.state import outbound
from integrations.tasks._ratings import _latest_rating

EXPORT_CHUNK = 500


def _movie_play_sets(user):
    """Return the two querysets behind a movie's dated watches.

    Each ``MoviePlay`` is one watch. A completed ``Movie`` row with no plays
    (imported or older entries) counts once, by its own end date.
    """
    plays = MoviePlay.objects.filter(
        movie__user=user,
        movie__item__isnull=False,
        end_date__isnull=False,
    ).values_list("movie__item_id", "end_date")
    legacy = (
        Movie.objects.filter(
            user=user,
            status=Status.COMPLETED.value,
            end_date__isnull=False,
        )
        .exclude(Exists(MoviePlay.objects.filter(movie=OuterRef("pk"))))
        .values_list("item_id", "end_date")
    )
    return plays, legacy


def _movie_plays(user):
    """Return ``(item_id, watched_at)`` for every dated watch of a movie."""
    plays, legacy = _movie_play_sets(user)
    return [*plays, *legacy]


def _episode_plays(user):
    return Episode.objects.filter(
        Episode.watch_q(),
        related_season__user=user,
        related_season__order_archived=False,
        item__isnull=False,
        end_date__isnull=False,
    ).values_list("item_id", "end_date")


def _status_sets(user):
    movies = Movie.objects.filter(
        user=user,
        status__in=[Status.PLANNING.value, Status.DROPPED.value],
    ).values_list("item_id", "status")
    shows = TV.objects.filter(
        user=user,
        status__in=[Status.PLANNING.value, Status.PAUSED.value, Status.DROPPED.value],
    ).values_list("item_id", "status")
    return movies, shows


def _statuses(user):
    movies, shows = _status_sets(user)
    return [*movies, *shows]


def _rated_items(user):
    """Movies and shows this user has rated, or cleared a rating on."""
    rated = Q(score__isnull=False) | Q(scored_at__isnull=False)
    return Item.objects.filter(media_type__in=["movie", "tv"]).filter(
        Exists(Movie.objects.filter(user=user, item_id=OuterRef("pk")).filter(rated))
        | Exists(TV.objects.filter(user=user, item_id=OuterRef("pk")).filter(rated))
        | Exists(RatingSyncEdit.objects.filter(user=user, item_id=OuterRef("pk"))),
    )


def library_preview(user):
    """Return how many plays, statuses and ratings an export would queue."""
    # Counts only: this runs on a page load, so it must not read the history.
    plays = (
        sum(rows.count() for rows in _movie_play_sets(user))
        + _episode_plays(user).count()
    )
    undated = (
        Movie.objects.filter(
            user=user,
            status=Status.COMPLETED.value,
            end_date__isnull=True,
        )
        .exclude(
            Exists(
                MoviePlay.objects.filter(movie=OuterRef("pk"), end_date__isnull=False),
            ),
        )
        .count()
        + Episode.objects.filter(
            Episode.watch_q(),
            related_season__user=user,
            related_season__order_archived=False,
            end_date__isnull=True,
        ).count()
    )
    return {
        "plays": plays,
        "undated": undated,
        "statuses": sum(rows.count() for rows in _status_sets(user)),
    }


def _next_revisions(binding, operation):
    """Return the highest revision used so far for each item, for one operation."""
    return dict(
        OutboundStateDelivery.objects.filter(binding=binding, operation=operation)
        .values_list("item_id")
        .annotate(top=Max("target_revision")),
    )


def _queue(binding, user, operation, rows, revisions, *, hold):
    """Create deliveries for ``(item_id, value)`` rows; return how many."""
    created = []
    for item_id, value in rows:
        revisions[item_id] = revisions.get(item_id, 0) + 1
        payload = (
            {"watched_at": value.isoformat()}
            if operation == "play"
            else {"status": value}
        )
        created.append(
            OutboundStateDelivery(
                binding=binding,
                user=user,
                item_id=item_id,
                operation=operation,
                target_revision=revisions[item_id],
                payload=payload,
                status=OutboundDeliveryStatus.PENDING.value,
                client_event_id=f"export-{binding.pk}-{operation}-{item_id}-{revisions[item_id]}",
                next_attempt_at=hold,
            ),
        )
    OutboundStateDelivery.objects.bulk_create(created, batch_size=EXPORT_CHUNK)
    return len(created)


@shared_task(name="Export library to WeTrakr", queue="celery")
def export_wetrakr_library(user_id):
    """Queue every dated play, and the statuses WeTrakr takes, once."""
    user = get_user_model().objects.filter(pk=user_id).first()
    account = WeTrakrAccount.objects.filter(user_id=user_id).first() if user else None
    binding = (
        SyncBinding.objects.filter(
            user_id=user_id,
            client_kind=SyncClientKind.WETRAKR.value,
            profile_key=account.profile_key,
        ).first()
        if account and account.profile_key
        else None
    )
    if binding is None:
        return 0
    hold = outbound.release_time(binding.delivery_schedule)
    already = set(
        OutboundStateDelivery.objects.filter(
            binding=binding,
            operation="play",
        )
        .exclude(status=OutboundDeliveryStatus.SUPERSEDED.value)
        .values_list("item_id", "payload__watched_at"),
    )
    total = 0

    if binding.allows(SyncDirection.OUTBOUND.value, CAPABILITY_PLAY_PUSH):
        revisions = _next_revisions(binding, "play")
        rows = [
            (item_id, when)
            for item_id, when in dict.fromkeys(
                [*_movie_plays(user), *_episode_plays(user)],
            )
            if (item_id, when.isoformat()) not in already
        ]
        for start in range(0, len(rows), EXPORT_CHUNK):
            total += _queue(
                binding,
                user,
                "play",
                rows[start : start + EXPORT_CHUNK],
                revisions,
                hold=hold,
            )

    if binding.allows(SyncDirection.OUTBOUND.value, CAPABILITY_STATUS_PUSH):
        revisions = _next_revisions(binding, "status")
        total += _queue(binding, user, "status", _statuses(user), revisions, hold=hold)

    if binding.allows(SyncDirection.OUTBOUND.value, CAPABILITY_RATING_PUSH):
        for item in _rated_items(user).iterator(chunk_size=EXPORT_CHUNK):
            found, score = _latest_rating(user, item)
            if found:
                total += len(
                    outbound.enqueue_rating_change(
                        user,
                        item,
                        score,
                        client_kind=SyncClientKind.WETRAKR.value,
                    ),
                )

    if total:
        outbound.kick_flush(binding)
    return total
