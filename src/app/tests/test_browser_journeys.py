"""Privacy, measurement semantics and real browser observer contracts."""

import importlib.util
import json
import os
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch
from wsgiref.simple_server import make_server

from django.core.servers.basehttp import ThreadedWSGIServer
from django.test import SimpleTestCase, tag
from django.test.testcases import QuietWSGIRequestHandler

spec = importlib.util.spec_from_file_location(
    "benchmark_browser",
    Path(__file__).resolve().parents[3] / "scripts/benchmark_browser.py",
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class BrowserJourneyContractTests(SimpleTestCase):
    def test_exported_paths_remove_credentials_queries_and_identifiers(self):
        self.assertEqual(
            harness.safe_path(
                "https://user:password@example.invalid/item/123/0123456789abcdef?token=secret#private"
            ),
            "/item/<id>/<id>",
        )
        self.assertEqual(harness.safe_path("file:///Users/private/secret.js"), "<file>")
        self.assertEqual(harness.safe_path("data:text/plain,private"), "<data>")

    def test_polling_keeps_distinct_anonymized_endpoint_ids_separate(self):
        rows = [
            {
                "method": "GET",
                "path": "/row/<id>",
                "group": group,
                "resource_type": "fetch",
                "start_ms": start,
            }
            for group, start in (("a", 100), ("b", 120), ("a", 600), ("a", 1100))
        ]
        result = harness.polling(rows, 1500)
        self.assertEqual(result[0]["count"], 3)
        self.assertEqual(result[0]["intervals_ms"], [500, 500])
        self.assertEqual(result[0]["frequency_hz"], 2)
        self.assertIsNone(result[1]["frequency_hz"])

    def test_partial_process_memory_never_claims_a_complete_total(self):
        session = Mock()
        session.send.return_value = {
            "processInfo": [
                {"id": 10, "type": "browser", "cpuTime": 1},
                {"id": 20, "type": "renderer", "cpuTime": 2},
            ]
        }
        with patch.object(
            harness.common,
            "memory",
            side_effect=[
                {"rss": 100, "uss": 50, "pss": None},
                {"rss": 200, "uss": None, "pss": None},
            ],
        ):
            result = harness.browser_resources(session)
        self.assertEqual(result["memory"]["rss"]["bytes"], 300)
        self.assertIsNone(result["memory"]["uss"]["bytes"])
        self.assertEqual(result["memory"]["uss"]["available_processes"], 1)

    def test_process_churn_is_explicit_and_pids_are_not_exported(self):
        before = {
            "memory": {},
            "processes": [{"pid": 10, "cpu_s": 1}, {"pid": 20, "cpu_s": 9}],
        }
        after = {
            "memory": {},
            "processes": [{"pid": 10, "cpu_s": 2}, {"pid": 30, "cpu_s": 0.5}],
        }
        result = harness.resource_delta(before, after)
        self.assertEqual(result["cpu_s"], 1.5)
        self.assertEqual(result["exited_processes"], 1)
        self.assertEqual(result["new_processes"], 1)
        self.assertNotIn('"pid"', json.dumps(result))

    def test_comparisons_withhold_ratios_for_incompatible_inputs(self):
        before = {
            "schema_version": 1,
            "protocol": harness.PROTOCOL,
            "viewports": {"mobile": {"width": 390}},
            "observe_ms": 1000,
            "executions": [
                {
                    "fixture_sha256": "synthetic",
                    "browser_version": "148",
                    "database": {"engine": "sqlite"},
                }
            ],
            "summaries": [
                {
                    "route": "home",
                    "viewport": "mobile",
                    "state": "fresh",
                    "mode": "instrumented",
                    "ready_median_ms": 100,
                }
            ],
        }
        after = deepcopy(before)
        after["summaries"][0]["ready_median_ms"] = 200
        self.assertEqual(harness.compare_reports(after, before)[0]["ratio"], 2)
        after["observe_ms"] = 30000
        result = harness.compare_reports(after, before)[0]
        self.assertFalse(result["comparable"])
        self.assertNotIn("ratio", result)
        self.assertIn("observe_ms differs", result["notes"])
        after["observe_ms"] = 1000
        after["discarded"] = True
        result = harness.compare_reports(after, before)[0]
        self.assertFalse(result["comparable"])
        self.assertIn("current run was discarded (machine load)", result["notes"])

    def test_stress_routes_stay_out_of_the_default_set(self):
        self.assertNotIn("podcast_detail", harness.DEFAULT_ROUTES)
        self.assertNotIn("album_detail_huge", harness.DEFAULT_ROUTES)
        self.assertIn("podcast_show", harness.DEFAULT_ROUTES)
        self.assertTrue(set(harness.ROUTES) >= set(harness.STRESS_ROUTES))


artwork_spec = importlib.util.spec_from_file_location(
    "benchmark_artwork",
    Path(__file__).resolve().parents[3] / "scripts/benchmark_artwork.py",
)
artwork = importlib.util.module_from_spec(artwork_spec)
artwork_spec.loader.exec_module(artwork)


class LoadingBaselineHelperTests(SimpleTestCase):
    def test_artwork_size_follows_the_provider_size_in_the_url(self):
        self.assertEqual(artwork.image_size("/t/p/w500/bench-1.jpg"), (500, 750))
        self.assertEqual(artwork.image_size("/t/p/w780/bench-still-1.jpg"), (780, 439))
        self.assertEqual(artwork.image_size("/t/p/w1280/backdrop.jpg"), (1280, 720))
        self.assertEqual(
            artwork.image_size("/igdb/image/upload/t_cover_big_2x/x.jpg"), (528, 704)
        )
        self.assertEqual(artwork.image_size("/image/x/600x600bb.jpg"), (600, 600))
        self.assertEqual(artwork.image_size("/unknown.png"), (300, 450))

    def test_phase_accounting_splits_requests_by_finish_time(self):
        base = "http://127.0.0.1:9"
        rows = {
            "doc": {
                "url": base + "/",
                "type": "Document",
                "start": 1.0,
                "end": 1.1,
                "bytes": 100,
                "failed": False,
                "cached": False,
            },
            "js": {
                "url": base + "/static/a.js",
                "type": "Script",
                "start": 1.1,
                "end": 1.2,
                "bytes": 50,
                "failed": False,
                "cached": False,
            },
            "api": {
                "url": base + "/api/rows?x=1",
                "type": "Fetch",
                "start": 1.3,
                "end": 1.5,
                "bytes": 30,
                "failed": False,
                "cached": False,
            },
            "img": {
                "url": "https://image.tmdb.org/t/p/w500/1.jpg",
                "type": "Image",
                "start": 1.4,
                "end": 2.0,
                "bytes": 900,
                "failed": False,
                "cached": False,
            },
            "open": {
                "url": base + "/api/slow",
                "type": "Fetch",
                "start": 1.6,
                "end": None,
                "bytes": 0,
                "failed": False,
                "cached": False,
            },
        }
        phases, exported = harness.phase_accounting(
            rows, base, [("to_dcl", 250), ("to_content", 600), ("to_settled", 1001)]
        )
        self.assertEqual(phases["to_dcl"]["requests"], 2)
        self.assertEqual(phases["to_content"]["api_requests"], 1)
        self.assertEqual(phases["to_settled"]["image_bytes"], 900)
        self.assertEqual(phases["unfinished"]["requests"], 1)
        self.assertEqual({row["origin"] for row in exported}, {"app", "artwork"})
        self.assertNotIn("x=1", json.dumps(exported))

    def test_nginx_like_wrapper_caches_static_and_gzips_text(self):
        body = b"x" * 1000

        def app(environ, start_response):
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/javascript"),
                    ("Cache-Control", "no-cache"),
                    ("Content-Length", "1000"),
                ],
            )
            return [body]

        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["headers"] = dict(headers)

        environ = {"PATH_INFO": "/static/ui/a.js", "HTTP_ACCEPT_ENCODING": "gzip"}
        result = b"".join(harness.nginx_like(app)(environ, start_response))
        self.assertEqual(
            captured["headers"]["Cache-Control"], "public, max-age=2592000, immutable"
        )
        self.assertEqual(captured["headers"]["Content-Encoding"], "gzip")
        self.assertEqual(int(captured["headers"]["Content-Length"]), len(result))
        self.assertLess(len(result), 100)

    def test_spread_ignores_missing_values(self):
        self.assertIsNone(harness.spread([None]))
        summary = harness.spread([3, None, 1, 2])
        self.assertEqual(
            (summary["n"], summary["median"], summary["min"], summary["max"]),
            (3, 2, 1, 3),
        )


@tag("slow", "benchmark", "playwright")
@unittest.skipUnless(
    os.environ.get("RUN_PLAYWRIGHT_BENCHMARKS"),
    "Opt-in Playwright benchmark validation",
)
class BrowserObserverIntegrationTests(SimpleTestCase):
    def test_observers_capture_real_lcp_long_tasks_polling_and_sanitized_profile(self):
        from playwright.sync_api import sync_playwright

        html = b"""<html><body><main><h1>Synthetic observer contract</h1></main><script>
          setInterval(() => fetch('/poll?token=never-export-this'), 120);
          setTimeout(() => {const start=performance.now(); while(performance.now()-start<80) {}}, 100);
        </script></body></html>"""

        def app(environ, start_response):
            body = b"{}" if environ["PATH_INFO"] == "/poll" else html
            start_response(
                "200 OK",
                [("Content-Type", "text/html"), ("Content-Length", str(len(body)))],
            )
            return [body]

        server = make_server(
            "127.0.0.1",
            0,
            app,
            server_class=ThreadedWSGIServer,
            handler_class=QuietWSGIRequestHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    context = browser.new_context(service_workers="block")
                    session = browser.new_browser_cdp_session()
                    result = harness.navigate(
                        context,
                        session,
                        f"http://127.0.0.1:{server.server_port}",
                        "/",
                        "instrumented",
                        700,
                        profile=True,
                    )
                    self.assertIsNotNone(result["observers"]["lcp"])
                    self.assertGreater(result["navigation"]["responseStart"], 0)
                    self.assertGreaterEqual(len(result["observers"]["long_tasks"]), 1)
                    self.assertGreaterEqual(result["polling"][0]["count"], 3)
                    self.assertIsNotNone(result["polling"][0]["frequency_hz"])
                    self.assertGreater(len(result["cpu_profile"]["nodes"]), 0)
                    self.assertEqual(result["page_error_count"], 0)
                    self.assertNotIn("never-export-this", json.dumps(result))
                    self.assertTrue(
                        all(
                            str(server.server_port) not in node["callFrame"]["url"]
                            for node in result["cpu_profile"]["nodes"]
                        )
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
