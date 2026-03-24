# Spine — Product Requirements Document (PRD)

**Version:** 1.0  
**Author:** Armaan  
**Date:** March 23, 2026  
**Status:** Draft

---

## 1. Overview

### 1.1 What is Spine?

Spine is a social media tracking platform for all types of media — movies, TV shows, anime, manga, video games, and books — in a single unified app. Think Letterboxd, but expanded beyond film to cover every major media category, with a modern social layer on top. Music support is planned as a future media type.

### 1.2 Origin

Spine is a fork of [Yamtrack](https://github.com/FuzzyGrim/Yamtrack), an open-source, self-hosted media tracker built in Django/Python. Yamtrack provides a mature tracking foundation (2,500+ commits, 2.2k GitHub stars) with rich metadata integrations, but has no social features and is designed exclusively for self-hosting. Spine takes the Yamtrack data and tracking layer and transforms it into a publicly hosted, social-first platform.

### 1.3 Goals

1. **Launch a publicly hosted instance of Spine** — no Docker setup required. Users sign up and start tracking immediately.
2. **Build a social layer** inspired by Letterboxd — profiles, following, reviews, ratings, public lists, and an activity feed — but for all media types.
3. **Serve as a portfolio-quality project** demonstrating full-stack engineering, product thinking, and the ability to ship a real consumer product.
4. **Maintain open-source roots** — Yamtrack is AGPL-3.0 licensed. Licensing approach for Spine is TBD, but the current plan is to keep the codebase open source and differentiate through the hosted service.

### 1.4 Non-Goals (v1)

The following are explicitly out of scope for the initial launch:

- Direct messaging / chat between users
- Groups or communities
- Algorithmic recommendations ("people who liked X also liked Y")
- Native mobile app (web-first, responsive design only)
- Push notifications or email notifications
- Calendar / upcoming releases feature (exists in Yamtrack, deprioritized)
- Jellyfin / Plex / Emby integration (exists in Yamtrack, deprioritized)
- Music as a media type (planned for a future release)

---

## 2. Target Users

### 2.1 Primary Persona — The Multi-Media Enthusiast

People who actively consume and track multiple types of media (not just film) and are frustrated by needing separate apps for each — Letterboxd for movies, Goodreads for books, MyAnimeList for anime, Backloggd for games. They want one place to log everything and share their taste across media types.

### 2.2 Secondary Persona — The Letterboxd Power User

Film-focused users who already understand the Letterboxd model (diary logging, star ratings, reviews, lists, following friends) and want that same experience extended to their other media consumption.

---

## 3. Tech Stack

### 3.1 Current Stack (Inherited from Yamtrack)

| Layer | Technology |
|---|---|
| Backend framework | Django (Python) |
| Database | PostgreSQL (production) / SQLite (dev) |
| Background jobs | Celery + Redis |
| Frontend | Django templates + Tailwind CSS |
| CSS build | Tailwind CLI |
| Metadata APIs | TMDB (movies/TV), MAL (anime/manga), IGDB (games), Open Library / Google Books (books), BoardGameGeek (board games) |
| Auth | django-allauth (OIDC + 100+ social providers) |
| Containerization | Docker + docker-compose |

### 3.2 Deployment Target (New for Spine)

Deployment strategy is TBD. Requirements for the hosting solution:

- Must support Django + PostgreSQL + Redis/Celery
- Should be modern and low-ops (not a raw VPS with manual Docker management)
- Candidates to evaluate: **Fly.io**, **Railway**, **Render**, **Coolify** (self-hosted PaaS on a VPS), **Cloudflare Workers** (would require significant architectural changes)
- Decision criteria: cost at low scale, ease of deployment, PostgreSQL managed hosting, Redis support, and ability to run Celery workers

### 3.3 Key Technical Decisions to Make

1. **Hosting platform** — evaluate candidates above and select based on Django compatibility and cost
2. **Media storage** — user avatars and potentially user-uploaded images need an object storage solution (e.g., Cloudflare R2, AWS S3, or Backblaze B2)
3. **Licensing** — AGPL-3.0 inherited from Yamtrack; decide whether to keep fully open source or pursue an open-core model
4. **Search** — for social features (finding users, searching reviews), evaluate whether Django ORM full-text search is sufficient or if a dedicated search layer (e.g., Meilisearch) is needed

---

## 4. Feature Requirements

### 4.1 Media Tracking (Inherited — Already Built)

These features exist in the Yamtrack codebase and are carried over into Spine.

**Supported media types:** Movies, TV shows (with individual season tracking), anime, manga, video games, books.

**Tracking fields per media item:**
- Status: Watching / Reading / Playing / Completed / Paused / Dropped / Planning
- Rating: 5-star scale
- Progress: Episode-level for TV/anime, page or percentage for books, freeform for other types
- Repeats: Rewatch / reread / replay count
- Start and end dates
- Notes (private, freeform text)

**Diary:** A chronological log of all media interactions, dated. Supports filtering by media type and tags. Entries show the date, media poster, title, star rating, and whether it was favorited or is a rewatch/replay. (Already built — see diary screenshot.)

**Search & discovery:** Search across all supported media types, powered by the underlying metadata APIs (TMDB, MAL, IGDB, etc.).

**Media detail pages:** Display metadata (synopsis, cast/crew, genre tags, runtime/length), aggregate scores from external sources (IMDb, Rotten Tomatoes, Letterboxd where applicable), and the user's own rating. "Watched" and "Log" actions are prominently displayed. (Already built — see movie detail screenshot.)

**Custom entries:** Users can create manual media entries for niche content not found in the supported APIs.

**Data portability:**
- CSV export of all tracked media
- Import from Trakt, Simkl, MyAnimeList, AniList, and Kitsu (with support for periodic automatic imports)

### 4.2 User Profiles (New — Priority 1)

Public-facing profile pages are the foundation of the social layer.

**Profile components:**
- **Username** — unique, URL-safe identifier (e.g., spine.app/u/armaan)
- **Avatar** — user-uploaded profile picture with a default fallback
- **Bio** — short freeform "about me" text
- **Location** — optional, user-entered (e.g., "Seattle / Boston")
- **Member since** — auto-generated join date
- **Hall of Fame** — a user-curated grid of favorite media across all types (pinned by the user, similar to Letterboxd's 4 favorites but expanded to ~4–8 items across media types)
- **Aggregate stats** — automatically computed: total movies watched, shows completed, books read, games played, anime watched, manga read. Displayed prominently on the profile.
- **Recent activity** — a feed of the user's most recent diary entries / tracking actions
- **Privacy controls** — users can set their profile to public or private. Private profiles are only visible to approved followers.

### 4.3 Follow System (New — Priority 1)

- Users can follow other users
- Following is asymmetric (like Twitter/Letterboxd, not mutual like Facebook)
- Follower and following counts displayed on profiles
- For private profiles, follow requests require approval
- Users can block other users (blocked users cannot view the blocker's profile or follow them)

### 4.4 Activity Feed (New — Priority 4)

A home feed showing recent activity from users you follow.

**Feed items include:**
- Diary entries (rated/logged a movie, book, game, etc.)
- Reviews posted
- Items added to lists
- Hall of Fame updates

**Feed design:**
- Reverse-chronological (no algorithmic sorting in v1)
- Grouped by date
- Filterable by media type (e.g., show only movie activity, only game activity)
- Each feed item links to the relevant media detail page and the user's profile

### 4.5 Reviews & Ratings (New — Priority 2)

Extending the existing rating system with a public review layer.

**Review types:**
- **Short reviews** — a few sentences, displayed inline on the media page (Letterboxd-style). Low friction to write.
- **Long-form reviews** — multi-paragraph, optionally titled, with basic formatting support (bold, italic, paragraphs). Displayed on a dedicated review page.

**Review features:**
- Reviews are public by default (with an option to make them private)
- Reviews are tied to a diary entry (a review is always associated with a specific watch/read/play date)
- Other users can "like" a review
- Media detail pages display a selection of popular/recent reviews from other users below the metadata
- A user's reviews are accessible from their profile
- Spoiler tagging — users can mark a review as containing spoilers, which collapses the text behind a warning

**Aggregate ratings:**
- Each media detail page shows the Spine community average rating alongside external scores (IMDb, RT, etc.)

### 4.6 Lists (Enhanced — Priority 3)

Yamtrack already has personal lists. Spine extends them with a social dimension.

**Enhancements over Yamtrack:**
- **Public visibility** — lists can be set to public, unlisted (link-only), or private
- **Likes** — users can like public lists
- **Comments** — users can comment on public lists
- **Rich descriptions** — list creators can add a description explaining the list's purpose or theme
- **Mixed-media lists** — a single list can contain movies, books, games, etc. (unique to Spine's multi-media identity)
- **Shareable URLs** — each public list gets a clean permalink (e.g., spine.app/u/armaan/lists/best-sci-fi)

### 4.7 Statistics (Enhanced)

Yamtrack already has a statistics page. Spine enhances it for the social context.

**Existing (from Yamtrack):**
- Tracking statistics per media type (count, time spent, score distribution, etc.)

**Enhancements:**
- Public stats page on each user's profile (respects privacy settings)
- Year-in-review style summaries (e.g., "2026 in Review" — total media consumed, top-rated, most active month)
- Genre breakdowns across all media types

---

## 5. Information Architecture

### 5.1 Navigation

The top navigation bar (already implemented) includes:

| Nav Item | Description |
|---|---|
| **Media** (dropdown) | Browse/search by media type |
| **Create** (dropdown) | Log a new entry, create a custom entry |
| **Lists** | View and manage personal lists; browse public lists |
| **Diary** | Chronological log of all activity |
| **Calendar** | (Deprioritized in v1 — may be hidden or stubbed) |
| **Statistics** | Personal stats dashboard |
| **Profile icon** | Settings, profile, sign out |

**New pages for social features:**
- `/feed` — home activity feed (logged-in users only)
- `/u/{username}` — public profile page
- `/u/{username}/reviews` — all reviews by a user
- `/u/{username}/lists` — all public lists by a user
- `/u/{username}/stats` — public stats page
- `/u/{username}/diary` — public diary view
- `/media/{type}/{id}/reviews` — all reviews for a specific media item

### 5.2 Data Model Additions

Key new models beyond what Yamtrack provides:

- **UserProfile** — extends Django User with avatar, bio, location, privacy settings, Hall of Fame selections
- **Follow** — from_user, to_user, created_at, status (accepted/pending for private profiles)
- **Block** — blocker, blocked, created_at
- **Review** — user, media_item, title (optional), body, is_spoiler, is_long_form, created_at, updated_at
- **ReviewLike** — user, review, created_at
- **ListLike** — user, list, created_at
- **ListComment** — user, list, body, created_at

---

## 6. User Flows

### 6.1 New User Onboarding

1. User visits spine.app and sees a landing page explaining the product
2. User signs up via email/password or social auth (Google, GitHub, Discord via django-allauth)
3. User is prompted to set a username, upload an avatar, and write a short bio
4. User is shown a quick onboarding flow: "What do you like?" — select favorite media types to personalize the empty state
5. User lands on their empty profile with prompts to start logging, rating, and building their Hall of Fame

### 6.2 Logging & Rating

1. User searches for a media item (e.g., "Breath of the Wild")
2. User arrives at the media detail page showing metadata, external scores, and community reviews
3. User clicks "Log" → selects status, assigns a star rating, optionally writes a review, sets the date
4. Entry appears in their diary, on their profile's recent activity, and in their followers' feeds

### 6.3 Social Discovery

1. User visits another user's profile via a link or search
2. User browses their Hall of Fame, stats, recent diary entries, reviews, and lists
3. User follows them
4. That user's activity now appears in the follower's home feed

---

## 7. Design & UI

### 7.1 Current Design Language

Spine already has a polished dark-mode UI built with Tailwind CSS. The design language includes:

- **Dark theme** as the default (charcoal/slate backgrounds, white text, purple/blue accent color)
- **Card-based layouts** for media grids and diary entries
- **Poster-forward design** — media artwork is prominently displayed throughout the app
- **Clean typography** with clear hierarchy
- **Responsive** — the existing UI should work across screen sizes

### 7.2 Design Principles for Social Features

- **Consistency** — new social pages (profiles, feeds, review pages) should match the existing design language exactly
- **Low friction** — logging, rating, and reviewing should be as fast as possible. The diary is the core interaction loop.
- **Media-first** — posters, cover art, and screenshots should always be the visual anchor. Text-heavy layouts should be avoided.
- **Progressive disclosure** — show the most important info first (rating, short review), let users click through for more (full review, all reviews)

---

## 8. Milestones & Phasing

### Phase 1: Public Hosting & Auth (Weeks 1–2)

**Goal:** Get Spine running as a publicly accessible hosted service with multi-user auth.

- Select and configure hosting platform (evaluate Fly.io, Railway, Render)
- Set up managed PostgreSQL and Redis
- Configure django-allauth for public signup (email + Google OAuth at minimum)
- Set up object storage for user avatars (Cloudflare R2 or S3-compatible)
- Configure production settings (HTTPS, CSRF, allowed hosts, static files via CDN)
- Remove self-hosting-specific features from the UI (Docker references, environment variable settings page)

### Phase 2: User Profiles & Follow System (Weeks 3–5)

**Goal:** Every user has a public-facing profile and can follow other users.

- Build UserProfile model (avatar, bio, location, privacy settings)
- Build profile page at `/u/{username}` displaying Hall of Fame, aggregate stats, recent activity
- Implement Hall of Fame selection UI (user picks favorites from their tracked media)
- Build Follow model and follow/unfollow actions
- Implement follower/following counts and lists on profiles
- Implement private profile logic (follow requests, visibility restrictions)
- Build block functionality
- Build user search (find users by username)

### Phase 3: Reviews & Ratings (Weeks 6–8)

**Goal:** Users can write reviews, and media pages surface community opinions.

- Build Review model with short and long-form support
- Integrate review writing into the "Log" flow (optional step after rating)
- Build review display on media detail pages (show top reviews + "see all" link)
- Build dedicated review pages for long-form reviews
- Implement review likes
- Build spoiler tag functionality (collapse behind warning)
- Compute and display Spine community average rating on media detail pages
- Build user review index at `/u/{username}/reviews`

### Phase 4: Social Lists (Weeks 9–10)

**Goal:** Lists become a social feature with public visibility, likes, and comments.

- Add visibility settings to existing List model (public, unlisted, private)
- Build public list browsing page
- Implement list likes and comments
- Add list descriptions
- Build shareable list URLs
- Ensure lists support mixed media types

### Phase 5: Activity Feed (Weeks 11–12)

**Goal:** Following someone is meaningful — their activity appears in your feed.

- Build feed generation logic (aggregate recent activity from followed users)
- Build feed UI at `/feed` with date grouping
- Add media type filtering to the feed
- Implement feed pagination (infinite scroll or "load more")
- Optimize feed queries for performance (denormalized activity table or materialized approach)

### Phase 6: Polish & Launch (Weeks 13–14)

**Goal:** Ship it.

- Landing page for logged-out users (explain what Spine is, show example profiles, CTA to sign up)
- SEO basics (meta tags, Open Graph for shared links)
- Public stats pages on profiles
- Performance audit and optimization (N+1 queries, caching frequently-accessed profiles)
- Error handling, 404 pages, empty states
- README / documentation for the open-source repo
- Soft launch and gather feedback

---

## 9. Success Metrics

| Metric | Target (3 months post-launch) |
|---|---|
| Registered users | 100+ |
| Weekly active users | 25+ |
| Media items logged | 5,000+ |
| Reviews written | 200+ |
| Public lists created | 50+ |
| GitHub stars on Spine repo | 100+ |

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **API rate limits** from TMDB/MAL/IGDB at scale | Medium | High | Implement aggressive caching of metadata; cache media detail pages server-side; batch API calls |
| **Feed performance** degrades as user count grows | Medium | Medium | Denormalize activity into a dedicated feed table rather than computing on-the-fly; add database indexes early |
| **Content moderation** — inappropriate reviews/comments | Medium | Medium | Start with report functionality + manual moderation; add automated filtering later if needed |
| **AGPL licensing** confusion with contributors or users | Low | Medium | Clearly document the license in README; add a CONTRIBUTING.md; decide on licensing approach before accepting external PRs |
| **Solo developer burnout** on a 14-week plan | Medium | High | Stick to the phased approach; ship each phase as a usable increment; cut scope aggressively if behind |
| **Hosting costs** at scale with PostgreSQL + Redis + Celery | Low | Medium | Start with free/cheap tiers; optimize queries before scaling infrastructure; consider SQLite for early stages |

---

## 11. Open Questions

1. **Hosting platform** — needs evaluation. Fly.io and Railway are the top candidates for Django + PostgreSQL + Redis.
2. **Licensing** — AGPL-3.0 from Yamtrack. Decide whether to keep fully open source or move to open-core.
3. **Music support** — which metadata API? Spotify API, MusicBrainz, or Last.fm? Scoping needed.
4. **Rating granularity** — current system is 5 stars. Should half-stars be supported (like Letterboxd's 0.5–5.0 scale)?
5. **Community moderation** — what moderation tools are needed at launch? Minimum viable: report button + admin dashboard.
6. **Discovery features** — trending media, popular reviews, and "similar taste" recommendations are desired long-term but need a separate PRD when the time comes.
