import math
import unicodedata

from django.conf import settings

from app.models import MediaTypes

RANKING_KEYS = {
    "author_name",
    "edition_count",
    "first_publish_year",
    "first_release_date",
    "game_type",
    "num_scoring_users",
    "popularity",
    "rating",
    "ratings_average",
    "ratings_count",
    "total_rating",
    "total_rating_count",
    "vote_count",
}


def normalize_search_text(value):
    """Normalize search text for forgiving ranking comparisons."""
    chars = []
    for char in unicodedata.normalize("NFKD", str(value or "")):
        if unicodedata.combining(char):
            continue
        category = unicodedata.category(char)
        chars.append(char.casefold() if category[0] in {"L", "N"} else " ")
    return " ".join("".join(chars).split())


def rank_results(query, results, media_type=None):
    """Return results ordered by relevance, popularity, and metadata quality."""
    ranked = [
        (_score(query, result, media_type), -index, result)
        for index, result in enumerate(results)
    ]
    ranked.sort(reverse=True)
    return [_without_ranking_fields(result) for _, _, result in ranked]


def _score(query, result, media_type):
    normalized_query = normalize_search_text(query)
    normalized_title = normalize_search_text(result.get("title") or result.get("name"))
    query_tokens = normalized_query.split()
    title_tokens = normalized_title.split()

    score = _relevance_score(normalized_query, normalized_title, query_tokens, title_tokens)
    popularity = _popularity_score(result)
    score += popularity
    score += _metadata_score(result)
    score += _media_type_score(result, media_type)
    score -= _junk_penalty(normalized_query, normalized_title, result, popularity)
    return score


def _relevance_score(normalized_query, normalized_title, query_tokens, title_tokens):
    if not normalized_query or not normalized_title:
        return 0
    if normalized_title == normalized_query:
        return 100
    if normalized_title.startswith(f"{normalized_query} "):
        return 76
    if f" {normalized_query} " in f" {normalized_title} ":
        return 66
    if _tokens_in_order(query_tokens, title_tokens):
        return 52
    if query_tokens and all(token in title_tokens for token in query_tokens):
        return 42
    return sum(8 for token in query_tokens if token in title_tokens)


def _tokens_in_order(query_tokens, title_tokens):
    if not query_tokens:
        return False
    pos = 0
    for token in title_tokens:
        if token == query_tokens[pos]:
            pos += 1
            if pos == len(query_tokens):
                return True
    return False


def _popularity_score(result):
    count = _first_number(
        result,
        (
            "total_rating_count",
            "ratings_count",
            "score_count",
            "num_scoring_users",
            "vote_count",
            "edition_count",
        ),
    )
    popularity = _first_number(result, ("popularity",))
    count_score = min(45, math.log10(count + 1) * 11) if count else 0
    popularity_score = min(25, math.log10(popularity + 1) * 8) if popularity else 0
    return count_score + popularity_score


def _metadata_score(result):
    score = 0
    if _has_real_image(result):
        score += 10
    if result.get("release_date") or result.get("first_release_date") or result.get("first_publish_year"):
        score += 6
    if result.get("author_name"):
        score += 4
    if _first_number(result, ("rating", "ratings_average", "total_rating")):
        score += 3
    return score


def _media_type_score(result, media_type):
    if media_type != MediaTypes.GAME.value:
        return 0
    game_type = result.get("game_type")
    if game_type in {0, 8, 9, 10}:
        return 10
    if game_type == 4:
        return 3
    if game_type in {1, 2, 3, 5, 6, 7}:
        return -12
    return 0


def _junk_penalty(normalized_query, normalized_title, result, popularity):
    if normalized_title != normalized_query:
        return 0
    if not normalized_query or len(normalized_query.split()) > 3:
        return 0
    has_quality = (
        _has_real_image(result)
        or result.get("release_date")
        or result.get("first_publish_year")
        or result.get("author_name")
        or popularity
    )
    return 60 if not has_quality else 0


def _first_number(result, keys):
    for key in keys:
        value = result.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0


def _has_real_image(result):
    image = result.get("image") or result.get("poster_path") or result.get("cover_i")
    return bool(image and image != settings.IMG_NONE)


def _without_ranking_fields(result):
    return {key: value for key, value in result.items() if key not in RANKING_KEYS}
