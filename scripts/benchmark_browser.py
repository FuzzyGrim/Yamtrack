# ruff: noqa: T201, PLR2004 -- opt-in standalone benchmark dimensions
"""BENCH-03: disposable Playwright journeys; JSON/Markdown, never personal tabs."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import benchmark_artwork as artwork  # noqa: E402
from scripts import benchmark_fixtures as common  # noqa: E402

PROTOCOL = "bench03-browser-v2"
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 900},
    "mobile": {"width": 390, "height": 844},
}
ROUTES = {
    "home": "/",
    "movies": "/medialist/movie",
    "tv": "/medialist/tv",
    "games": "/medialist/game",
    "music": "/medialist/music",
    "books": "/medialist/book",
    "podcasts": "/medialist/podcast",
    "search": "/search?q=Synthetic",
    "history": "/history?year=2024&m=12",
    "history_range": "/history?start-date=2024-07-01&end-date=2025-01-31",
    "statistics": "/statistics",
    "calendar": "/calendar?month=1&year=2025",
    "collection": "/collection/",
    "lists": "/lists",
    "list_detail": "/list/bench-browser-list",
    "tags": "/tags",
    "settings": "/settings/preferences",
    "discover": "/discover",
    # Resolved after seeding, by created name (see resolve_routes).
    "movie_detail": None,
    "tv_detail": None,
    "season_detail": None,
    "album_detail": None,
    "podcast_show": None,
    # Stress cases, not in the default set: the base fixture's one album and one
    # podcast show each hold size / 10 children (2,000 at 20k).
    "album_detail_huge": None,
    "podcast_detail": None,
}
STRESS_ROUTES = ("album_detail_huge", "podcast_detail")
DEFAULT_ROUTES = [name for name in ROUTES if name not in STRESS_ROUTES]


# Useful-content selectors: real data in the DOM, not a skeleton. Every page also
# records "mount" (first [data-react-mounted] island root). Refined from DOM
# probes of the current components; see docs/browser-benchmarks.md.
def _island(root, content=".media-card", **extra):
    mounted = f"{root}[data-react-mounted]"
    marks = {
        "mount": mounted,
        "content": f"{mounted} {content}" if content else mounted,
    }
    return {**marks, **extra}


LIBRARY = _island("[data-library-page-root]")
DETAIL = _island(
    "[data-detail-composition-root]",
    content=None,
    actions="[data-track-action-root] button, [data-rating-chip-root][data-react-mounted]",
)
READY = {
    "home": _island("[data-home-root]"),
    **dict.fromkeys(("movies", "tv", "games", "music", "books", "podcasts"), LIBRARY),
    "search": _island("[data-search-results-root]"),
    # The month view is still server-rendered (legacy chrome): no island.
    "history": {"mount": "[data-history-legacy-chrome]", "content": "main .media-card"},
    "history_range": _island("[data-history-feed-root]"),
    "statistics": _island(
        "[data-statistics-summary-root]",
        content="[data-testid=stats-summary-value]",
        charts="[data-statistics-charts-root][data-react-mounted] svg",
    ),
    "calendar": _island("[data-release-calendar-root]", content="img"),
    "collection": _island(
        "[data-collection-browser-root]", content="[data-testid=collection-grid-item]"
    ),
    "lists": _island("[data-lists-page-root]"),
    "list_detail": _island("[data-list-detail-page-root]"),
    "tags": _island("[data-tag-index-root]", content="svg circle"),
    "settings": _island(
        "[data-settings-view-root]", content="[data-testid=settings-nav]"
    ),
    # Legacy page; providers are offline in the fixture, so rows are empty shells.
    "discover": _island("[data-discover-row-root]", content=None),
    "movie_detail": DETAIL,
    "tv_detail": {
        **DETAIL,
        "children": "[data-rails-row-root][data-react-mounted] .media-card",
    },
    "season_detail": {**DETAIL, "children": "[data-episode-has-data]"},
    "album_detail": _island(
        "[data-rating-chip-root]",
        content=None,
        children="[data-track-form-root][data-react-mounted]",
    ),
    "podcast_detail": _island("[data-podcast-episode-manager-root]", content=None),
    "podcast_show": _island("[data-podcast-episode-manager-root]", content=None),
    "album_detail_huge": _island(
        "[data-rating-chip-root]",
        content=None,
        children="[data-track-form-root][data-react-mounted]",
    ),
}
DEFAULT_READY = "main"
# Lighthouse's mobile profile: 4x CPU slowdown, 150 ms RTT, 1.6 Mbps down.
THROTTLES = {
    "none": None,
    "mobile4x": {
        "cpu": 4,
        "network": {
            "offline": False,
            "latency": 150,
            "downloadThroughput": 1638.4 * 1024 / 8,
            "uploadThroughput": 675 * 1024 / 8,
        },
    },
}
OBSERVER = ROOT / "scripts/browser_journey_observers.js"
NAV_FIELDS = (
    "fetchStart",
    "domainLookupStart",
    "domainLookupEnd",
    "connectStart",
    "connectEnd",
    "requestStart",
    "responseStart",
    "responseEnd",
    "domInteractive",
    "domContentLoadedEventEnd",
    "loadEventEnd",
    "transferSize",
    "encodedBodySize",
    "decodedBodySize",
)


GZIP_TYPES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)


def nginx_like(app):
    """Wrap the WSGI app with production nginx's static caching and gzip.

    Mirrors nginx.conf: /static/ is ``public, max-age=2592000, immutable`` and
    text responses of at least 256 bytes are gzipped at level 5, so cold and warm
    byte counts and throttled transfer times resemble a real install.
    """
    import gzip

    def wrapped(environ, start_response):
        captured = {}

        def capture(status, headers, exc_info=None):
            captured.update(status=status, headers=headers, exc_info=exc_info)
            return lambda data: None

        result = app(environ, capture)
        try:
            body = b"".join(result)
        finally:
            if hasattr(result, "close"):
                result.close()
        static = environ["PATH_INFO"].startswith("/static/")
        headers = [
            (key, value)
            for key, value in captured["headers"]
            if key.lower() != "content-length"
            and not (static and key.lower() == "cache-control")
        ]
        if static:
            headers.append(("Cache-Control", "public, max-age=2592000, immutable"))
        kind = next((v for k, v in headers if k.lower() == "content-type"), "")
        encoded = any(k.lower() == "content-encoding" for k, _ in headers)
        if (
            "gzip" in environ.get("HTTP_ACCEPT_ENCODING", "")
            and kind.startswith(GZIP_TYPES)
            and len(body) >= 256
            and not encoded
        ):
            body = gzip.compress(body, compresslevel=5, mtime=0)
            headers += [("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding")]
        headers.append(("Content-Length", str(len(body))))
        start_response(captured["status"], headers, captured["exc_info"])
        return [body]

    return wrapped


def apply_throttle(cdp, name):
    """Apply a named CPU and network profile to one page's DevTools session."""
    profile = THROTTLES[name]
    if profile:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": profile["cpu"]})
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", profile["network"])


def safe_path(url):
    """Drop origin, fragment, queries and identifier segments from exported URLs."""
    parsed = urlsplit(url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return f"<{parsed.scheme}>"
    path = parsed.path
    return "/".join(
        "<id>" if part.isdecimal() or re.fullmatch(r"[a-fA-F0-9]{16,}", part) else part
        for part in path.split("/")
    )


def polling(waterfall, observed_ms):
    """Report repeat fetches and intervals; repetition alone does not prove polling."""
    groups = defaultdict(list)
    for row in waterfall:
        if row["resource_type"] in {"fetch", "xhr"}:
            groups[(row["method"], row["path"], row.get("group", row["path"]))].append(
                row["start_ms"]
            )
    return [
        {
            "method": method,
            "path": path,
            "group": group,
            "count": len(times),
            "requests_per_second": len(times) / (observed_ms / 1000),
            "intervals_ms": [
                b - a for a, b in zip(sorted(times), sorted(times)[1:], strict=False)
            ],
            "frequency_hz": 1000
            / statistics.median(
                [b - a for a, b in zip(sorted(times), sorted(times)[1:], strict=False)]
            )
            if len(times) > 1 and len(set(times)) == len(times)
            else None,
            "note": "repeat endpoint candidate, not proof of a timer; short windows are right-censored",
        }
        for (method, path, group), times in sorted(groups.items())
    ]


def browser_resources(session):
    """Snapshot only this dedicated browser's processes; retain partial-metric notes."""
    rows = [
        {
            "pid": info["id"],
            "type": info["type"],
            "cpu_s": info["cpuTime"],
            "memory_bytes": common.memory(int(info["id"])),
        }
        for info in session.send("SystemInfo.getProcessInfo")["processInfo"]
    ]
    totals = {}
    for metric in ("rss", "uss", "pss"):
        values = [row["memory_bytes"][metric] for row in rows]
        totals[metric] = {
            "bytes": sum(values)
            if all(value is not None for value in values)
            else None,
            "available_processes": sum(value is not None for value in values),
            "processes": len(rows),
        }
    # PIDs are kept internally for deltas, never exported.
    return {"processes": rows, "memory": totals}


def resource_delta(before, after):
    """Compute surviving/new-process CPU; explicitly count vanished processes."""
    old = {row["pid"]: row for row in before["processes"]}
    new = {row["pid"]: row for row in after["processes"]}
    return {
        "cpu_s": sum(
            max(row["cpu_s"] - old.get(pid, {"cpu_s": 0})["cpu_s"], 0)
            for pid, row in new.items()
        ),
        "new_processes": len(new.keys() - old.keys()),
        "exited_processes": len(old.keys() - new.keys()),
        "memory_before_bytes": before["memory"],
        "memory_after_bytes": after["memory"],
    }


def network_log(cdp):
    """Collect per-request timing and wire bytes from the DevTools Network domain."""
    rows = {}

    def sent(event):
        rows.setdefault(
            event["requestId"],
            {
                "url": event["request"]["url"],
                "type": event.get("type"),
                "start": event["timestamp"],
                "end": None,
                "bytes": 0,
                "failed": False,
                "cached": False,
            },
        )

    def cached(event):
        if event["requestId"] in rows:
            rows[event["requestId"]]["cached"] = True

    def finished(event):
        if event["requestId"] in rows:
            row = rows[event["requestId"]]
            # Cache hits report their stored size; count wire bytes only.
            row.update(
                end=event["timestamp"],
                bytes=0 if row["cached"] else event["encodedDataLength"],
            )

    def failed(event):
        if event["requestId"] in rows:
            rows[event["requestId"]].update(end=event["timestamp"], failed=True)

    def response(event):
        row = rows.get(event["requestId"])
        if row is not None:
            row["type"] = event.get("type", row["type"])
            if event["response"].get("fromDiskCache") or event["response"].get(
                "fromServiceWorker"
            ):
                row["cached"] = True

    cdp.on("Network.requestWillBeSent", sent)
    cdp.on("Network.requestServedFromCache", cached)
    cdp.on("Network.responseReceived", response)
    cdp.on("Network.loadingFinished", finished)
    cdp.on("Network.loadingFailed", failed)
    return rows


def phase_accounting(rows, base, boundaries):
    """Split requests and bytes into phases by when each response finished."""
    document = min(rows.values(), key=lambda row: row["start"], default=None)
    if document is None:
        return {}, []
    origin = document["start"]
    exported = []
    for row in sorted(rows.values(), key=lambda row: row["start"]):
        host = urlsplit(row["url"]).netloc
        exported.append(
            {
                "path": safe_path(row["url"]),
                "origin": "app"
                if host == urlsplit(base).netloc
                else "artwork"
                if host in artwork.HOSTS
                else "<external>",
                "type": row["type"],
                "start_ms": (row["start"] - origin) * 1000,
                "end_ms": (row["end"] - origin) * 1000 if row["end"] else None,
                "bytes": row["bytes"],
                "failed": row["failed"],
                "cached": row["cached"],
            }
        )
    names = [name for name, limit in boundaries if limit is not None]
    limits = [limit for _, limit in boundaries if limit is not None]
    phases = {
        name: {
            "requests": 0,
            "bytes": 0,
            "api_requests": 0,
            "image_requests": 0,
            "image_bytes": 0,
            "script_bytes": 0,
        }
        for name in [*names, "unfinished"]
    }
    for row in exported:
        name = "unfinished"
        if row["end_ms"] is not None:
            # Phases are cumulative windows: to_dcl <= to_content <= to_settled.
            name = next(
                (
                    n
                    for n, limit in zip(names, limits, strict=True)
                    if row["end_ms"] <= limit
                ),
                "unfinished",
            )
        phase = phases[name]
        phase["requests"] += 1
        phase["bytes"] += row["bytes"]
        phase["api_requests"] += row["type"] in {"Fetch", "XHR"}
        phase["image_requests"] += row["type"] == "Image"
        phase["image_bytes"] += row["bytes"] if row["type"] == "Image" else 0
        phase["script_bytes"] += row["bytes"] if row["type"] == "Script" else 0
    return phases, exported


def navigate(
    context,
    browser_session,
    base,
    path,
    mode,
    observe_ms,
    profile=False,
    ready=None,
    throttle="none",
    settle_max_ms=20000,
    quiet_ms=750,
    dump_dom=False,
):
    """Measure server, shell, island mount, useful content and settled moments."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = context.new_page()
    # Playwright's clock replaces Performance entries too. Anchor Date only.
    page.add_init_script("""(() => {
      const RealDate = Date, origin = performance.now();
      const epoch = RealDate.parse('2025-01-01T00:00:00Z');
      const now = () => epoch + performance.now() - origin;
      function FixtureDate(...args) {
        if (!new.target) return new RealDate(now()).toString();
        return Reflect.construct(RealDate, args.length ? args : [now()], new.target);
      }
      Object.setPrototypeOf(FixtureDate, RealDate);
      FixtureDate.prototype = RealDate.prototype;
      FixtureDate.now = now;
      window.Date = FixtureDate;
    })();""")
    ready = ready or {"content": "main"}
    marks = {"mount": "[data-react-mounted]", **ready}
    cdp = context.new_cdp_session(page)
    cdp.send("Performance.enable")
    net = None
    if mode == "instrumented":
        page.add_init_script(f"window.__floppyReadySelectors = {json.dumps(marks)};")
        page.add_init_script(path=str(OBSERVER))
        cdp.send("Network.enable")
        net = network_log(cdp)
    apply_throttle(cdp, throttle)
    if profile:
        cdp.send("Profiler.enable")
        cdp.send("Profiler.start")
    waterfall, requests = [], {}
    started = None
    dropped = 0
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(True))

    def request_start(request):
        nonlocal dropped
        if len(waterfall) >= 10000:
            dropped += 1
            return
        row = {
            "path": safe_path(request.url)
            if urlsplit(request.url).netloc == urlsplit(base).netloc
            else "<external>",
            "method": request.method,
            "group": hashlib.sha256(urlsplit(request.url).path.encode()).hexdigest()[
                :16
            ],
            "resource_type": request.resource_type,
            "start_ms": (time.perf_counter() - started) * 1000,
            "status": None,
            "finished_ms": None,
            "failed": False,
        }
        requests[request] = row
        waterfall.append(row)

    def response_received(response):
        if response.request in requests:
            requests[response.request]["status"] = response.status

    def request_end(request):
        if request in requests:
            requests[request]["finished_ms"] = (time.perf_counter() - started) * 1000

    def request_failed(request):
        if request in requests:
            requests[request].update(
                failed=True, finished_ms=(time.perf_counter() - started) * 1000
            )

    if mode == "instrumented":
        page.on("request", request_start)
        page.on("response", response_received)
        page.on("requestfinished", request_end)
        page.on("requestfailed", request_failed)
    before = browser_resources(browser_session)
    own_before = common.memory()
    own_cpu = time.process_time()
    started = time.perf_counter()
    try:
        response = page.goto(base + path, wait_until="domcontentloaded", timeout=60000)
        if response.status != 200 or urlsplit(page.url).path != urlsplit(path).path:
            msg = f"Journey did not reach its authenticated route: {path} status={response.status}"
            raise RuntimeError(msg)
        shell_ms = (time.perf_counter() - started) * 1000
        selector = ready["content"]
        ready_timeout = False
        try:
            page.locator(selector).first.wait_for(state="attached", timeout=30000)
        except PlaywrightTimeout:
            ready_timeout = True
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        ready_ms = (time.perf_counter() - started) * 1000
        ready_pending = page.evaluate("""() => ({rows: document.querySelectorAll('[data-home-row-slot]').length,
          htmx: document.querySelectorAll('.htmx-request').length})""")
        settled_by = "observe"
        if net is not None:
            # Settled: no request in flight for quiet_ms, capped at settle_max_ms.
            settled_by = "cap"
            quiet_since = time.perf_counter()
            deadline = started + settle_max_ms / 1000
            seen = len(net)
            while time.perf_counter() < deadline:
                page.wait_for_timeout(100)
                inflight = sum(row["end"] is None for row in net.values())
                if inflight or len(net) != seen:
                    quiet_since = time.perf_counter()
                    seen = len(net)
                elif (time.perf_counter() - quiet_since) * 1000 >= quiet_ms:
                    settled_by = "quiet"
                    break
        else:
            page.wait_for_timeout(observe_ms)
        elapsed_ms = (time.perf_counter() - started) * 1000
        own_cpu = time.process_time() - own_cpu
        result = page.evaluate("""() => ({
          navigation: performance.getEntriesByType('navigation')[0]?.toJSON() || {},
          observers: window.__floppyBenchmark ? window.__floppyBenchmark() : null,
          cards: document.querySelectorAll('main .media-card').length,
          images: Array.from(document.images).filter(i => i.complete && i.naturalWidth).length,
          pending_rows: document.querySelectorAll('[data-home-row-slot]').length,
          inflight_htmx: document.querySelectorAll('.htmx-request').length,
          declared_polls: Array.from(document.querySelectorAll('[hx-trigger]')).map(e => e.getAttribute('hx-trigger')).filter(v => /every\\s/.test(v))
        })""")
        metrics = {
            row["name"]: row["value"]
            for row in cdp.send("Performance.getMetrics")["metrics"]
        }
        after = browser_resources(browser_session)
        navigation = {key: result["navigation"].get(key) for key in NAV_FIELDS}
        observers = result["observers"] or {}
        page_marks = observers.get("marks", {})
        moments = {
            "ttfb_ms": navigation.get("responseStart"),
            "document_ms": navigation.get("responseEnd"),
            "dcl_ms": navigation.get("domContentLoadedEventEnd"),
            "load_ms": navigation.get("loadEventEnd") or None,
            "mount_ms": page_marks.get("mount", {}).get("frame_ms"),
            "content_ms": page_marks.get("content", {}).get("frame_ms"),
        }
        phases, network = ({}, [])
        if net is not None:
            ends = [
                row["end_ms"]
                for row in phase_accounting(net, base, [])[1]
                if row["end_ms"]
            ]
            moments["settled_ms"] = max(ends) if ends else None
            phases, network = phase_accounting(
                net,
                base,
                [
                    ("to_dcl", moments["dcl_ms"]),
                    ("to_content", moments["content_ms"]),
                    ("to_settled", (moments["settled_ms"] or 0) + 1),
                ],
            )
        sample = {
            "mode": mode,
            "throttle": throttle,
            "shell_ms": shell_ms,
            "ready_ms": ready_ms,
            "ready_timeout": ready_timeout,
            "wall_ms": elapsed_ms,
            "settled_by": settled_by,
            "readiness_selectors": marks,
            "moments": moments,
            "phases": phases,
            "network": network,
            "pending_rows_at_ready": ready_pending["rows"],
            "inflight_htmx_at_ready": ready_pending["htmx"],
            "status": response.status,
            "navigation": navigation,
            "observers": result["observers"],
            "waterfall": waterfall,
            "dropped_network_entries": dropped,
            "page_error_count": len(page_errors),
            "polling": polling(waterfall, elapsed_ms)
            if mode == "instrumented"
            else None,
            "declared_poll_triggers": result["declared_polls"],
            "cards": result["cards"],
            "loaded_images": result["images"],
            "pending_rows": result["pending_rows"],
            "inflight_htmx": result["inflight_htmx"],
            "runner": {
                "cpu_s": own_cpu,
                "memory_before_bytes": own_before,
                "memory_after_bytes": common.memory(),
            },
            "browser": resource_delta(before, after),
            "renderer_cdp": {
                key: metrics.get(key)
                for key in (
                    "TaskDuration",
                    "ScriptDuration",
                    "LayoutDuration",
                    "RecalcStyleDuration",
                    "JSHeapUsedSize",
                )
            },
        }
        if dump_dom:
            sample["dom"] = page.content()
        if profile:
            cpu_profile = cdp.send("Profiler.stop")["profile"]
            for node in cpu_profile["nodes"]:
                frame = node["callFrame"]
                frame["url"] = safe_path(frame["url"])
            sample["cpu_profile"] = cpu_profile
        return sample
    finally:
        page.close()


def resolve_routes(user, long_show):
    """Fill detail routes from rows found by created name, never by position."""
    from django.urls import reverse
    from django.utils.text import slugify

    from app.models import Album, Item, PodcastShow
    from app.podcast_views import podcast_show_url

    routes = dict(ROUTES)
    movie = (
        Item.objects.filter(media_type="movie", title__startswith="Synthetic ")
        .order_by("title")
        .first()
    )
    routes["movie_detail"] = reverse(
        "media_details", args=["manual", "movie", movie.media_id, slugify(movie.title)]
    )
    if long_show is not None:
        routes["tv_detail"] = reverse(
            "media_details",
            args=["manual", "tv", long_show.media_id, slugify(long_show.title)],
        )
        routes["season_detail"] = reverse(
            "season_details",
            args=["manual", long_show.media_id, slugify(long_show.title), 16],
        )
    for name, title in (
        ("album_detail", "Synthetic Long Album"),
        ("album_detail_huge", "Synthetic Album"),
    ):
        album = Album.objects.filter(title=title).first()
        if album is not None:
            routes[name] = reverse(
                "music_album_details",
                args=[
                    album.artist_id,
                    slugify(album.artist.name),
                    album.pk,
                    slugify(album.title),
                ],
            )
    for name, uuid in (
        ("podcast_detail", "bench-podcast"),
        ("podcast_show", "bench-podcast-small"),
    ):
        show = PodcastShow.objects.filter(podcast_uuid=uuid).first()
        if show is not None:
            # The show's one page; the old show URL only redirects here.
            routes[name] = podcast_show_url(show)
    return {name: path for name, path in routes.items() if path}


def serve_for_devices(options, user, routes, directory):
    """Serve the seeded fixture for simulators instead of driving Chromium.

    Simulators reach the host loopback (Android as 10.0.2.2). A throwaway password
    for the synthetic user goes to credentials.json in the output directory, never
    to stdout. Artwork hosts are not mapped: native artwork is not measured.
    """
    import secrets

    from django.conf import settings
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.cache import cache
    from django.core.servers.basehttp import ThreadedWSGIServer
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from django.test.testcases import QuietWSGIRequestHandler

    from lists.models import CustomList

    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "10.0.2.2"]
    listing = CustomList.objects.get(public_slug="bench-browser-list")
    password = secrets.token_urlsafe(18)
    user.set_password(password)
    user.save(update_fields=["password"])
    (directory / "credentials.json").write_text(
        json.dumps(
            {
                "username": user.username,
                "password": password,
                "routes": routes,
                "list_id": listing.pk,
            },
            indent=2,
        )
    )
    connections.close_all()
    app = nginx_like(StaticFilesHandler(get_wsgi_application()))

    def with_cache_reset(environ, start_response):
        # Loopback-only benchmark hook: a cold pass starts from an empty cache.
        if environ["PATH_INFO"] == "/__bench/clear-cache":
            cache.clear()
            start_response("204 No Content", [("Content-Length", "0")])
            return [b""]
        return app(environ, start_response)

    server = make_server(
        "127.0.0.1",
        options.serve_port,
        with_cache_reset,
        server_class=ThreadedWSGIServer,
        handler_class=QuietWSGIRequestHandler,
    )
    print(f"serving http://127.0.0.1:{options.serve_port} (Ctrl-C stops)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def worker(options):
    """Seed and serve a disposable SQLite instance before starting Playwright."""
    directory = options.output
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "dataset.sqlite3"
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.test_settings",
        SECRET="bench-browser-only",  # noqa: S106
        FLOPPY_DB_PATH=str(database),
        FLOPPY_TEST_ALLOW_NETWORK="0",
    )
    sys.path.insert(0, str(ROOT / "src"))
    import django
    from django.conf import settings

    settings.DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(database),
            "OPTIONS": {"timeout": 30},
        }
    }
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    settings.ALLAUTH_TRUSTED_CLIENT_IP_HEADER = None
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CELERY_BROKER_URL = "memory://"
    django.setup()
    import requests
    from django.apps import apps
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.cache import cache
    from django.core.management import call_command
    from django.core.servers.basehttp import ThreadedWSGIServer
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from django.test import Client
    from django.test.testcases import QuietWSGIRequestHandler
    from playwright.sync_api import sync_playwright

    from app.benchmark_data import EPOCH, build_dataset
    from lists.models import CustomList, CustomListItem
    from users.home_screen import ensure_home_screen_rows
    from users.models import HomeScreenRow, User

    settings.MIGRATION_MODULES = dict.fromkeys(
        app.label for app in apps.get_app_configs()
    )
    call_command("migrate", run_syncdb=True, verbosity=0)
    with connections["default"].cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL")
    with (
        patch("django.utils.timezone.now", return_value=EPOCH),
        patch(
            "requests.sessions.Session.request",
            side_effect=requests.ConnectionError("Synthetic offline provider"),
        ) as providers,
        patch("celery.app.task.Task.apply_async", return_value=None) as queued,
    ):
        models, generation = common.measure(
            lambda: build_dataset(options.size, options.seed)
        )
        user = User.objects.get(username="bench-user-1")
        User.objects.filter(pk=user.pk).update(statistics_default_range="All Time")
        ensure_home_screen_rows(user)
        custom_list = CustomList.objects.bulk_create(
            [
                CustomList(
                    owner=user,
                    name="Synthetic browser list",
                    visibility="public",
                    public_slug="bench-browser-list",
                )
            ]
        )[0]
        from app.models import (
            Album,
            AlbumTracker,
            Artist,
            ArtistTracker,
            Item,
            PodcastShow,
            PodcastShowTracker,
        )

        ArtistTracker.objects.bulk_create(
            [ArtistTracker(user=user, artist=Artist.objects.first())]
        )
        AlbumTracker.objects.bulk_create(
            [AlbumTracker(user=user, album=Album.objects.first())]
        )
        PodcastShowTracker.objects.bulk_create(
            [PodcastShowTracker(user=user, show=PodcastShow.objects.first())]
        )

        CustomListItem.objects.bulk_create(
            [
                CustomListItem(custom_list=custom_list, item=item, added_by=user)
                for item in Item.objects.filter(media_type="movie").order_by("pk")[:24]
            ]
        )
        long_show = None
        if options.extras:
            import events.tasks  # noqa: F401 -- signal handlers reach it lazily
            from app.benchmark_data import add_loading_extras
            from events.models import Event

            long_show = add_loading_extras(user, options.seed)
            models.append(Event)
        routes = resolve_routes(user, long_show)
        client = Client()
        client.force_login(user)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        models += [
            HomeScreenRow,
            CustomList,
            CustomListItem,
            ArtistTracker,
            AlbumTracker,
            PodcastShowTracker,
        ]
        checksum = common.export_fixture(models, directory / "fixture.json.gz")
        if "statistics" in options.routes:
            from app.statistics_sync import run_sync

            stats = run_sync(user.pk, only_ranges=["All Time"])
            if stats["status"] != "done":
                msg = f"Statistics preparation failed: {stats['status']}"
                raise RuntimeError(msg)
        database_info = common.database_metadata(connections["default"])
        if options.serve_port:
            serve_for_devices(options, user, routes, directory)
            return
        connections.close_all()
        server = make_server(
            "127.0.0.1",
            0,
            nginx_like(StaticFilesHandler(get_wsgi_application())),
            server_class=ThreadedWSGIServer,
            handler_class=QuietWSGIRequestHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        samples = []
        mocked_assets = 0
        realistic = options.images == "realistic"

        def outside(url):
            host = urlsplit(url).netloc
            if host == urlsplit(base).netloc:
                return False
            return not (realistic and host in artwork.HOSTS)

        def browser_route(route):
            nonlocal mocked_assets
            mocked_assets += 1
            route.fulfill(
                status=200,
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="96"><rect width="64" height="96" fill="#777"/></svg>',
            )

        first_in_process = True
        try:
            with artwork.ArtworkServer() as art, sync_playwright() as playwright:
                # Any page.route disables Chromium's HTTP cache, so with realistic
                # artwork other hosts are refused by the resolver instead.
                rules = art.resolver_rules if realistic else ""
                rules = ", ".join(
                    filter(None, [rules, "MAP * ~NOTFOUND", "EXCLUDE 127.0.0.1"])
                )
                launch = {
                    "args": [
                        f"--host-resolver-rules={rules}",
                        *(art.browser_args if realistic else []),
                    ]
                }
                if options.browser_channel:
                    launch["channel"] = options.browser_channel
                browser = playwright.chromium.launch(**launch)
                session = browser.new_browser_cdp_session()
                version = browser.version
                for run in range(1, options.runs + 1):
                    for viewport in options.viewports:
                        for name in [n for n in options.routes if n in routes]:
                            # Alternate the control/instrument order across repeat workers.
                            modes = (
                                ["control", "instrumented"]
                                if options.control
                                else ["instrumented"]
                            )
                            if (options.repeat_index + run) % 2 == 0:
                                modes.reverse()
                            for mode in modes:
                                # Cold = new context (empty HTTP cache, no SW) and
                                # cleared server cache; warm = repeat in that context.
                                cache.clear()
                                context = browser.new_context(
                                    viewport=VIEWPORTS[viewport],
                                    is_mobile=viewport == "mobile",
                                    has_touch=viewport == "mobile",
                                    device_scale_factor=options.dpr,
                                    service_workers=options.service_workers,
                                    ignore_https_errors=not realistic,
                                    locale="en-US",
                                    timezone_id="UTC",
                                    color_scheme="light",
                                )
                                context.add_cookies(
                                    [
                                        {
                                            "name": settings.SESSION_COOKIE_NAME,
                                            "value": cookie,
                                            "url": base,
                                        }
                                    ]
                                )
                                if not realistic:
                                    context.route(outside, browser_route)
                                try:
                                    for state in options.states:
                                        started_at = time.perf_counter()
                                        try:
                                            sample = navigate(
                                                context,
                                                session,
                                                base,
                                                routes[name],
                                                mode,
                                                options.observe_ms,
                                                ready=READY.get(
                                                    name, {"content": DEFAULT_READY}
                                                ),
                                                throttle=options.throttle,
                                                settle_max_ms=options.settle_max_ms,
                                                dump_dom=options.dump_dom,
                                            )
                                        except Exception as error:
                                            # A page that never loads is a result, not a crash.
                                            samples.append(
                                                {
                                                    "mode": mode,
                                                    "route": name,
                                                    "viewport": viewport,
                                                    "state": state,
                                                    "run": run,
                                                    "error": type(error).__name__,
                                                    "error_after_ms": (
                                                        time.perf_counter() - started_at
                                                    )
                                                    * 1000,
                                                    "first_in_process": first_in_process,
                                                }
                                            )
                                            first_in_process = False
                                            print(
                                                f"repeat={options.repeat_index} run={run} {viewport} {name} {mode} {state} error={type(error).__name__}",
                                                flush=True,
                                            )
                                            break
                                        if options.dump_dom and state == "fresh":
                                            (
                                                directory
                                                / f"dom-{viewport}-{name}.html"
                                            ).write_text(sample.pop("dom", ""))
                                        sample.pop("dom", None)
                                        samples.append(
                                            {
                                                **sample,
                                                "route": name,
                                                "viewport": viewport,
                                                "state": state,
                                                "run": run,
                                                "first_in_process": first_in_process,
                                            }
                                        )
                                        first_in_process = False
                                        content = sample["moments"].get("content_ms")
                                        print(
                                            f"repeat={options.repeat_index} run={run} {viewport} {name} {mode} {state} "
                                            f"content={content and round(content)} timeout={sample['ready_timeout']}",
                                            flush=True,
                                        )
                                    if options.profile and mode == "instrumented":
                                        profiled = navigate(
                                            context,
                                            session,
                                            base,
                                            routes[name],
                                            "instrumented",
                                            options.observe_ms,
                                            profile=True,
                                            ready=READY.get(
                                                name, {"content": DEFAULT_READY}
                                            ),
                                            throttle=options.throttle,
                                        )
                                        artifact = f"profile-{viewport}-{name}-{run}.cpuprofile"
                                        (directory / artifact).write_text(
                                            json.dumps(profiled.pop("cpu_profile"))
                                        )
                                        samples.append(
                                            {
                                                **profiled,
                                                "mode": "profiled",
                                                "route": name,
                                                "viewport": viewport,
                                                "state": "repeat",
                                                "run": run,
                                                "cpu_profile_artifact": artifact,
                                            }
                                        )
                                finally:
                                    context.close()
                artwork_bytes = art.served_bytes
                cpu, started = time.process_time(), time.perf_counter()
                for _ in range(5):
                    browser_resources(session)
                collector = {
                    "calls": 5,
                    "wall_s": time.perf_counter() - started,
                    "cpu_s": time.process_time() - cpu,
                }
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
            connections.close_all()
        receipt = {
            "protocol": PROTOCOL,
            "repeat": options.repeat_index,
            "fixture_sha256": checksum,
            "size": options.size,
            "seed": options.seed,
            "browser_version": version,
            "database": database_info,
            "generation": generation,
            "samples": samples,
            "provider_calls_mocked": providers.call_count,
            "queued_tasks_mocked": queued.call_count,
            "external_assets_mocked": mocked_assets,
            "browser_resource_collector": collector,
            "artwork_bytes_served": artwork_bytes,
            "routes_resolved": {name: safe_path(path) for name, path in routes.items()},
        }
        (directory / "sample.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        )
    for path in directory.glob("dataset.sqlite3*"):
        path.unlink()


def spread(values):
    """Median, min, max and interquartile range of the non-null values."""
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    quartiles = (
        statistics.quantiles(values, n=4) if len(values) > 1 else [values[0]] * 3
    )
    return {
        "n": len(values),
        "median": statistics.median(values),
        "min": values[0],
        "max": values[-1],
        "p25": quartiles[0],
        "p75": quartiles[2],
    }


def loading_summary(samples):
    """BENCH-03 v2 loading moments, phase bytes and observer totals per group."""
    rows = [sample for sample in samples if sample.get("moments")]
    if not rows:
        return {}

    def moment(name):
        return [row["moments"].get(name) for row in rows]

    def phase(name, field):
        return [row["phases"].get(name, {}).get(field) for row in rows]

    def total(field):
        return [
            sum(value.get(field, 0) for value in row["phases"].values()) for row in rows
        ]

    observers = [row["observers"] or {} for row in rows]
    return {
        "moments": {
            name: spread(moment(name))
            for name in (
                "ttfb_ms",
                "document_ms",
                "dcl_ms",
                "mount_ms",
                "content_ms",
                "settled_ms",
            )
        },
        "mount_after_dcl_ms": spread(
            [
                row["moments"]["mount_ms"] - row["moments"]["dcl_ms"]
                for row in rows
                if row["moments"].get("mount_ms") and row["moments"].get("dcl_ms")
            ]
        ),
        "extra_marks_ms": {
            name: spread(
                [
                    (o.get("marks", {}).get(name) or {}).get("frame_ms")
                    for o in observers
                ]
            )
            for name in sorted(
                {name for o in observers for name in o.get("marks", {})}
                - {"mount", "content"}
            )
        },
        "lcp_ms": spread([(o.get("lcp") or {}).get("start_ms") for o in observers]),
        "cls": spread([o.get("layout_shift") for o in observers]),
        "long_task_total_ms": spread(
            [sum(t["duration_ms"] for t in o.get("long_tasks", [])) for o in observers]
        ),
        "requests": spread(total("requests")),
        "bytes": spread(total("bytes")),
        "image_bytes": spread(total("image_bytes")),
        "image_requests": spread(total("image_requests")),
        "api_requests": spread(total("api_requests")),
        "requests_to_content": spread(
            [
                a + b
                for a, b in zip(
                    phase("to_dcl", "requests"),
                    phase("to_content", "requests"),
                    strict=True,
                )
                if a is not None and b is not None
            ]
        ),
        "bytes_to_content": spread(
            [
                a + b
                for a, b in zip(
                    phase("to_dcl", "bytes"), phase("to_content", "bytes"), strict=True
                )
                if a is not None and b is not None
            ]
        ),
        "ready_timeouts": sum(row.get("ready_timeout", False) for row in rows),
        "first_in_process": [
            row["moments"].get("content_ms")
            for row in rows
            if row.get("first_in_process")
        ],
    }


def compare_reports(current, baseline):
    """Withhold ratios when recorded browser, fixture or journey settings differ."""
    keys = (
        "schema_version",
        "protocol",
        "machine",
        "size",
        "seed",
        "observe_ms",
        "viewports",
        "routes",
        "states",
        "control",
        "profile",
        "settings",
        "playwright_version",
        "throttle",
        "images",
        "extras",
        "dpr",
        "browser_channel",
        "service_workers",
    )
    reasons = [
        f"{key} differs" for key in keys if current.get(key) != baseline.get(key)
    ]
    reasons += [
        f"{name} run was discarded (machine load)"
        for name, run in (("current", current), ("baseline", baseline))
        if run.get("discarded")
    ]
    for key in ("fixture_sha256", "browser_version", "database"):
        if any(
            row[key] != current["executions"][0][key]
            for row in [*current["executions"], *baseline["executions"]]
        ):
            reasons.append(f"{key} differs")
    old = {
        (row["route"], row["viewport"], row["state"], row["mode"]): row
        for row in baseline["summaries"]
    }
    rows = []
    for row in current["summaries"]:
        key = (row["route"], row["viewport"], row["state"], row["mode"])
        notes = [*reasons, *([] if key in old else ["group absent from baseline"])]
        result = {
            "route": key[0],
            "viewport": key[1],
            "state": key[2],
            "mode": key[3],
            "comparable": not notes,
            "notes": notes,
        }
        if not notes:
            before = old[key]["ready_median_ms"]
            result.update(
                before_ready_ms=before,
                after_ready_ms=row["ready_median_ms"],
                ratio=row["ready_median_ms"] / before if before else None,
                before_n=old[key].get("n"),
                after_n=row.get("n"),
            )
        rows.append(result)
    return rows


def report(options, executions):
    """Write raw receipts and variance/overhead tables for compatible journey groups."""
    options.discarded = options.max_load is not None and any(
        row["end"][0] > options.max_load for row in getattr(options, "loads", [])
    )
    if len({row["fixture_sha256"] for row in executions}) != 1:
        msg = "Repeated browser fixture checksums differ"
        raise RuntimeError(msg)
    groups = defaultdict(list)
    errors = defaultdict(list)
    for execution in executions:
        for sample in execution["samples"]:
            if sample.get("error"):
                errors[(sample["route"], sample["viewport"], sample["state"])].append(
                    {"error": sample["error"], "after_ms": sample["error_after_ms"]}
                )
                continue
            groups[
                (sample["route"], sample["viewport"], sample["state"], sample["mode"])
            ].append(sample)
    summaries = []
    for (route, viewport, state, mode), samples in sorted(groups.items()):
        values = [sample["ready_ms"] for sample in samples]
        lcp = [
            sample["observers"]["lcp"]["start_ms"]
            for sample in samples
            if sample["observers"] and sample["observers"]["lcp"]
        ]
        tasks = [
            len(sample["observers"]["long_tasks"])
            for sample in samples
            if sample["observers"] and "longtask" in sample["observers"]["supported"]
        ]
        summaries.append(
            {
                "route": route,
                "viewport": viewport,
                "state": state,
                "mode": mode,
                "n": len(values),
                "ready_median_ms": statistics.median(values),
                "ready_min_ms": min(values),
                "ready_max_ms": max(values),
                "ready_cv_percent": statistics.pstdev(values)
                / statistics.mean(values)
                * 100,
                "lcp_median_ms": statistics.median(lcp) if lcp else None,
                "long_tasks_median": statistics.median(tasks) if tasks else None,
                "network_requests_median": statistics.median(
                    len(sample["waterfall"]) for sample in samples
                )
                if mode != "control"
                else None,
                "browser_cpu_median_s": statistics.median(
                    sample["browser"]["cpu_s"] for sample in samples
                ),
                "runner_cpu_median_s": statistics.median(
                    sample["runner"]["cpu_s"] for sample in samples
                ),
                **loading_summary(samples),
            }
        )
    notes = [
        "Synthetic SQLite WSGI server; no personal browser/profile/session data is read or exported. Cookies, headers, bodies, console text and DOM text are excluded.",
        "Moments come from the page: TTFB/DCL from Navigation Timing; island mount and useful content are the first animation frame after a per-surface selector (READY) matches, observed by a MutationObserver. Settled = last response end once nothing has been in flight for 750 ms (capped). ready_ms is the older Playwright-side readiness, kept for v1 comparisons.",
        "Cold (fresh) = new browser context (empty HTTP cache, no service worker) plus cleared server cache; warm (repeat) = same context and server cache, second navigation. Cold process = the first journey of each worker (first_in_process). With realistic images the HTTP cache is live (no Playwright routing) and other hosts are refused by the resolver. OS caches and DB buffers are not reset.",
        "Artwork is generated locally at the provider size the URL names and served over loopback HTTPS (benchmark_artwork.py), so throttling and caching apply. Bytes are DevTools encodedDataLength (wire bytes, 0 for cache hits). Static files get nginx.conf's immutable cache header and gzip.",
        "LCP is the latest observed candidate at the bounded window, not a final Web Vital; null means unsupported/no candidate. Long tasks are tasks >=50 ms, capped at 10000; control mode does not install observers/listeners.",
        "Polling groups repeated fetch/xhr paths with queries/identifier segments removed. Repetition is not proof of a timer. Short windows are right-censored; no observed repetition does not mean no polling. Declared HTMX timer strings are retained.",
        "Python CPU includes the in-process server and Playwright driver callbacks. Browser CPU is a process-snapshot delta: exited processes are missed and new-process CPU includes startup. RSS sums double-count shared memory; PSS/USS totals are null if any listed process is unavailable.",
        "The Playwright Node driver and total machine resources are not measured. Mobile is viewport/touch emulation on desktop Chromium; it is throttled only with --throttle mobile4x (4x CPU, 150 ms RTT, 1.6 Mbps down).",
        "Browser resource collection is calibrated separately and lies outside operation timers. Opt-in profiles and instrument/control ratios are observations, not significance tests or production speedups.",
        "Provider HTTP raises a synthetic offline error; tasks are recorded but not run. Statistics All Time is prebuilt. Artwork/background completion is not represented. Navigation failures/auth redirects fail the run.",
    ]
    payload = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 -- fixed Git command
            cwd=ROOT,
            text=True,
        ).strip(),
        "tracked_tree_dirty": subprocess.run(
            ["git", "diff", "--quiet", "HEAD"],  # noqa: S607 -- fixed Git command
            cwd=ROOT,
            check=False,
        ).returncode
        != 0,
        "machine": common.machine(),
        "size": options.size,
        "seed": options.seed,
        "repeats": options.repeats,
        "command": [
            "python",
            "scripts/benchmark_browser.py",
            *[
                "<output>"
                if index and sys.argv[index] == "--output"
                else "<baseline>"
                if index and sys.argv[index] == "--baseline"
                else argument
                for index, argument in enumerate(sys.argv[1:])
            ],
        ],
        "observe_ms": options.observe_ms,
        "viewports": {key: VIEWPORTS[key] for key in options.viewports},
        "routes": options.routes,
        "states": options.states,
        "control": options.control,
        "profile": options.profile,
        "runs": options.runs,
        "throttle": options.throttle,
        "throttle_profile": THROTTLES[options.throttle],
        "images": options.images,
        "extras": options.extras,
        "browser_channel": options.browser_channel,
        "service_workers": options.service_workers,
        "dpr": options.dpr,
        "playwright_version": importlib.metadata.version("playwright"),
        "settings": {
            "clock": "2025-01-01T00:00:00Z",
            "server": "ThreadedWSGIServer",
            "debug": False,
            "cache": "isolated locmem",
            "broker": "memory://; tasks mocked",
            "http_cache": "enabled (realistic images) / disabled by routing (placeholder)",
            "static": "nginx-like: immutable 30d cache headers, gzip level 5",
            "service_workers": options.service_workers,
            "locale": "en-US",
            "timezone": "UTC",
            "color_scheme": "light",
            "device_scale_factor": options.dpr,
            "browser_date_clock": "fixed epoch plus native monotonic elapsed; Performance/timers unchanged",
            "statistics_prebuilt": "All Time"
            if "statistics" in options.routes
            else None,
        },
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/benchmark_browser.py",
                "scripts/browser_journey_observers.js",
                "scripts/benchmark_artwork.py",
                "scripts/benchmark_fixtures.py",
                "src/app/benchmark_data.py",
                "uv.lock",
            )
        },
        "load_average": getattr(options, "loads", []),
        "max_load": options.max_load,
        "discarded": options.discarded,
        "summaries": summaries,
        "journey_errors": [
            {"route": key[0], "viewport": key[1], "state": key[2], "errors": value}
            for key, value in sorted(errors.items())
        ],
        "executions": executions,
        "notes": notes,
    }
    payload["instrumentation_overhead"] = []
    by_group = {
        (row["route"], row["viewport"], row["state"], row["mode"]): row
        for row in summaries
    }
    for row in summaries:
        control = by_group.get((row["route"], row["viewport"], row["state"], "control"))
        if row["mode"] == "instrumented" and control:
            payload["instrumentation_overhead"].append(
                {
                    "route": row["route"],
                    "viewport": row["viewport"],
                    "state": row["state"],
                    "control_ready_ms": control["ready_median_ms"],
                    "instrumented_ready_ms": row["ready_median_ms"],
                    "ready_ratio": row["ready_median_ms"] / control["ready_median_ms"],
                    "control_n": control["n"],
                    "instrumented_n": row["n"],
                }
            )
    if options.baseline:
        raw = options.baseline.read_bytes()
        payload["baseline_sha256"] = hashlib.sha256(raw).hexdigest()
        payload["baseline_comparison"] = compare_reports(payload, json.loads(raw))
    (options.output / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# BENCH-03 browser journeys",
        "",
        f"Commit: `{payload['commit']}`; seed `{options.seed}`; size `{options.size}`; observation `{options.observe_ms} ms`.",
        f"Fixture SHA-256: `{executions[0]['fixture_sha256']}`",
        "Load average (1/5/15 min) per worker, start -> end: "
        + "; ".join(
            f"{row['start'][0]:.1f} -> {row['end'][0]:.1f}"
            for row in payload["load_average"]
        )
        + (" **DISCARDED: above --max-load**" if payload["discarded"] else ""),
        "",
        "| Route / viewport / state / mode | N | Ready median ms | Min-max ms | CV % |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in summaries:
        lines.append(  # noqa: PERF401 -- readable Markdown table
            f"| {row['route']} / {row['viewport']} / {row['state']} / {row['mode']} | {row['n']} | {row['ready_median_ms']:.2f} | {row['ready_min_ms']:.2f}-{row['ready_max_ms']:.2f} | {row['ready_cv_percent']:.1f} |"
        )
    lines += [
        "",
        "## Loading moments (median, p25-p75 in brackets; ms from navigation start)",
        "",
        "| Route / viewport / state | TTFB | DCL | Island mount | Useful content | Settled | Mount after DCL | LCP | CLS | Long tasks ms | Requests | KB total | Image KB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def cell(value, scale=1, digits=0):
        if not value:
            return "-"
        return f"{value['median'] / scale:.{digits}f} [{value['p25'] / scale:.{digits}f}-{value['p75'] / scale:.{digits}f}]"

    for row in summaries:
        if row["mode"] != "instrumented" or "moments" not in row:
            continue
        moments = row["moments"]
        lines.append(
            f"| {row['route']} / {row['viewport']} / {row['state']} | {cell(moments['ttfb_ms'])} | {cell(moments['dcl_ms'])} | {cell(moments['mount_ms'])} | {cell(moments['content_ms'])} | {cell(moments['settled_ms'])} | {cell(row['mount_after_dcl_ms'])} | {cell(row['lcp_ms'])} | {cell(row['cls'], digits=3)} | {cell(row['long_task_total_ms'])} | {cell(row['requests'])} | {cell(row['bytes'], 1024)} | {cell(row['image_bytes'], 1024)} |"
        )
    lines += [
        "",
        "| Route / viewport / state / mode | LCP candidate ms | Long tasks | Requests | Browser CPU ms | Python CPU ms |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in summaries:
        lines.append(
            f"| {row['route']} / {row['viewport']} / {row['state']} / {row['mode']} | {row['lcp_median_ms']} | {row['long_tasks_median']} | {row['network_requests_median']} | {row['browser_cpu_median_s'] * 1000:.1f} | {row['runner_cpu_median_s'] * 1000:.1f} |"
        )
    lines += [
        "",
        "Null LCP/long-task/request metrics mean control disabled, unsupported or no candidate; raw observer support flags distinguish these cases.",
        "",
        "| Route / viewport / state | Instrumented / control readiness ratio |",
        "| --- | --- |",
    ]
    lines += [
        f"| {row['route']} / {row['viewport']} / {row['state']} | {row['ready_ratio']:.3f} |"
        for row in payload["instrumentation_overhead"]
    ]
    if not options.control:
        lines += ["", "Matched-control overhead unavailable: rerun with --control."]
    if options.baseline:
        lines += [
            "",
            "| Route / viewport / state / mode | New / baseline readiness ratio or incompatibility |",
            "| --- | --- |",
        ]
        for row in payload["baseline_comparison"]:
            value = row.get("ratio") if row["comparable"] else "; ".join(row["notes"])
            lines.append(
                f"| {row['route']} / {row['viewport']} / {row['state']} / {row['mode']} | {value} |"
            )
    if payload["journey_errors"]:
        lines += ["", "## Journeys that did not load", ""]
        lines += [
            f"- {row['route']} / {row['viewport']} / {row['state']}: "
            + ", ".join(
                f"{error['error']} after {error['after_ms'] / 1000:.0f} s"
                for error in row["errors"]
            )
            for row in payload["journey_errors"]
        ]
    lines += [
        "",
        "Machine: `" + json.dumps(payload["machine"], sort_keys=True) + "`",
        "",
    ]
    lines += [f"- {note}" for note in notes]
    lines += [
        "",
        "All navigation, LCP/long-task entries, request waterfalls, polling intervals and CPU/memory snapshots are in report.json and per-worker sample.json.",
    ]
    (options.output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    """Run repeat workers in fresh processes; output directories must be new."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--observe-ms", type=int, default=1000)
    parser.add_argument(
        "--routes", choices=list(ROUTES), nargs="+", default=DEFAULT_ROUTES
    )
    parser.add_argument(
        "--viewports", choices=list(VIEWPORTS), nargs="+", default=list(VIEWPORTS)
    )
    parser.add_argument(
        "--states", choices=("fresh", "repeat"), nargs="+", default=["fresh", "repeat"]
    )
    parser.add_argument(
        "--control",
        action="store_true",
        help="Add matched journeys without observers/network listeners",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Cold+warm journeys per route inside each worker",
    )
    parser.add_argument("--throttle", choices=list(THROTTLES), default="none")
    parser.add_argument(
        "--images", choices=("realistic", "placeholder"), default="realistic"
    )
    parser.add_argument(
        "--no-extras",
        dest="extras",
        action="store_false",
        help="Skip the 16-season show, calendar events and artwork URLs",
    )
    parser.add_argument(
        "--browser-channel", help="Use an installed browser, e.g. chrome"
    )
    parser.add_argument(
        "--service-workers", choices=("block", "allow"), default="block"
    )
    parser.add_argument("--dpr", type=float, default=1)
    parser.add_argument("--settle-max-ms", type=int, default=20000)
    parser.add_argument(
        "--dump-dom",
        action="store_true",
        help="Save cold DOM snapshots (local probing only)",
    )
    parser.add_argument(
        "--serve-port",
        type=int,
        help="Seed once, then serve the fixture on this loopback port for simulators (no browser)",
    )
    parser.add_argument(
        "--max-load",
        type=float,
        help="Refuse to start above this 1-minute load average; mark the run "
        "discarded if any worker ends above it",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repeat-index", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Add separate warm CPU-profile trials with sanitized .cpuprofile artifacts",
    )
    parser.add_argument(
        "--baseline", type=Path, help="Compare a matching BENCH-03 report.json"
    )
    options = parser.parse_args()
    options.output = options.output.resolve()
    if (
        options.size < 10
        or options.size % 10
        or options.repeats < 2
        or options.observe_ms < 100
        or options.runs < 1
    ):
        parser.error("Use size >=10 divisible by 10, repeats >=2, observe-ms >=100")
    if options.states not in (["fresh", "repeat"], ["fresh"]):
        parser.error("--states must be 'fresh' or 'fresh repeat' in that order")
    if options.worker:
        worker(options)
        return
    if options.serve_port:
        options.output.mkdir(parents=True, exist_ok=False)
        options.output = options.output / "serve"
        options.repeat_index = 1
        worker(options)
        return
    if (
        options.baseline
        and json.loads(options.baseline.read_text()).get("protocol") != PROTOCOL
    ):
        parser.error("--baseline requires a BENCH-03 report with matching protocol")
    options.loads = []
    if options.max_load is not None and os.getloadavg()[0] > options.max_load:
        parser.error(
            f"Machine busy: 1-minute load {os.getloadavg()[0]:.1f} is above "
            f"--max-load {options.max_load}. Try again when it is quiet."
        )
    options.output.mkdir(parents=True, exist_ok=False)
    receipts = []
    for repeat in range(1, options.repeats + 1):
        load_start = os.getloadavg()
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--output",
            str(options.output / f"repeat-{repeat}"),
            "--size",
            str(options.size),
            "--seed",
            str(options.seed),
            "--observe-ms",
            str(options.observe_ms),
            "--repeat-index",
            str(repeat),
            "--routes",
            *options.routes,
            "--viewports",
            *options.viewports,
            "--states",
            *options.states,
        ]
        command += [
            "--runs",
            str(options.runs),
            "--throttle",
            options.throttle,
            "--images",
            options.images,
            "--service-workers",
            options.service_workers,
            "--dpr",
            str(options.dpr),
            "--settle-max-ms",
            str(options.settle_max_ms),
        ]
        if not options.extras:
            command.append("--no-extras")
        if options.browser_channel:
            command += ["--browser-channel", options.browser_channel]
        if options.dump_dom:
            command.append("--dump-dom")
        if options.control:
            command.append("--control")
        if options.profile:
            command.append("--profile")
        subprocess.run(  # noqa: S603 -- fixed interpreter/script, no shell
            command,
            cwd=ROOT,
            env={**os.environ, "TZ": "UTC", "PYTHONHASHSEED": "0"},
            check=True,
        )
        receipts.append(
            json.loads((options.output / f"repeat-{repeat}/sample.json").read_text())
        )
        options.loads.append(
            {"repeat": repeat, "start": load_start, "end": os.getloadavg()}
        )
    report(options, receipts)
    if options.discarded:
        print(
            f"DISCARDED: load ended above --max-load {options.max_load}; "
            "see load_average in report.json"
        )
    print(f"Reports: {options.output / 'report.json'} and report.md")


if __name__ == "__main__":
    main()
