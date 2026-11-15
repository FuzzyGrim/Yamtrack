"""The workflow half of PR-result reuse: the publish gate needs nothing more.

scripts/reuse_pr_tests.py proves the pull request run tested the pushed tree.
Equal trees mean the same workflow file, but that file chooses tests by event:
a pull request run and the publish gate on the same tree can run different
tests. Reuse is only sound if every test the publish gate would run also ran
on the pull request. These tests hold the workflow to that, so a later edit
that gives the publish gate a test of its own fails here instead of being
skipped silently on the next merge.

If one of these fails because the shard step was rewritten (for example test
selection moved into a script), re-establish the property for the new wiring
and update this file with it. Do not delete the check.

Run: .venv/bin/python -m unittest scripts/tests/test_reuse_pr_workflow.py
"""

import ast
import itertools
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP_TESTS = ROOT / ".github" / "workflows" / "app-tests.yml"
DOCKER_IMAGE = ROOT / ".github" / "workflows" / "docker-image.yml"
TEST_SOURCES = (ROOT / "src", ROOT / "mcp_server")

# Expressions the shard's test step may read, and nothing else. Each one is
# substituted per case below. A new expression is a new way for the two paths
# to differ, so it fails the test until it is accounted for here.
KNOWN_EXPRESSIONS = {"github.event_name", "matrix.suite"}
SELECTION_STEP = "Run Tests"

# Options of scripts/test.sh that take a value, and the ones that choose tests.
VALUE_OPTIONS = {"--exclude-tag", "--tag", "--durations", "--shard"}
NEUTRAL_OPTIONS = {"--timing", "--durations"}


def load(path: Path) -> dict:
    """Parse a workflow file."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def squash(text: str) -> str:
    """Collapse whitespace so folded YAML expressions compare as one line."""
    return re.sub(r"\s+", " ", str(text)).strip()


def shard_job() -> dict:
    """Return the test_shards job of App Tests."""
    return load(APP_TESTS)["jobs"]["test_shards"]


def selection_step(job: dict) -> dict:
    """Return the one step that runs the tests."""
    steps = [step for step in job["steps"] if step.get("name") == SELECTION_STEP]
    if len(steps) != 1:
        msg = f"test_shards needs exactly one {SELECTION_STEP!r} step"
        raise AssertionError(msg)
    return steps[0]


def invocations(script: str, *, event: str, suite: str, apps: str, release: bool):
    """Run the step's script with scripts/test.sh replaced by a recorder.

    Returns the argument lists of every scripts/test.sh call it makes.
    """
    found = set(re.findall(r"\$\{\{\s*([^}]*?)\s*\}\}", script))
    unknown = found - KNOWN_EXPRESSIONS
    if unknown:
        msg = (
            f"the {SELECTION_STEP!r} step reads {sorted(unknown)}; decide how each "
            "value differs between a pull request and the publish gate"
        )
        raise AssertionError(msg)
    script = re.sub(r"\$\{\{\s*github\.event_name\s*\}\}", event, script)
    script = re.sub(r"\$\{\{\s*matrix\.suite\s*\}\}", suite, script)
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "calls"
        recorder = 'record() { printf "%s\\x1f" "$@" >> "$CALLS"; printf "\\n" >> "$CALLS"; }\n'
        body = recorder + script.replace("scripts/test.sh", "record")
        subprocess.run(  # noqa: S603
            ["bash", "-euo", "pipefail", "-c", body],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                "PATH": "/usr/bin:/bin",
                "CALLS": str(log),
                "TEST_APPS": apps,
                "RELEASE_QUALITY_GATES": "true" if release else "false",
            },
        )
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [line.split("\x1f")[:-1] for line in lines]


def parse(args: list[str]) -> tuple[list[str], set[str], set[str], set[str]]:
    """Split one scripts/test.sh call into labels, tags, excluded tags, flags."""
    labels, tags, excluded, flags = [], set(), set(), set()
    values = iter(args)
    for arg in values:
        if arg in VALUE_OPTIONS:
            value = next(values)
            if arg == "--tag":
                tags.add(value)
            elif arg == "--exclude-tag":
                excluded.add(value)
            elif arg not in NEUTRAL_OPTIONS:
                flags.add(f"{arg}={value}")
        elif arg.startswith("-"):
            if arg not in NEUTRAL_OPTIONS:
                flags.add(arg)
        else:
            labels.append(arg)
    return labels, tags, excluded, flags


def decorator_tags(node) -> set[str]:
    """Return the names given to Django's @tag(...) on a class or function."""
    names = set()
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "tag"
        ):
            names |= {
                arg.value
                for arg in decorator.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            }
    return names


def tagged_tests(tag_name: str):
    """Yield (where, effective tags) for every class or test carrying tag_name.

    Effective tags are the class's plus the method's, which is what Django's
    tag filter applies.
    """
    for source in TEST_SOURCES:
        for path in source.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if tag_name not in text:
                continue
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                class_tags = decorator_tags(node)
                if tag_name in class_tags:
                    yield f"{path.relative_to(ROOT)}::{node.name}", class_tags
                for item in node.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        method_tags = decorator_tags(item)
                        if tag_name in method_tags:
                            yield (
                                f"{path.relative_to(ROOT)}::{node.name}.{item.name}",
                                class_tags | method_tags,
                            )


class PublishGateNeedsNothingThePullRequestSkippedTests(unittest.TestCase):
    """Every test the publish gate selects also runs on the pull request."""

    def test_every_shard_selects_no_test_the_pull_request_left_out(self):
        job = shard_job()
        script = selection_step(job)["run"]
        shards = job["strategy"]["matrix"]["include"]
        self.assertTrue(shards, "test_shards has no matrix entries")
        for shard in shards:
            suite, apps = shard["suite"], shard.get("apps", "")
            with self.subTest(suite=suite):
                pull = invocations(
                    script, event="pull_request", suite=suite, apps=apps, release=False
                )
                gate = invocations(
                    script, event="push", suite=suite, apps=apps, release=True
                )
                self.assertEqual(len(pull), 1, f"{suite}: pull request calls {pull}")
                self.assertEqual(len(gate), 1, f"{suite}: publish gate calls {gate}")
                p_labels, p_tags, p_excluded, p_flags = parse(pull[0])
                g_labels, g_tags, g_excluded, g_flags = parse(gate[0])

                self.assertEqual(g_labels, p_labels, f"{suite}: different test labels")
                self.assertEqual(g_flags, p_flags, f"{suite}: different runner flags")
                # --tag narrows a run. The gate narrowing less than the pull
                # request would select tests the pull request never ran.
                self.assertTrue(
                    not p_tags or (g_tags and g_tags <= p_tags),
                    f"{suite}: the gate selects tags {g_tags}, the pull request {p_tags}",
                )
                # A tag the pull request excludes and the gate does not is
                # only harmless if every test carrying it is excluded from the
                # gate by another tag.
                for name in sorted(p_excluded - g_excluded):
                    for where, tags in tagged_tests(name):
                        self.assertTrue(
                            tags & g_excluded,
                            f"{suite}: {where} is tagged {name!r}, which pull "
                            f"requests skip, but the gate excludes none of {tags}",
                        )

    def test_the_tag_scan_sees_the_known_exclusion(self):
        # Guards the scan itself: if it found nothing it would pass vacuously.
        found = dict(tagged_tests("flaky_alphabet_rail"))
        self.assertTrue(found, "no test is tagged flaky_alphabet_rail any more")
        for tags in found.values():
            self.assertIn("playwright", tags)

    def test_the_shard_job_reads_nothing_that_differs_by_event(self):
        # Outside the selection step, a shard must behave the same on a pull
        # request and on the publish gate, and see no secrets: fork pull
        # requests get none, so a secret could only add tests on the gate.
        job = dict(shard_job())
        job["steps"] = [
            step for step in job["steps"] if step.get("name") != SELECTION_STEP
        ]
        text = yaml.safe_dump(job)
        for needle in ("github.event_name", "github.ref", "inputs.", "secrets."):
            self.assertNotIn(needle, text)
        step = selection_step(shard_job())
        self.assertNotIn("secrets.", yaml.safe_dump(step))
        self.assertEqual(
            set(step.get("env", {})), {"RELEASE_QUALITY_GATES"}, "new step input"
        )


class SkippingIsReachableOnlyThroughTheDecisionTests(unittest.TestCase):
    """The decision job is the only thing that can skip the shards."""

    def test_decision_output_is_forced_to_run_off_the_publish_gate(self):
        output = load(APP_TESTS)["jobs"]["reuse_decision"]["outputs"]["decision"]
        self.assertEqual(
            squash(output),
            "${{ (inputs.release_quality_gates && github.event_name == 'push' && "
            "github.ref == 'refs/heads/latest' && "
            "steps.decide.outputs.decision == 'reuse') && 'reuse' || 'run' }}",
        )

    def test_shards_skip_only_on_a_successful_reuse_decision(self):
        self.assertEqual(
            squash(shard_job()["if"]),
            "${{ !cancelled() && !(needs.reuse_decision.result == 'success' && "
            "needs.reuse_decision.outputs.decision == 'reuse') }}",
        )

    def test_no_other_job_is_conditional_on_the_decision(self):
        # Only the shards may be skipped and only the roll-up may read why.
        jobs = load(APP_TESTS)["jobs"]
        readers = {
            name
            for name, job in jobs.items()
            if "reuse_decision"
            in yaml.safe_dump({k: v for k, v in job.items() if k != "steps"})
            or "needs.reuse_decision" in yaml.safe_dump(job.get("steps", []))
        }
        self.assertEqual(readers, {"test_shards", "test"})

    def test_roll_up_passes_only_on_tested_shards_or_a_real_reuse(self):
        # Runs the roll-up's own script for every combination of results.
        job = load(APP_TESTS)["jobs"]["test"]
        self.assertEqual(job["name"], "test (3.12)")
        self.assertIn("migration_checks", job["needs"])
        self.assertIn("oauth_postgres", job["needs"])
        steps = [s for s in job["steps"] if "SHARD_RESULT" in s.get("env", {})]
        self.assertEqual(len(steps), 1, "one roll-up step reads the shard result")
        step = steps[0]
        other_inputs = set(step["env"]) - {
            "DECISION_RESULT",
            "DECISION",
            "SHARD_RESULT",
            "MIGRATION_RESULT",
            "OAUTH_RESULT",
        }
        results = ("success", "failure", "skipped", "cancelled")
        always = ("success", "failure", "skipped")
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / "output"
            for decision_result in ("success", "failure"):
                for decision in ("reuse", "run"):
                    for shards in results:
                        for migration, oauth in itertools.product(always, always):
                            env = dict.fromkeys(other_inputs, "success")
                            env.update(
                                PATH="/usr/bin:/bin",
                                GITHUB_OUTPUT=str(output),
                                DECISION_RESULT=decision_result,
                                DECISION=decision,
                                SHARD_RESULT=shards,
                                MIGRATION_RESULT=migration,
                                OAUTH_RESULT=oauth,
                            )
                            passed = (
                                subprocess.run(  # noqa: S603
                                    ["bash", "-e", "-c", step["run"]],  # noqa: S607
                                    env=env,
                                    capture_output=True,
                                    check=False,
                                    timeout=30,
                                ).returncode
                                == 0
                            )
                            expected = migration == oauth == "success" and (
                                shards == "success"
                                or (
                                    shards == "skipped"
                                    and decision_result == "success"
                                    and decision == "reuse"
                                )
                            )
                            self.assertEqual(passed, expected, env)

    def test_publishing_still_needs_the_smoke_and_the_whole_gate(self):
        build = load(DOCKER_IMAGE)["jobs"]["build"]
        self.assertEqual(set(build["needs"]), {"test", "quality"})
        condition = squash(build["if"])
        self.assertIn("needs.test.result == 'success'", condition)
        self.assertIn("needs.quality.result == 'success'", condition)
        quality = load(DOCKER_IMAGE)["jobs"]["quality"]
        self.assertEqual(quality["uses"], "./.github/workflows/app-tests.yml")
        self.assertIs(quality["with"]["release_quality_gates"], True)


if __name__ == "__main__":
    unittest.main()
