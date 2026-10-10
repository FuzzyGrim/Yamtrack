"""Keyless stage work discovery using Wikidata's public Action API."""

import re

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app import stage as stage_identity
from app.models import MediaTypes, Sources, StageForms
from app.providers import commons, services, wikipedia

BASE_URL = "https://www.wikidata.org/w/api.php"
FORM_IDS = {
    "Q25379": "play",
    "Q2743": "musical",
    "Q1344": "opera",
    "Q15079786": "ballet",
    "Q785522": "opera",
    "Q918727": "other",
    "Q13409536": "other",
}
WORK_IDS = [*FORM_IDS, "Q58483083", "Q58483088", "Q116476516", "Q7725634"]
STAGE_WORK_IDS = set(WORK_IDS) - {"Q7725634"}
GENRE_FORMS = {**FORM_IDS, "Q41425": "ballet", "Q123578591": "ballet"}
EXCLUDED_TYPES = {
    "Q5",
    "Q11424",
    "Q5398426",
    "Q4167410",
    "Q4167836",
    "Q7777570",
    "Q43099500",
    "Q35140",
    "Q2635894",
    "Q109349450",
    "Q356055",
    "Q7697093",
}
CREATOR_ROLES = {
    "P50": "Authors",
    "P86": "Composers",
    "P87": "Librettists",
    "P676": "Lyricists",
    "P1809": "Choreographers",
}
PAGE_SIZE = 20
MAX_BATCHES = 3
CLASSIFICATION_VERSION = 8
CLASS_DEPTH_LIMIT = 3
CLASS_COUNT_LIMIT = 50
LABEL_VERSION = 1
LABEL_BATCH_LIMIT = 50
TYPE_PROPERTIES = ("P31", "P7937", "P136")
TYPE_ANCHORS = set(WORK_IDS) | set(GENRE_FORMS) | EXCLUDED_TYPES
SCREEN_TYPES = {"Q11424", "Q5398426"}


def request_data(params):
    """Fetch one bounded request and surface provider back-pressure."""
    try:
        data = services.api_request(
            Sources.WIKIDATA.value,
            "GET",
            BASE_URL,
            params={"format": "json", **params},
            headers={"User-Agent": "Yamtrack (https://github.com/FuzzyGrim/Yamtrack)"},
        )
    except requests.RequestException as error:
        raise services.ProviderAPIError(Sources.WIKIDATA.value, error) from error
    if not isinstance(data, dict) or "error" in data:
        error = data.get("error") if isinstance(data, dict) else None
        message = (
            error.get("info", "Invalid API response")
            if isinstance(error, dict)
            else "Invalid API response"
        )
        raise services.ProviderAPIError(
            Sources.WIKIDATA.value,
            ValueError(message),
        )
    return data


def entities(identifiers, *, props="info|labels|aliases|descriptions|claims|sitelinks"):
    """Read public entities in supported batches, following provider redirects."""
    identifiers = list(dict.fromkeys(identifiers))
    result = {}
    for offset in range(0, len(identifiers), 50):
        batch = identifiers[offset : offset + 50]
        response = request_data(
            {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": props,
                "sitefilter": "|".join(
                    language + "wiki" for language in wikipedia.LANGUAGES
                ),
                "languages": "en",
                "languagefallback": 1,
                "redirects": "yes",
            }
        )
        result.update(response.get("entities", {}))
    return result


def values(entity, property_id):
    """Read non-deprecated concrete claim values."""
    return [
        claim["mainsnak"]["datavalue"]["value"]
        for claim in entity.get("claims", {}).get(property_id, [])
        if claim.get("rank") != "deprecated"
        and "datavalue" in claim.get("mainsnak", {})
    ]


def identifiers(entity, property_id):
    """Read entity references from a claim."""
    return [
        value["id"]
        for value in values(entity, property_id)
        if isinstance(value, dict) and "id" in value
    ]


def resolved_type(identifier, graph, path=()):
    """Stop at reviewed anchors; cycles and incomplete ancestry remain unresolved."""
    if identifier in TYPE_ANCHORS:
        return {identifier}
    if identifier in path or len(path) >= CLASS_DEPTH_LIMIT:
        return None
    parents = graph.get(identifier, {}).get("parents", [])
    if not parents:
        return None
    resolved = set()
    incomplete = False
    for parent in parents:
        parent_types = resolved_type(parent, graph, (*path, identifier))
        if parent_types is None:
            incomplete = True
        else:
            resolved.update(parent_types)
    if incomplete and not EXCLUDED_TYPES.intersection(resolved):
        return None
    return resolved


def classify_entities(work_entities, graph):
    """Resolve specific source classes within one shared per-search graph budget."""
    frontier = {
        identifier
        for entity in work_entities.values()
        for property_id in TYPE_PROPERTIES
        for identifier in identifiers(entity, property_id)
    }
    for _depth in range(CLASS_DEPTH_LIMIT):
        pending = sorted(frontier - TYPE_ANCHORS - graph.keys())[
            : max(0, CLASS_COUNT_LIMIT - len(graph))
        ]
        uncached = []
        for identifier in pending:
            cached = cache.get(f"wikidata_class_v{CLASSIFICATION_VERSION}_{identifier}")
            if cached is None:
                uncached.append(identifier)
            else:
                graph[identifier] = cached
        fetched = entities(uncached, props="info|claims")
        for identifier in uncached:
            entity = fetched.get(identifier, {"missing": ""})
            record = {
                "parents": identifiers(entity, "P279"),
                "revision": entity.get("lastrevid"),
            }
            graph[identifier] = record
            if "missing" not in entity and record["revision"]:
                cache.set(
                    f"wikidata_class_v{CLASSIFICATION_VERSION}_{identifier}",
                    record,
                    3600,
                )
        for identifier in pending:
            graph.setdefault(identifier, {"parents": [], "revision": None})
        frontier = {
            parent
            for identifier in frontier - TYPE_ANCHORS
            for parent in graph.get(identifier, {}).get("parents", [])
        }
    for entity in work_entities.values():
        entity["resolved_types"] = {
            property_id: [
                resolved_type(identifier, graph)
                for identifier in identifiers(entity, property_id)
            ]
            for property_id in TYPE_PROPERTIES
        }


def type_evidence(entity, property_id):
    """Return resolved anchors while retaining explicit claims on unhydrated data."""
    resolved = entity.get("resolved_types", {}).get(property_id)
    if resolved is None:
        return identifiers(entity, property_id)
    return sorted({anchor for anchors in resolved if anchors for anchor in anchors})


def has_type_conflict(entity):
    """Distinguish explicit medium conflicts from cross-medium genre ancestry."""
    direct_stage_work = STAGE_WORK_IDS.intersection(identifiers(entity, "P31"))
    for property_id in TYPE_PROPERTIES:
        direct_types = identifiers(entity, property_id)
        resolved = entity.get("resolved_types", {}).get(
            property_id, [{identifier} for identifier in direct_types]
        )
        for identifier, anchors in zip(direct_types, resolved, strict=True):
            if property_id == "P31" and identifier == "Q7777570" and direct_stage_work:
                continue
            conflicts = EXCLUDED_TYPES.intersection(anchors or ())
            if inherited_screen_genre(entity, property_id, identifier, anchors):
                conflicts -= SCREEN_TYPES
            if conflicts:
                return True
    return False


def has_direct_stage_identity(entity):
    """Require direct stage-work evidence independently of production credits."""
    direct_types = set(identifiers(entity, "P31"))
    return bool(STAGE_WORK_IDS.intersection(direct_types)) or (
        "Q7725634" in direct_types
        and bool(set(FORM_IDS).intersection(identifiers(entity, "P7937")))
    )


def inherited_screen_genre(entity, property_id, identifier, anchors):
    """Permit shared genres only alongside independent direct stage evidence."""
    if property_id == "P31" or identifier in EXCLUDED_TYPES or not anchors:
        return False
    return has_direct_stage_identity(entity) and (
        property_id == "P136" or bool(set(FORM_IDS).intersection(anchors))
    )


def forms(entity):
    """Require explicit work form evidence without guessing unresolved forms."""
    if "missing" in entity:
        return []
    if describes_non_work(entity):
        return []
    types = type_evidence(entity, "P31")
    if has_type_conflict(entity):
        return []
    if not set(WORK_IDS).intersection(types) and any(
        anchors is None for anchors in entity.get("resolved_types", {}).get("P31", [])
    ):
        return []
    if production_credits_without_work_identity(entity):
        return []
    evidence = type_evidence(entity, "P7937") + types
    classified = [FORM_IDS[value] for value in evidence if value in FORM_IDS]
    if STAGE_WORK_IDS.intersection(types):
        classified.extend(
            GENRE_FORMS[value]
            for value in type_evidence(entity, "P136")
            if value in GENRE_FORMS
        )
    named_forms = [form for form in classified if form != "other"]
    return list(dict.fromkeys(named_forms or classified))


def production_credits_without_work_identity(entity):
    """Do not mistake premiere personnel for sufficient production-only evidence."""
    has_production_credits = all(
        values(entity, prop) for prop in ("P272", "P161", "P57")
    )
    return has_production_credits and (
        not has_direct_stage_identity(entity)
        or bool(EXCLUDED_TYPES.intersection(identifiers(entity, "P31")))
    )


def describes_non_work(entity):
    """Reject explicit venue/event descriptions and corroborated staging records."""
    description = entity.get("descriptions", {}).get("en", {}).get("value", "")
    if re.match(
        r"^(?:an? |the )?(?:performing arts (?:cent(?:er|re)|venue)|"
        r"theat(?:er|re) (?:building|company)|"
        r"(?:ballet|opera|musical|theatrical) "
        r"(?:performance|production) (?:at|in|of))\b",
        description.strip(),
        re.IGNORECASE,
    ):
        return True
    return all(values(entity, prop) for prop in ("P57", "P655", "P144"))


def label(entity):
    """Read a display label, preserving the provider's language fallback."""
    labels = entity.get("labels", {})
    return labels.get("en", labels[min(labels)] if labels else {}).get(
        "value", entity.get("id", "")
    )


def artwork_candidates(entity):
    """Preserve preferred work images, then ordinary images and linked logos."""
    claims = [
        claim
        for property_id in ("P18", "P154")
        for claim in entity.get("claims", {}).get(property_id, [])
        if claim.get("rank") != "deprecated"
    ]
    claims.sort(key=lambda claim: claim.get("rank") != "preferred")
    values = [
        claim.get("mainsnak", {}).get("datavalue", {}).get("value") for claim in claims
    ]
    return list(dict.fromkeys(value for value in values if isinstance(value, str)))


def transform(entity, related):
    """Return Yamtrack metadata for a classified work."""
    work_forms = forms(entity)
    details = {"forms": ", ".join(StageForms(value).label for value in work_forms)}
    creators = []
    for property_id, role in CREATOR_ROLES.items():
        names = [
            label(related[identifier])
            for identifier in identifiers(entity, property_id)
            if identifier in related and "missing" not in related[identifier]
        ]
        if names:
            details[role] = ", ".join(names)
            creators.extend(names)
    languages = [
        label(related[identifier])
        for identifier in identifiers(entity, "P364")
        if identifier in related and "missing" not in related[identifier]
    ]
    if languages:
        details["original_language"] = ", ".join(languages)
    return {
        "media_id": entity["id"],
        "source": Sources.WIKIDATA.value,
        "source_url": f"https://www.wikidata.org/wiki/{entity['id']}",
        "media_type": MediaTypes.STAGE.value,
        "title": label(entity),
        "image": settings.IMG_NONE,
        "stage_artwork": {},
        "artwork_candidates": artwork_candidates(entity),
        "work_revision": entity.get("lastrevid"),
        "classification_version": CLASSIFICATION_VERSION,
        "label_version": LABEL_VERSION,
        "wikipedia_sitelinks": {
            language: entity["sitelinks"][language + "wiki"]["title"]
            for language in wikipedia.LANGUAGES
            if isinstance(
                entity.get("sitelinks", {}).get(language + "wiki", {}).get("title"), str
            )
        },
        "stage_forms": work_forms,
        "work_description": " / ".join([details["forms"], *dict.fromkeys(creators)]),
        "synopsis": entity.get("descriptions", {})
        .get("en", {})
        .get("value", "No synopsis available."),
        "max_progress": 1,
        "score": None,
        "score_count": None,
        "details": details,
        "related": {},
    }


def hydrate_labels(records):
    """Fill missing display labels in one optional batch without changing claims."""
    pending = {}
    for entity in records:
        if entity.get("labels") or "missing" in entity:
            continue
        identifier = entity["id"]
        cached = cache.get(f"wikidata_labels_v{LABEL_VERSION}_{identifier}")
        if cached is not None:
            entity["labels"] = cached
        else:
            pending.setdefault(identifier, []).append(entity)
    if not pending:
        return False
    requested = list(pending)[:LABEL_BATCH_LIMIT]
    try:
        response = request_data(
            {"action": "wbgetentities", "ids": "|".join(requested), "props": "labels"}
        )
    except services.ProviderAPIError:
        return True
    returned = response.get("entities", {})
    incomplete = len(pending) > LABEL_BATCH_LIMIT
    for identifier in requested:
        entity = returned.get(identifier, {}) if isinstance(returned, dict) else {}
        labels = entity.get("labels") if isinstance(entity, dict) else None
        if (
            not isinstance(entity, dict)
            or entity.get("id") != identifier
            or not isinstance(labels, dict)
        ):
            incomplete = True
            continue
        valid_labels = {
            language: entry
            for language, entry in labels.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("value"), str)
            and entry["value"].strip()
        }
        if len(valid_labels) != len(labels):
            incomplete = True
            continue
        cache.set(f"wikidata_labels_v{LABEL_VERSION}_{identifier}", valid_labels, 3600)
        for record in pending[identifier]:
            record["labels"] = valid_labels
    return incomplete


def hydrate(work_entities):
    """Hydrate labels once per batch of eligible works."""
    references = [
        identifier
        for entity in work_entities
        for property_id in [*CREATOR_ROLES, "P364"]
        for identifier in identifiers(entity, property_id)
    ]
    related = entities(references, props="labels")
    incomplete = hydrate_labels([*work_entities, *related.values()])
    return [
        {**transform(entity, related), "labels_incomplete": incomplete}
        for entity in work_entities
    ]


def illustrate(work, artwork, *, unavailable=False):
    """Attach image and credit together after work selection and pagination."""
    work = work.copy()
    work["artwork_unavailable"] = (
        artwork is None
        or unavailable
        or bool(artwork and artwork.get("lookup_incomplete"))
    )
    if artwork:
        artwork = {
            key: value for key, value in artwork.items() if key != "lookup_incomplete"
        }
    work["wikipedia_version"] = wikipedia.VERSION
    if artwork and artwork["work_id"] != work["media_id"]:
        artwork = stage_identity.retarget_artwork(artwork, work["media_id"])
    work.update(
        image=(artwork or {}).get("image", settings.IMG_NONE),
        stage_artwork=artwork or {},
    )
    work["artwork_policy"] = commons.SCHEMA
    return work


def stage(media_id):
    """Resolve a work identity and reject unsupported or ambiguous records."""
    if not re.fullmatch(r"Q[1-9][0-9]*", media_id):
        services.raise_not_found_error(Sources.WIKIDATA.value, media_id, "stage")
    media_id = stage_identity.canonical_id(media_id)
    key = f"wikidata_stage_{media_id}"
    cached = cache.get(key)
    if (
        cached is not None
        and cached.get("artwork_policy") == commons.SCHEMA
        and cached.get("classification_version") == CLASSIFICATION_VERSION
        and cached.get("label_version") == LABEL_VERSION
        and cached.get("wikipedia_version") == wikipedia.VERSION
    ):
        return cached
    entity = entities([media_id]).get(media_id, {})
    classify_entities({media_id: entity}, {})
    if not forms(entity):
        services.raise_not_found_error(
            Sources.WIKIDATA.value, media_id, "stage work with a supported form"
        )
    stage_identity.record_redirect(media_id, entity)
    terminal_id = stage_identity.canonical_id(entity["id"])
    if terminal_id != entity["id"]:
        return stage(terminal_id)
    work = hydrate([entity])[0]
    result = illustrate_page([work], wikipedia.artworks([work]))[0]
    if not any(result.get(key) for key in ("artwork_unavailable", "labels_incomplete")):
        cache.set(f"wikidata_stage_{result['media_id']}", result, 3600)
    return result


def select_search_batch(params, selected, type_graph, *, optional=False):
    """Classify one provider batch without changing result order or identity rules."""
    try:
        data = request_data(params)
        hits = data.get("query", {}).get("search", [])[:50]
        work_entities = entities([hit["title"] for hit in hits])
        classify_entities(work_entities, type_graph)
    except services.ProviderAPIError:
        if optional:
            return None
        raise
    for hit in hits:
        entity = work_entities.get(hit["title"], {})
        if forms(entity):
            stage_identity.record_redirect(hit["title"], entity)
            selected.setdefault(entity["id"], entity)
    return data.get("continue", {})


def search(query, page):
    """Filter a bounded candidate window before canonical result pagination."""
    literal = " ".join(query.split())[:200]
    key = f"wikidata_stage_search_v16_{literal}"
    cached = cache.get(key)
    if cached is None:
        escaped = literal.replace("\\", "\\\\").replace('"', '\\"')
        filters = [
            f"P31={identifier}" for identifier in WORK_IDS if identifier != "Q7725634"
        ]
        filters.extend(f"P7937={identifier}" for identifier in FORM_IDS)
        filters.extend(f"P136={identifier}" for identifier in GENRE_FORMS)
        params = {
            "action": "query",
            "list": "search",
            "srnamespace": 0,
            "srlimit": 50,
            "srprop": "",
            "srsearch": f'inlabel:"{escaped}@*" haswbstatement:' + "|".join(filters),
        }
        selected = {}
        continuation = {}
        type_graph = {}
        incomplete = False
        for _batch in range(MAX_BATCHES):
            continuation = select_search_batch(
                {**params, **continuation}, selected, type_graph
            )
            if not continuation:
                break
        if len(selected) < PAGE_SIZE and not continuation and _batch < MAX_BATCHES - 1:
            continuation = select_search_batch(
                {**params, "srsearch": f'inlabel:"{escaped}@*"'},
                selected,
                type_graph,
                optional=bool(selected),
            )
            incomplete = continuation is None
            _batch += 1
        variant = re.match(r"(?i)^the (\S.*)$", literal)
        if variant and not selected and continuation == {} and _batch < MAX_BATCHES - 1:
            shortened = variant[1].replace("\\", "\\\\").replace('"', '\\"')
            continuation = select_search_batch(
                {**params, "srsearch": f'inlabel:"{shortened}@*"'},
                selected,
                type_graph,
                optional=True,
            )
            incomplete = continuation is None
        results = hydrate(list(selected.values()))
        cached = {
            "results": results,
            "limited": incomplete
            or bool(continuation)
            or len(type_graph) >= CLASS_COUNT_LIMIT,
        }
        if not incomplete and not any(
            work.get("labels_incomplete") for work in results
        ):
            cache.set(key, cached, 900)
    page = max(1, page)
    canonical_results = {}
    for work in cached["results"]:
        canonical_id = stage_identity.canonical_id(work["media_id"])
        if canonical_id != work["media_id"]:
            canonical_results.setdefault(
                canonical_id,
                {
                    **work,
                    "media_id": canonical_id,
                    "artwork_work_id": work["media_id"],
                },
            )
        else:
            canonical_results[canonical_id] = work
    results = list(canonical_results.values())
    displayed = results[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    article_artworks = wikipedia.artworks(displayed)
    illustrated = illustrate_page(displayed, article_artworks)
    response = helpers.format_search_response(
        page, PAGE_SIZE, len(results), illustrated
    )
    response["limited"] = cached["limited"]
    return response


def illustrate_page(displayed, article_artworks):
    """Use Wikipedia first and one batched direct-file Commons fallback."""
    fallback = commons.artworks(
        [work for work in displayed if not article_artworks[work["media_id"]]]
    )
    return [
        illustrate(
            work,
            article_artworks[work["media_id"]]
            or fallback.get(work.get("artwork_work_id", work["media_id"])),
            unavailable=article_artworks[work["media_id"]] is None,
        )
        for work in displayed
    ]
