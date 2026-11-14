"""What one media card shows, as plain data for the shared React card.

``card_context`` (app.card_surfaces) already settles the surface flags and the
subtitle profile. This module turns that same context into the card's visible
content so the template and the React card read one answer. Hover actions,
modals and the rating button are added by later slices.
"""

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from app.icon_specs import file_icon
from app.models import MediaTypes, Status
from app.templatetags import app_tags
from users.templatetags import user_tags

SQUARE_TYPES = {MediaTypes.PODCAST.value, MediaTypes.MUSIC.value}


def _safe(obj, attr):
    """Read ``attr`` from a model, or a key from a provider-result dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def card_shape(media_type, video_landscape=False):
    """Poster, square or landscape: the artwork's aspect ratio."""
    if media_type in SQUARE_TYPES:
        return "square"
    if media_type == MediaTypes.VIDEO.value and video_landscape:
        return "landscape"
    return "poster"


def _href(ctx):
    item, media = ctx.get("item"), ctx.get("media")
    show = ctx.get("podcast_show")
    if _safe(media, "card_tile_url"):
        return media.card_tile_url
    if _safe(media, "album"):
        return app_tags.music_album_url(media.album)
    if _safe(media, "artist"):
        return app_tags.music_artist_url(media.artist)
    if ctx.get("use_podcast_show") and show:
        slug = show.slug or show.title or show.podcast_uuid
        return reverse(
            "media_details",
            kwargs={
                "source": "pocketcasts",
                "media_type": "podcast",
                "media_id": _safe(show, "podcast_uuid"),
                "title": str(slug),
            },
        )
    url = app_tags.media_url(item)
    if ctx.get("public_list_reference"):
        from urllib.parse import quote

        return f"{url}?public_view=1&public_list={quote(ctx['public_list_reference'])}"
    if ctx.get("public_view"):
        return f"{url}?public_view=1"
    return url


def _title(ctx, media_type):
    item = ctx["item"]
    if media_type == MediaTypes.SEASON.value:
        return ctx.get("season_card_title_override") or app_tags.season_card_title(
            item,
        )
    return app_tags.display_title(item, ctx.get("user")) or ctx.get("title") or ""


def _status_chip(ctx):
    """Return the top-left chip: a status, an upcoming release, or none."""
    media = ctx.get("media")
    if ctx.get("show_media_type_chip"):
        kind = ctx.get("media_type_chip_type") or ctx.get("card_media_type") or _safe(ctx["item"], "media_type")
        look = app_tags.home_media_type_chip(ctx.get("user"), kind)
        return {
            "kind": "media-type",
            "type": kind,
            "style": look["style"],
            "color": look["color"],
            "contrast": look["contrast"],
            "label": app_tags.media_type_readable(kind),
        }
    if not ctx.get("show_status_chip", True):
        return None
    sort = ctx.get("current_sort") or ""
    hide_on_mobile = sort in {"score", "time_left", "progress"} or bool(
        ctx.get("show_episode_queue"),
    )
    event = _safe(media, "next_event")
    has_event = (
        bool(ctx.get("show_next_event_chip"))
        and event is not None
        and not event.is_max_datetime
    )
    event_label = None
    if has_event:
        number = event.content_number
        event_label = (
            f"{app_tags.short_unit(media.item.media_type)}{number}"
            if number
            else _("Release")
        )
    if _safe(media, "is_statusless"):
        return {"kind": "no-status", "label": _("No Status"), "hideOnMobile": hide_on_mobile}
    if not media:
        return None
    recommend = bool(ctx.get("is_recommend_mode"))
    watched = (
        _safe(media, "end_date")
        if _safe(media, "end_date") is not None
        else (_safe(media, "aggregated_end_date") or _safe(media, "status"))
    )
    if _safe(media, "status") and (not recommend or watched):
        if has_event:
            return {"kind": "event", "label": event_label, "icon": file_icon("clock"), "hideOnMobile": hide_on_mobile}
        status = _safe(media, "aggregated_status") or _safe(media, "status")
        return {
            "kind": "status",
            "status": status,
            "colorClass": app_tags.status_color(status),
            "hideOnMobile": hide_on_mobile,
        }
    if has_event:
        return {"kind": "event", "label": event_label, "icon": file_icon("clock"), "hideOnMobile": hide_on_mobile}
    return None


def _progress_bar(ctx, media_type):
    """Fill percentage for the inset bar, or None when it should not show."""
    media = ctx.get("media")
    if not media or not ctx.get("card_show_progress"):
        return None
    status = _safe(media, "aggregated_status") or _safe(media, "status")
    if status not in {Status.IN_PROGRESS.value, Status.PAUSED.value}:
        return None
    percent = _safe(media, "progress_percentage")
    if media_type == MediaTypes.PODCAST.value:
        if percent is None:
            return None
    elif media_type == MediaTypes.MOVIE.value or not (_safe(media, "max_progress") and _safe(media, "progress")):
        return None
    elif percent is None:
        percent = int(_safe(media, "progress") * 100 / _safe(media, "max_progress"))
    return {
        "percent": percent,
        "paused": status == Status.PAUSED.value,
        # "dormant" keeps the bar visible at rest instead of only on hover.
        "dormant": ctx.get("card_progress_display") == "dormant",
    }


def _plays(count):
    return ngettext("• %(count)d play", "• %(count)d plays", count) % {"count": count}


def _progress_text(ctx, media_type):
    """Return the ' • …' progress tail after the release year, or an empty string."""
    media, user = ctx.get("media"), ctx.get("user")
    if media is None:
        return ""
    progress_value_ = _safe(media, "progress")
    max_value_ = _safe(media, "max_progress")
    fmt_progress_ = _safe(media, "formatted_progress")
    fmt_max_ = _safe(media, "formatted_max_progress")
    fmt_agg_ = _safe(media, "formatted_aggregated_progress")
    aggregated = _safe(media, "aggregated_progress")
    pct = bool(_safe(user, "book_comic_manga_progress_percentage"))
    if media_type == MediaTypes.BOOK.value and _safe(media, "max_progress"):
        audiobook = _safe(media.item, "format") == "audiobook"
        partial = _safe(media, "progress") and _safe(media, "status") not in {
            Status.PLANNING.value,
            Status.COMPLETED.value,
        }
        if partial and pct:
            return f"• {int(progress_value_ * 100 / max_value_)}%"
        if partial:
            tail = "" if audiobook else " " + _("pages")
            return f"• {fmt_progress_}/{fmt_max_}{tail}"
        tail = "" if audiobook else " " + ngettext("page", "pages", app_tags.safe_count(_safe(media, "max_progress")))
        return f"• {fmt_max_}{tail}"
    if _safe(media, "max_progress") and media_type != MediaTypes.MOVIE.value:
        value = aggregated if aggregated is not None else _safe(media, "progress")
        if not (value or media_type == MediaTypes.SEASON.value):
            return ""
        count = app_tags.safe_count(_safe(media, "max_progress"))
        if media_type == MediaTypes.SEASON.value:
            done = _safe(media, "completed_episode_count") or 0
            return "• " + ngettext(
                "%(done)s / %(total)s episode",
                "%(done)s / %(total)s episodes",
                count,
            ) % {"done": done, "total": _safe(media, "max_progress")}
        if pct and media_type in {MediaTypes.COMIC.value, MediaTypes.MANGA.value}:
            return f"• {int((value or 0) * 100 / max_value_)}%"
        unit = ""
        if media_type == MediaTypes.TV.value:
            unit = " " + ngettext("episode", "episodes", count)
        elif app_tags.long_unit(media.item.media_type):
            base = app_tags.long_unit(media.item.media_type).lower()
            unit = " " + (_(base) if _safe(media, "max_progress") == 1 else _(base + "s"))
        return f"• {value or 0} / {max_value_}{unit}"
    if media_type == MediaTypes.MOVIE.value:
        if aggregated:
            return _plays(app_tags.safe_count(aggregated))
        if _safe(media, "progress"):
            return _plays(app_tags.safe_count(_safe(media, "progress")))
        repeats = _safe(media, "repeats")
        if repeats and _safe(media, "status") != Status.PLANNING.value:
            return _plays(app_tags.safe_count(repeats))
        return ""
    if aggregated:
        return f"• {fmt_agg_}"
    if _safe(media, "progress"):
        return f"• {fmt_progress_}"
    return ""


def _subtitle_lines(ctx, media_type):
    """Lines under the title, then what a credited person did on this title."""
    lines = _item_lines(ctx, media_type)
    credit = ctx.get("credit")
    if credit:
        from users.card_metadata import card_lines

        lines.extend(
            {
                "clamp": True,
                "dormant": bool(line.get("dormant")),
                "parts": [
                    {"text": part["text"], **({"score": True} if part.get("score") else {})}
                    for part in line["parts"]
                ],
            }
            for line in card_lines(ctx.get("user"), "person", credit, None)
        )
    return lines


def _item_lines(ctx, media_type):
    """Lines under the title, in the same precedence the template used."""
    media, item, user = ctx.get("media"), ctx.get("item"), ctx.get("user")
    lines = []
    matched = ctx.get("matched_title")
    title = _title(ctx, media_type)
    if matched and matched != title and not ctx.get("use_podcast_show"):
        lines.append({
            "clamp": True,
            "tooltip": _("Matched on %(value_1)s") % {"value_1": matched},
            "parts": [{"text": _("Matched: %(value_1)s") % {"value_1": matched}}],
        })
    show = ctx.get("podcast_show")
    if _safe(media, "home_music_card"):
        text = _safe(media, "card_subtitle_text")
        if text:
            lines.append({"clamp": True, "parts": [{"text": text}]})
        elif _safe(media, "card_subtitle_date"):
            lines.append({"clamp": True, "parts": [{"text": user_tags.user_date_format(media.card_subtitle_date, user)}]})
        return lines
    if (
        ctx.get("show_episode_identity")
        and _safe(item, "season_number") is not None
        and _safe(item, "episode_number") is not None
    ):
        season, episode = _safe(item, "season_number"), _safe(item, "episode_number")
        lines.append({"parts": [{"text": f"S{season:02d} E{episode:02d}"}]})
        return lines
    if ctx.get("use_podcast_show") and show:
        text = show.title
        percent = _safe(media, "progress_percentage")
        status = _safe(media, "aggregated_status") or _safe(media, "status")
        if percent is not None and status in {Status.IN_PROGRESS.value, Status.PAUSED.value}:
            text += f" • {percent}%"
        lines.append({"clamp": True, "parts": [{"text": text}]})
        return lines
    override, match = ctx.get("subtitle_override"), ctx.get("subtitle_match_percent")
    if override or match is not None:
        bits = []
        if override:
            bits.append(app_tags.iso_date_format(override, user))
        if match is not None:
            label = _(ctx.get("subtitle_match_label") or "match")
            bits.append(f"{match}% {label}")
        lines.append({"parts": [{"text": " • ".join(bits)}]})
        if ctx.get("provenance_text"):
            lines.append({"variant": "provenance", "parts": [{"text": ctx["provenance_text"]}]})
        return lines
    if ctx.get("show_played_chip") and media and _safe(media, "last_played_at"):
        when = user_tags.user_datetime_format(_safe(media, "last_played_at"), user)
        if _safe(media, "play_count") and media_type == MediaTypes.MUSIC.value:
            plays = ngettext("%(c)d play", "%(c)d plays", _safe(media, "play_count")) % {"c": _safe(media, "play_count")}
            when = f"{plays} {when}"
        lines.append({"parts": [{"text": when}]})
        return lines
    event = _safe(media, "next_event")
    if ctx.get("show_next_event_subtitle") and event is not None and not event.is_max_datetime:
        head = (
            f"{app_tags.short_unit(media.item.media_type)}{event.content_number}"
            if event.content_number
            else _("Release")
        )
        clock = app_tags.user_event_time(event, user)
        if app_tags.event_within_days(event, 7):
            day = f"{event.datetime:%a}"
        else:
            day = user_tags.user_date_format(event.datetime, user)
        lines.append({"parts": [{"text": f"{head} • {day}{' ' + clock if clock else ''}"}]})
        return lines
    if ctx.get("card_use_lines"):
        lines.extend(
            {
                "clamp": True,
                "dormant": bool(line.get("dormant")),
                "parts": [
                    {"text": part["text"], **({"score": True} if part.get("score") else {})}
                    for part in line["parts"]
                ],
            }
            for line in ctx.get("card_line_list") or []
        )
        return lines
    year = app_tags.release_year(item, media)
    if year:
        text = str(year)
        if _safe(item, "series_position"):
            from django.template.defaultfilters import floatformat

            text += " " + _("· Book %(value_1)s") % {"value_1": floatformat(_safe(item, "series_position"))}
        tail = _progress_text(ctx, media_type)
        parts = [{"text": text}]
        if tail:
            parts.append({"text": tail, "muted": True})
        lines.append({"join": "plain", "parts": parts})
    elif _safe(item, "series_position"):
        from django.template.defaultfilters import floatformat

        lines.append({"parts": [{"text": _("Planned · Book %(value_1)s") % {"value_1": floatformat(_safe(item, "series_position"))}}]})
    elif ctx.get("show_release_year_placeholder") and _safe(item, "id"):
        lines.append({
            "placeholderItemId": _safe(item, "id"),
            "progressText": _progress_text(ctx, media_type).removeprefix("• "),
            "parts": [],
        })
    return lines


def _corner_chips(ctx, media_type):
    """Small overlays on the artwork other than the status chip."""
    item, media = ctx["item"], ctx.get("media")
    chips = []
    credit = ctx.get("credit")
    runtime = _safe(credit, "watched_person_runtime_display") if credit else None
    if runtime:
        chips.append({"kind": "runtime", "text": runtime})
    if (
        media_type == MediaTypes.SEASON.value
        and _safe(item, "provider_metadata_status") == "local_only_missing_season"
    ):
        chips.append({"kind": "local-only", "text": _("Local only")})
    done = ctx.get("collection_completeness")
    if done and done.get("is_partial"):
        chips.append({
            "kind": "collected",
            "text": _("%(value_1)s/%(value_2)s collected") % {"value_1": done["collected"], "value_2": done["total"]},
            "title": _("%(value_1)s of %(value_2)s episodes collected") % {"value_1": done["collected"], "value_2": done["total"]},
        })
    if media and ctx.get("show_episode_queue") and _safe(media, "next_episode_queue"):
        chips.append({
            "kind": "queue",
            "text": app_tags.episode_queue_label(media),
            "href": app_tags.episode_queue_url(item, media) or "",
        })
    elif (
        media
        and not ctx.get("is_recommend_mode")
        and not ctx.get("public_view")
        and getattr(ctx.get("user"), "is_authenticated", False)
        and getattr(ctx["user"], "quick_season_update_mobile", "") in {"next_episode", "both"}
        and media_type in {MediaTypes.SEASON.value, MediaTypes.TV.value, MediaTypes.ANIME.value}
    ):
        href = app_tags.next_episode_url(item, media)
        if href:
            chips.append({
                "kind": "next-episode",
                "text": _("Next ep"),
                "compactText": _("Next"),
                "href": href,
            })
    return chips


def card_model(ctx):
    """Return the card's visible content from a ``card_context`` result."""
    item = ctx["item"]
    media_type = ctx.get("card_media_type") or _safe(item, "media_type")
    image = ctx.get("image_override") or _safe(item, "image")
    show = ctx.get("podcast_show")
    if ctx.get("use_podcast_show") and show and show.image:
        image = show.image
    video = media_type == MediaTypes.VIDEO.value
    return {
        "mediaType": _safe(item, "media_type"),
        "shape": card_shape(media_type, bool(ctx.get("video_landscape"))),
        "video": video,
        "publicView": bool(ctx.get("public_view")),
        "recommend": bool(ctx.get("is_recommend_mode")),
        "bulkItemId": _safe(item, "id") if ctx.get("enable_bulk_select") else None,
        "href": _href(ctx),
        "image": {
            "src": app_tags.image_url(
                app_tags.youtube_landscape_thumbnail(image) if video else image,
            ),
            "fallback": app_tags.image_url(image) if video else "",
            "source": ctx.get("image_source") or "primary",
        },
        "title": _title(ctx, media_type),
        "altTitle": None
        if ctx.get("use_podcast_show") or ctx.get("hide_alt_title") or media_type == MediaTypes.SEASON.value
        else app_tags.alternative_title(item, ctx.get("user")),
        "statusChip": _status_chip(ctx),
        "cornerChips": _corner_chips(ctx, media_type),
        "titleLinks": [
            {"url": part["url"], "label": part["label"]}
            for part in (_safe(ctx.get("media"), "card_links") or [])
        ],
        "progressBar": _progress_bar(ctx, media_type),
        "lines": _subtitle_lines(ctx, media_type),
        "actions": card_actions(ctx),
        "cardKey": app_tags.item_slot_id("media-card", item),
        "statusId": app_tags.item_slot_id("media-status-chip", item),
        "ratingId": app_tags.item_slot_id("media-card-rating", item),
        "rating": rating_model(ctx, media_type),
        "ratingEditable": not (
            ctx.get("public_view")
            or ctx.get("is_recommend_mode")
            or ctx.get("rating_read_only")
            or ctx.get("use_podcast_show")
        ),
        "csrfToken": str(ctx.get("csrf_token") or ""),
    }


def _plays_text(count):
    """Return the "N play(s)" label History shows for repeat viewings."""
    return ngettext("%(value_1)s play", "%(value_1)s plays", count) % {"value_1": count}


def _history_info_parts(entry, media_type, history_mode, user):
    """Return the History line: what happened, then when."""
    when = user_tags.user_datetime_format
    played = _safe(entry, "played_at_local")
    if history_mode == "release":
        return [
            {"text": _("Released"), "link": True},
            {"text": user_tags.user_date_format(played, user), "muted": True},
        ]
    plays = _safe(entry, "play_count") or 1
    progress = _safe(entry, "progress_display")
    if progress and media_type in {MediaTypes.GAME.value, MediaTypes.BOARDGAME.value}:
        return [
            {"text": progress, "link": True},
            {"text": _safe(entry, "date_range_display"), "muted": True},
        ]
    if media_type in {MediaTypes.MOVIE.value, MediaTypes.PODCAST.value}:
        return [
            {"text": _plays_text(plays), "link": True},
            {"text": when(played, user), "muted": True},
        ]
    if media_type == MediaTypes.MUSIC.value:
        return [
            {"text": _plays_text(plays), "link": True},
            {"text": _safe(entry, "time_range_display"), "muted": True},
        ]
    label = _safe(entry, "episode_label") or _safe(entry, "episode_code")
    if label:
        return [{"text": label, "link": True}, {"text": when(played, user), "muted": True}]
    return [{"text": when(played, user)}]


def history_card_model(entry, card_class, history_mode, user, request=None):
    """Return the History card's visible content for the shared React card."""
    from users.card_metadata import card_lines, hides_zero_rating, shows_score

    item = _safe(entry, "item")
    media_type = _safe(entry, "media_type")
    show = _safe(entry, "show")
    if media_type == MediaTypes.MUSIC.value and _safe(entry, "album"):
        href = app_tags.music_album_url(entry["album"] if isinstance(entry, dict) else entry.album)
    elif media_type == MediaTypes.PODCAST.value and show and _safe(show, "podcast_uuid"):
        slug = _safe(show, "slug") or _safe(show, "title") or ""
        href = reverse(
            "media_details",
            kwargs={
                "source": "pocketcasts",
                "media_type": "podcast",
                "media_id": _safe(show, "podcast_uuid"),
                "title": str(slug),
            },
        )
    else:
        href = app_tags.media_url(item)
    title = (
        app_tags.display_title(item, user)
        or _safe(entry, "display_title")
        or _safe(entry, "title")
        or ""
    )
    poster = _safe(entry, "poster")
    video = media_type == MediaTypes.VIDEO.value
    status = _safe(entry, "status")
    chip = None
    if status or (media_type == MediaTypes.EPISODE.value and history_mode != "release"):
        value = status or Status.COMPLETED.value
        chip = {
            "kind": "status-native",
            "status": value,
            "label": _("Status: %(value_1)s") % {"value_1": _(value)},
            "colorClass": app_tags.status_color(value),
        }
    corner = []
    if _safe(entry, "runtime_display"):
        corner.append({"kind": "runtime-top", "text": entry["runtime_display"] if isinstance(entry, dict) else entry.runtime_display})
    score = _safe(entry, "score")
    score_text = None
    if shows_score(user, media_type) and score is not None:
        try:
            visible = not hides_zero_rating(user, media_type) or float(score) > 0
        except (TypeError, ValueError):
            visible = True
        if visible:
            score_text = str(app_tags.score_display(score, user))
    lines = [
        {"join": "plain", "parts": _history_info_parts(entry, media_type, history_mode, user)},
    ]
    lines.extend(
        {
            "clamp": True,
            "dormant": bool(line.get("dormant")),
            "parts": [
                {"text": part["text"], **({"score": True} if part.get("score") else {})}
                for part in line["parts"]
            ],
        }
        for line in card_lines(user, media_type, item, entry)
    )
    return {
        "mediaType": media_type,
        "shape": "square" if card_class == "search-result-card-square" else "poster",
        "video": video,
        "boost": True,
        "href": href,
        "image": {
            "src": app_tags.image_url(
                app_tags.youtube_landscape_thumbnail(poster) if video else poster,
            ),
            "fallback": app_tags.image_url(poster) if video else "",
            "source": "primary",
        },
        "title": title,
        "altTitle": app_tags.alternative_title(item, user),
        "statusChip": chip,
        "cornerChips": corner,
        "titleLinks": [],
        "progressBar": None,
        "scoreText": score_text,
        "lines": lines,
        "actions": _with_href(history_actions(entry, history_mode, user, request), href),
    }


def row_card_model(item, media, title, matched_title, user, request=None):
    """Return the Search list-view row: the same card content in a row layout."""
    from users.card_metadata import card_lines

    media_type = _safe(item, "media_type")
    ctx = {"item": item, "media": media, "title": title, "user": user, "request": request}
    resolved = _title(ctx, media_type)
    lines = [
        {
            "clamp": True,
            "dormant": bool(line.get("dormant")),
            "parts": [
                {"text": part["text"], **({"score": True} if part.get("score") else {})}
                for part in line["parts"]
            ],
        }
        for line in card_lines(user, media_type, item, None)
    ]
    return {
        "layout": "row",
        "mediaType": media_type,
        "href": _href(ctx),
        "image": {"src": app_tags.image_url(_safe(item, "image")), "fallback": "", "source": "primary"},
        "title": resolved,
        "altTitle": app_tags.alternative_title(item, user),
        "matched": (
            {
                "text": _("Matched: %(value_1)s") % {"value_1": matched_title},
                "title": _("Matched on %(value_1)s") % {"value_1": matched_title},
            }
            if matched_title and matched_title != resolved
            else None
        ),
        "lines": lines,
        "cornerChips": [],
        "titleLinks": [],
        "actions": card_actions(ctx),
    }


def lists_modal_spec(item, target, return_url=""):
    """Describe the Add-to-lists form for the shared popup host."""
    return {
        "kind": "modal",
        "modal": "lists",
        "tone": TONES["lists"],
        "title": _("Add to custom lists"),
        "url": app_tags.media_view_url("lists_modal", item),
        "vals": {"return_url": return_url},
        "target": target,
        "labels": {
            "title": _("Lists"),
            "close": _("Close"),
            "loading": _("Loading"),
            "loadFailed": _("Couldn't load your lists."),
            "retry": _("Try again"),
            "filterByTag": _("Filter by tag"),
            "allLists": _("All lists"),
            "countOne": _("On %s list"),
            "countMany": _("On %s lists"),
            "add": _("Add"),
            "remove": _("Remove"),
            "addTo": _("Add item to %s"),
            "removeFrom": _("Remove item from %s"),
            "emptyTag": _("No lists with this tag."),
            "emptyLists": _("You haven't created any lists yet."),
            "toggleFailed": _("Couldn't update this list. Please try again."),
        },
    }


TONES = {"track": "indigo", "lists": "emerald", "history": "amber", "plan": "indigo", "hide": "rose"}


def _history_href(ctx, media_type):
    """Return the History page filtered to this item, as the hover button links."""
    from urllib.parse import quote

    item, media = ctx["item"], ctx.get("media")
    kind = _safe(item, "media_type")
    media_id = quote(str(_safe(item, "media_id") or ""), safe="")
    source = quote(str(_safe(item, "source") or ""), safe="")
    base = reverse("history")
    show = _safe(media, "show") or _safe(_safe(media, "tracker"), "show")
    if kind in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}:
        query = f"media_type={MediaTypes.TV.value}&media_id={media_id}&source={source}"
        if _safe(item, "season_number"):
            query += f"&season_number={_safe(item, 'season_number')}"
    elif kind == MediaTypes.TV.value:
        query = f"media_type={MediaTypes.TV.value}&media_id={media_id}&source={source}"
    elif kind == MediaTypes.PODCAST.value and show:
        query = f"media_type={MediaTypes.PODCAST.value}&podcast_show={show.id}"
    else:
        query = f"media_type={kind}&media_id={media_id}&source={source}"
    if kind in {MediaTypes.GAME.value, MediaTypes.BOARDGAME.value}:
        query += "&logging_style=sessions"
    return f"{base}?{query}"


def card_actions(ctx):
    """Return the hover buttons, their modal targets and list-order controls."""
    from django.conf import settings

    mode = ctx.get("hover_action_mode") or "standard"
    if ctx.get("is_recommend_mode") or ctx.get("public_view") or mode == "none":
        return None
    item, media = ctx["item"], ctx.get("media")
    media_type = ctx.get("card_media_type") or _safe(item, "media_type")
    uid = ctx.get("card_uid")
    suffix = f"-{uid}" if uid else ""
    row = ctx.get("home_row_id")
    return_url = ctx.get("return_url") or (
        ctx["request"].get_full_path() if ctx.get("request") else ""
    )
    media_pk = _safe(media, "id")
    show = ctx.get("podcast_show") if ctx.get("use_podcast_show") else None
    album = _safe(media, "album") if _safe(media, "home_music_card") else None
    artist = _safe(media, "artist") if _safe(media, "home_music_card") else None
    lists_id = f"{app_tags.component_id('lists', item)}{suffix}"
    track_id = f"{app_tags.component_id('track', item, media_pk)}{suffix}"
    items = []

    def lists_button():
        return {
            **lists_modal_spec(item, f"#{lists_id}", return_url),
            "icon": file_icon("list-add"),
        }

    def history_button():
        return {
            "kind": "link",
            "tone": TONES["history"],
            "title": _("View your activity history"),
            "icon": file_icon("history"),
            "href": _history_href(ctx, media_type),
        }

    def track_button(title, url, vals, target, icon="edit"):
        return {
            "kind": "modal",
            "modal": "track",
            "tone": TONES["track"],
            "title": title,
            "icon": file_icon(icon),
            "url": url,
            "vals": vals,
            "target": target,
        }

    if mode == "discover":
        base = {
            "candidate_media_type": media_type,
            "identity_media_type": _safe(item, "identity_media_type") or "",
            "library_media_type": _safe(item, "library_media_type") or "",
            "source": _safe(item, "source"),
            "media_id": _safe(item, "media_id"),
            "season_number": _safe(item, "season_number")
            if _safe(item, "season_number") is not None
            else "",
            "active_media_type": ctx.get("discover_active_media_type") or "all",
            "show_more": "1" if ctx.get("discover_show_more") else "0",
            "row_key": ctx.get("discover_row_key") or "",
            "title": _title(ctx, media_type),
            "image": _safe(item, "image") or settings.IMG_NONE,
            "release_date": str(_safe(item, "release_date") or ""),
            "discover_debug": "1" if ctx.get("discover_debug") else "0",
        }
        target = f"#discover-row-{ctx['discover_row_key']}" if ctx.get("discover_row_key") else "#discover-rows"
        swap = "outerHTML" if ctx.get("discover_row_key") else "innerHTML"
        for key, title, icon, tone in (
            ("planning", _("Add to Planning"), "planning", "plan"),
            ("dismiss", _("Hide from Discover"), "eye-closed", "hide"),
        ):
            items.append(
                {
                    "kind": "post",
                    "tone": TONES[tone],
                    "title": title,
                    "icon": file_icon(f"states/{icon}" if icon == "planning" else icon),
                    "url": reverse("discover_action"),
                    "vals": {**base, "action": key},
                    "target": target,
                    "swap": swap,
                },
            )
        items.append(lists_button())
    elif _safe(item, "media_type") == MediaTypes.EPISODE.value:
        vals = {"return_url": return_url, "episode_number": str(_safe(item, "episode_number"))}
        vals.update({"instance_id": str(media_pk)} if media else {"is_create": "1"})
        items.append(
            track_button(
                _("Watched") if media else _("Mark watched"),
                reverse(
                    "track_modal",
                    kwargs={
                        "source": _safe(item, "source"),
                        "media_type": "episode",
                        "media_id": _safe(item, "media_id"),
                        "season_number": _safe(item, "season_number"),
                    },
                ),
                vals,
                f"#{track_id}",
            ),
        )
        items.extend([lists_button(), history_button()])
    else:
        home = {"home_row_id": str(row)} if row else {}
        if show:
            target = f"#podcast-show-track-modal-{show.id}-{media_pk}{f'-home-{row}' if row else ''}"
            items.append(
                track_button(
                    _("Edit tracking"),
                    reverse("podcast_show_track_modal", args=[show.id]),
                    {"return_url": return_url, **home},
                    target,
                ),
            )
        elif album:
            items.append(
                track_button(
                    _("Edit tracking"),
                    reverse("album_track_modal", args=[album.id]),
                    {"return_url": return_url, **home},
                    f"#album-track-modal-{album.id}{f'-home-{row}' if row else ''}",
                ),
            )
        elif artist:
            items.append(
                track_button(
                    _("Edit tracking"),
                    reverse("artist_track_modal", args=[artist.id]),
                    {"return_url": return_url, **home},
                    f"#artist-track-modal-{artist.id}{f'-home-{row}' if row else ''}",
                ),
            )
        else:
            vals = {"return_url": return_url, **home}
            if media:
                vals["instance_id"] = str(media_pk)
            items.append(
                track_button(
                    _("Add to tracker"),
                    app_tags.media_view_url("track_modal", item),
                    vals,
                    f"#{track_id}",
                ),
            )
        items.extend([lists_button(), history_button()])

    overlay_href = (
        _safe(media, "card_tile_url")
        or (app_tags.music_album_url(media.album) if _safe(media, "album") else None)
        or (app_tags.music_artist_url(media.artist) if _safe(media, "artist") else None)
        or (_href({**ctx, "public_view": False, "public_list_reference": None}))
    )
    ordering = None
    if ctx.get("list_ordering_enabled") and ctx.get("custom_list") is not None:
        ordering = {
            "url": reverse("list_reorder_item", args=[ctx["custom_list"].id]),
            "itemId": _safe(item, "id"),
            "dragIcon": file_icon("drag-handle"),
            "labels": {
                "first": _("Move to first"),
                "back": _("Move back"),
                "next": _("Move next"),
                "last": _("Move to last"),
                "drag": _("Drag to reorder"),
            },
        }
    return {
        "href": overlay_href,
        "items": items,
        "ordering": ordering,
        "modals": {"track": track_id, "lists": lists_id},
        "csrfToken": str(ctx.get("csrf_token") or ""),
        "clickable": bool(getattr(ctx.get("user"), "clickable_media_cards", False)),
    }


def _with_href(actions, href):
    """Point a card's overlay link at its page."""
    if actions:
        actions["href"] = href
    return actions


def history_actions(entry, history_mode, user, request):
    """Return the History card's hover buttons: edit tracking, lists, history."""
    from urllib.parse import quote

    if history_mode == "release" or not (_safe(entry, "item") and _safe(_safe(entry, "item"), "media_type")):
        return None
    item = entry["item"] if isinstance(entry, dict) else entry.item
    media_type = _safe(entry, "media_type")
    key = _safe(entry, "entry_key")
    return_url = request.get_full_path() if request else ""
    track_vals = {"return_url": return_url, "standard_modal": "1"}
    if _safe(entry, "instance_id"):
        track_vals["instance_id"] = str(entry["instance_id"] if isinstance(entry, dict) else entry.instance_id)
    media_id = quote(str(_safe(item, "media_id") or ""), safe="")
    source = quote(str(_safe(item, "source") or ""), safe="")
    show = _safe(entry, "show")
    if media_type == MediaTypes.MUSIC.value and _safe(entry, "album"):
        album = entry["album"] if isinstance(entry, dict) else entry.album
        query = f"album={_safe(album, 'id')}"
    elif media_type == MediaTypes.PODCAST.value and show:
        query = f"media_type={MediaTypes.PODCAST.value}&podcast_show={_safe(show, 'id')}"
    elif media_type == MediaTypes.EPISODE.value:
        query = f"media_type={MediaTypes.TV.value}&media_id={media_id}&source={source}"
        if _safe(item, "season_number"):
            query += f"&season_number={_safe(item, 'season_number')}"
    else:
        query = f"media_type={media_type}&media_id={media_id}&source={source}"
    if media_type in {MediaTypes.GAME.value, MediaTypes.BOARDGAME.value}:
        query += "&logging_style=sessions"
    track_id = app_tags.component_id("track", item, key)
    lists_id = app_tags.component_id("lists", item, key)
    return {
        "href": None,
        "items": [
            {
                "kind": "modal",
                "modal": "track",
                "tone": "indigo",
                "title": _("Edit tracking"),
                "icon": file_icon("edit"),
                "url": app_tags.media_view_url("track_modal", item),
                "vals": track_vals,
                "target": f"#{track_id}",
            },
            {
                **lists_modal_spec(item, f"#{lists_id}", return_url),
                "icon": file_icon("list-add"),
            },
            {
                "kind": "link",
                "tone": "amber",
                "title": _("View your activity history"),
                "icon": file_icon("history"),
                "href": f"{reverse('history')}?{query}",
            },
        ],
        "ordering": None,
        "modals": {"track": track_id, "lists": lists_id},
        "csrfToken": "",
        "clickable": bool(getattr(user, "clickable_media_cards", False)),
    }


def rating_label(user, media_type, value):
    """Return the score as the user's scale shows it, or None when it is hidden."""
    from users.card_metadata import hides_zero_rating, shows_score

    if value is None or not shows_score(user, media_type):
        return None
    try:
        if hides_zero_rating(user, media_type) and float(value) <= 0:
            return None
    except (TypeError, ValueError):
        pass
    return str(app_tags.score_display(value, user))


def rating_model(ctx, media_type):
    """Return the title-row rating: a button that opens the star picker, or text."""
    user, media, item = ctx.get("user"), ctx.get("media"), ctx["item"]
    if not media or not getattr(user, "ratings_enabled", True):
        return None
    value = _safe(media, "aggregated_score")
    if value is None:
        value = _safe(media, "formatted_score")
    label = rating_label(user, media_type, value)
    rate_url = _safe(media, "score_url")
    editable = not (
        ctx.get("public_view")
        or ctx.get("is_recommend_mode")
        or ctx.get("rating_read_only")
        or ctx.get("use_podcast_show")
    )
    if not rate_url and editable and _safe(media, "id"):
        rate_url = reverse(
            "update_media_score",
            args=[_safe(item, "media_type"), _safe(media, "id")],
        )
    if not rate_url and label is None:
        return None
    try:
        number = float(label) if label is not None else None
    except ValueError:
        number = None
    return {
        "label": label,
        "value": number,
        "rateUrl": rate_url or None,
        "scaleMax": app_tags.rating_scale_max(user),
        "rateLabel": _("Rate"),
    }


def progress_changer_model(media, csrf_token=""):
    """Return the shared quick-progress control's props for one tracked row."""
    media_type = media.item.media_type
    percentage = bool(getattr(media, "progress_is_percentage", False))
    show_units = (
        media_type not in {MediaTypes.GAME.value, MediaTypes.MOVIE.value}
        and not percentage
    )
    max_progress = getattr(media, "max_progress", None)
    unit = ""
    if show_units and getattr(media, "progress_unit", None) != "minutes":
        count = max_progress or (getattr(media, "progress", 0) or 0)
        if media_type == MediaTypes.TV.value:
            unit = ngettext("Episode", "Episodes", count)
        else:
            unit = app_tags.progress_unit_label(media_type, count)
    value = getattr(media, "formatted_progress", None) or str(
        getattr(media, "progress", ""),
    )
    progress = getattr(media, "progress", 0) or 0
    formatted_max = getattr(media, "formatted_max_progress", None) or str(max_progress)
    return {
        "value": str(value),
        "max": str(formatted_max) if max_progress else None,
        "unit": str(unit),
        "showMax": bool(max_progress) and show_units,
        "canDecrease": progress != 0,
        "canIncrease": max_progress is None or progress != max_progress,
        "decreaseLabel": str(_("Decrease progress")),
        "increaseLabel": str(_("Increase progress")),
        "url": reverse("progress_edit", args=[media_type, media.id]),
        "target": f"progress-{media_type}-{media.id}",
        "csrfToken": str(csrf_token or ""),
        "watchOperationId": (
            app_tags.watch_operation_id()
            if media_type in {MediaTypes.TV.value, MediaTypes.SEASON.value}
            else ""
        ),
    }
