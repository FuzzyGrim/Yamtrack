# AGENTS.md

This file gives AI coding agents project-specific operating instructions for this repository.

Status: Advisory v1.
Enforcement: Advisory (not mandatory gate).

---

## 1) Project Intent

`spine` (formerly YamTrack) is a Django-based multi-media tracker with:
- Tracking for TV, seasons/episodes, movies, anime, manga, games, books, comics
- Diary logging
- Calendar/release events
- Notifications
- Import/export and webhook integrations
- User profiles, preferences, Hall of Fame, and custom lists

Current product direction from maintainer:
- Evolve from local/private tracker into a social platform (Letterboxd-style social behaviors) for all media types.

Source product intent reference:
- `spine-prd.md` (early PRD; directionally useful even if some implementation details have advanced).

---

## 2) Tech Stack (Current)

- Backend: Django 5.x (`src/`)
- Async/background: Celery + Redis
- DB: SQLite by default, PostgreSQL supported
- Templates/UI: Django templates + HTMX + Alpine + Tailwind build pipeline
- Auth: Django allauth
- History/auditing: `django-simple-history`
- API/data providers: TMDB, MAL, MangaUpdates, IGDB, OpenLibrary, Hardcover, ComicVine

---

## 3) Repository Map

- `src/app/`: Core tracking domain (models, forms, views, providers, statistics, template tags)
- `src/users/`: Custom user model, profile/settings/preferences
- `src/events/`: Calendar events, release processing, notifications, ICS export
- `src/lists/`: Custom collections and collaborators
- `src/integrations/`: Imports/exports + webhook handlers
- `src/templates/`: Shared and app-specific templates/components
- `src/config/`: Django settings, URLs, Celery/Gunicorn config
- `src/static/`: CSS/JS/static assets

Supporting docs/scripts:
- `LOCAL_SETUP.md`, `FRONTEND.md`, `start-dev.sh`
- `docker-compose*.yml`, `Dockerfile`, `entrypoint.sh`, `supervisord.conf`

---

## 4) Non-Negotiable Safety Rules

1. Do not commit or expose secrets from `.env` or settings defaults.
2. Do not remove/disable DB constraints casually, especially around `Item`, status enums, and uniqueness checks.
3. Preserve migration integrity:
   - Schema/model changes require migrations.
   - Never hand-edit old migrations unless explicitly requested.
   - Include migrations for social features and data model changes (prototypes included).
4. Destructive/contract-breaking migrations are not allowed.
5. Preserve async behavior:
   - If changing event, notification, diary, or import flows, validate Celery task triggering still works.
6. Avoid broad refactors unless explicitly requested; prefer minimal, bounded changes.
7. Do not break HTMX partial responses when modifying views/templates.
8. Do not introduce breaking URL changes without explicit migration/redirect plan.

---

## 5) Local Development Commands

Python/package workflow standard:
- Use `venv` + `pip` for Python dependencies.
- Use `npm` for frontend/Tailwind.

Recommended:
- `./start-dev.sh` (starts Tailwind watcher + Django dev server)

Manual:
- `source venv/bin/activate`
- `npm run build-css` (watch mode)
- `cd src && python manage.py runserver`

Background services (when needed):
- Redis
- `cd src && celery -A config worker -l info`
- `cd src && celery -A config beat -l info`

---

## 6) Testing and Validation

Primary checks before handing off non-trivial changes:

1. Targeted tests for changed area:
   - `cd src && python manage.py test <app_or_test_path>`
2. If change touches shared/core behavior, run broader relevant suites when practical.
3. For template/HTMX changes, verify:
   - Full-page load
   - Partial fragment response
   - No broken CSRF/HTMX headers
4. For Celery-triggering logic, verify task enqueue paths still execute.

Canonical test command (from repo docs): Django test runner via `python manage.py test`.
`pytest` config exists and can be used as supplemental tooling.

Lint/format policy (current):
- Required lint check: `ruff check src` (matches CI workflow in `.github/workflows/app-tests.yml`).
- Template formatting: `djlint` is available/configured but not currently a CI-required gate.
- Do not introduce new formatter/linter requirements unless explicitly requested.

---

## 7) Domain Guardrails

### 7.1 Core Tracking Models

Be careful when changing:
- `src/app/models.py`:
  - `Item` uniqueness/check constraints
  - `Media`/`BasicMedia` status and progress semantics
  - TV/Season/Episode relationship logic
  - Diary model behavior and constraints (target product direction: social diary across all media types)

### 7.2 User and Preferences

Be careful when changing:
- `src/users/models.py`:
  - Custom `User` preference fields and validation constraints
  - Enabled media type behavior (impacts UI visibility, search, events, stats)

### 7.3 Calendar and Notifications

Be careful when changing:
- `src/events/calendar.py`, `src/events/models.py`, `src/events/notifications.py`
  - Event generation/update cleanup
  - Sentinel datetime handling and sorting
  - Notification dedupe/exclusions

### 7.4 Integrations

Be careful when changing:
- `src/integrations/`:
  - Import modes (`new` vs `overwrite`)
  - OAuth callback handling
  - Webhook matching logic
  - CSV export field compatibility

---

## 8) Template/Frontend Guardrails

1. Keep existing server-rendered + HTMX architecture unless a task explicitly requests otherwise.
2. For components in `src/templates/app/components/`:
   - Maintain expected `hx-target`/`hx-swap` behavior.
   - Avoid adding JS that duplicates existing HTMX behavior.
3. CSS source of truth:
   - Edit `src/static/css/input.css`
   - `src/static/css/main.css` is build output.

---

## 9) Social Product Defaults (Maintainer Decisions)

These defaults are now agent guidance:

1. Privacy defaults:
   - Public by default for social surfaces.
   - Users can set private accounts.
2. Visibility scope:
   - Profiles, diary, ratings/reviews, lists, and hall of fame are in social scope.
3. Follow model:
   - One-way follow graph.
   - Private accounts require follow approval.
4. Block behavior:
   - Blocked users cannot view blocker profile content or follow.
5. Interactions:
   - Likes only for now.
   - No social comments in current phase.
6. Moderation/control:
   - Content edit/delete by author and project owner/admin only.
   - No full moderation-role system required yet.
7. Spoilers:
   - Users can mark reviews/log content as spoilers.
8. Rollout scope:
   - Social features should apply to all media types together (not phased by type).
9. Feed scope/order:
   - Include diary logs, ratings, and list activity.
   - Reverse-chronological ordering (no algorithmic ranking yet).
10. Social notifications:
    - Disabled for now (no follow/like/comment notification work unless requested later).
11. Performance expectation:
    - Prioritize fast pages and query hygiene; no hard SLA currently defined.
12. Rate limiting:
    - No product-level social action limits required for now unless abuse appears.
13. Audit logging:
    - Log key social actions (follow/unfollow, follow request accept/reject, block/unblock, likes, review/list visibility changes, and privileged deletions).

Implementation preference:
- Keep tracking foundations stable while adding social layers.
- Reuse existing history/signals patterns where sensible.
- Avoid tight coupling between provider ingestion and social graph/feed logic.

---

## 10) Architecture & Delivery Preferences

1. Feature flags:
   - Not mandatory by default.
   - Use feature flags only when explicitly requested or when risk is high.
2. API strategy:
   - Continue server-rendered + HTMX-first delivery.
   - Add dedicated APIs only when a task explicitly needs them.
3. Backward compatibility:
   - Prefer preserving existing URLs and integration behavior.
   - If breaking changes are unavoidable, include explicit migration/redirect notes.
4. Deployment guidance:
   - Docker is supported but optional for contributors.
   - Local dev commands from `LOCAL_SETUP.md` and `FRONTEND.md` are primary references.
5. Documentation precedence:
   - If setup instructions conflict across docs, treat `LOCAL_SETUP.md` as the source of truth.

---

## 11) Change Scope Expectations for Agents

Every non-trivial change should include:

1. What changed
2. Why it changed
3. Risk points
4. Test coverage added/updated (or what remains untested)
5. Any migration/data impact

If uncertain about business behavior, stop and ask instead of assuming.

---

## 12) Remaining Clarification Points (Non-Blocking)

These do not block current implementation but should be finalized later:

1. Long-term deployment platform choice for publicly hosted Spine.
