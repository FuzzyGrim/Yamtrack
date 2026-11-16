import faulthandler
import multiprocessing
import os

from django.db.backends.signals import connection_created
from fakeredis import FakeConnection

from config.test_network import install_test_network_guard, test_network_enabled

from .settings import *  # noqa: F403

# Django's default SQLite test database is a shared in-memory URI. It cannot use
# a persistent WAL/DELETE journal, and the production connection hook is for
# durable database files. The SQLite safety helper has dedicated coverage for
# both memory and persistent names, so do not emit a corruption-grade mismatch
# for the expected MEMORY test journal.
if USING_SQLITE_DATABASE:  # noqa: F405
    connection_created.disconnect(configure_sqlite_connection)  # noqa: F405

# Shards run by scripts/test.sh --shards are separate processes started at
# once. On PostgreSQL they would all create and drop the same test database.
if not USING_SQLITE_DATABASE:  # noqa: F405
    from config.test_sharding import shard_database_name

    _test_database = DATABASES["default"].setdefault("TEST", {})  # noqa: F405
    _test_database["NAME"] = shard_database_name(
        _test_database.get("NAME") or f"test_{DATABASES['default']['NAME']}",  # noqa: F405
        os.environ.get("FLOPPY_TEST_SHARD"),
    )

# Ordinary tests must be deterministic and offline. An explicit network-tag run
# opts out through FLOPPY_TEST_ALLOW_NETWORK before this settings module loads.
install_test_network_guard()

# AniBridge is a runtime data source, not part of an ordinary test's contract.
# Use a minimal deterministic snapshot for the mapping cases exercised by the
# offline suite. Explicit network-tag runs leave this unset and verify the real
# download path instead.
ANIBRIDGE_MAPPING_DATA_OVERRIDE = None
if not test_network_enabled():
    ANIBRIDGE_MAPPING_DATA_OVERRIDE = {
        "tvdb_show:74796:s2": {"mal:269": {"1-20": "21-40"}},
        "tvdb_show:74796:s17": {"mal:53998": {"14-": "1-"}},
        "anidb:3651:R": {"mal:849": {}},
    }

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_CACHE_URL,  # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "COMPRESSOR": "app.cache_safety.SizedZlibCompressor",
            "CONNECTION_POOL_KWARGS": {"connection_class": FakeConnection},
        },
    },
}

# Django's default PBKDF2 hasher costs ~300 ms per call on CI-class hardware.
# The suite creates a user in hundreds of setUp methods and logs in through
# `client.login` hundreds more times, so that lands squarely on every
# auth-touching test: measured at 32 s of the 54 s that `app.tests.views.test_history`
# spends executing tests (60%). Tests assert on authorization, never on the
# strength of the hash, so use the cheapest correct hasher. Production is
# unaffected -- this file is only loaded by the test settings module.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Building the test database replays 422 migrations, measured at 141 s before a
# single test runs -- the same floor for one targeted test as for the whole
# suite. 99.6% of that is migration application, and `users` alone is 89 s of
# it: seventeen migrations drop and re-add the ten `*_sort_valid` check
# constraints on `users_user`, and SQLite rebuilds the whole table for each of
# those ~30 operations.
#
# FLOPPY_TEST_FAST_DB=1 builds the schema straight from the models instead.
# It is opt-in, not the default, because it stops the suite exercising the
# migration graph and skips RunPython data migrations. Use it for iterating;
# let CI and any migration-sensitive run replay the real thing.
if os.environ.get("FLOPPY_TEST_FAST_DB") == "1":
    # PostgreSQL validates deferred user -> auth -> contenttypes foreign keys
    # when syncdb finishes, before migrations for the remaining apps run.
    # Build those referenced tables in the same syncdb pass.
    MIGRATION_MODULES = dict.fromkeys(
        ("app", "users", "lists", "integrations", "events", "auth", "contenttypes")
    )

# Django's parallel runner has been seen to lose worker results and then wait
# for them forever: every worker idle, the parent blocked, and the pool's
# _handle_results thread gone. See docs/architecture/test-suite-cost.md.
#
# faulthandler costs nothing until something goes wrong and turns a hard crash
# into a stack trace. FLOPPY_TEST_WATCHDOG=<seconds> additionally dumps every
# thread's stack if the run outlives that budget, which is how to capture the
# hang the next time it happens.
faulthandler.enable()
_watchdog_seconds = os.environ.get("FLOPPY_TEST_WATCHDOG")
if _watchdog_seconds:
    faulthandler.dump_traceback_later(float(_watchdog_seconds), repeat=True)

# "spawn" re-imports instead of forking. It was used to test (and disprove) the
# theory that the lost results came from forking workers out of a parent that
# already had pool threads running; it hangs under spawn too. Kept as a knob.
_start_method = os.environ.get("FLOPPY_TEST_START_METHOD")
if _start_method:
    multiprocessing.set_start_method(_start_method, force=True)

# The race is not root-caused, so make it survivable rather than fatal: this
# runner counts results instead of waiting for StopIteration, bounds every wait
# including pool teardown, and re-runs any subsuite whose result never arrived.
# It skips nothing.
TEST_RUNNER = "config.test_runner.ResilientDiscoverRunner"


CELERY_TASK_ALWAYS_EAGER = True
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_STORE_EAGER_RESULT = True

TESTING = True

# Keep test output bounded: the console handler writes to the real stderr,
# bypassing unittest --buffer, so INFO logs flood parallel test runs.
LOGGING["handlers"]["console"]["level"] = "WARNING"  # noqa: F405
LOGGING["root"]["level"] = "WARNING"  # noqa: F405

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"

# Trakt API key for testing (production default is empty; see #464)
TRAKT_API = "test_trakt_api_key"

# Hardcover token for testing (production default is empty; see #1025)
HARDCOVER_API = "Bearer test_hardcover_token"
