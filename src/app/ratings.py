import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
    When,
)
from django.db.models.functions import Round
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from app.models import Item, ItemRating, MediaTypes, Sources
from app.providers import omdb, services

logger = logging.getLogger(__name__)

EMPTY_RATING = "\u2013"
WEIGHT_MAX = 10
NORMALIZED_MAX = Decimal(100)
ONE_PLACE = Decimal("0.1")
RATINGS_MAX_AGE = timedelta(days=7)
RATED_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
)
IMDB_ID_RE = re.compile(r"(tt\d+)")
NUMBER_FIELD = DecimalField(max_digits=8, decimal_places=2)
SCORE_FIELD = DecimalField(max_digits=5, decimal_places=1)

# key, label, native scale, display suffix
RATING_SOURCES = (
    ("imdb", _("IMDb"), Decimal(10), "/10"),
    ("rotten_tomatoes", _("Rotten Tomatoes"), Decimal(100), "%"),
    ("metacritic", _("Metacritic"), Decimal(100), ""),
    ("tmdb", _("TMDB"), Decimal(10), "/10"),
    ("letterboxd", _("Letterboxd"), Decimal(5), "/5"),
)
OMDB_SOURCE_KEYS = ("imdb", "rotten_tomatoes", "metacritic")
SOURCE_SCALE = {key: scale for key, _label, scale, _suffix in RATING_SOURCES}


def weight_field(key):
    """Return the User field that stores this source's weight."""
    return f"rating_weight_{key}"


def user_weights(user):
    """Return the user's weight for every public source."""
    return {key: getattr(user, weight_field(key)) for key, *_rest in RATING_SOURCES}


def weight_form_rows(user):
    """Return preference-form rows for the public rating weights."""
    return [
        {
            "name": weight_field(key),
            "label": label,
            "value": getattr(user, weight_field(key)),
        }
        for key, label, _scale, _suffix in RATING_SOURCES
    ]


def apply_weight_post(user, post):
    """Copy posted weights onto the user. Missing keys keep their current value."""
    for key, *_rest in RATING_SOURCES:
        raw = post.get(weight_field(key))
        if raw is None or raw == "":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        setattr(user, weight_field(key), min(WEIGHT_MAX, max(0, value)))


def normalize(value, scale):
    """Return ``(raw, 0-100)`` for a score on ``scale``. Missing stays missing."""
    if value is None:
        return None, None
    raw = Decimal(str(value)).quantize(ONE_PLACE)
    normalized = (raw * NORMALIZED_MAX / Decimal(scale)).quantize(ONE_PLACE)
    normalized = min(max(normalized, Decimal(0)), NORMALIZED_MAX)
    return raw, normalized


def weighted_average(normalized, weights):
    """Average normalized scores that exist and have a weight above zero."""
    total = Decimal(0)
    weight_sum = 0
    for key, value in normalized.items():
        weight = weights.get(key, 0)
        if value is None or weight <= 0:
            continue
        total += Decimal(value) * weight
        weight_sum += weight
    if weight_sum == 0:
        return None
    return (total / Decimal(weight_sum)).quantize(ONE_PLACE)


def format_public_value(value):
    """Format a score. Missing values are an en dash, never ``0``."""
    if value is None:
        return EMPTY_RATING
    text = format(Decimal(str(value)).quantize(ONE_PLACE), "f")
    if text.endswith(".0"):
        return text[:-2]
    return text


def format_source_value(value, suffix):
    """Format one source on its own scale."""
    if value is None:
        return EMPTY_RATING
    text = format_public_value(value)
    if suffix == "%":
        return f"{text}%"
    if suffix:
        return f"{text}{suffix}"
    return text


def annotate_public_rating(queryset, user):
    """Annotate ``public_rating`` using this user's weights. Missing scores are null."""
    weights = user_weights(user)
    numerator_terms = []
    weight_terms = []
    for key, *_rest in RATING_SOURCES:
        weight = weights[key]
        if weight <= 0:
            continue
        field = f"item__public_rating__{key}_normalized"
        present = Q(**{f"{field}__isnull": False})
        numerator_terms.append(
            Case(
                When(
                    present,
                    then=F(field) * Value(weight, output_field=NUMBER_FIELD),
                ),
                default=Value(0),
                output_field=NUMBER_FIELD,
            ),
        )
        weight_terms.append(
            Case(
                When(present, then=Value(weight)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )

    empty = Value(None, output_field=SCORE_FIELD)
    if not numerator_terms:
        return queryset.annotate(public_rating=empty)

    numerator = _add_expressions(numerator_terms)
    denominator = _add_expressions(weight_terms)
    divided = ExpressionWrapper(
        F("_public_rating_numerator") / F("_public_rating_weight"),
        output_field=SCORE_FIELD,
    )
    return queryset.annotate(
        _public_rating_numerator=numerator,
        _public_rating_weight=denominator,
    ).annotate(
        public_rating=Case(
            When(_public_rating_weight=0, then=empty),
            default=Round(divided, precision=1),
            output_field=SCORE_FIELD,
        ),
    )


def is_ratable(item):
    """Return whether this catalog item can store a public rating."""
    return (
        item.media_type in RATED_MEDIA_TYPES
        and item.season_number is None
        and item.episode_number is None
    )


def parse_letterboxd(raw):
    """Return a 0-5 Letterboxd score, or None when the field is cleared."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = Decimal(str(raw).strip()).quantize(ONE_PLACE)
    except InvalidOperation as error:
        message = "Invalid Letterboxd score"
        raise ValueError(message) from error
    if value < 0 or value > SOURCE_SCALE["letterboxd"]:
        message = "Letterboxd score must be from 0 to 5"
        raise ValueError(message) from None
    return value


def page_context(user, source, media_type, media_id):
    """Build the detail-page rating block and queue a refresh when it is stale."""
    if media_type not in RATED_MEDIA_TYPES:
        return {"show": False}

    item = _find_item(source, media_type, media_id)
    if item is not None:
        schedule_refresh(item)
    return build_display(
        item,
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
    )


def build_display(item, user, *, source, media_type, media_id):
    """Return template context for the public rating breakdown."""
    rating = ItemRating.objects.filter(item=item).first() if item is not None else None
    normalized = {}
    sources = []
    for key, label, _scale, suffix in RATING_SOURCES:
        raw = getattr(rating, f"{key}_value", None) if rating is not None else None
        normalized[key] = (
            getattr(rating, f"{key}_normalized", None) if rating is not None else None
        )
        sources.append(
            {
                "key": key,
                "label": label,
                "display": format_source_value(raw, suffix),
                "missing": raw is None,
            },
        )
    aggregate = weighted_average(normalized, user_weights(user)) if rating else None
    letterboxd_input = ""
    if rating is not None and rating.letterboxd_value is not None:
        letterboxd_input = format_public_value(rating.letterboxd_value)
    return {
        "show": True,
        "source": source,
        "media_type": media_type,
        "media_id": media_id,
        "aggregate_display": format_public_value(aggregate),
        "aggregate_missing": aggregate is None,
        "sources": sources,
        "letterboxd_input": letterboxd_input,
    }


def save_letterboxd(source, media_type, media_id, value):
    """Store a manual Letterboxd score on the shared item."""
    item = _find_item(source, media_type, media_id)
    if item is None:
        metadata = services.get_media_metadata(media_type, media_id, source)
        item = Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            title=metadata["title"],
            image=metadata["image"],
        )
    rating, _created = ItemRating.objects.get_or_create(item=item)
    raw, normalized = normalize(value, SOURCE_SCALE["letterboxd"])
    rating.letterboxd_value = raw
    rating.letterboxd_normalized = normalized
    rating.save(update_fields=["letterboxd_value", "letterboxd_normalized"])
    return item


def schedule_refresh(item):
    """Queue an external refresh when the stored ratings are missing or old."""
    if not is_ratable(item):
        return
    updated_at = (
        ItemRating.objects.filter(item=item)
        .values_list("ratings_updated_at", flat=True)
        .first()
    )
    if updated_at is not None and timezone.now() - updated_at < RATINGS_MAX_AGE:
        return

    from app.tasks import refresh_item_public_rating  # noqa: PLC0415

    refresh_item_public_rating.delay(item.pk)


def refresh_stale_ratings(*, batch_size=100):
    """Refresh ratable items whose external ratings are missing or older than 7 days.

    Returns ``(updated, deferred)``. Deferred items were skipped because the
    OMDb daily cap was reached.
    """
    updated = 0
    deferred = 0
    allow_omdb = True
    for item in _iter_stale_items(batch_size):
        try:
            result = refresh_item_rating(item, allow_omdb=allow_omdb)
        except omdb.OmdbBudgetError:
            allow_omdb = False
            deferred += 1
            continue
        if result == "deferred":
            deferred += 1
        elif result == "updated":
            updated += 1
    return updated, deferred


def refresh_item_rating(item, *, allow_omdb=True):
    """Fetch TMDB and OMDb scores for one item. Letterboxd is left unchanged."""
    if not is_ratable(item):
        return "skipped"

    metadata = _metadata_for(item)
    if metadata is False:
        return "failed"

    imdb_id = _imdb_id(metadata)
    omdb_scores = None
    if imdb_id and settings.OMDB_API_KEY:
        if not allow_omdb and not _omdb_cached(imdb_id):
            return "deferred"
        try:
            omdb_scores = omdb.fetch_ratings(imdb_id)
        except omdb.OmdbRequestError:
            logger.exception("OMDb failed for item %s", item.pk)
            return "failed"

    rating, _created = ItemRating.objects.get_or_create(item=item)
    _apply_tmdb(rating, metadata)
    _apply_omdb(rating, omdb_scores)
    rating.ratings_updated_at = timezone.now()
    rating.save(
        update_fields=[
            "imdb_value",
            "imdb_normalized",
            "rotten_tomatoes_value",
            "rotten_tomatoes_normalized",
            "metacritic_value",
            "metacritic_normalized",
            "tmdb_value",
            "tmdb_normalized",
            "ratings_updated_at",
        ],
    )
    return "updated"


def _add_expressions(terms):
    total = terms[0]
    for term in terms[1:]:
        total += term
    return total


def _find_item(source, media_type, media_id):
    return Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=None,
        episode_number=None,
    ).first()


def _metadata_for(item):
    """Return TMDB metadata, None when this source has none, or False on failure."""
    if item.source != Sources.TMDB.value or item.media_type not in (
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
    ):
        return None
    try:
        return services.get_media_metadata(
            item.media_type,
            item.media_id,
            item.source,
        )
    except (requests.RequestException, services.ProviderAPIError):
        logger.exception("Could not load metadata for item %s", item.pk)
        return False


def _imdb_id(metadata):
    if not metadata:
        return None
    link = (metadata.get("external_links") or {}).get("IMDb") or ""
    match = IMDB_ID_RE.search(link)
    if match is None:
        return None
    return match.group(1)


def _tmdb_raw(metadata):
    if not metadata:
        return None
    score = metadata.get("score")
    votes = metadata.get("score_count")
    if score is None or not votes:
        return None
    return Decimal(str(score))


def _apply_tmdb(rating, metadata):
    raw, normalized = normalize(_tmdb_raw(metadata), SOURCE_SCALE["tmdb"])
    rating.tmdb_value = raw
    rating.tmdb_normalized = normalized


def _apply_omdb(rating, scores):
    """Write OMDb scores. ``None`` clears them, which is what a missing key does."""
    scores = scores or {}
    for key in OMDB_SOURCE_KEYS:
        raw, normalized = normalize(scores.get(key), SOURCE_SCALE[key])
        setattr(rating, f"{key}_value", raw)
        setattr(rating, f"{key}_normalized", normalized)


def _omdb_cached(imdb_id):
    """Return whether this IMDb id already has a cached OMDb response."""
    return cache.get(f"omdb_ratings_{imdb_id}") is not None


def _iter_stale_items(batch_size):
    """Yield stale items in primary-key order, one batch at a time."""
    cutoff = timezone.now() - RATINGS_MAX_AGE
    last_pk = 0
    stale = (
        Q(public_rating__isnull=True)
        | Q(
            public_rating__ratings_updated_at__isnull=True,
        )
        | Q(public_rating__ratings_updated_at__lt=cutoff)
    )
    base = Item.objects.filter(
        media_type__in=RATED_MEDIA_TYPES,
        season_number__isnull=True,
        episode_number__isnull=True,
    )
    while True:
        batch = list(
            base.filter(stale, pk__gt=last_pk).order_by("pk")[:batch_size],
        )
        if not batch:
            return
        for item in batch:
            last_pk = item.pk
            yield item
