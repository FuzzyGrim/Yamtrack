"""Server-owned content slots for the incremental React settings-card pilot."""

from uuid import uuid4

from django import template
from django.template.base import token_kwargs
from django.template.loader import render_to_string
from django.urls import reverse

register = template.Library()

SPINNER_SIZES = (8, 48)
BADGE_SIZES = {"xs", "sm", "md", "tag"}
BADGE_TONES = {
    "indigo", "rose", "violet", "neutral", "outline", "tint", "genre",
    "green-soft", "indigo-soft", "emerald-soft", "amber-soft",
}
BUTTON_TONES = {"primary", "danger-solid", "danger-deep", "surface", "ghost-danger"}
BUTTON_ATTRS = {"id", "name", "value", "form", "formaction", "title"}
BUTTON_ATTR_PREFIXES = ("hx-", "data-", "aria-")


@register.tag
def settings_card(parser, token):
    """Render a shared card without passing its forms or credentials to React."""
    bits = token.split_contents()
    options = token_kwargs(bits[1:], parser)
    allowed = {"title", "tone", "badge", "info_label", "info_title", "enabled"}
    if "title" not in options or set(options) - allowed or any("=" not in bit for bit in bits[1:]):
        message = "settings_card requires title and named presentation options only"
        raise template.TemplateSyntaxError(message)
    body = parser.parse(("endsettings_card",))
    parser.delete_first_token()
    return SettingsCardNode(options, body)


class SettingsCardNode(template.Node):
    """Compose escaped presentation props and a separately rendered content slot."""

    def __init__(self, options, body):
        """Keep the compiled expressions and Django body for each render."""
        self.options = options
        self.body = body

    def render(self, context):
        """Render the shell, or only its body for legacy dialog consumers."""
        if "enabled" in self.options and not self.options["enabled"].resolve(context):
            return self.body.render(context)
        props = {name: str(value.resolve(context)) for name, value in self.options.items()}
        props.pop("enabled", None)
        if props.get("tone") not in {"neutral", "info", "warning", "success"}:
            props["tone"] = "neutral"
        # The body remains Django HTML, outside the React root. Never serialize it.
        return render_to_string("users/components/settings_card.html", {
            "card": props,
            "props_id": f"settings-card-{uuid4().hex}",
            "body": self.body.render(context),
        })


@register.tag
def filter_bar(parser, token):
    """Render the shared filter bar with its server toolbar as the fallback."""
    bits = token.split_contents()
    options = token_kwargs(bits[1:], parser)
    allowed = {
        "endpoint", "target", "form_id", "surface", "ranges",
        "platform_multi", "media_types", "views", "saved_views", "state",
    }
    if set(options) - allowed or any("=" not in bit for bit in bits[1:]):
        message = "filter_bar accepts named presentation options only"
        raise template.TemplateSyntaxError(message)
    body = parser.parse(("endfilter_bar",))
    parser.delete_first_token()
    return FilterBarNode(options, body)


class FilterBarNode(template.Node):
    """Render the universal filter bar around the legacy toolbar fallback."""

    def __init__(self, options, body):
        """Keep the compiled expressions and Django body for each render."""
        self.options = options
        self.body = body

    def render(self, context):
        """Resolve options, serialize normalized state and keep a fallback."""
        from app.filter_bar import build_filter_bar_props

        resolved = {
            name: value.resolve(context) for name, value in self.options.items()
        }
        props = build_filter_bar_props(
            context,
            endpoint=resolved.get("endpoint", ""),
            target=resolved.get("target", ""),
            form_id=resolved.get("form_id", "filter-form"),
            surface=str(resolved.get("surface", "library")),
            ranges=bool(resolved.get("ranges", False)),
            platform_multi=bool(resolved.get("platform_multi", False)),
            media_types=resolved.get("media_types"),
            views=resolved.get("views"),
            saved_views=resolved.get("saved_views"),
            state=resolved.get("state"),
        )
        return render_to_string("app/components/filter_bar.html", {
            "props": props,
            "props_id": f"filter-bar-{uuid4().hex}",
            "body": self.body.render(context),
        })


@register.simple_tag
def settings_link_chip(label, url):
    """Render a shared link chip with a usable server fallback."""
    return render_to_string("users/components/settings_link_chip.html", {
        "chip": {"label": label, "href": url, "tone": "info",
                 "hrefAttrs": {"target": "_blank", "rel": "noopener"}},
        "props_id": f"settings-chip-{uuid4().hex}",
    })


@register.simple_tag(takes_context=True)
def rating_stars(context, post_url):
    """Render the shared React star row; the caller's popup owns open/close state."""
    from django.utils.translation import gettext as _

    from app.templatetags.app_tags import rating_scale_max

    return render_to_string("app/components/rating_stars.html", {
        "props": {
            "postUrl": str(post_url),
            "scaleMax": rating_scale_max(context.get("user")),
            "csrfToken": str(context.get("csrf_token") or ""),
            "label": str(_("Rate")),
        },
        "props_id": f"rating-stars-{uuid4().hex}",
    })


def _number_or_value(value):
    """Render integral display scores without a trailing `.0`."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def _derived_value(derived, name):
    if derived is None:
        return None
    if isinstance(derived, dict):
        return derived.get(name)
    return getattr(derived, name, None)


@register.simple_tag(takes_context=True)
def rating_chip(
    context,
    instance=None,
    media_type="item",
    post_url="",
    derived_tv_score=None,
    *,
    score=None,
    layout=None,
    rate_title=None,
):
    """Render the shared personal score chip and popup with a server fallback."""
    from django.template.defaultfilters import floatformat
    from django.utils.translation import gettext as _

    from app.templatetags.app_tags import rating_scale_max, score_display
    from users.models import FIVE_POINT_RATING_SCALE

    user = context.get("user")
    scale_max = rating_scale_max(user)
    if score is None and instance is not None:
        score = getattr(instance, "score", None)
    compact = layout == "compact"
    display = _number_or_value(score_display(score, user)) if score is not None else None
    if display is None:
        display_value = None
        card_display_value = None
    elif compact:
        display_value = str(display)
        card_display_value = str(display)
    elif scale_max == FIVE_POINT_RATING_SCALE:
        display_value = f"{display}/5"
        card_display_value = f"{display}/5"
    else:
        display_value = str(display)
        card_display_value = floatformat(display, 1)

    derived = None
    derived_score = _derived_value(derived_tv_score, "score")
    if derived_score is not None:
        coverage = render_to_string(
            "app/components/derived_tv_rating_coverage.html",
            {"derived_tv_score": derived_tv_score},
        ).strip()
        derived = {
            "score": floatformat(derived_score, "-1"),
            "coverage": coverage,
            "label": str(_derived_value(derived_tv_score, "label") or ""),
            "title": str(_derived_value(derived_tv_score, "title") or ""),
        }

    noun = {
        "tv": _("show"),
        "season": _("show"),
        "movie": _("movie"),
        "music": _("track"),
        "track": _("track"),
        "artist": _("artist"),
        "album": _("album"),
        "comic_issue": _("issue"),
        "episode": _("episode"),
    }.get(media_type, _("item"))
    labels = {
        "rate": str(_("Rate")),
        "derived": str(_("Derived")),
        "yourRating": str(_("Your rating")),
        "close": str(_("Close")),
        "prompt": f"{_('Rate this')} {noun}",
    }
    if rate_title is not None:
        labels["rateTitle"] = str(rate_title)
    if score is not None:
        labels["yourScore"] = str(_("Your score"))
    if derived is not None:
        labels["derivedRating"] = str(_("Derived rating"))
    elif score is None:
        labels["addRating"] = str(_("Add rating"))
    else:
        labels["editRating"] = str(_("Edit rating"))

    csrf_token = context.get("csrf_token")
    if not csrf_token and context.get("request"):
        csrf_token = context["request"].META.get("CSRF_COOKIE", "")

    props = {
        "postUrl": str(post_url),
        "csrfToken": str(csrf_token or ""),
        "scaleMax": scale_max,
        "value": float(display) if display is not None else None,
        "displayValue": display_value,
        "cardDisplayValue": card_display_value,
        "derived": derived,
        "labels": labels,
    }
    if layout:
        props["layout"] = layout
    return render_to_string("app/components/rating_chip.html", {
        "props": props,
        "props_id": f"rating-chip-{uuid4().hex}",
    })


@register.simple_tag
def artwork_picker_props(uid, title, current, save_url, candidates_url, next_url):
    """Build the shared cover picker contract; the server form stays the fallback."""
    from django.utils.translation import gettext as _

    from app.templatetags.app_tags import image_url

    labels = {
        "choose": str(_("choose an image")),
        "drag": str(_("drag and drop")),
        "urlToggle": str(_("enter a URL")),
        "urlPlaceholder": str(_("Link to an image, or to a book or comic page")),
        "urlLabel": str(_("Image or page link")),
        "current": str(_("Current")),
        "new": str(_("New")),
        "save": str(_("Save Changes")),
        "cancel": str(_("Cancel")),
        "retry": str(_("Try again")),
        "candidatesEmpty": str(_("No other covers found.")),
        "candidatesError": str(_("Other covers could not load.")),
        "saveError": str(_("Could not save that cover.")),
    }
    return {
        "formId": f"{uid}-form",
        "fallbackId": f"{uid}-fallback",
        "title": "",
        "current": {"uri": str(image_url(current) or "")},
        "saveUrl": str(save_url),
        "candidatesUrl": str(candidates_url),
        "nextUrl": str(next_url or ""),
        "accepted": str(_("JPEG, PNG or WebP, up to 10 MB")),
        "labels": labels,
    }


@register.simple_tag
def edition_picker_props(
    uid,
    picker_type,
    description,
    provider,
    placeholder,
    list_url,
    selection_url,
    return_url,
    selected=None,
    selected_id="",
):
    """Build the shared edition picker contract; the server results stay the fallback."""
    from django.utils.translation import gettext as _

    from app.templatetags.app_tags import image_url

    def meta_for(edition):
        if not edition:
            return []
        if picker_type == "music":
            meta = []
            first = " · ".join(
                str(value)
                for value in (
                    edition.get("release_date") or _("Date unknown"),
                    edition.get("country"),
                    edition.get("status"),
                )
                if value
            )
            if first:
                meta.append(first)
            second = " · ".join(
                str(value)
                for value in (
                    edition.get("format") or _("Format unknown"),
                    edition.get("packaging"),
                    edition.get("label"),
                    (
                        _("%(count)s tracks") % {"count": edition.get("track_count")}
                        if edition.get("track_count")
                        else ""
                    ),
                )
                if value
            )
            if second:
                meta.append(second)
            if edition.get("barcode"):
                meta.append(_("Barcode: %(code)s") % {"code": edition["barcode"]})
            return meta
        line = " · ".join(
            str(value)
            for value in (edition.get("language"), edition.get("format"))
            if value
        )
        return [line] if line else []

    selected_row = None
    if selected:
        identifier = selected.get("release_id") if picker_type == "music" else selected.get("id")
        selected_row = {
            "id": identifier,
            "title": str(selected.get("title") or ""),
            "uri": str(image_url(selected.get("image")) or ""),
            "meta": meta_for(selected),
        }
    labels = {
        "selectedEdition": str(_("Selected")),
        "editionsLoading": str(_("Looking for editions…")),
        "editionsError": str(_("Editions could not load.")),
        "noEditions": str(_("No editions found.")),
        "noEditionsQuery": str(_("No editions match this search.")),
        "retry": str(_("Try again")),
    }
    return {
        "formId": f"{uid}-form",
        "fallbackId": f"{uid}-fallback",
        "title": "",
        "description": str(description or ""),
        "provider": str(provider or ""),
        "placeholder": str(placeholder or ""),
        "pickerType": picker_type,
        "listUrl": str(list_url),
        "selectionUrl": str(selection_url or ""),
        "returnUrl": str(return_url or ""),
        "selected": selected_row,
        "selectedId": str(selected_id or ""),
        "labels": labels,
    }


@register.simple_tag
def spinner(size=16, tone="accent"):
    """Render the shared React loading ring; the server markup is the fallback."""
    return render_to_string("users/components/spinner.html", {
        "props": {
            "size": size if isinstance(size, int) and SPINNER_SIZES[0] <= size <= SPINNER_SIZES[1] else 16,
            "tone": tone if tone in {"accent", "muted", "white"} else "accent",
        },
        "props_id": f"spinner-{uuid4().hex}",
    })


@register.simple_tag
def badge(label, size="sm", tone="outline", href="", nowrap=False):
    """Render the shared React badge; the server markup is the fallback."""
    return render_to_string("users/components/badge.html", {
        "props": {
            "label": str(label),
            "size": size if size in BADGE_SIZES else "sm",
            "tone": tone if tone in BADGE_TONES else "outline",
            "href": str(href) if str(href).startswith(("/", "?")) else "",
            "nowrap": bool(nowrap),
        },
        "props_id": f"badge-{uuid4().hex}",
    })


def _button_attrs(attrs):
    """Keep the attributes a button may carry; names use `_` where HTML uses `-`."""
    kept = {}
    for raw_name, value in attrs.items():
        name = raw_name.replace("_", "-") if raw_name.startswith(("hx_", "data_", "aria_")) else raw_name
        # hx-on* attributes run inline script, so they stay out of the adapter.
        if name in BUTTON_ATTRS or (name.startswith(BUTTON_ATTR_PREFIXES) and not name.startswith("hx-on")):
            kept[name] = str(value)
    return kept


@register.simple_tag
def progress_changer(media, csrf_token=""):
    """Render the shared quick progress controls with a working server fallback."""
    from app.media_card_model import progress_changer_model

    return render_to_string("users/components/progress_changer.html", {
        "props": progress_changer_model(media, csrf_token),
        "props_id": f"progress-changer-{uuid4().hex}",
    })


@register.simple_tag
def button(label, tone="primary", size="sm", width="auto", *, submit=False, disabled=False, **attrs):
    """Render the shared React button; the server markup is the fallback.

    `label` is text only. `attrs` take id, name, value, form, formaction, title and
    hx_*, data_*, aria_* (written with `_`). A button that htmx swaps in place of itself,
    or one that carries Alpine bindings, stays hand-written.
    """
    tone = tone if tone in BUTTON_TONES else "primary"
    small = size != "base" or tone == "ghost-danger"
    auto = width != "full"
    classes = "ui-button ui-button-flat"
    classes += "" if tone == "primary" else f" ui-button-tone-{tone}"
    classes += (" ui-button-small" if small else "") + (" ui-button-auto" if auto else "")
    return render_to_string("users/components/button.html", {
        "props": {
            "label": str(label),
            "tone": None if tone == "primary" else tone,
            "small": small,
            "auto": auto,
            "type": "submit" if submit else "button",
            "disabled": bool(disabled),
            "attrs": _button_attrs(attrs),
        },
        "classes": classes,
        "props_id": f"button-{uuid4().hex}",
    })


def _extra_value(extra, name):
    """Read a suggestion extra that may be a mapping or a namespace."""
    if isinstance(extra, dict):
        return extra.get(name, "")
    return getattr(extra, name, "")


@register.simple_tag(takes_context=True)
def _date_time_picker_values(context):
    """Build the shared date/time picker template values for either renderer.

    Dynamic suggestion expressions stay on the legacy Alpine include; static
    consumer configurations are handed to the React presentation explicitly.
    """
    from django.utils.translation import get_language, pgettext
    from django.utils.translation import gettext as _

    field_label = str(context.get("field_label") or _("Date"))
    mobile_label = str(context.get("mobile_label") or "")
    copy_from = str(context.get("copy_from") or "")
    copy_label = (
        str(context.get("copy_from_label") or copy_from)
        if copy_from
        else ""
    )
    track_time = bool(context.get("track_time"))
    use_12_hour = bool(context.get("use_12_hour"))

    raw_suggestion_label = str(context.get("suggestion_label") or "")
    if raw_suggestion_label == "Release Date":
        suggestion_label = str(pgettext("date picker shortcut", "Release Date"))
    elif raw_suggestion_label:
        suggestion_label = str(_(raw_suggestion_label))
    else:
        suggestion_label = ""

    mode = context.get("quick_action_mode")
    quick_actions = []
    if mode != "end":
        quick_actions.append({"id": "start-now", "label": str(pgettext("date picker shortcut", "Start Now"))})
    if mode != "start":
        quick_actions.append({"id": "just-finished", "label": str(pgettext("date picker shortcut", "Just Finished"))})
    if mode != "end" or context.get("end_release_shortcut"):
        quick_actions.append({
            "id": "release-date",
            "label": str(pgettext("date picker shortcut", "Release Date")),
            "requiresSuggestion": True,
        })

    extras = []
    for extra in context.get("suggestion_extras") or []:
        date = str(_extra_value(extra, "date") or "")
        label = str(_extra_value(extra, "label") or "")
        if date and label:
            extras.append({"date": date, "label": str(_(label))})

    copy_empty_message = ""
    if copy_from:
        copy_empty_message = str(_("%(label)s is empty") % {"label": copy_label})

    field_name = str(context.get("field_name") or "")
    field_value = str(context.get("field_value") or "")
    props = {
        "fieldName": field_name,
        "fieldId": str(context.get("field_id") or ""),
        "initialValue": field_value,
        "trackTime": track_time,
        "use12Hour": use_12_hour,
        "weekStart": str(context.get("week_start") or "monday"),
        "locale": str(get_language() or ""),
        "defaultNow": bool(context.get("default_now")),
        "suggestionDate": str(context.get("suggestion_date") or ""),
        "suggestionLabel": suggestion_label,
        "suggestionRuntimeMinutes": str(context.get("suggestion_runtime_minutes") or ""),
        "runtimeMinutes": str(context.get("runtime_minutes") or ""),
        "copyFrom": copy_from,
        "copyLabel": copy_label,
        "copyEmptyMessage": copy_empty_message,
        "quickActions": quick_actions,
        "suggestionExtras": extras,
        "labels": {
            "field": field_label,
            "mobileLabel": mobile_label,
            "notSet": str(_("Not set")),
            "openPicker": str(_("Open %(label)s picker") % {"label": field_label}),
            "picker": str(_("%(label)s picker") % {"label": field_label}),
            "quickActions": str(_("%(label)s quick actions") % {"label": field_label}),
            "clear": str(_("Clear date")),
            "close": str(_("Close date picker")),
            "previousMonth": str(_("Previous month")),
            "nextMonth": str(_("Next month")),
            "selectMonth": str(_("Select month")),
            "previousYear": str(_("Previous year")),
            "nextYear": str(_("Next year")),
            "selectYear": str(_("Select year")),
            "previousYears": str(_("Previous years")),
            "nextYears": str(_("Next years")),
            "year": str(_("Year")),
            "back": str(_("Back")),
            "time": str(_("Time")),
            "now": str(_("Now")),
            "none": str(_("None")),
            "copy": str(_("Copy from %(label)s") % {"label": copy_label}) if copy_from else "",
            "suggestedDate": str(_("Suggested date")),
            "hour": str(_("Hour")),
            "minute": str(_("Minute")),
            "second": str(_("Second")),
            "earlierHour": str(_("Earlier hour")),
            "laterHour": str(_("Later hour")),
            "earlierMinute": str(_("Earlier minute")),
            "laterMinute": str(_("Later minute")),
            "earlierSecond": str(_("Earlier second")),
            "laterSecond": str(_("Later second")),
            "activity": str(_("View activity for")),
        },
    }
    return {
        "props": props,
        "props_id": f"date-time-picker-{uuid4().hex}",
        "field_name": field_name,
        "field_id": props["fieldId"],
        "field_value": field_value,
        "track_time": track_time,
    }


@register.simple_tag(takes_context=True)
def date_time_picker(context):
    """Render the shared React date/time field with the existing hidden value."""
    return render_to_string(
        "app/components/date_time_picker_react.html",
        _date_time_picker_values(context),
    )


@register.simple_tag(takes_context=True)
def date_time_picker_props(context):
    """Render the picker's hidden value and props without a React mount host.

    The track modal keeps the release date-picker markup; this tag supplies the
    same serialized contract the React picker would receive.
    """
    values = _date_time_picker_values(context)
    # The release markup already renders the quick-action buttons; keep those
    # labels out of the serialized contract so the page does not duplicate them.
    values["props"]["suggestionLabel"] = ""
    return render_to_string(
        "app/components/date_time_picker_props.html",
        values,
    )


@register.simple_tag(takes_context=True)
def detail_composition(context, part="synopsis"):
    """Render the shared detail header or synopsis with a server fallback."""
    from html import unescape

    from django.utils.html import strip_tags
    from django.utils.translation import gettext as _
    from django.utils.translation import ngettext

    from app.models import MediaTypes
    from app.templatetags.app_tags import (
        alternative_title,
        display_title,
        image_url,
        release_year,
    )

    media = context.get("media")
    if not isinstance(media, dict):
        return None
    media_type = context.get("media_type") or media.get("media_type")
    user = context.get("user")
    public_view = bool(context.get("public_view"))
    details = media.get("details") if isinstance(media.get("details"), dict) else {}
    title = display_title(media, user)
    alt_title = alternative_title(media, user)
    facts = []
    year = release_year(details)
    if year:
        facts.append({"label": str(_("Year")), "value": str(year)})
    runtime = details.get("runtime")
    if runtime:
        facts.append({"label": str(_("Runtime")), "value": str(runtime)})
    episodes = details.get("episodes")
    if isinstance(episodes, (list, tuple)):
        episodes = len(episodes)
    if isinstance(episodes, int) and not isinstance(episodes, bool) and episodes > 0:
        facts.append({
            "label": str(ngettext("Episode", "Episodes", episodes)),
            "value": str(episodes),
        })
    status = details.get("status")
    if status:
        facts.append({"label": str(_("Status")), "value": str(status)})
    subtitle = ""
    if media_type == MediaTypes.SEASON.value:
        subtitle = str(media.get("season_header_title") or "")
    elif media.get("series_name"):
        subtitle = str(media["series_name"])
    props = {
        "part": part,
        "title": title,
        "altTitle": alt_title or "",
        "subtitle": subtitle,
        "artwork": {
            "src": image_url(media.get("image")) or "",
            "alt": title,
            "aspect": "poster",
        },
        "facts": facts,
        "synopsis": {
            "text": unescape(strip_tags(str(media.get("synopsis") or ""))).strip(),
            "previewLines": 3,
        },
        "publicView": public_view,
        "readOnly": public_view,
        "labels": {
            "altTitle": str(_("Show alternative title")),
            "publicView": str(_("Public view")),
            "readMore": str(_("Read more")),
            "showLess": str(_("Show less")),
            "noSynopsis": str(_("No synopsis available.")),
        },
        "actions": None,
        "sections": [],
    }
    return render_to_string("app/components/detail_composition.html", {
        "props": props,
        "props_id": f"detail-composition-{uuid4().hex}",
    })


@register.simple_tag(takes_context=True)
def detail_related(context):
    """Render the related-media sections and Steam DLCs with a server fallback."""
    from django.utils.translation import gettext as _

    from app.card_surfaces import card_context
    from app.templatetags.app_tags import related_section_label

    media = context.get("media") or {}
    page_context = context.flatten()
    sections = []
    for name, related_items in (media.get("related") or {}).items():
        if name in {"seasons", "recommendations"} or not related_items:
            continue
        items = []
        for index, result in enumerate(related_items):
            item = result.get("item") if isinstance(result, dict) else result
            if not item:
                continue
            title = item.get("season_header_title") or item.get("title") or ""
            card = card_context(page_context, "related", {
                "item": item,
                "media": result.get("media") if isinstance(result, dict) else None,
                "title": title,
            })
            model = (card.get("react_props") or {}).get("model")
            if model:
                items.append({
                    "key": model.get("cardKey") or f"{name}-{index}",
                    "active": bool(item.get("media_id")) and str(item.get("media_id")) == str(media.get("media_id")),
                    "model": model,
                })
        if items:
            sections.append({
                "id": name,
                "label": str(_(related_section_label(name))),
                "items": items,
            })

    steam_dlcs = [
        {
            "title": str(dlc.get("title") or ""),
            "image": str(dlc.get("image") or ""),
            "url": str(dlc.get("url") or ""),
        }
        for dlc in (media.get("steam_dlcs") or [])
        if dlc.get("title") and dlc.get("url")
    ]
    labels = {
        "showMore": str(_("Show more")),
        "showLess": str(_("Show less")),
        "dlcs": str(_("DLCs")),
        "empty": str(_("No related media available.")),
        "loading": str(_("Loading related media…")),
        "altTitle": str(_("Show alternative title")),
        "nextEpisode": str(_("Jump to next episode")),
        "noStatus": str(_("No Status")),
        "rate": str(_("Rate")),
        "close": str(_("Close")),
    }
    if not sections and not steam_dlcs:
        return ""
    props = {"model": {"sections": sections, "steamDlcs": steam_dlcs}, "labels": labels}
    return render_to_string("app/components/detail_related.html", {
        "props": props,
        "props_id": f"detail-related-{uuid4().hex}",
        "sections": sections,
        "steam_dlcs": steam_dlcs,
    }, request=context.get("request"))


def _people_plain_text(value):
    """Turn provider-authored HTML into the plain text the shared hero renders."""
    import re
    from html import unescape

    from django.utils.html import strip_tags

    with_breaks = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    return unescape(strip_tags(with_breaks)).strip()


def _people_labels():
    """Keep hero copy localized on the server while the shared view stays prop-driven."""
    from django.utils.translation import gettext as _

    return {
        "showMore": str(_("Show more")),
        "showLess": str(_("Show less")),
        "viewReads": str(_("View Reads in History")),
        "viewPlays": str(_("View Plays in History")),
        "visitWebsite": str(_("Visit Website")),
        "source": str(_("Source")),
        "born": str(_("Born")),
        "died": str(_("Died")),
        "placeOfBirth": str(_("Place of Birth")),
        "founded": str(_("Founded")),
        "titles": str(_("Titles")),
        "developed": str(_("Developed")),
        "published": str(_("Published")),
        "overview": str(_("Overview")),
        "playTrailer": str(_("Play trailer")),
        "viewFullSize": str(_("View full size")),
        "fitImage": str(_("Fit image")),
        "fillFrame": str(_("Fill frame")),
        "previous": str(_("Previous")),
        "next": str(_("Next")),
        "close": str(_("Close")),
        "timeToBeat": str(_("Time to Beat")),
        "submissions": str(_("submissions")),
        "timeToBeatPending": str(_("Fetching cached time-to-beat data in the background.")),
        "timeToBeatEmpty": str(_("Time-to-beat data is not available yet.")),
        "watchedOnYouTube": str(_("Watched on YouTube")),
        "started": str(_("Started")),
        "youtubeEstimate": str(
            _("Estimated from how far you got in each video. Your playtime is not affected."),
        ),
        "readingActivity": str(_("Reading Activity")),
        "listeningTime": str(_("Listening Time")),
        "mostRead": str(_("Most Read")),
        "topAuthors": str(_("Top Authors")),
        "noReadingData": str(_("No reading data available.")),
        "noTitles": str(_("No titles available.")),
        "watchedContent": str(_("Watched Content")),
        "readContent": str(_("Read Content")),
        "filmography": str(_("Filmography")),
        "bibliography": str(_("Bibliography")),
        "noFilmography": str(_("No filmography available.")),
        "noBibliography": str(_("No bibliography available.")),
        "noWatched": str(_("No watched titles yet.")),
        "noRead": str(_("No read titles yet.")),
    }


def _people_island(context, root, props, body):
    """Render one shared People island with the server markup as its fallback."""
    return render_to_string("app/components/people_island.html", {
        "root": root,
        "props": props,
        "props_id": f"people-{root}-{uuid4().hex}",
        "body": body,
    }, request=context.get("request"))


def _people_cast_props(context, row, source, collapse_key):
    """Shape one cast or crew row for the shared person card rail."""
    from django.conf import settings

    from app.templatetags.app_tags import image_url
    from app.templatetags.app_tags import slug as slug_value
    from users.card_metadata import (
        DISPLAY_ALWAYS,
        DISPLAY_HOVER,
        card_lines,
        resolve_profile,
        subtitle_display,
    )

    # The release person card followed the user's card settings: which lines
    # show, and whether they wait for hover or stay visible at rest.
    user = context.get("user") or getattr(context.get("request"), "user", None)
    profile_lines = resolve_profile(user, "person").get("lines") or []
    subtitle_always = subtitle_display(user, "person") == DISPLAY_ALWAYS and not any(
        line.get("display") == DISPLAY_HOVER for line in profile_lines
    )

    items = []
    for person in row.get("items") or []:
        name = person.get("name") or "Unknown Person"
        person_id = person.get("person_id")
        role = person.get("role") or ""
        department = person.get("department") or ""
        href = ""
        if person_id and source:
            href = reverse(
                "person_detail",
                kwargs={
                    "source": source,
                    "person_id": person_id,
                    "name": slug_value(name),
                },
            )
        items.append({
            "id": f"{source}:{person_id}" if person_id else name,
            "name": name,
            "subtitle": role,
            "lines": [department] if department and department != role else [],
            "cardLines": [
                {"text": line["text"], "dormant": bool(line.get("dormant"))}
                for line in card_lines(user, "person", person, None)
            ],
            "subtitleAlways": subtitle_always,
            "imageUrl": image_url(person.get("image") or settings.IMG_NONE),
            "imageFallback": image_url(settings.IMG_NONE),
            "personId": str(person_id) if person_id else "",
            "source": source or "",
            "slug": slug_value(name),
            "href": href,
        })
    view_all = None
    if row.get("view_all_url") and row.get("view_all_text"):
        view_all = {
            "label": str(row["view_all_text"]),
            "href": row["view_all_url"],
        }
    return {
        "kind": "cast",
        "title": str(row.get("title") or ""),
        "items": items,
        "viewAll": view_all,
        "collapseKey": collapse_key or "",
        "labels": _people_labels(),
    }


def _people_game_lengths_props(game_lengths, pending):
    """Shape the game length cards for the shared medium extras view."""
    from django.utils.translation import gettext as _

    if not game_lengths:
        return {
            "available": False,
            "pending": bool(pending),
            "cards": [],
        }
    cards = [
        {
            "label": str(_(card.get("label") or "")),
            "value": card.get("value") or "",
            "count": card.get("count"),
            "glyph": "◷",
        }
        for card in game_lengths.get("cards") or []
    ]
    return {
        "sourceLabel": str(_(game_lengths.get("source_label") or "")),
        "sourceUrl": game_lengths.get("source_url") or "",
        "available": bool(game_lengths.get("available")),
        "pending": bool(pending) and not game_lengths.get("available"),
        "cards": cards,
    }


def _people_youtube_props(youtube_watch):
    """Shape the YouTube watch summary for the shared medium extras view."""
    if not youtube_watch:
        return None
    return {
        "seconds": youtube_watch.get("seconds"),
        "hoursLabel": youtube_watch.get("hours_label") or "",
        "videos": youtube_watch.get("videos") or 0,
    }


def _people_section_visible(context, family, zone, section):
    """Match the server's section visibility so the shared hero keeps user layout choices."""
    from users.appearance import resolved_detail_layouts

    user = context.get("user")
    layouts = resolved_detail_layouts(
        getattr(user, "detail_page_layouts", {}) if getattr(user, "is_authenticated", False) else {},
    )
    return section in layouts.get(family, {}).get(zone, [])


def _person_hero_props(context):
    """Shape the person page's existing values into the shared hero contract."""
    from django.conf import settings
    from django.utils.translation import gettext as _
    from django.utils.translation import ngettext

    from app.templatetags.app_tags import image_url, source_readable
    from users.templatetags.user_tags import user_date_format

    person = context.get("person") or {}
    user = context.get("user")
    is_author = bool(context.get("is_author"))
    counts = []
    tracked_plays_count = context.get("tracked_plays_count")
    if tracked_plays_count is not None:
        if is_author:
            count = context.get("watched_book_count") or 0
            counts.append({
                "label": ngettext(
                    "%(value_1)s book", "%(value_1)s books", count,
                ) % {"value_1": count},
            })
        else:
            count = context.get("watched_movie_count") or 0
            counts.append({
                "label": ngettext(
                    "%(value_1)s movie", "%(value_1)s movies", count,
                ) % {"value_1": count},
            })
            count = context.get("watched_show_count") or 0
            counts.append({
                "label": ngettext(
                    "%(value_1)s show", "%(value_1)s shows", count,
                ) % {"value_1": count},
            })
        counts.append({
            "label": ngettext(
                "%(count)s tracked read" if is_author else "%(count)s tracked play",
                "%(count)s tracked reads" if is_author else "%(count)s tracked plays",
                tracked_plays_count,
            ) % {"count": tracked_plays_count},
        })
        tracked_hours_count = context.get("tracked_hours_count")
        if tracked_hours_count:
            counts.append({"label": str(tracked_hours_count)})

    return {
        "kind": "person",
        "headingLevel": 1,
        "person": {
            "name": str(person.get("name") or _("Unknown Person")),
            "imageUrl": image_url(person.get("image") or settings.IMG_NONE),
            "biography": _people_plain_text(person.get("biography")),
            "knownForDepartment": person.get("known_for_department") or "",
            "birthDate": user_date_format(person.get("birth_date"), user)
            if person.get("birth_date") else "",
            "deathDate": user_date_format(person.get("death_date"), user)
            if person.get("death_date") else "",
            "placeOfBirth": person.get("place_of_birth") or "",
            "sourceLabel": source_readable(context.get("source")),
            "sourceUrl": context.get("source_url") or "",
            "isAuthor": is_author,
        },
        "counts": counts,
        "historyUrl": context.get("history_filter_url") or "",
        "canViewHistory": True,
        "showBiography": _people_section_visible(context, "person", "hero", "biography"),
        "labels": _people_labels(),
    }


def _studio_hero_props(context):
    """Shape the studio page's existing values into the shared hero contract."""
    from django.conf import settings
    from django.utils.translation import gettext as _
    from django.utils.translation import ngettext

    from app.templatetags.app_tags import (
        image_url,
        media_type_readable,
        media_type_readable_plural,
        source_readable,
    )
    from users.templatetags.user_tags import user_date_format

    studio = context.get("studio")
    user = context.get("user")
    source = context.get("source")
    credited_media_type = context.get("credited_media_type")
    title_count = context.get("studio_games_count") or 0
    developed_count = context.get("studio_developed_count")
    published_count = context.get("studio_published_count")
    media_label = (
        media_type_readable_plural(credited_media_type)
        if credited_media_type else str(_("Titles"))
    )
    counts = []
    if credited_media_type:
        label = (
            media_type_readable(credited_media_type) if title_count == 1
            else media_type_readable_plural(credited_media_type)
        )
        counts.append({"label": f"{title_count} {label.lower()}"})
    else:
        counts.append({
            "label": ngettext(
                "%(value_1)s title", "%(value_1)s titles", title_count,
            ) % {"value_1": title_count},
        })
    if developed_count is not None:
        counts.append({"label": _("%(value_1)s developed") % {"value_1": developed_count}})
    if published_count is not None:
        counts.append({"label": _("%(value_1)s published") % {"value_1": published_count}})

    founded = context.get("studio_founded")
    return {
        "kind": "studio",
        "headingLevel": 1,
        "sourcePlacement": "facts",
        "studio": {
            "name": str(getattr(studio, "name", "") or _("Unknown Studio")),
            "logoUrl": image_url(
                (getattr(studio, "logo", "") if studio else "") or settings.IMG_NONE,
            ),
            "description": _people_plain_text(context.get("studio_description")),
            "founded": user_date_format(founded, user) if founded else "",
            "mediaLabel": media_label,
            "titleCount": title_count,
            "developedCount": developed_count,
            "publishedCount": published_count,
            "sourceLabel": source_readable(source),
            "websiteUrl": context.get("studio_source_url") or "",
        },
        "counts": counts,
        "showBiography": _people_section_visible(context, "studio", "hero", "overview"),
        "labels": _people_labels(),
    }


@register.tag
def people_hero(parser, token):
    """Wrap the server hero fallback in the shared person/studio mount."""
    _, *args = token.split_contents()
    kind = args[0].strip("\"'") if args and not args[1:] else ""
    if kind not in {"person", "studio"}:
        message = 'people_hero requires exactly "person" or "studio"'
        raise template.TemplateSyntaxError(message)
    body = parser.parse(("endpeople_hero",))
    parser.delete_first_token()
    return PeopleHeroNode(kind, body)


class PeopleHeroNode(template.Node):
    """Render shared hero props and keep the server markup as the fallback."""

    def __init__(self, kind, body):
        """Store the profile kind and its fallback body."""
        self.kind = kind
        self.body = body

    def render(self, context):
        """Render the hero island or leave the existing server markup intact."""
        props = (
            _person_hero_props(context) if self.kind == "person"
            else _studio_hero_props(context)
        )
        return render_to_string("app/components/people_hero.html", {
            "props": props,
            "props_id": f"people-hero-{uuid4().hex}",
            "body": self.body.render(context),
        })


@register.tag
def people_cast(parser, token):
    """Wrap a cast or crew row in the shared person card rail."""
    bits = token.split_contents()
    options = token_kwargs(bits[1:], parser)
    allowed = {"row", "source", "collapse_key"}
    if "row" not in options or set(options) - allowed or any("=" not in bit for bit in bits[1:]):
        message = "people_cast requires row= and optional source=/collapse_key= only"
        raise template.TemplateSyntaxError(message)
    body = parser.parse(("endpeople_cast",))
    parser.delete_first_token()
    return PeopleCastNode(options, body)


class PeopleCastNode(template.Node):
    """Render cast card props beside the existing server row."""

    def __init__(self, options, body):
        """Keep the compiled row/source values and fallback body."""
        self.options = options
        self.body = body

    def render(self, context):
        """Render the shared rail when the row has people, else the fallback."""
        row = self.options["row"].resolve(context)
        if not row or not row.get("items"):
            return self.body.render(context)
        source = (
            self.options["source"].resolve(context)
            if "source" in self.options else context.get("source")
        )
        collapse_key = (
            str(self.options["collapse_key"].resolve(context))
            if "collapse_key" in self.options else ""
        )
        props = _people_cast_props(context, row, source, collapse_key)
        # The server cards shown before React mounts read the same props, so a
        # person link is built in one place for both (#1953).
        with context.push(people_cast_cards=[
            {"person": person, "card": card}
            for person, card in zip(row.get("items") or [], props["items"], strict=True)
        ]):
            body = self.body.render(context)
        return _people_island(context, "cast", props, body)


@register.tag
def people_extras(parser, token):
    """Wrap one medium-extras section in the shared extras mount."""
    bits = token.split_contents()
    part = bits[1].strip("\"'") if len(bits) > 1 else ""
    if part not in {"game_lengths", "youtube"}:
        message = 'people_extras requires "game_lengths" or "youtube"'
        raise template.TemplateSyntaxError(message)
    body = parser.parse(("endpeople_extras",))
    parser.delete_first_token()
    return PeopleExtrasNode(part, body)


class PeopleExtrasNode(template.Node):
    """Render medium-extra props beside the existing server section."""

    def __init__(self, part, body):
        """Store which extras section this block wraps."""
        self.part = part
        self.body = body

    def render(self, context):
        """Render the shared extras view when this section has data."""
        if self.part == "game_lengths":
            if not _people_section_visible(context, "game", "content", "game_lengths"):
                return self.body.render(context)
            game_lengths = context.get("game_lengths")
            props = {
                "kind": "extras",
                "gameLengths": _people_game_lengths_props(
                    game_lengths,
                    context.get("game_lengths_pending"),
                ),
                "labels": _people_labels(),
            }
            if not game_lengths:
                return self.body.render(context)
            return _people_island(context, "extras", props, self.body.render(context))
        youtube_watch = _people_youtube_props(context.get("youtube_watch"))
        if not youtube_watch:
            return self.body.render(context)
        return _people_island(
            context,
            "extras",
            {
                "kind": "extras",
                "youtubeWatch": youtube_watch,
                "labels": _people_labels(),
            },
            self.body.render(context),
        )


def _detail_children_issue_props(context):
    """Build the shared comic-issue row model, or None for other media types."""
    from django.middleware.csrf import get_token
    from django.utils.text import slugify
    from django.utils.translation import gettext as _

    from app.models import MediaTypes, Status
    from app.templatetags.app_tags import image_url

    media = context.get("media")
    if not isinstance(media, dict):
        return None
    media_type = context.get("media_type") or media.get("media_type")
    if media_type != MediaTypes.COMIC.value:
        return None
    issues = media.get("episodes") or []
    if not issues:
        return None

    request = context.get("request")
    user = getattr(request, "user", None)
    authenticated = bool(user and user.is_authenticated)
    public_view = bool(context.get("public_view"))
    ratings_enabled = getattr(user, "ratings_enabled", False)
    scale_max = getattr(user, "rating_scale_max", 10)

    labels = {
        "seasons": str(_("Seasons")),
        "episodes": str(_("Episodes")),
        "issues": str(_("Issues")),
        "season": str(_("Season")),
        "dropped": str(_("Dropped")),
        "watched": str(_("Watched")),
        "unwatched": str(_("Unwatched")),
        "read": str(_("Read")),
        "unread": str(_("Unread")),
        "collected": str(_("Collected")),
        "notAired": str(_("Not aired yet")),
        "history": str(_("History")),
        "noHistory": str(_("No activity recorded yet.")),
        "noChildren": str(_("Nothing to show yet.")),
        "toggleOrder": str(_("Toggle row order")),
        "oldestFirst": str(_("Oldest first")),
        "newestFirst": str(_("Newest first")),
        "showAllThumbnails": str(_("Show all thumbnails")),
        "hideUnseenThumbnails": str(_("Hide unseen thumbnails")),
        "revealThumbnail": str(_("Reveal thumbnail")),
        "hiddenThumbnail": str(_("Thumbnail hidden")),
        "rate": str(_("Rate")),
        "close": str(_("Close")),
        "track": str(_("Track")),
        "lists": str(_("Lists")),
        "collection": str(_("Collection")),
    }

    rows = []
    for issue in issues:
        media_id = str(issue.get("media_id") or "")
        history = issue.get("history") or []
        latest = history[0] if history else None
        score = getattr(latest, "score", None) if latest else None
        if score is not None and authenticated:
            score = user.format_score_for_display(score)
        actions = []
        if authenticated and not public_view:
            actions.append({"key": "track", "label": labels["track"]})
            actions.append({"key": "lists", "label": labels["lists"]})
            if history:
                actions.append({"key": "history", "label": labels["history"]})
        number = issue.get("issue_number")
        title = issue.get("title") or str(_("Unknown"))
        href = ""
        source = issue.get("source")
        if source and media_id:
            href = reverse(
                "media_details",
                kwargs={
                    "source": source,
                    "media_type": issue.get("media_type") or MediaTypes.COMIC_ISSUE.value,
                    "media_id": media_id,
                    "title": slugify(title or str(number or media_id)),
                },
            )
        rows.append({
            "key": media_id,
            "number": number,
            "code": str(_("Issue #%(number)s") % {"number": number}) if number else "",
            "title": title,
            "dateLabel": issue.get("cover_date") or "",
            "image": {"src": image_url(issue.get("image") or ""), "fallback": ""},
            "overview": "",
            "watched": bool(history),
            "dropped": bool(latest and getattr(latest, "status", "") == Status.DROPPED.value),
            "score": score,
            "scaleMax": scale_max,
            "canRate": bool(authenticated and not public_view and ratings_enabled and history),
            "rateLabel": labels["rate"],
            "rateUrl": (
                reverse("update_media_score", args=[MediaTypes.COMIC_ISSUE.value, latest.id])
                if authenticated and not public_view and ratings_enabled and latest
                else ""
            ),
            "href": href,
            "obfuscated": False,
            "unreleased": False,
            "actions": actions,
        })

    props = {
        "model": {"kind": "comic", "issues": rows, "obfuscation": {}},
        "labels": labels,
        "csrfToken": get_token(request) if request is not None else "",
    }
    return {"props": props, "props_id": f"detail-children-{uuid4().hex}"}


@register.simple_tag(takes_context=True)
def detail_children_props(context):
    """Return the shared child-row model for a surface that has one."""
    return _detail_children_issue_props(context)


@register.simple_tag(takes_context=True)
def settings_view_react(context):
    """Return the shared settings navigation/page model for the browser root."""
    from users.settings_nav import settings_view_model

    request = context.get("request")
    if request is None:
        return {"id": "settings-view-props", "props": None}
    return {
        "id": f"settings-view-{uuid4().hex}",
        "props": settings_view_model(request),
    }
