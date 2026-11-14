"""Build presentation state for the shared filter bar.

The server still owns URL parsing, normalization and query execution. This
module only turns the menu data a view already computed into the options and
normalized state the universal FilterBar renders.
"""

from django.utils.translation import gettext as _

DEFAULT_RELATIVE_UNITS = [
    ("days", _("Days")),
    ("weeks", _("Weeks")),
    ("months", _("Months")),
    ("years", _("Years")),
]

CLEAR_ALL_VALUES = {
    "rating": "all",
    "collection": "all",
    "progress": "all",
    "release": "all",
    "groupBy": "item",
}


def _options(values):
    """Normalize string options or {value,label} mappings."""
    result = []
    for value in values or []:
        if isinstance(value, dict):
            if "value" not in value:
                continue
            result.append({
                "value": str(value["value"]),
                "label": str(value.get("label", value["value"])),
            })
        elif isinstance(value, (list, tuple)):
            if not value:
                continue
            result.append({"value": str(value[0]), "label": str(value[-1])})
        elif value is not None and str(value) != "":
            result.append({"value": str(value), "label": str(value)})
    return result


def _translate_options(values):
    return [
        {"value": option["value"], "label": str(_(option["label"]))}
        for option in _options(values)
    ]


def _state_list(state, *keys):
    for key in keys:
        value = state.get(key)
        if value:
            return [str(item) for item in value]
    return []


def _section(key, label, state_key, options, *, kind="single", searchable=True,
             clear_value="", modes=None, mode_key="", mode_value=""):
    section = {
        "key": key,
        "label": str(label),
        "state": state_key,
        "type": kind,
        "options": options,
        "clearValue": clear_value,
        "searchable": bool(searchable),
    }
    if modes:
        section["modes"] = modes
        section["modeState"] = mode_key
        section["modeValue"] = mode_value
    return section


def _has_any(filter_data, key):
    return bool(filter_data.get(key))


def _section_options(platforms, *, multi):
    if multi:
        return _options(platforms)
    return _options(platforms)


def _sections(context, filter_data, state, *, platform_multi):
    sections = []
    show = filter_data.get
    if show("show_group_by") and context.get("group_by_choices"):
        sections.append(_section(
            "groupBy", _("Group By"), "group_by",
            _translate_options(context["group_by_choices"]),
            clear_value="item",
        ))
    if show("lists"):
        sections.append(_section(
            "lists", _("List"), "selectedLists",
            [{"value": str(item["id"]), "label": str(item["label"])}
             for item in filter_data["lists"] if "id" in item],
            kind="multi",
        ))
    rating_options = _translate_options(
        context.get("rating_choices")
        or [("all", _("All")), ("rated", _("Rated")), ("not_rated", _("Not Rated"))],
    )
    sections.append(_section(
        "ratingMode", _("Rating"), "rating", rating_options, clear_value="all",
    ))
    sections.append(_section(
        "collection", _("Collection"), "collection",
        [{"value": "all", "label": _("All")},
         {"value": "collected", "label": _("Collected")},
         {"value": "not_collected", "label": _("Not Collected")}],
        clear_value="all",
    ))
    if show("show_progress"):
        sections.append(_section(
            "progress", _("Progress"), "progress",
            [{"value": "all", "label": _("Any")},
             {"value": "not_caught_up", "label": _("Not Caught Up")},
             {"value": "caught_up", "label": _("Caught Up")}],
            clear_value="all",
        ))
    if _has_any(filter_data, "genres"):
        sections.append(_section(
            "genre", _("Genre"), "genre", _options(filter_data["genres"]),
        ))
    if _has_any(filter_data, "implied_genres"):
        sections.append(_section(
            "impliedGenre", _("Implied Genre"), "implied_genre",
            _options(filter_data["implied_genres"]),
        ))
    if _has_any(filter_data, "years"):
        sections.append(_section(
            "year", _("Year"), "year", _options(filter_data["years"]),
        ))
    sections.append(_section(
        "release", _("Released"), "release",
        [{"value": "all", "label": _("Any")},
         {"value": "released", "label": _("Released")},
         {"value": "not_released", "label": _("Not Released")}],
        clear_value="all",
    ))
    if _has_any(filter_data, "sources"):
        sections.append(_section(
            "source", _("Source"), "source", _options(filter_data["sources"]),
        ))
    if _has_any(filter_data, "media_statuses"):
        sections.append(_section(
            "mediaStatus", _("Availability"), "media_status",
            _options(filter_data["media_statuses"]),
        ))
    if show("show_languages") and _has_any(filter_data, "languages"):
        sections.append(_section(
            "language", _("Language"), "language", _options(filter_data["languages"]),
        ))
    if show("show_countries") and _has_any(filter_data, "countries"):
        sections.append(_section(
            "country", _("Country"), "country", _options(filter_data["countries"]),
        ))
    if show("show_platforms") and _has_any(filter_data, "platforms"):
        modes = None
        mode_key = ""
        mode_value = ""
        if platform_multi:
            modes = [{"value": "or", "label": _("Any")},
                     {"value": "and", "label": _("All")},
                     {"value": "not", "label": _("Not")}]
            mode_key = "platformMode"
            mode_value = state.get("platform_mode", "or")
        sections.append(_section(
            "platforms" if platform_multi else "platform",
            _("Platform"),
            "selectedPlatforms" if platform_multi else "platform",
            _section_options(filter_data["platforms"], multi=platform_multi),
            kind="multi" if platform_multi else "single",
            modes=modes,
            mode_key=mode_key,
            mode_value=mode_value,
        ))
    if show("show_origins") and _has_any(filter_data, "origins"):
        sections.append(_section(
            "origin", _("Origin"), "origin", _options(filter_data["origins"]),
        ))
    if show("show_formats") and _has_any(filter_data, "formats"):
        sections.append(_section(
            "format", _("Format"), "format", _options(filter_data["formats"]),
        ))
    if show("show_authors") and _has_any(filter_data, "authors"):
        sections.append(_section(
            "author", _("Author"), "author", _options(filter_data["authors"]),
            searchable=True,
        ))
    if show("show_providers"):
        provider_options = _options(filter_data.get("providers"))
        seen = {option["value"] for option in provider_options}
        for option in _options(filter_data.get("all_providers")):
            if option["value"] not in seen:
                provider_options.append(option)
                seen.add(option["value"])
        if provider_options:
            sections.append(_section(
                "provider", _("Provider"), "provider", provider_options,
            ))
    if _has_any(filter_data, "departments"):
        sections.append(_section(
            "department", _("Department"), "department",
            _options(filter_data["departments"]),
        ))
    if _has_any(filter_data, "tags"):
        sections.append(_section(
            "tags", _("Tags"), "selectedTags", _options(filter_data["tags"]),
            kind="multi",
            modes=[{"value": "or", "label": _("Any")},
                   {"value": "and", "label": _("All")},
                   {"value": "not", "label": _("Not")}],
            mode_key="tagMode",
            mode_value=state.get("tag_mode", "or"),
        ))
    return sections


def _range_options(filter_data, relative_units):
    return [
        {"key": "rating", "label": _("Rating"), "type": "number",
         "placeholder": "0-10"},
        {"key": "releaseDate", "label": _("Release date"), "type": "date",
         "placeholder": "YYYY-MM-DD", "relative": True, "units": relative_units},
        {"key": "dateAdded", "label": _("Date added"), "type": "date",
         "placeholder": "YYYY-MM-DD", "relative": True, "units": relative_units},
        {"key": "completedDate", "label": _("Completed date"), "type": "date",
         "placeholder": "YYYY-MM-DD", "relative": True, "units": relative_units},
    ]


def _range_state(state):
    ranges = {}
    for key, prefix in (
        ("rating", "rating"),
        ("releaseDate", "release_date"),
        ("dateAdded", "date_added"),
        ("completedDate", "completed_date"),
    ):
        if key == "rating":
            ranges[key] = {
                "from": state.get("rating_min", ""),
                "to": state.get("rating_max", ""),
                "within": "",
                "unit": "days",
            }
            continue
        ranges[key] = {
            "from": state.get(f"{prefix}_from", ""),
            "to": state.get(f"{prefix}_to", ""),
            "within": state.get(f"{prefix}_within", ""),
            "unit": state.get(f"{prefix}_within_unit", "days"),
        }
    return ranges


def _field_state(state, sections):
    fields = {}
    modes = {}
    for section in sections:
        if section["type"] == "multi":
            fields[section["key"]] = _state_list(state, section["state"])
        else:
            fields[section["key"]] = str(state.get(section["state"], "") or "")
        if section.get("modeState"):
            modes[section["key"]] = state.get(section["modeState"], section.get("modeValue", ""))
    return fields, modes


def build_filter_bar_props(context, *, endpoint, target, form_id, surface,
                           ranges=False, platform_multi=False,
                           media_types=None, views=None, saved_views=None,
                           state=None):
    """Return the JSON props for the universal FilterBar."""
    filter_data = context.get("filter_data") or context.get("list_filter_data") or {}
    state = state or context.get("media_list_filter_state") \
        or context.get("list_filter_state") or {}
    if not isinstance(state, dict):
        state = {}
    status_choices = [
        (value, label) for value, label in (context.get("status_choices") or [])
        if str(value).lower() != "all"
    ]
    sort_choices = context.get("sort_choices") or []
    if not context.get("supports_critic_rating_sort"):
        sort_choices = [(value, label) for value, label in sort_choices
                        if value != "critic_rating"]
    if not filter_data.get("show_authors"):
        sort_choices = [(value, label) for value, label in sort_choices
                        if value != "author"]

    sections = _sections(context, filter_data, state, platform_multi=platform_multi)
    fields, modes = _field_state(state, sections)
    relative_units = _options(
        filter_data.get("relative_date_units") or DEFAULT_RELATIVE_UNITS,
    )
    range_options = _range_options(filter_data, relative_units) if ranges else []

    media_type_options = []
    for item in media_types or []:
        value = item.get("value") if isinstance(item, dict) else item[0]
        label = item.get("label") if isinstance(item, dict) else item[-1]
        media_type_options.append({"value": str(value), "label": str(_(label))})

    view_options = []
    for item in views or []:
        value = item.get("value") if isinstance(item, dict) else item[0]
        label = item.get("label") if isinstance(item, dict) else item[-1]
        view_options.append({"value": str(value), "label": str(_(label))})

    saved_view_options = [
        {
            "id": str(item.pk),
            "label": str(item.name),
            "url": str(item.get_absolute_url()),
        }
        for item in saved_views or []
    ]

    filters = {
        "mediaTypes": _state_list(state, "media_types", "mediaTypes"),
        "statuses": _state_list(state, "status", "statuses"),
        "sort": str(state.get("sort") or (sort_choices[0][0] if sort_choices else "")),
        "direction": str(state.get("direction") or "desc"),
        "view": str(state.get("view") or (view_options[0]["value"] if view_options else "")),
        "ranges": _range_state(state),
        "fields": fields,
        "modes": modes,
    }
    if context.get("current_sort"):
        filters["sort"] = str(context["current_sort"])
    if context.get("current_direction"):
        filters["direction"] = str(context["current_direction"])
    if context.get("current_layout"):
        filters["view"] = str(context["current_layout"])
    if context.get("current_group_by"):
        filters["fields"]["groupBy"] = str(context["current_group_by"])

    labels = {
        "filters": str(_("Filters")),
        "allStatuses": str(_("Status")),
        "statuses": str(_("Statuses")),
        "mediaType": str(_("Media type")),
        "date": str(_("Date")),
        "addedDate": str(_("Added date")),
        "completedDate": str(_("Completed date")),
        "releaseDate": str(_("Release date")),
        "releaseStatus": str(_("Release status")),
        "startDate": str(_("Start date")),
        "airDate": str(_("Air date")),
        "status": str(_("Status")),
        "sort": str(_("Sort")),
        "view": str(_("View")),
        "range": str(_("Range")),
        "savedViews": str(_("Saved views")),
        "clear": str(_("Clear")),
        "clearAll": str(_("Clear all filters")),
        "apply": str(_("Apply")),
        "close": str(_("Close")),
        "any": str(_("Any")),
        "all": str(_("All")),
        "searchOptions": str(_("Search options")),
        "noOptions": str(_("No options found")),
        "from": str(_("From")),
        "to": str(_("To")),
        "within": str(_("In the last")),
        "direction": str(_("Direction")),
        "ascending": str(_("ascending")),
        "descending": str(_("descending")),
    }

    return {
        "endpoint": str(endpoint or ""),
        "target": str(target or ""),
        "formId": str(form_id or "filter-form"),
        "surface": surface,
        "options": {
            "mediaTypes": media_type_options,
            "statuses": _translate_options(status_choices),
            "sorts": _translate_options(sort_choices),
            "views": view_options,
            "savedViews": saved_view_options,
            "ranges": range_options,
            "sections": sections,
        },
        "filters": filters,
        "labels": labels,
    }
