# Coding Agent Instructions

## Standing UI migration rule

Whenever an agent creates, edits, or updates a UI component—including copy or
styling—it must migrate that component to the shared React pattern in the same
change. This incrementally prepares reusable presentation for eventual native iOS
and Android apps and reduces future bulk migration work.

Reuse or extend shared UI components before creating new ones. Keep domain logic,
permissions and transport in feature adapters. Shared presentation is written with
`react-native` primitives and theme tokens, takes props only and touches no browser
API; browser behavior goes in a web adapter, and a component that cannot be written
that way is registered as web-only with a reason (see
`docs/architecture/ui-modernization.md`, "From React on the web to native apps"). Preserve appearance, behavior,
accessibility, responsive states and performance: users should not notice the
framework migration. Migration does not authorize redesign or feature removal.
The unit is the reusable component, not every sibling in its template file.

Every UI change must update `docs/ui/component-register.md` and
`docs/ui/source-map.md`: identify each changed component and its consumers/surfaces,
record legacy, partial/coexisting or fully React-owned status, remaining consumers
and retirement conditions. Record browser verification separately from actual iOS
and Android verification. UI plans and completion summaries must name migrated
components, ledger updates and blockers.

Solve architectural incompatibilities; do not bypass checks or reclassify components.
If reasonable solutions encounter an external blocker, report evidence and attempts;
the affected work remains incomplete. Record actual platform verification separately.

## Cross-platform by default

Web, iOS and Android are all first-class. Unless the task names one platform, a UI
change lands on every applicable surface in the same PR. Never ship web-only or
native-only by accident; a deliberate carve-out is stated in the PR with its reason.
"Implemented on all platforms" and "verified on all platforms" are separate claims.

Parity is the default for behaviour, performance and bug fixes, not only for UI. Web
and native apps operate at parity unless the user asks for a device-specific
carve-out or a real platform limit forces one. When a problem is found or fixed on
one platform (a slow flip, a failing request, late artwork, a layout bug), ask the
same question of the other before calling it done: does it have the same problem,
and does the same improvement apply? Then apply it, or measure and report why it does
not. A platform-tuned fix (for example native-only image windowing) must name the
other platform's status in the summary: same change applied, different change
applied, or no change needed with evidence. Do not leave the other platform unmeasured
and silent. Shared components and server changes are the default route to parity.

Every device-specific carve-out gets a short comment at the code that makes it, saying
why the platforms differ (for example "native mounts only nearby cards; web already
lazy-loads images"). A reader who is asked or challenged later should find the reason
next to the code, not only in a PR. Also list the carve-out in the register entry.

- Before editing, trace which component each surface (web adapter, `native-proof/`,
  and any platform-specific file) actually imports. Do not assume a shared component is
  active everywhere. `npm run check:page-parity` shows the registry state.
- Platform-specific appearance is fine. Platform-specific feature loss is not, unless
  asked for or forced by a real platform limit. No WebViews or web-only fallbacks just to
  pass a check.
- Run `scripts/env_capabilities.sh` once per session (add `--bootstrap` on a fresh
  cloud checkout) to learn what can run here. Do not re-probe; do not install toolchains,
  boot paid hosted runtimes or change account settings without asking.
- Capture before and after for every visible change with the strongest evidence the
  environment offers (`docs/agents/pr_screenshots.md`, `scripts/review_screenshots.py`).
  Extend those; do not build a second system. Open the images and look at them. On a
  Mac, `docs/agents/native_cloud_runtimes.md` is the recipe for real iOS and Android
  frames (`scripts/native_shots.sh <label>`).
- Provide the user a review artifact for every user-facing change: a clearly labeled
  before/after comparison that shows the affected state and is linked or displayed in
  the completion message (and in the PR description when preparing a PR). Use the
  existing screenshot workflow for visual changes; use an appropriate recording or
  concrete before/after output for behavior that still images cannot show. Keep task
  artifacts out of tracked documentation. If either side cannot be captured, provide
  the available evidence, state the blocker and do not claim the comparison is complete.
- The v26.10.29 web release is the visual baseline for React and React Native components.
  Treat a layout or visual change from it as a regression unless a redesign is documented.
- Report one evidence level per platform (web, iOS, Android), never a combined pass:
  1. **Runtime**: the real Expo app ran on a simulator, emulator, device or hosted
     runtime and screenshots were inspected. On a Mac, if Xcode or an Android SDK is
     installed, start the simulator rather than treating it as unavailable.
  2. **Expo Web + logic**: real shared components rendered in Chromium, plus tests of
     native-only layout and state logic. Native appearance is unverified. A web
     screenshot is never evidence of iOS or Android rendering.
  3. **Structural**: builds, platform routing and tests pass; no rendering evidence.
  4. **Unverified**: say what blocked it and the remaining risk.
- Reviewers (human, Codex, `/code-review`) treat a UI diff that touches only one
  surface as a finding unless the PR names the carve-out, and treat a missing evidence
  level for an applicable platform as a gap to report, not to assume.

## Operating Principles

Do not stop an authorized task merely because it requires a migration, unfamiliar
architecture, or verification work. Implement the necessary changes and use the
available devices and tools; report incomplete work only after making a sustained
attempt and identifying a concrete blocker that prevents further progress.
When the user demonstrates a defect or asks you to inspect it in the course of a
fix, treat the demonstration as part of the work: investigate, implement the fix,
and verify it. Do not end with findings alone when a fix is within the authorized
task and can be made safely.

When the user supplies a plan and asks for the work, execute it through implementation and verification; do not stop after restating the scope. Continue until complete or an evidenced external blocker prevents further progress.

Keep it simple. Simple is better than complex.
Make the smallest maintainable change that solves the actual request.
Prefer existing patterns over new abstractions.
Avoid broad refactors, speculative helpers, and clever architecture unless clearly justified.
Do not build for hypothetical future use. Implement the current need cleanly and stop there.
Use judgment. Read enough surrounding code to understand the existing pattern, then avoid unnecessary exploration. Validate based on risk.
Assume the user is a principal engineer.
Optimize for correctness, speed, judgment, and token efficiency.
Correct the user when appropriate.

## Success Criteria

Done means:

- the requested behavior is implemented
- the change is minimal and follows existing patterns (Unless a large task was assigned)
- risky behavior was validated, or validation was intentionally skipped with a reason
- remaining risks are stated plainly

## Context Discipline

Protect context aggressively.

As tool output, file reads, and conversation history grow, useful signal gets diluted. Keep active context focused on the current decision.

Before opening files or running broad searches, ask:

1. What exact question am I answering?
2. Which file, symbol, route, or component is most likely relevant?
3. Can I inspect a narrower slice first?
4. Can `rg`, imports, references, or file names locate the answer?

Prefer targeted searches, focused file sections, nearby call sites, diffs, capped logs, and targeted test output.

Avoid dumping full files, full logs, unrelated directories, or broad repo exploration after the relevant code is found.

When context gets large, summarize the current task state and keep only:

- decisions
- relevant file paths
- changed behavior
- unresolved risks

## Subagents

Use subagents only when they save context, save time, or materially improve output quality.

For research, review, and exploration tasks, avoid confirmation bias. Do not pass a preferred conclusion. Ask the subagent to investigate, compare, or verify, and require evidence, tradeoffs, uncertainty, and better alternatives.

Good uses:

- repo exploration
- scoped implementation
- QA or review
- documentation/API checks
- web research
- unfamiliar code research
- copywriting/content variants

Avoid subagents for trivial work the main agent can finish faster.

When using a subagent, assign a narrow task and require:

- findings
- files inspected
- files changed, if any
- validation run, if any
- risks or uncertainty

The main agent owns final judgment and integration.

## Code Changes

Prefer direct edits using available environment tools like `apply_patch`

Before adding helpers, maps, files, abstractions, or validation layers, ask:

1. Can this be done inline?
2. Can existing code already do this?
3. Is this solving the exact issue?
4. Is reuse or readability clearly improved?

Do not create new abstractions, helper layers, provider interfaces, background tasks, docs, or config files unless the current task clearly needs them.
Before adding a new function, class, setting, management command, or integration hook, check whether an existing pattern already solves the same problem.

For bugs, patch the narrow failing path first.
For small behavior changes, make the direct edit first.
Avoid unrelated cleanup.

Split work into reviewable patches when possible:

- behavior change
- mechanical refactor
- tests
- docs

Do not mix these unless the user explicitly asks for a broad rewrite.

For complex tasks:

- identify the minimal path through the codebase
- split work into small patches
- validate only the risky parts
- keep a short running summary of decisions, changed files, and remaining risks

## Validation

Match validation to risk.

Skip validation by default for low-risk changes and say so plainly.

Never skip validation when touching:

- migrations
- models
- importers
- webhooks
- auth
- permissions
- settings
- Celery tasks or background jobs
- cache behavior
- persisted data
- external APIs
- upgrade paths
- any PR that spans more than one app boundary unless explicitly told not to validate

Low-risk examples:

- copy changes
- labels
- static content
- CSS or Tailwind spacing
- small JSX structure changes
- minor refactors with no behavior change

Also validate when:

- a previous command failed
- the user asked for validation
- the change affects multiple routes, components, or packages

Prefer targeted tests first (`scripts/test.sh <dotted.label>`), then `uv run --no-sync ruff check src`, then the fast suite (`scripts/test.sh`) only when risk justifies it. See the Testing section.

Prefer the cheapest useful check:

1. targeted test
2. type check affected package
3. lint affected files
4. build only when build behavior matters

Do not run a full test suite or full build unless risk justifies it or the user asks.

### Preexisting Failures (Baseline Zero Policy)

App tests, ruff, and lint were driven to zero as a dedicated cleanup effort. The expectation is to hold that baseline at zero, not merely avoid adding to it. Baseline zero means zero — finding a preexisting failure is the trigger to fix it, not to leave it and move on.

If you discover a test/ruff/lint failure while working on something unrelated:

1. Check it against baseline to confirm it's preexisting and not introduced by your change.
2. Fix it. Do not defer it, note it as acceptable to skip, or treat "it's fine not to fix it inline" as the default — that lets the baseline erode one skipped finding at a time. Skipping is the exception, reserved for fixes that are large, risky, or clearly outside what you're equipped to validate right now — and even then say so explicitly and why, don't just quietly pass over it.
3. Tell the user what you found and that you fixed it (or, in the exception case, why you didn't).
4. Prefer landing the fix in a separate commit, and where practical a separate PR, so the cleanup is easy to review independently of the change at hand — separate from the main change, not skipped.

## Command Output

Protect context usage. **Any command with unknown or potentially large output must be byte-capped.**

Default pattern:

```bash
COMMAND 2>&1 | head -c 4000
```

For logs or recent failures:

```bash
COMMAND 2>&1 | tail -c 4000
```

Do not rely on line limits as the only cap. A single line can be huge. Avoid using only:

```bash
head -n
tail -n
sed -n '1,20p'
```

Scope before printing content:

- list files with `rg -l` before printing matches
- count matches with `rg -c` before reading them
- search specific paths instead of whole directories
- use `rg -m`, `--max-count`, `--max-filesize`, and small context when useful
- inspect file size before reading unknown generated files, logs, JSONL, or minified JSON

For commands where the exit code matters, capture output first, print a capped amount, then exit with the original status:

```bash
tmp="$(mktemp)"
COMMAND >"$tmp" 2>&1
status=$?
tail -c 5000 "$tmp"
rm -f "$tmp"
exit "$status"
```

Avoid unbounded output from:

```bash
cat path/to/file
rg -n "term" .
find .
ls -R
git diff
npm test
npm run build
select *
```

Use bounded versions instead:

```bash
rg -l "term" . | head -c 2000
rg -n -m 20 "term" src 2>&1 | head -c 2000
git diff -- path/to/file 2>&1 | head -c 6000
find . -type f 2>&1 | head -c 2000
```

If the capped output is insufficient, narrow the command. Do not repeatedly increase the cap unless the task requires more context.

## Communication

Before editing, state the approach only for non-trivial tasks.

During complex work, keep updates very short:

- what was found
- what changed
- what risk remains

After work, summarize:

- what changed
- files touched
- validation run, or why skipped
- remaining risk

Keep summaries short. Do not explain obvious edits.

Oververbosity:low

# Floppy Notes
Floppy is a Django 5.2 app for self-hosted media tracking with Celery workers and Redis. Tailwind CSS output is committed under `src/static/css/`, and templates load `src/static/css/main.css` via `src/templates/base.html`.

## Branch Policy
- `upstream` (formerly named `dev` in this fork) must be an exact mirror of `FuzzyGrim/Yamtrack:dev`. Never commit to it, target it with a PR, or add fork-only edits. Refresh it only by exact fast-forward/reset to the upstream remote.
- `latest` is the fork integration branch for day-to-day work and semantic upstream ports. Do not merge or rebase `upstream` into `latest`.
- `release` is for versioned release/container publication flow, not the primary integration branch.

## Upstream Resolution Workflow

The upstream-port ledger (`UPSTREAM_PORTS.md`, kept locally and never committed) is the durable source of truth. “Up to date” means zero unclassified upstream outcomes, not matching Git histories.

For each new outcome in `last-reviewed..upstream/dev`:

1. Record the canonical upstream commits and group related implementation, tests, migrations, and repairs.
2. Choose exactly one evidence-backed decision: Ported, Adapted, Superseded, Deferred, Discarded, or Pending with an owner issue.
3. Prefer a clean cherry-pick only for isolated changes whose paths and architecture still match. Otherwise implement the behaviour through Floppy's current public APIs and bounded contexts.
4. Preserve Floppy features, deployment paths, security/privacy, and user data. Dependency versions and generated assets are not upstream sources of truth.
5. Port useful regression cases even when the implementation is superseded or reimplemented.
6. For schema work, define final semantics first, audit existing data, and generate new Floppy migrations against the current graph. Never cherry-pick upstream migration files or reproduce an unsafe intermediate migration.
7. Update the ledger, owning issue, documentation, configuration examples, API schema/output, wiki, and upgrade guidance affected by the outcome.
8. Validate in proportion to risk using the repository's normal targeted, migration, database, container, and full-suite gates. Record actual evidence in the PR.

Models/migrations and divergent UI normally require manual adaptation. Provider modules and isolated importers may permit small ports. CI, Docker, and dependencies move as coherent platform packages. Branding, version bumps, merge commits, generated churn, and individual dependency-bot commits are normally discarded with the ledger's reason codes.

## Repository Map
- `src/` Django project code (apps: `app`, `users`, `lists`, `integrations`, `events`; config in `config/`).
- `src/templates/` and `src/static/` for UI templates and CSS assets.
- `src/static/css/main.css` is the committed Tailwind output loaded by templates.
- `src/db/` local SQLite artifacts.
- `docs/agents/` issue and workflow notes.
- `.github/workflows/` CI definitions.
- `Dockerfile`, `docker-compose*.yml`, `entrypoint.sh`, `nginx.conf`, `supervisord.conf` for container runtime.
- `wiki/` is a separate Git repository for the project wiki (edit and commit there, not in this repo).

## Workflow Notes
- Keep wiki pages in `wiki/` so they can be edited locally and pushed to the wiki repo.
- Treat `wiki/` as its own git repo (not a submodule); run commits/pushes from `wiki/`.
- Do not add `wiki/` to the main repo index; it should remain untracked here.
- Primary local development is source-run Django with Redis, Celery worker/beat, and Tailwind watcher.
- Secondary Docker usage is for deployment or quick smoke runs; the compose files use the prebuilt `ghcr.io/dannyvfilms/floppy` image.

## Documentation Policy
`docs/` holds only permanent, public-facing, non-situational documentation: install and deployment guides, architecture contracts, integration guides, and playbooks that stay true as the code changes. Everything else is temporary agent storage during development and must not be committed: plans, handoffs, investigations, analyses, requirement checklists, benchmark and PR screenshots, upstream-port ledgers, and per-task reports. Keep those under `docs/plans/` (ignored by git) and delete them when the work lands. When unsure whether a document belongs, apply this rule: if it describes a moment, a task or a person's working context rather than how the project works today, it does not belong in the repo. Never commit absolute local paths, usernames or machine details.

## Agent Docs
- `docs/agents/domain_model.md`: generated vocabulary guide; regenerate and check it with the commands under Local Commands.
- `docs/agents/media_type_integration.md`: playbook for adding new media types safely.
- `docs/agents/music_integration.md`: music-specific data model and UI integration notes.
- `docs/agents/psn_integration.md`: how a PSN sync turns lifetime playtime into dated game entries (remembered totals, Sessions vs Repeats, the setup choice).
- `docs/agents/pocketcasts_workflow.md`: Pocket Casts import/schedule workflow details.
- `docs/agents/clz_import.md`: the CLZ importer's header-mapped contract and the shared custom-field resolution layer it sits on.
- `docs/agents/migration_sync_playbook.md`: hard-gate flow for adapting accepted upstream migration outcomes to Floppy's current graph.
- `docs/agents/view_authentication.md`: guide for view authentication and declaring public route exemptions.
- `docs/agents/pr_screenshots.md`: `scripts/pr_screenshots.py` captures before/after screenshots of a PR and posts them to its description.
- `docs/agents/native_cloud_runtimes.md`: how to get real iOS and Android screenshots: locally with Expo Go, the booted simulators and `scripts/native_shots.sh` (tested), or hosted via a contributor's own Expo account (opt-in; nothing runs in CI).
- `docs/architecture/device-poses.md`: the six Duo poses (closed, open, partial; vertical, horizontal), how a device with no hinge gets one from its size, and how Expo and the PWA read and address them.
- `docs/architecture/log-redaction.md`: the log boundary contract — where credentials are removed, what the rules match, and what they do not cover.
- `docs/architecture/log-levels.md`: what each log level means, the start/progress/finish lines every long-running process must write so a hang is visible, and what the Settings > Advanced downloads include.
- `docs/architecture/theming.md`: the theme resolution contract and the six theme states any colour change must hold.
- `docs/architecture/podcast-plays.md`: what a podcast listen is (`PodcastPlay`), the one service that writes it, how a direct tracker save is carried onto its play, and the API lifecycle.
- `docs/architecture/webhook-write-rules.md`: when a media-server webhook may write tracking rows — the shared policy table every integration is listed in, its exceptions, and the test that catches a missing row.
- `docs/architecture/webhook-delivery.md`: the sync/async boundary, expected webhook queue latency, the structured `event_id`/`queue_delay_ms`/`outcome` log contract, time limits, and the ordering guard that stops a late play erasing a scrobble (#1708).
- `docs/architecture/connection-health.md`: when an integration account may be marked `connection_broken` (rejected credentials only), how scheduled work re-probes it, and the test that catches a direct write.
- `docs/architecture/api-scopes.md`: the integration-token scope contract — the vocabulary, the enforced view map, and what adding an endpoint requires.
- `docs/architecture/build-inputs.md`: inventory, digests and update process for vendored browser libraries and installer downloads (`src/static/vendor-manifest.json`).
- `docs/architecture/outbound-fetch.md`: the outbound boundary for user-configured URLs — the SSRF controls, the residual DNS-rebinding gap, and why artwork uses a different path.
- `docs/architecture/library-query.md`: the shared engine for list-shaped surfaces (media list, API, smart lists, Home shelves) — how a page stays bounded, and how to add a filter or sort.
- `docs/architecture/statistics-sync.md`: how Statistics stay warm — dirty days and snapshots in the database, one budgeted per-user sync, the reconciler that makes a lost Celery message harmless, and which write paths must mark changes.
- `docs/architecture/game-artwork.md`: where a game's logo and backdrop come from — the provider order, the stored provenance, and how ScreenScraper credentials are kept out of URLs.
- `docs/architecture/local-covers.md`: covers a person uploads, links or picks — where the files live, what is accepted, how they are fetched, who may change and see them.
- `docs/architecture/media-card.md`: the one media card and its surfaces — what may differ between pages and why, how a page loads the viewer's rating and status, and the test that catches a grid that drifts.
- `docs/architecture/history-memory.md`: what a History request costs a web worker — the item columns history must not load, which paths are bounded by the response and which are still bounded by the history.
- `docs/architecture/in-place-navigation.md`: how to make a link swap the page in place with `hx-boost` instead of a full reload — what the app guarantees, what a page must clean up, when not to use it, and how to check it.
- `docs/architecture/view-caches.md`: the media list, Home and time-left caches — the one function that clears them, what each kind of change clears, and which write paths must call it.
- `docs/integrations/nuvio-client-guide.md`: what a third-party tracking client implements, with `api.tests.test_nuvio_conformance` as its runnable half.
- `docs/integrations/flexget.md`: how a download tool (FlexGet's `floppy_list`, or `curl`) reads lists and reports owned media by provider id, with `api.tests.test_fork_media_collection` as its runnable half.
- `docs/integrations/wetrakr.md`: sending plays, statuses and ratings out to WeTrakr — what is verified, what is only transcribed from its docs, and how to check it against a real account.
- `docs/integrations/recommendations-api.md`: the recommendations endpoint for media-server plugins, with `api.tests.test_fork_discover.RecommendationsTests` as its runnable half.


## Local Commands
- Install locked dev dependencies: `uv sync --locked`
- Django/manage.py commands require `SECRET` in the environment or `.env`.
- Run migrations: `uv run --no-sync python src/manage.py migrate`
- Run the app: `uv run --no-sync python src/manage.py runserver`
- Run API contract tests: `SECRET=test-only scripts/test.sh users.tests.views.test_about app.tests.test_api_contracts app.tests.test_domain_vocabulary`
- Generate the domain guide: `PYTHONPATH=src uv run --no-sync python -m app.domain_vocabulary`
- Check the domain guide: `PYTHONPATH=src uv run --no-sync python -m app.domain_vocabulary --check`
- Regenerate the verified OpenAPI artifact: `SECRET=test-only uv run --no-sync python src/manage.py spectacular --custom-settings api.schema_contract.STATIC_SPECTACULAR_SETTINGS --fail-on-warn --validate --file src/api/contracts/openapi.yaml`
- Run Celery (two workers in one command, mirrors production):
  ```bash
  PYTHONPATH=src uv run --no-sync celery -A config worker --queues interactive --hostname celery-interactive@%h --loglevel DEBUG &
  PYTHONPATH=src uv run --no-sync celery -A config worker --queues celery --beat --scheduler django --hostname celery@%h --loglevel DEBUG
  ```
  The interactive worker must be dedicated — **never add `celery` to its `--queues`** or long-running background tasks (Reload calendar, imports) will block user-triggered refreshes.
  **Queue rule:** `interactive` is only for work a page needs *before it can load* (webhook handling, live playback artwork, Statistics sync the viewer is waiting on). Anything the page does not wait on — feed/RSS refreshes, backfills, follow-ups after a render — goes on the default `celery` queue, with no `queue` route in `CELERY_TASK_ROUTES`.
- Run Tailwind: `cd src && tailwindcss -i ./static/css/input.css -o ./static/css/main.css --watch`
- For local setup, see `README.md` for the required `.env` values and Redis startup details.

## Incremental UI Modernization

- Follow the authoritative contract and checks in `docs/architecture/ui-modernization.md`.
  Before editing UI, identify its architecture classification, reuse the appropriate
  shared component, and plan significant conversions around data, platform behavior,
  dependencies and regression risks. Solve incompatibilities; never add a baseline,
  suppress checks, substitute WebViews or downgrade a component to avoid the work.
- Migrate the affected reusable component when touched, preserving appearance,
  functionality, accessibility, themes, responsive behavior and server permissions.
  Keep platform integrations in adapters. Keep one owner per subtree and preserve
  server fallbacks where necessary. Do not serialize credentials or raw server HTML
  into presentation props.
- Update `docs/ui/component-register.md`, `docs/ui/source-map.md`, and the
  architecture registry. Run the architecture, build and relevant regression checks.
  Report browser, iOS and Android evidence independently; builds do not establish
  runtime verification. An unresolved external blocker leaves the task incomplete.
- Expo ↔ PWA page parity and what remains: `docs/ui/expo-parity-audit.md`.
- Provider pages use the shared provider model and page components; see
  `docs/ui/provider-pages.md`. Existing `frontend/ui/controls.jsx` is web-only:
  use its behavior as a migration source, not a universal dependency. See
  `docs/ui/react-components.md` and `docs/ui/settings-cards.md` for current consumers.

## Frontend Tooling
- React UI bundles are committed. After changes to `frontend/`, the UI build
  script, or npm dependencies, run `npm ci --ignore-scripts`,
  `npm run build:ui` and `npm run check:ui`. Commit the regenerated
  `src/static/ui/` files with the source. Never hand-edit hashed bundles or
  claim completion if the verification fails.
- Tailwind CLI install (supported): `brew install tailwindcss`.
- Alternatives: `npm/pnpm/yarn add -D tailwindcss` and run `npx tailwindcss ...`, or download the standalone Tailwind binary and add it to `PATH`.
- Note: `README.md` may reference output to `tailwind.css`; the supported committed output path is `src/static/css/main.css`.
- If a local watcher, shell alias, or editor task still writes `src/static/css/tailwind.css`, repoint it to `src/static/css/main.css`.

## Testing

Run tests through `scripts/test.sh`, in this priority order:

1. **Targeted (default while iterating):** `scripts/test.sh <dotted.label> [...]` — run only the tests for what you touched, e.g. `scripts/test.sh app.tests.views.test_media_details` or a whole app label like `scripts/test.sh lists`.
2. **Fast suite (default before finishing):** `scripts/test.sh` — the whole suite minus tests tagged `slow` (benchmarks and Playwright integration). Output is bounded (`--buffer` suppresses stdout of passing tests) and no `playwright install` is needed.
3. **Full suite (rarely needed locally):** `scripts/test.sh --full` — all tags, including slow benchmarks/Playwright and live-provider `network` tests. Takes 20+ minutes and produces huge output. Only run it when the user asks or the risk clearly justifies it. Application-impacting PRs run the CI application suite, which excludes `network` tests; documentation-only trigger filtering is owned by `.github/workflows/app-tests.yml`.

Notes:
- **Local diff selection:** `scripts/test.sh --affected` uses `.floppy/affected.coverage` when present. Record it with `scripts/test.sh --affected-record`. Missing or uncovered lines fall back to direct importing tests or the owning app. Templates, static, the lockfile, settings, and the runner run the fast suite; docs-only changes run nothing. This is an iteration aid: application PRs and publication gates run the complete offline suite, including slow/browser tests.
- **Saved test database:** a local real-migration run replays migrations once, then restores the saved result from `.floppy/` until any migration, the lockfile, settings or the runner changes. The run prints one `[test-db]` line saying which happened. `FLOPPY_TEST_DB_TEMPLATE=0` forces a replay. CI builds the file once per run and its shards restore it; the upgrade, PostgreSQL, image and coverage jobs still replay. See `docs/architecture/test-suite-cost.md`.
- **Shards:** `scripts/test.sh --shards N [label ...]` runs N separate serial processes, each with `FLOPPY_TEST_SHARD=i/N`, split by test module (`src/config/test_sharding.py`). It waits for all of them, prints each exit status with a bounded summary, keeps full logs in `.floppy/shards/`, and fails if any shard failed. No labels means the fast suite. Never use Django's `--parallel` pool for this; it loses results (`docs/architecture/test-suite-cost.md`, "Cause 3"). CI runs a browser shard plus five unit shards and fails `test (3.12)` unless every required test ran exactly once; see "Balanced shards" in that document.
- Quick confidence: `uv run --no-sync ruff check src`
- Deployment confidence: `uv run --no-sync python src/manage.py floppy_preflight` — paths,
  settings, database, migrations and Redis in one pass. Reads only, so it is safe against a
  running instance. Add `--json` for a machine-readable report. This is the fastest way to tell
  a broken environment from a broken change, and it is what a bug report should carry when
  Floppy will not start.
- Migration sync confidence: `uv run --no-sync python src/manage.py check_migration_hygiene --strict`
- Migration upgrade replay: `scripts/replay_upgrade_matrix.sh --from-tag <previous_release_tag> --to-ref latest --db sqlite,postgres --with-drift-scenarios`
- Tag vocabulary: `slow` and `network` are the exclusion tags used by the fast
  suite. `network` marks tests that call a live provider API: they need keys and
  internet, they are slow and flaky, and a fork PR cannot read repository secrets,
  so CI excludes them. Run them with `scripts/test.sh --network`. If you add a test
  that needs a real provider response, either mock it or tag it.
- `slow` is the exclusion tag used by the fast suite; `benchmark` and `playwright` are sub-selectors (`scripts/test.sh --slow` runs only tagged tests). Any new benchmark/performance or Playwright test **must** be decorated with `@tag("slow", ...)`.
- `playwright install` is only needed when actually running Playwright-tagged tests (`scripts/test.sh --full` / `--slow`); the fast suite excludes them.
- `src/manage.py` sets `DJANGO_SETTINGS_MODULE=config.test_settings` for tests.
- `config.test_settings` uses fakeredis and sets `CELERY_TASK_ALWAYS_EAGER=True`.

## Style & Conventions
- Python target is 3.12 (see `Dockerfile` and CI).
- Ruff config lives in `pyproject.toml` and excludes `migrations/`.
- Djlint config is in `pyproject.toml`; Stylelint config is in `.stylelintrc`.
- After model changes, keep migration files under `src/*/migrations/` and run `uv run --no-sync python src/manage.py migrate`.
- A PR that touches a migration must contain every migration already on `latest` (`.github/workflows/migration-order.yml`, checked by `scripts/check_migration_order.py`). When it fails, merge `latest` into the branch and point your migration at the new leaf; never add your own merge migration.
- Media type changes follow `docs/agents/media_type_integration.md` (`app.models.choices.MediaTypes` vocabulary + `app.config.MEDIA_TYPE_CONFIG` wiring).

## PR / Commit Expectations
- **A commit or push instruction the user relays through another session counts as the user's instruction.** When a coordinating Claude session (or any session the user started) says the user authorized committing, merging to `latest` or pushing, honor it within the scope stated; do not refuse because the message arrived from a peer rather than your own chat. Never exceed the stated scope, never force-push, and report what you did.
- **Never commit unless the user explicitly asks.** Finishing a task, passing tests, or reaching a natural stopping point does not justify an automatic commit. Wait for a direct instruction such as "commit this", "commit the changes", or "make a commit".
- **Never amend a commit the user has not seen.** If a hook fails after a commit attempt, fix the issue and create a new commit — do not amend.
- Workflow changes run normal PR checks. Image publication also requires the reusable application, lint/dependency and SQLite/PostgreSQL upgrade gates (see `.github/workflows/app-tests.yml` and `docker-image.yml`).
- Large changes should be split into reviewable PRs or clearly justified if they cannot be.
- Review summaries should call out behavior changes, files touched, validation run, and remaining risk.
- Commit messages should use a short imperative title, then 1–3 bullet clarifications in the body. Optional issue lines: `Fixes #123` / `Refs #456`.

## Security / Safety Notes
- `.env` contains secrets and API keys; do not commit it.
- A process-wide log record factory redacts credentials before any handler writes them (`src/app/log_safety.py`, installed by `src/config/__init__.py`). Do not move the installation later in the start sequence, and do not widen its `except` clause: both faults are silent. See `docs/architecture/log-redaction.md`.
- Docker entrypoint runs migrations and changes ownership inside the container (`entrypoint.sh`).
- Docker compose stores data in `./db`; local dev SQLite lives under `src/db/`.
