import contextlib
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from django.templatetags.static import static
from django.utils.text import slugify

from app import helpers
from app.models import Item, MediaTypes, Sources
from app.services import game_lengths as game_length_services
from app.services import trakt_popularity as trakt_popularity_service
from app.templatetags import app_tags


def _format_game_length_minutes(minutes):
    """Return a display string for stored game-length minutes."""
    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        minutes = 0
    return helpers.minutes_to_hhmm(minutes) if minutes > 0 else "--"


def _format_game_length_seconds(seconds):
    """Return a display string for stored game-length seconds."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    return _format_game_length_minutes(round(seconds / 60)) if seconds > 0 else "--"


def _build_game_length_card(label, value, count):
    """Return display metadata for a summary game-length card."""
    card_styles = {
        "Main Story": {
            "icon_template": "app/icons/book-open.svg",
            "icon_background": "rgba(96, 165, 250, 0.2)",
            "icon_color": "#60a5fa",
        },
        "Main + Extras": {
            "icon_template": "app/icons/list.svg",
            "icon_background": "rgba(52, 211, 153, 0.2)",
            "icon_color": "#34d399",
        },
        "Completionist": {
            "icon_template": "app/icons/ribbon.svg",
            "icon_background": "rgba(245, 158, 11, 0.2)",
            "icon_color": "#f59e0b",
        },
        "All PlayStyles": {
            "icon_template": "app/icons/four-square.svg",
            "icon_background": "rgba(167, 139, 250, 0.2)",
            "icon_color": "#a78bfa",
        },
        "Hastily": {
            "icon_template": "app/icons/clock-reversing.svg",
            "icon_background": "rgba(245, 158, 11, 0.2)",
            "icon_color": "#f59e0b",
        },
        "Normally": {
            "icon_template": "app/icons/clock.svg",
            "icon_background": "rgba(96, 165, 250, 0.2)",
            "icon_color": "#60a5fa",
        },
        "Completely": {
            "icon_template": "app/icons/circle-check.svg",
            "icon_background": "rgba(52, 211, 153, 0.2)",
            "icon_color": "#34d399",
        },
    }
    style = card_styles.get(
        label,
        {
            "icon_template": "app/icons/clock.svg",
            "icon_background": "rgba(129, 140, 248, 0.2)",
            "icon_color": "#818cf8",
        },
    )
    return {
        "label": label,
        "value": value,
        "count": count or 0,
        **style,
    }


def _build_game_lengths_context(detail_item):
    """Return template-ready game-length metadata for a stored item."""
    if not detail_item:
        return None

    payload = detail_item.provider_game_lengths or {}
    external_ids = detail_item.provider_external_ids or {}
    active_source = detail_item.provider_game_lengths_source or payload.get(
        "active_source"
    )
    if active_source == "hltb":
        hltb_payload = payload.get("hltb") or {}
        cards = []
        card_specs = [
            (
                "Main Story",
                hltb_payload.get("summary", {}).get("main_minutes"),
                hltb_payload.get("counts", {}).get("main"),
            ),
            (
                "Main + Extras",
                hltb_payload.get("summary", {}).get("main_plus_minutes"),
                hltb_payload.get("counts", {}).get("main_plus"),
            ),
            (
                "Completionist",
                hltb_payload.get("summary", {}).get("completionist_minutes"),
                hltb_payload.get("counts", {}).get("completionist"),
            ),
            (
                "All PlayStyles",
                hltb_payload.get("summary", {}).get("all_styles_minutes"),
                hltb_payload.get("counts", {}).get("all_styles"),
            ),
        ]
        for label, minutes, count in card_specs:
            if (minutes or 0) <= 0:
                continue
            cards.append(
                _build_game_length_card(
                    label, _format_game_length_minutes(minutes), count
                )
            )

        single_player_rows = [
            {
                "label": row.get("label") or "",
                "count": row.get("count") or 0,
                "average": _format_game_length_minutes(row.get("average_minutes")),
                "median": _format_game_length_minutes(row.get("median_minutes")),
                "rushed": _format_game_length_minutes(row.get("rushed_minutes")),
                "leisure": _format_game_length_minutes(row.get("leisure_minutes")),
            }
            for row in hltb_payload.get("single_player_table") or []
        ]

        platform_rows = [
            {
                "platform": row.get("platform") or "",
                "count": row.get("count") or 0,
                "main": _format_game_length_minutes(row.get("main_minutes")),
                "main_plus": _format_game_length_minutes(row.get("main_plus_minutes")),
                "completionist": _format_game_length_minutes(
                    row.get("completionist_minutes")
                ),
                "fastest": _format_game_length_minutes(row.get("fastest_minutes")),
                "slowest": _format_game_length_minutes(row.get("slowest_minutes")),
            }
            for row in hltb_payload.get("platform_table") or []
        ]

        return {
            "available": bool(cards),
            "source": "hltb",
            "source_label": "How Long to Beat",
            "source_url": hltb_payload.get("url")
            or (
                f"https://howlongtobeat.com/game/{external_ids['hltb_game_id']}"
                if external_ids.get("hltb_game_id")
                else None
            ),
            "match": detail_item.provider_game_lengths_match,
            "cards": cards,
            "submission_count": (hltb_payload.get("counts") or {}).get("all_styles")
            or 0,
            "single_player_rows": single_player_rows,
            "platform_rows": platform_rows,
        }

    if active_source == "igdb":
        igdb_payload = payload.get("igdb") or {}
        summary = igdb_payload.get("summary") or {}
        cards = []
        for label, key in (
            ("Hastily", "hastily_seconds"),
            ("Normally", "normally_seconds"),
            ("Completely", "completely_seconds"),
        ):
            value = summary.get(key) or 0
            if value <= 0:
                continue
            cards.append(
                _build_game_length_card(
                    label,
                    _format_game_length_seconds(value),
                    summary.get("count") or 0,
                ),
            )

        return {
            "available": bool(cards),
            "source": "igdb",
            "source_label": "Internet Games Database",
            "source_url": None,
            "match": detail_item.provider_game_lengths_match,
            "cards": cards,
            "submission_count": summary.get("count") or 0,
            "single_player_rows": [],
            "platform_rows": [],
        }

    return None


_SERIES_GRAPH_LEGEND = [
    {"label": "Awesome", "color": "#1d6e3e"},
    {"label": "Great", "color": "#2d9e5f"},
    {"label": "Good", "color": "#c9970a"},
    {"label": "Regular", "color": "#c47c1a"},
    {"label": "Bad", "color": "#c0392b"},
    {"label": "Garbage", "color": "#7b2fbe"},
]

# Score thresholds for the episode-score color scale (matches
# _SERIES_GRAPH_LEGEND labels above).
SCORE_THRESHOLD_AWESOME = 9.0
SCORE_THRESHOLD_GREAT = 8.0
SCORE_THRESHOLD_GOOD = 7.0
SCORE_THRESHOLD_REGULAR = 6.0
SCORE_THRESHOLD_BAD = 5.0


def _score_to_color(score):
    """Return background hex color for an episode score value."""
    if score is None:
        return None
    if score >= SCORE_THRESHOLD_AWESOME:
        return "#1d6e3e"
    if score >= SCORE_THRESHOLD_GREAT:
        return "#2d9e5f"
    if score >= SCORE_THRESHOLD_GOOD:
        return "#c9970a"
    if score >= SCORE_THRESHOLD_REGULAR:
        return "#c47c1a"
    if score >= SCORE_THRESHOLD_BAD:
        return "#c0392b"
    return "#7b2fbe"


def _build_series_graph_from_raw(season_number, raw_episodes, source):
    """Build series graph data from raw in-memory provider episode dicts.

    Used during the shell render where raw TMDB/TVDB episode data is already
    in season_metadata["episodes"] but no DB Items have been written yet.
    raw_episodes is the list from TMDB/TVDB before process_episodes() runs.
    """
    ep_scores = []
    for ep in raw_episodes or []:
        ep_num = ep.get("episode_number")
        if ep_num is None:
            continue
        if source == Sources.TMDB.value:
            vote_avg = ep.get("vote_average")
            vote_cnt = ep.get("vote_count", 0)
            score = round(float(vote_avg), 1) if vote_avg else None
            votes = int(vote_cnt) if vote_cnt else 0
        elif source == Sources.TVDB.value:
            score = ep.get("score")
            votes = int(ep.get("score_count") or 0)
        else:
            continue
        if score:
            ep_scores.append({"ep": ep_num, "score": score, "votes": votes})

    if not ep_scores:
        return None

    ep_scores.sort(key=lambda e: e["ep"])
    season_eps = {e["ep"]: e for e in ep_scores}
    all_ep_numbers = sorted(season_eps)
    season_label = f"S{season_number}"

    episode_rows = [
        {
            "ep": en,
            "cells": [
                {
                    "score": season_eps[en]["score"],
                    "votes": season_eps[en]["votes"],
                    "color": _score_to_color(season_eps[en]["score"]),
                }
            ],
        }
        for en in all_ep_numbers
    ]

    return {
        "seasons": [{"label": season_label, "episodes": ep_scores}],
        "episode_rows": episode_rows,
        "legend": _SERIES_GRAPH_LEGEND,
        "row_label": "E",
        "title": "Episode Ratings",
    }


def _rating_fields(*, use_trakt=False, use_imdb=False):
    """Return the (rating_field, count_field) Item field names for a rating source."""
    if use_trakt:
        return "trakt_rating", "trakt_rating_count"
    if use_imdb:
        return "imdb_rating", "imdb_rating_count"
    return "provider_rating", "provider_rating_count"


def _build_series_graph_data(
    source,
    media_id,
    season_number=None,
    *,
    use_trakt=False,
    use_imdb=False,
    include_unrated=False,
):
    """Query stored episode ratings and return series graph data dict or None.

    On season pages pass season_number to get a single-season grid.
    On show pages omit it to get all seasons.

    Returns a dict with:
      seasons: list of {label, episodes}
      episode_rows: list of {ep, cells} where cells align with seasons columns
      legend: list of {label, color}
    """
    from app.models import Item, MediaTypes

    filters = {
        "media_id": str(media_id),
        "source": source,
        "media_type": MediaTypes.EPISODE.value,
    }
    if season_number is not None:
        filters["season_number"] = season_number
    else:
        filters["season_number__gt"] = 0

    rating_field, count_field = _rating_fields(use_trakt=use_trakt, use_imdb=use_imdb)

    episode_query = Item.objects.filter(**filters)
    if not include_unrated:
        episode_query = episode_query.exclude(**{f"{rating_field}__isnull": True})

    episodes = list(
        episode_query.order_by("season_number", "episode_number").values(
            "season_number", "episode_number", rating_field, count_field
        )
    )

    if not episodes:
        return None

    # Build per-season episode lookup: {season_num: {ep_num: {score, votes}}}
    seasons_map: dict = {}
    for ep in episodes:
        sn = ep["season_number"]
        en = ep["episode_number"]
        if sn is None or en is None:
            continue
        if sn not in seasons_map:
            seasons_map[sn] = {}
        seasons_map[sn][en] = {
            "score": ep[rating_field],
            "votes": ep[count_field] or 0,
        }

    sorted_seasons = sorted(seasons_map.keys())
    all_ep_numbers = sorted({en for sn in sorted_seasons for en in seasons_map[sn]})

    seasons = [
        {
            "label": f"S{sn}",
            "episodes": [
                {
                    "ep": en,
                    "score": seasons_map[sn].get(en, {}).get("score"),
                    "votes": seasons_map[sn].get(en, {}).get("votes", 0),
                }
                for en in all_ep_numbers
                if en in seasons_map[sn]
            ],
        }
        for sn in sorted_seasons
    ]

    # Build row-major structure for the template grid
    episode_rows = [
        {
            "ep": en,
            "cells": [
                {
                    "score": seasons_map[sn].get(en, {}).get("score"),
                    "votes": seasons_map[sn].get(en, {}).get("votes", 0),
                    "color": _score_to_color(seasons_map[sn].get(en, {}).get("score")),
                }
                for sn in sorted_seasons
            ],
        }
        for en in all_ep_numbers
    ]

    return {
        "seasons": seasons,
        "episode_rows": episode_rows,
        "legend": _SERIES_GRAPH_LEGEND,
        "row_label": "E",
        "title": "Episode Ratings",
    }


def _build_episode_graph_from_season_cache(source, media_id, related_seasons):
    """Build a full SxE episode graph by reading per-season TMDB cache entries.

    The TMDB season cache (keyed per season_number) already holds the raw
    episode list with vote_average/vote_count — no API call needed.
    Returns None if none of the seasons are cached yet.
    """
    from django.core.cache import cache as django_cache

    from app.models import Sources

    if source != Sources.TMDB.value:
        return None

    from app.providers.tmdb import (
        _season_cache_key,
    )

    season_numbers = sorted(
        {
            s.get("season_number")
            for s in (related_seasons or [])
            if s.get("season_number") is not None and s.get("season_number") > 0
        }
    )
    if not season_numbers:
        return None

    seasons_map: dict = {}
    for sn in season_numbers:
        season_data = django_cache.get(_season_cache_key(media_id, sn))
        if not isinstance(season_data, dict):
            continue
        for ep in season_data.get("episodes") or []:
            ep_num = ep.get("episode_number")
            vote_avg = ep.get("vote_average")
            if ep_num is None or not vote_avg:
                continue
            if sn not in seasons_map:
                seasons_map[sn] = {}
            seasons_map[sn][ep_num] = {
                "score": round(float(vote_avg), 1),
                "votes": int(ep.get("vote_count") or 0),
            }

    if not seasons_map:
        return None

    sorted_seasons = sorted(seasons_map.keys())
    all_ep_numbers = sorted({en for sn in sorted_seasons for en in seasons_map[sn]})
    if not all_ep_numbers:
        return None

    seasons = [
        {
            "label": f"S{sn}",
            "episodes": [
                {
                    "ep": en,
                    "score": seasons_map[sn].get(en, {}).get("score"),
                    "votes": seasons_map[sn].get(en, {}).get("votes", 0),
                }
                for en in all_ep_numbers
                if en in seasons_map[sn]
            ],
        }
        for sn in sorted_seasons
    ]

    episode_rows = [
        {
            "ep": en,
            "cells": [
                {
                    "score": seasons_map[sn].get(en, {}).get("score"),
                    "votes": seasons_map[sn].get(en, {}).get("votes", 0),
                    "color": _score_to_color(seasons_map[sn].get(en, {}).get("score")),
                }
                for sn in sorted_seasons
            ],
        }
        for en in all_ep_numbers
    ]

    return {
        "seasons": seasons,
        "episode_rows": episode_rows,
        "legend": _SERIES_GRAPH_LEGEND,
        "row_label": "E",
        "title": "Episode Ratings",
    }


def _build_season_scores_graph(related_seasons, source):
    """Build a season-summary graph from related.seasons data on a TV show page.

    Produces the same structure as _build_series_graph_from_raw() but rows are
    seasons (labelled S1, S2...) rather than episodes.  Used when per-episode
    data is not yet available in the shell render.
    """
    if not related_seasons:
        return None

    cells = []
    for season in sorted(related_seasons, key=lambda s: s.get("season_number") or 0):
        sn = season.get("season_number")
        score = season.get("score")
        if sn is None or sn <= 0 or not score:
            continue
        cells.append(
            {
                "ep": sn,
                "score": score,
                "votes": season.get("score_count") or 0,
                "color": _score_to_color(score),
            }
        )

    if not cells:
        return None

    return {
        "seasons": [{"label": "Rating", "episodes": cells}],
        "episode_rows": [{"ep": cell["ep"], "cells": [cell]} for cell in cells],
        "legend": _SERIES_GRAPH_LEGEND,
        "row_label": "S",
        "title": "Season Ratings",
    }


def _build_stored_season_scores_graph(
    source, media_id, *, use_trakt=False, use_imdb=False
):
    """Build a season-summary graph from stored season rating fields."""
    from app.models import Item, MediaTypes

    rating_field, count_field = _rating_fields(use_trakt=use_trakt, use_imdb=use_imdb)

    seasons = list(
        Item.objects.filter(
            media_id=str(media_id),
            source=source,
            media_type=MediaTypes.SEASON.value,
            season_number__gt=0,
        )
        .exclude(**{f"{rating_field}__isnull": True})
        .order_by("season_number")
        .values("season_number", rating_field, count_field)
    )

    cells = []
    for season in seasons:
        season_number = season["season_number"]
        score = season[rating_field]
        if season_number is None or score is None:
            continue
        cells.append(
            {
                "ep": season_number,
                "score": round(float(score), 1),
                "votes": season[count_field] or 0,
                "color": _score_to_color(score),
            }
        )

    if not cells:
        return None

    return {
        "seasons": [{"label": "Rating", "episodes": cells}],
        "episode_rows": [{"ep": cell["ep"], "cells": [cell]} for cell in cells],
        "legend": _SERIES_GRAPH_LEGEND,
        "row_label": "S",
        "title": "Season Ratings",
    }


def _build_trakt_popularity_context(detail_item, route_media_type):
    """Return template-ready stored Trakt popularity metadata for a detail item."""
    if (
        not detail_item
        or route_media_type
        not in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
            MediaTypes.SEASON.value,
        )
        or not trakt_popularity_service.trakt_provider.is_configured()
        or detail_item.trakt_rating_count is None
    ):
        return None

    rating = detail_item.trakt_rating
    if rating is not None:
        with contextlib.suppress(InvalidOperation, TypeError, ValueError):
            rating = float(
                Decimal(str(rating)).quantize(
                    Decimal("0.1"),
                    rounding=ROUND_DOWN,
                ),
            )

    return {
        "rating": rating,
        "rating_count": detail_item.trakt_rating_count,
        "rank": detail_item.trakt_popularity_rank,
        "score": detail_item.trakt_popularity_score,
        "fetched_at": detail_item.trakt_popularity_fetched_at,
    }


def _build_imdb_rating_context(detail_item, route_media_type):
    """Return template-ready stored IMDB rating metadata for a detail item."""
    if (
        not detail_item
        or route_media_type
        not in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
            MediaTypes.EPISODE.value,
            MediaTypes.SEASON.value,
        )
        or detail_item.imdb_rating_count is None
    ):
        return None

    rating = detail_item.imdb_rating
    if rating is not None:
        with contextlib.suppress(InvalidOperation, TypeError, ValueError):
            rating = float(
                Decimal(str(rating)).quantize(
                    Decimal("0.1"),
                    rounding=ROUND_DOWN,
                ),
            )

    return {
        "rating": rating,
        "rating_count": detail_item.imdb_rating_count,
    }


def _build_mal_rating_context(detail_item, route_media_type):
    """Return template-ready stored MyAnimeList rating metadata for anime."""
    is_anime = route_media_type == MediaTypes.ANIME.value or (
        route_media_type == MediaTypes.TV.value
        and detail_item
        and detail_item.media_type == MediaTypes.TV.value
        and detail_item.library_media_type == MediaTypes.ANIME.value
    )
    if (
        not detail_item
        or not is_anime
        or detail_item.mal_rating is None
        or detail_item.mal_rating_count is None
        or detail_item.mal_rating_count <= 0
    ):
        return None

    rating = detail_item.mal_rating
    if rating is not None:
        with contextlib.suppress(InvalidOperation, TypeError, ValueError):
            rating = float(
                Decimal(str(rating)).quantize(
                    Decimal("0.1"),
                    rounding=ROUND_DOWN,
                ),
            )

    return {
        "rating": rating,
        "rating_count": detail_item.mal_rating_count,
    }


def _build_opencritic_context(detail_item, route_media_type):
    """Return template-ready stored OpenCritic scores for a game."""
    if (
        not detail_item
        or route_media_type != MediaTypes.GAME.value
        or detail_item.opencritic_score is None
    ):
        return None
    return {
        "score": round(detail_item.opencritic_score),
        "percent_recommended": (
            round(detail_item.opencritic_percent_recommended)
            if detail_item.opencritic_percent_recommended is not None
            else None
        ),
        "tier": detail_item.opencritic_tier,
        "url": detail_item.opencritic_url,
    }


def _apply_cached_hltb_link(media_metadata, detail_item):
    """Prefer a stored direct HLTB link when one has already been resolved."""
    if not detail_item or not isinstance(media_metadata, dict):
        return
    if detail_item.media_type != MediaTypes.GAME.value:
        return

    external_links = media_metadata.setdefault("external_links", {})
    if not isinstance(external_links, dict):
        external_links = {}
        media_metadata["external_links"] = external_links

    hltb_game_id = (detail_item.provider_external_ids or {}).get("hltb_game_id")
    if hltb_game_id:
        external_links["HowLongToBeat"] = (
            f"https://howlongtobeat.com/game/{hltb_game_id}"
        )
    elif "HowLongToBeat" not in external_links:
        search_url = game_length_services.get_hltb_search_url(
            media_metadata.get("title")
        )
        if search_url:
            external_links["HowLongToBeat"] = search_url


# Brand tints live in the chip border and fill; the label itself must stay
# readable, so it tracks the theme text token instead of a fixed light tint.
_DETAIL_LINK_ACCENT_CLASSES = "text-[var(--color-text)]"

_DETAIL_LINK_BRANDS = {
    Sources.TMDB.value: {
        "logo_src": static("img/tmdb-logo.png"),
        "chip_classes": "border-cyan-400/18 bg-cyan-500/[0.07]",
        "badge_classes": "border-cyan-400/28 bg-cyan-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "TMDB",
    },
    Sources.TVDB.value: {
        "logo_src": static("img/tvdb-logo.png"),
        "chip_classes": "border-teal-400/18 bg-teal-500/[0.07]",
        "badge_classes": "border-teal-400/28 bg-teal-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "TVDB",
    },
    Sources.MAL.value: {
        "logo_src": static("img/myanimelist-logo.svg"),
        "chip_classes": "border-indigo-400/18 bg-indigo-500/[0.07]",
        "badge_classes": "border-indigo-400/28 bg-indigo-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "MAL",
    },
    Sources.MANGAUPDATES.value: {
        "chip_classes": "border-fuchsia-400/18 bg-fuchsia-500/[0.07]",
        "badge_classes": "border-fuchsia-400/28 bg-fuchsia-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "MU",
    },
    Sources.MANGABAKA.value: {
        "logo_src": static("img/mangabaka-logo.png"),
        "chip_classes": "border-violet-400/18 bg-violet-500/[0.07]",
        "badge_classes": "border-violet-400/28 bg-violet-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "MB",
    },
    Sources.IGDB.value: {
        "logo_src": static("img/igdb-logo.png"),
        "chip_classes": "border-orange-400/18 bg-orange-500/[0.07]",
        "badge_classes": "border-orange-400/28 bg-orange-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "IGDB",
    },
    Sources.OPENLIBRARY.value: {
        "chip_classes": "border-sky-400/18 bg-sky-500/[0.07]",
        "badge_classes": "border-sky-400/28 bg-sky-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "OL",
    },
    Sources.HARDCOVER.value: {
        "logo_src": static("img/hardcover-logo.png"),
        "chip_classes": "border-amber-400/18 bg-amber-500/[0.07]",
        "badge_classes": "border-amber-400/28 bg-amber-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "HC",
    },
    Sources.COMICVINE.value: {
        "chip_classes": "border-lime-400/18 bg-lime-500/[0.07]",
        "badge_classes": "border-lime-400/28 bg-lime-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "CV",
    },
    Sources.BGG.value: {
        "chip_classes": "border-stone-400/18 bg-stone-500/[0.07]",
        "badge_classes": "border-stone-400/28 bg-stone-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "BGG",
    },
    Sources.MUSICBRAINZ.value: {
        "chip_classes": "border-rose-400/18 bg-rose-500/[0.07]",
        "badge_classes": "border-rose-400/28 bg-rose-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "MB",
    },
    Sources.POCKETCASTS.value: {
        "chip_classes": "border-orange-400/18 bg-orange-500/[0.07]",
        "badge_classes": "border-orange-400/28 bg-orange-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "PC",
    },
    Sources.GPODDER.value: {
        "chip_classes": "border-sky-400/18 bg-sky-500/[0.07]",
        "badge_classes": "border-sky-400/28 bg-sky-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "GP",
    },
    Sources.AUDIOBOOKSHELF.value: {
        "chip_classes": "border-teal-400/18 bg-teal-500/[0.07]",
        "badge_classes": "border-teal-400/28 bg-teal-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "ABS",
    },
    Sources.MANUAL.value: {
        "chip_classes": "border-slate-400/18 bg-slate-500/[0.07]",
        "badge_classes": "border-slate-400/28 bg-slate-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "MAN",
    },
    "anilist": {
        "logo_src": static("img/anilist-logo.svg"),
        "chip_classes": "border-sky-400/18 bg-sky-500/[0.07]",
        "badge_classes": "border-sky-400/28 bg-sky-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "AL",
    },
    "kitsu": {
        "logo_src": static("img/kitsu-logo.png"),
        "chip_classes": "border-orange-400/18 bg-orange-500/[0.07]",
        "badge_classes": "border-orange-400/28 bg-orange-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "KT",
    },
    "simkl": {
        "logo_src": static("img/simkl-logo.png"),
        "chip_classes": "border-violet-400/18 bg-violet-500/[0.07]",
        "badge_classes": "border-violet-400/28 bg-violet-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "SK",
    },
    "steam": {
        "logo_src": static("img/steam-logo.ico"),
        "chip_classes": "border-slate-400/18 bg-slate-500/[0.07]",
        "badge_classes": "border-slate-400/28 bg-slate-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "STM",
    },
    "xbox": {
        "logo_src": static("img/xbox-logo.svg"),
        "chip_classes": "border-green-400/18 bg-green-500/[0.07]",
        "badge_classes": "border-green-400/28 bg-green-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "XBX",
    },
    "plex": {
        "logo_src": static("img/plex-logo.svg"),
        "chip_classes": "border-amber-400/18 bg-amber-500/[0.07]",
        "badge_classes": "border-amber-400/28 bg-amber-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "PLX",
    },
    "lastfm": {
        "logo_src": static("img/lastfm-logo.png"),
        "chip_classes": "border-rose-400/18 bg-rose-500/[0.07]",
        "badge_classes": "border-rose-400/28 bg-rose-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "LFM",
    },
    "soundcloud": {
        "chip_classes": "border-orange-400/18 bg-orange-500/[0.07]",
        "badge_classes": "border-orange-400/28 bg-orange-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "SC",
    },
    "spotify": {
        "chip_classes": "border-green-400/18 bg-green-500/[0.07]",
        "badge_classes": "border-green-400/28 bg-green-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "SP",
    },
    "imdb": {
        "logo_src": static("img/imdb-logo.png"),
        "chip_classes": "border-amber-400/18 bg-amber-500/[0.07]",
        "badge_classes": "border-amber-400/28 bg-amber-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "IMDb",
    },
    "trakt": {
        "logo_src": static("img/trakt-logo.svg"),
        "chip_classes": "border-rose-400/18 bg-rose-500/[0.07]",
        "badge_classes": "border-rose-400/28 bg-rose-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "Trakt",
    },
    "wikidata": {
        "logo_src": static("img/wikidata-logo.png"),
        "chip_classes": "border-sky-400/18 bg-sky-500/[0.07]",
        "badge_classes": "border-sky-400/28 bg-sky-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "WD",
    },
    "letterboxd": {
        "logo_src": static("img/letterboxd-logo.png"),
        "chip_classes": "border-emerald-400/18 bg-emerald-500/[0.07]",
        "badge_classes": "border-emerald-400/28 bg-emerald-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "LB",
    },
    "youtube": {
        "chip_classes": "border-red-400/18 bg-red-500/[0.07]",
        "badge_classes": "border-red-400/28 bg-red-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "YT",
    },
    "howlongtobeat": {
        "logo_src": static("img/hltb-logo.png"),
        "chip_classes": "border-amber-400/18 bg-amber-500/[0.07]",
        "badge_classes": "border-amber-400/28 bg-amber-500/14",
        "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
        "fallback_text": "HLTB",
    },
}

_DEFAULT_DETAIL_LINK_BRAND = {
    "chip_classes": "border-slate-400/18 bg-slate-500/[0.07]",
    "badge_classes": "border-slate-400/28 bg-slate-500/14",
    "accent_classes": _DETAIL_LINK_ACCENT_CLASSES,
    "fallback_text": "LINK",
}


def _normalize_detail_link_brand_key(value):
    """Return a normalized lookup key for link-provider branding."""
    return slugify(str(value or "")).replace("-", "")


def _build_detail_link_entry(label, url, brand_key):
    """Return a template-ready chip payload for a media detail link."""
    if not url:
        return None

    brand = _DETAIL_LINK_BRANDS.get(
        _normalize_detail_link_brand_key(brand_key),
        _DEFAULT_DETAIL_LINK_BRAND,
    )
    fallback_text = (
        brand.get("fallback_text")
        or slugify(label).replace("-", "")[:4].upper()
        or "LINK"
    )
    return {
        "label": label,
        "url": url,
        "brand": _normalize_detail_link_brand_key(brand_key),
        "chip_classes": brand["chip_classes"],
        "badge_classes": brand["badge_classes"],
        "accent_classes": brand["accent_classes"],
        "logo_src": brand.get("logo_src"),
        "fallback_text": fallback_text,
    }


def _build_detail_link_sections(
    media_metadata, media_type, identity_provider, display_provider, item=None
):
    """Return grouped source and external link chips for the media detail action row."""
    if not isinstance(media_metadata, dict):
        return []

    tracking_source_entries = []
    metadata_source_entries = []
    external_entries = []
    seen_urls = set()

    def append_entry(collection, label, url, brand_key):
        if not url or url in seen_urls:
            return
        entry = _build_detail_link_entry(label, url, brand_key)
        if entry is None:
            return
        seen_urls.add(url)
        collection.append(entry)

    primary_source_url = media_metadata.get(
        "tracking_source_url"
    ) or media_metadata.get("source_url")
    if primary_source_url:
        append_entry(
            tracking_source_entries,
            app_tags.source_readable(identity_provider),
            primary_source_url,
            identity_provider,
        )

    display_source_url = media_metadata.get("display_source_url")
    if display_provider != identity_provider and display_source_url:
        append_entry(
            metadata_source_entries,
            app_tags.source_readable(display_provider),
            display_source_url,
            display_provider,
        )

    if media_type == MediaTypes.MOVIE.value and identity_provider == Sources.TMDB.value:
        media_id = media_metadata.get("media_id")
        if media_id:
            append_entry(
                external_entries,
                "Letterboxd",
                f"https://letterboxd.com/tmdb/{media_id}",
                "letterboxd",
            )

    if item is not None and item.source == Sources.YOUTUBE.value:
        # Built from the id, not from a reported URL, so it is always a
        # YouTube link. It does not depend on live metadata, so the chip also
        # shows when the page uses the stored fallback.
        append_entry(
            external_entries,
            "YouTube",
            f"https://www.youtube.com/watch?v={item.media_id}",
            "youtube",
        )

    external_links = media_metadata.get("external_links")
    if isinstance(external_links, dict):
        for name, url in external_links.items():
            append_entry(external_entries, name, url, name)

    is_anime = media_type == MediaTypes.ANIME.value or (
        media_type == MediaTypes.TV.value
        and item is not None
        and item.media_type == MediaTypes.TV.value
        and item.library_media_type == MediaTypes.ANIME.value
    )
    if is_anime and item is not None:
        from app.services.mal_ratings import resolve_mal_id

        mal_id = resolve_mal_id(item)
        if mal_id:
            append_entry(
                external_entries,
                "MyAnimeList",
                f"https://myanimelist.net/anime/{mal_id}",
                Sources.MAL.value,
            )

    # Links rebuilt from resolved external ids on the Item. Covers the best-effort
    # IMDB match for games (see app.services.imdb_game_credits), which IGDB itself
    # doesn't provide, and keeps the chips alive when media_metadata is the stored
    # fallback rather than a live payload (#1077). Appended last so payload links
    # keep their order; identical URLs dedupe through seen_urls.
    if item is not None:
        from app.providers import tmdb

        stored_links = tmdb.get_external_links(item.provider_external_ids or {})
        for name, url in stored_links.items():
            append_entry(external_entries, name, url, name)

    sections = []
    if metadata_source_entries:
        if tracking_source_entries:
            sections.append(
                {
                    "title": "Tracking Source",
                    "entries": tracking_source_entries,
                }
            )
        sections.append(
            {
                "title": "Metadata Source",
                "entries": metadata_source_entries,
            }
        )
    elif tracking_source_entries:
        sections.append(
            {
                "title": "Source",
                "entries": tracking_source_entries,
            }
        )
    if external_entries:
        sections.append({"title": "External links", "entries": external_entries})
    return sections


def _build_static_row(
    row_id,
    title,
    items,
    *,
    view_all_url=None,
    view_all_text=None,
    card_width_class="w-32",
):
    """Build a row dict for _scrollable_row.html with no HTMX loading (all items pre-rendered)."""
    shown = list(items)
    return {
        "row_id": row_id,
        "title": title,
        "items": shown,
        "total": len(shown),
        "loaded_count": len(shown),
        "card_width_class": card_width_class,
        "summary_inline": None,
        "show_played_chip": False,
        "view_all_url": view_all_url,
        "view_all_text": view_all_text,
    }


def _game_cast_and_crew_from_credits(item):
    """Return (cast, crew) display rows for a game's best-effort IMDB credits.

    Person profile pages aren't wired up for the IMDB source yet, so these rows
    omit person_id — person_card_inline.html renders them as plain (unlinked)
    cards instead of pointing at a person_detail page that doesn't exist.
    """
    from app.models import CreditRoleType, ItemPersonCredit

    credits_qs = (
        ItemPersonCredit.objects.filter(item=item)
        .select_related("person")
        .order_by("sort_order")
    )
    cast, crew = [], []
    for credit in credits_qs:
        person = credit.person
        if not person:
            continue
        row = {
            "name": person.name,
            "image": person.image,
            "role": credit.role,
            "department": credit.department,
        }
        if credit.role_type == CreditRoleType.CAST.value:
            cast.append(row)
        elif credit.role_type == CreditRoleType.CREW.value:
            crew.append(row)
    return cast, crew


def _build_detail_person_rows(media_metadata, item=None):
    """Build cast_row, crew_row, and recommendations_row dicts for _scrollable_row.html."""
    if not isinstance(media_metadata, dict):
        return {}
    cast = media_metadata.get("cast") or []
    crew = media_metadata.get("crew") or []

    if (
        not cast
        and not crew
        and item is not None
        and item.media_type == MediaTypes.GAME.value
        and item.source == Sources.IGDB.value
    ):
        cast, crew = _game_cast_and_crew_from_credits(item)
    raw_recommendations = (media_metadata.get("related") or {}).get(
        "recommendations"
    ) or []
    # enrich_items_with_user_data wraps each item as {"item": <dict>, "media": <model>}.
    # Normalize any unwrapped raw dicts (shell render, before enrichment runs) into
    # the same shape so home_grid.html can read entry.item / entry.media uniformly.
    recommendations = [
        r
        if (isinstance(r, dict) and "item" in r and "media" in r)
        else {"item": r, "media": None}
        for r in raw_recommendations
    ]

    total_cast = media_metadata.get("total_cast_count")
    source_url = media_metadata.get("source_url")
    view_all_url = None
    view_all_text = None
    if total_cast and total_cast > len(cast) and source_url:
        view_all_url = source_url
        view_all_text = f"Full cast ({total_cast})"

    return {
        "cast_row": _build_static_row(
            "detail-cast",
            "Cast",
            cast[:20],
            view_all_url=view_all_url,
            view_all_text=view_all_text,
            card_width_class="w-44",
        ),
        "crew_row": _build_static_row(
            "detail-crew", "Crew", crew[:20], card_width_class="w-44"
        ),
        "seasons_row": _build_static_row(
            "detail-seasons", "Seasons", (media_metadata.get("related") or {}).get("seasons") or [],
            card_width_class="w-44",
        ),
        "recommendations_row": {**_build_static_row(
            "detail-recommendations",
            "Recommendations",
            recommendations,
            card_width_class="w-44",
        ), "card_surface": "detail_recommendations"},
    }


_BOOK_FORMAT_LABELS = {
    "audiobook": "Audiobook",
    "ebook": "eBook",
    "hardcover": "Hardcover",
    "paperback": "Paperback",
}


def _book_isbns(item):
    return {
        "".join(ch for ch in str(value) if ch.isalnum()).upper()
        for value in item.isbn or []
        if value
    } - {""}


def _book_author_keys(item):
    return {
        key
        for key in (Item._title_comparison_key(a) for a in item.authors or [])
        if key
    }


def _other_book_items(user, item):
    """Return the viewer's other tracked Items that are the same book."""
    if (
        item is None
        or item.media_type != MediaTypes.BOOK.value
        or not getattr(user, "is_authenticated", False)
    ):
        return []

    title_key = Item._title_comparison_key(item.title)
    isbns = _book_isbns(item)
    authors = _book_author_keys(item)
    if not title_key and not isbns:
        return []

    candidates = Item.objects.filter(
        media_type=MediaTypes.BOOK.value,
        book__user=user,
    ).exclude(pk=item.pk)
    matches = []
    for other in candidates.distinct():
        same_isbn = bool(isbns & _book_isbns(other))
        same_work = (
            bool(title_key)
            and title_key == Item._title_comparison_key(other.title)
            and bool(authors & _book_author_keys(other))
        )
        if same_isbn or same_work:
            matches.append(other)
    return matches


def _book_format_label(book_item):
    return _BOOK_FORMAT_LABELS.get((book_item.format or "").lower(), "Book")


def build_book_formats(user, item):
    """Return the formats of this book the viewer tracks, or [] if only one.

    An audiobook and an ebook are separate Items on purpose (their progress
    units differ), so this only lists and links them. Two Items match when
    they share an ISBN, or share a title and at least one author. The current
    Item has no link. Audiobooks sort last, and when several audiobooks are
    tracked each is labelled with its narrator so they can be told apart.
    """
    others = _other_book_items(user, item)
    if not others:
        return []

    items = sorted(
        [item, *others],
        key=lambda book: (_book_format_label(book) == "Audiobook", book.pk),
    )
    audiobook_count = sum(_book_format_label(book) == "Audiobook" for book in items)
    formats = []
    for book in items:
        label = _book_format_label(book)
        narrators = ", ".join(book.narrators or [])
        if label == "Audiobook" and audiobook_count > 1 and narrators:
            label = f"{label} ({narrators})"
        formats.append(
            {
                "label": label,
                "href": None if book.pk == item.pk else app_tags.media_url(book),
            },
        )
    return formats

