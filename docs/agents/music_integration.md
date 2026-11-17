# Music Media Type Integration

This document provides a comprehensive reference for the Music media type implementation in Yamtrack, including the hierarchical data model, MusicBrainz provider integration, and UI patterns.

## Overview

Music follows a **TV → Season → Episode** pattern but adapted for **Artist → Album → Track**:

| TV Pattern | Music Pattern |
|------------|---------------|
| TV (show) | Artist |
| Season | Album |
| Episode | Track |
| TV (Media subclass) | ArtistTracker |
| Season (Media subclass) | AlbumTracker |
| Episode (separate model) | Track (separate model) |
| Movie (Media subclass) | Music (Media subclass) |

Key differences from TV:
- Artist and Album are **container models** (not Media subclasses)
- `ArtistTracker` and `AlbumTracker` handle user tracking for artists/albums
- `Music` (Media subclass) tracks individual songs (like how Episode tracks individual episodes)
- `Track` is the metadata catalog (like Episode), populated from MusicBrainz

## Data Model

### Core Models (`src/app/models.py`)

#### Artist
Container for music artists. Not a Media subclass.

```python
class Artist(models.Model):
    name = models.CharField(max_length=255)
    sort_name = models.CharField(max_length=255, blank=True, default="")
    musicbrainz_id = models.CharField(max_length=36, unique=True, null=True, blank=True)
    image = models.URLField(blank=True, default="")  # Wikipedia photo
    country = models.CharField(max_length=5, blank=True, default="")  # ISO country code
    genres = models.JSONField(default=list, blank=True)  # Top genres/tags from MusicBrainz
    discography_synced_at = models.DateTimeField(null=True, blank=True)  # When albums were fetched
```

#### Album
Container for albums. Not a Media subclass.

```python
class Album(models.Model):
    title = models.CharField(max_length=255)
    musicbrainz_release_id = models.CharField(max_length=36, null=True, blank=True)  # Specific release
    musicbrainz_release_group_id = models.CharField(max_length=36, null=True, blank=True)  # Release group
    artist = models.ForeignKey(Artist, related_name="albums", null=True, blank=True)
    release_date = models.DateField(null=True, blank=True)
    image = models.URLField(blank=True, default="")  # Cover art from Cover Art Archive
    release_type = models.CharField(max_length=50, blank=True, default="")  # "Album", "EP", "Compilation", etc.
    genres = models.JSONField(default=list, blank=True)  # Genres/tags from release metadata
    tracks_populated = models.BooleanField(default=False)  # Whether Track rows exist
```

#### Track
Metadata catalog for tracks (like Episode). Not per-user.

```python
class Track(models.Model):
    album = models.ForeignKey(Album, related_name="tracklist")
    title = models.CharField(max_length=500)
    musicbrainz_recording_id = models.CharField(max_length=36, null=True)
    track_number = models.PositiveIntegerField(null=True)
    disc_number = models.PositiveIntegerField(default=1)
    duration_ms = models.PositiveIntegerField(null=True)
    genres = models.JSONField(default=list, blank=True)
    
    @property
    def duration_formatted(self):
        """Return duration as mm:ss string."""
```

#### Music (Media subclass)
Per-user tracking for individual songs.

```python
class Music(Media):
    album = models.ForeignKey(Album, related_name="music_entries", null=True)
    artist = models.ForeignKey(Artist, related_name="music_entries", null=True)
    track = models.ForeignKey(Track, related_name="music_entries", null=True)
    
    @property
    def formatted_progress(self):
        """Return progress as play count."""
```

#### ArtistTracker
Per-user tracking for artists (like TV show tracking).

```python
class ArtistTracker(models.Model):
    user = models.ForeignKey(User, related_name="artist_trackers")
    artist = models.ForeignKey(Artist, related_name="trackers")
    status = models.CharField(choices=Status.choices, default=Status.IN_PROGRESS.value)  # In Progress, Completed, etc.
    score = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)  # 0-10
    start_date = models.DateTimeField(null=True)
    end_date = models.DateTimeField(null=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

#### AlbumTracker
Per-user tracking for albums (like Season tracking).

```python
class AlbumTracker(models.Model):
    user = models.ForeignKey(User, related_name="album_trackers")
    album = models.ForeignKey(Album, related_name="trackers")
    status = models.CharField(choices=Status.choices, default=Status.IN_PROGRESS.value)
    score = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    start_date = models.DateTimeField(null=True)
    end_date = models.DateTimeField(null=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Trackers are unique per user+artist/album (single tracker per target), default to `In Progress`, and mirror Media fields (status, score, start/end dates, notes).

#### MusicReleasePreference
Per-user display preference for a concrete MusicBrainz release/pressing. This is intentionally separate from `AlbumTracker`: it does not change the shared album identity or move play history to another tracklist.

```python
class MusicReleasePreference(models.Model):
    user = models.ForeignKey(User, related_name="music_release_preferences")
    album = models.ForeignKey(Album, related_name="music_release_preferences")
    release_id = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Preferences are unique per user+album. The selected release overrides only the detail-page cover and release metadata; the canonical `Album`, `Track`, `Music`, collection, and history rows remain unchanged.

On the album detail page and album tracking modal, a saved preference is hydrated from
MusicBrainz only when the selected release still belongs to the album's release group.
If the release is missing, no longer belongs to that group, or MusicBrainz is
unavailable, the UI falls back to the canonical album metadata and cover without
deleting the preference or changing any tracking rows.

### Enums

```python
class Sources(models.TextChoices):
    MUSICBRAINZ = "musicbrainz", "MusicBrainz"

class MediaTypes(models.TextChoices):
    MUSIC = "music", "Music"
```

### User Preferences (`src/users/models.py`)

```python
# Per-user music settings
music_enabled = models.BooleanField(default=True)
music_layout = models.CharField(default="grid", choices=LAYOUT_CHOICES)
music_direction = models.CharField(default="desc", choices=DIRECTION_CHOICES)
music_sort = models.CharField(default="title", choices=SORT_CHOICES)
music_status = models.CharField(default="all", choices=STATUS_CHOICES)
```

## Provider Integration (`src/app/providers/musicbrainz.py`)

### MusicBrainz API

Base URL: `https://musicbrainz.org/ws/2/`

Rate limiting: 1 request per second (enforced via `_rate_limit()`)

### Key Functions

#### Search Functions

```python
def search(query, page=1, skip_cover_art=False):
    """Search for music recordings (tracks)."""
    # Searches /recording endpoint
    # Returns: media_id, title, artist_name, album_title, image, duration_minutes

def search_artists(query, page=1):
    """Search for artists."""
    # Searches /artist endpoint
    # Returns: artist_id, name, disambiguation, type, country

def search_releases(query, page=1, skip_cover_art=False):
    """Search for releases (albums)."""
    # Searches /release endpoint
    # Returns: release_id, title, artist_name, artist_id, release_date, image

def search_combined(query, page=1):
    """Combined search for music (artists + albums + tracks)."""
    # Page 1: Top 5 artists, top 5 albums, 20 tracks
    # Page 2+: Just tracks (pagination)
    # skip_cover_art=True for faster search
```

#### Metadata Functions

```python
def get_artist(artist_id):
    """Get detailed artist metadata."""
    # Fetches from /artist/{id} with inc=url-rels+genres+tags+ratings
    # Gets bio/image from Last.fm (by MBID) first, Wikipedia as fallback
    # Returns: name, type, country, genres, tags, rating, bio, image

def get_artist_discography(artist_id, skip_cover_art=False):
    """Get all albums for an artist."""
    # Fetches /release-group with artist filter
    # Filters to Album, EP, Single, Broadcast, Other (plus legacy Compilation)
    # Returns list of albums with cover art (unless skip_cover_art=True)

def recording(media_id):
    """Get detailed track metadata."""
    # Fetches /recording/{id} with inc=releases+artists
    # Returns: title, artist, album, duration, release_date, image

def get_release_for_group(release_group_id):
    """Get a representative release for a release group."""
    # Fetches /release filtered by release-group
    # Used to fill in release_date and cover art when only group MBID is known

def get_release(release_id, skip_cover_art=False):
    """Get detailed release metadata (album-level)."""
    # Fetches /release/{id} with recordings, labels, release-groups, and media
    # Returns: title, artist, release metadata, cover, and tracklist (optional)
    # skip_cover_art avoids cover art calls when hydrating many releases

def get_release_group_releases(release_group_id):
    """Get all releases/pressings in a MusicBrainz release group."""
    # Browses /release with release-group filtering and pagination
    # Returns normalized release_id, date, country, status, format, packaging,
    # labels, catalog numbers, barcode, track count, and cover URL.
    # Results are cached and official releases sort before other statuses.
```

#### Cover Art Functions

```python
def get_cover_art(release_id=None, release_group_id=None):
    """Get cover art from Cover Art Archive."""
    # Tries release_id first, then release_group_id as fallback
    # Prefers "Front" cover type
    # Returns image URL or settings.IMG_NONE

def _get_cover_art(release_id):
    """Internal cover art fetch (single release)."""
    # Fetches from https://coverartarchive.org/release/{id}
    # Handles redirects to archive.org
```

#### Last.fm Integration

```python
def get_lastfm_bio(mbid):
    """Fetch an artist bio from Last.fm's artist.getInfo, keyed by MBID."""
    # Requires settings.LASTFM_API_KEY (optional, user-supplied); returns
    # None immediately if unset.
    # Resolves the artist deterministically by MusicBrainz ID, so unlike
    # guessing a Wikipedia title from the artist name, it can't land on an
    # unrelated same-named entity (see issue #979, e.g. "Tool" the band vs.
    # the Wikipedia disambiguation/tool-the-implement page).
    # Strips Last.fm's "Read more on Last.fm" boilerplate and HTML markup.
    # Caches hits for 7 days and misses for 1 day.
```

#### Wikipedia Integration (fallback)

```python
def get_wikipedia_data(title):
    """Fetch Wikipedia bio and image."""
    # Uses Wikipedia REST API: /api/rest_v1/page/summary/{title}
    # Treats a "type": "disambiguation" response as a miss (not a valid bio)
    # Caches hits for 7 days and misses for 1 day
    # Returns: extract (bio text), image (photo URL)

def get_wikipedia_extract(title):
    """Legacy wrapper returning only the extract string."""
```

### Bio Resolution Strategy

`get_artist()` resolves an artist's bio in priority order, stopping at the
first strategy that returns text:

1. **Last.fm `artist.getInfo`, by MBID** — primary source when
   `LASTFM_API_KEY` is configured; deterministic, no title guessing.
2. Check MusicBrainz `url-rels` for a Wikipedia link (e.g., `Queen_(band)`)
   and use the exact article title if found.
3. If the MusicBrainz `type` is `"Group"`, try `{name}_(band)` — the
   standard Wikipedia disambiguation suffix for musical groups.
4. Try the artist name directly (works for unambiguous names like
   `Kenny G`).
5. Fall back to `{name}_({disambiguation})` using MusicBrainz's own
   disambiguation string (e.g., `Queen_(band)`).

Steps 2-5 are Wikipedia-only and only run when step 1 doesn't yield a bio
(no Last.fm key configured, artist not found, or empty bio) — they're the
fallback path for users who haven't set up Last.fm.

## Services (`src/app/services/music.py`)

### Discography Sync

```python
def needs_discography_sync(artist: Artist) -> bool:
    """Check if discography needs syncing."""
    # True if never synced or synced > 7 days ago

def sync_artist_discography(artist: Artist, force: bool = False) -> int:
    """Sync all albums from MusicBrainz."""
    # Creates/updates Album records
    # Skips cover art for speed (loaded async via HTMX)
    # Sets artist.discography_synced_at
```

### Track Population

```python
def populate_album_tracks(album: Album) -> int:
    """Populate Track rows for an album."""
    # Called by the album repair task (app.music_page_repair) that an album page queues
    # Fetches tracks from MusicBrainz
    # Creates Track rows with title, number, duration
```

### Metadata helpers & MBID resolution

- `resolve_artist_mbid(name, sort_name=None)`: safe MusicBrainz lookup with multiple search variants; used to attach MBIDs to existing artists during enrichment.
- `resolve_album_mbid(album_title, artist_name=None)`: resolves release and release-group IDs while preferring album/EP/compilation types.
- `merge_artist_records(source, target)`: re-homes albums, trackers, and music rows into the canonical artist when MBIDs collide.
- `get_artist_hero_image(artist)`: picks the best hero image (Wikipedia > album art fallback) for detail pages.
- `refresh_album_cover_art` / `refresh_missing_album_covers`: fetch cover art from Cover Art Archive when MBIDs are present.
- `ensure_album_has_release_id` / `album_has_musicbrainz_id`: guard helpers used in views/enrichment before API calls.

### Linkage/backfill helpers

- `link_music_to_tracks(user, limit=None)`: connect `Music` rows to `Track` models via MBIDs, track numbers, or normalized titles.
- `backfill_music_runtimes(user, limit=None)`: set `Item.runtime_minutes` from linked track durations (or album tracklist matches when only recording IDs exist).
- `fix_music_album_links(user, limit=None)`: move `Music` rows onto canonical albums after dedupe/MBID attachment.

### Library enrichment tasks & validation

- `enrich_music_library_task(user_id)`: post-import job that attaches artist MBIDs, syncs discographies, dedupes albums, populates tracks, prefetches covers (respecting `MUSIC_DEFER_COVER_PREFETCH`), and backfills runtimes.
- `enrich_albums_task(user_id)`: resolves album MBIDs for user-linked albums, populates tracklists, links `Music` to `Track`, and fills runtimes.
- `populate_album_tracks_batch` / `prefetch_album_covers_batch`: batch tasks to hydrate tracks/covers for many albums after imports or scrobbles.
- Validation: `manage.py validate_music_library` (powered by `app/services/music_validation.py`) reports coverage metrics (MBIDs, track links, runtimes, missing metadata) for a user’s music library.

### Playback / Scrobble Pipeline (`src/app/services/music_scrobble.py`)

- **Plex webhooks** translate music payloads into `MusicPlaybackEvent` and call `record_music_playback`. Jellyfin and Emby webhooks do not currently support music (only TV and Movie).
- Plex imports (`src/integrations/imports/plex.py`) can also import music history with `defer_cover_prefetch=True` for faster batch processing.
- Plays (`media.play`) only update existing Music rows; scrobbles (`media.scrobble`) create or advance progress/history.
- Metadata resolution prefers MBIDs (recording/release/release-group/artist) and falls back to MusicBrainz search; payload validation drops mismatched MBIDs to avoid hijacking the wrong artist/album/track.
- Healing on scrobble:
  - Attaches artist MBID when found and triggers discography sync.
  - Dedupes albums/tracks by normalized title and re-homes trackers and Music rows to the canonical records.
  - Prefetches cover art for **all** missing album images for the artist (no limit) so posters arrive without page visits.
  - Ensures `ArtistTracker` and `AlbumTracker` exist for the user.
- Dedupe also runs in the repair task an artist or album page queues when it sees duplicate titles (`app.music_page_repair`), and when forcing a discography sync.

### Cover Art Prefetch

```python
def prefetch_album_covers(artist: Artist, limit: int = 20):
    """Prefetch cover art for albums missing images."""
    # Called async via HTMX after artist page loads
    # Also invoked after scrobbles (limit=None) to hydrate new artists completely
    # Only fetches for albums with missing images
    # Respects rate limits
```

## Views (`src/app/views.py`)

### Artist Views

```python
@require_GET
def artist_detail(request, artist_id):
    """Artist detail page (like TV show detail)."""
    # Attaches missing MBIDs/merges duplicates, then syncs discography if needed
    # Gets Wikipedia bio/image
    # Calculates play counts per album
    # Dedupes albums and kicks off HTMX cover prefetch in the background
    # Shows ArtistTracker modal for tracking

@require_GET
def prefetch_artist_covers(request, artist_id):
    """HTMX endpoint for async cover art loading."""
    # Called 500ms after artist page loads
    # Fetches missing album covers
    # Returns updated album grid HTML
```

### Album Views

```python
@require_GET
def album_detail(request, album_id):
    """Album detail page (like Season detail)."""
    # Heals incomplete albums by attaching release/group IDs and deduping to canonical album
    # Populates tracks if needed (release->release-group fallback)
    # Shows track list with play status
    # Links user Music rows to Track rows for listen counts/history chips
    # AlbumTracker modal for album tracking
```

### Tracking Views

```python
@require_GET
def artist_track_modal(request, artist_id):
    """Render artist tracking modal."""
    # Same UI pattern as TV show tracking

@require_POST
def artist_save(request):
    """Save/update ArtistTracker."""

@require_POST
def artist_delete(request):
    """Delete ArtistTracker."""

@require_GET
def album_track_modal(request, album_id):
    """Render album tracking modal."""

@require_POST
def album_save(request):
    """Save/update AlbumTracker."""

@require_POST
def album_delete(request):
    """Delete AlbumTracker."""

@require_POST
def song_save(request):
    """Add a listen for a track (like episode_save)."""

@require_GET
def list_music_releases(request, album_id):
    """Return the HTMX release/pressing picker for an album."""

@require_POST
def set_music_release(request, album_id):
    """Save a validated per-user release/pressing preference."""
```

### Creation Views

```python
@require_GET
def create_artist_from_search(request, artist_mbid):
    """Create Artist from MusicBrainz ID."""
    # Fetches artist metadata
    # Gets Wikipedia bio/image
    # Syncs discography (skip_cover_art=True)
    # Redirects to artist detail

@require_GET
def create_album_from_search(request, release_mbid):
    """Create Album from MusicBrainz release ID."""
```

### Search View

- `search` view renders `search_music.html` when `media_type=music`, calling `musicbrainz.search_combined` (artists + albums + tracks).
- Page 1 shows top artists/albums plus tracks; page 2+ paginates tracks only. Creation buttons hit `create_artist_from_search` / `create_album_from_search`.
- Cover art is skipped during search for speed and filled later when visiting detail pages.

## URL Patterns (`src/app/urls.py`)

```python
# Artist navigation
path("music/artist/<int:artist_id>/", views.artist_detail, name="artist_detail"),
path("music/artist/<int:artist_id>/covers/", views.prefetch_artist_covers, name="prefetch_artist_covers"),

# Album navigation
path("music/album/<int:album_id>/", views.album_detail, name="album_detail"),

# Artist tracking
path("music/artist/<int:artist_id>/track_modal/", views.artist_track_modal, name="artist_track_modal"),
path("music/artist/save/", views.artist_save, name="artist_save"),
path("music/artist/delete/", views.artist_delete, name="artist_delete"),

# Album tracking
path("music/album/<int:album_id>/track_modal/", views.album_track_modal, name="album_track_modal"),
path("music/album/save/", views.album_save, name="album_save"),
path("music/album/delete/", views.album_delete, name="album_delete"),

# Track/song tracking
path("music/song/save/", views.song_save, name="song_save"),

# Creation from search
path("music/artist/create/<str:artist_mbid>/", views.create_artist_from_search, name="create_artist_from_search"),
path("music/album/create/<str:release_mbid>/", views.create_album_from_search, name="create_album_from_search"),

# Metadata sync
path("music/artist/<int:artist_id>/sync/", views.sync_artist_discography_view, name="sync_artist_discography"),
path("music/album/<int:album_id>/sync/", views.sync_album_metadata_view, name="sync_album_metadata"),

# Release/pressing picker
path("details/music/album/<int:album_id>/releases", views.list_music_releases, name="list_music_releases"),
path("details/music/album/<int:album_id>/release", views.set_music_release, name="set_music_release"),
```

A track detail page uses the same shared media details shell, nested under its album: `/details/music/artist/<artist_id>/<artist_slug>/album/<album_id>/<album_slug>/track/<track_id>/<track_slug>/`. `music/track/<track_id>/` redirects there. Genre chips show `Track.genres` when that list has entries, otherwise the album's genres.

## Templates

### Artist Detail (`src/templates/app/music_artist_detail.html`)

Layout matches TV show detail:
- **Hero section**: Artist image (from Wikipedia), name, genre chips, bio
- **Score cards**: MB Score (from MusicBrainz rating), Your Score (from ArtistTracker)
- **Action button**: "Add to Library" / status button (opens tracking modal)
- **Left column**: Your History, Actions (lists, history, sync), Details (type, origin, dates)
- **Right column**: Album grid with async cover loading via HTMX

### Album Detail (`src/templates/app/music_album_detail.html`)

Layout matches Season detail:
- **Hero section**: Album cover, title, artist link, release type/year chips, stats
- **Action button**: Album tracking modal
  - **Left column**: Your History, Actions, and Details
  - **Right column**: Track list with "Track Song" buttons for each track

The release picker uses the shared Hardcover edition-picker interaction pattern in the
album tracking modal's Metadata tab, but validates MusicBrainz release IDs against the
album's release group and keeps release selection separate from tracking state.

The picker is available to authenticated users even when an album has no
`AlbumTracker`. It sends a safe `return_url` through the HTMX form and uses the
following internal endpoints:

- `GET details/music/album/<album_id>/releases`: browse and filter release-group pressings.
- `POST details/music/album/<album_id>/release`: validate membership in the release group and save the per-user preference.

### Album Grid (`src/templates/app/components/album_grid.html`)

Reusable component for displaying album cards:
- Cover art (or placeholder icon)
- Album title
- Release year and type
- Play count badge (if user has listens)

### Tracking Modals

- `artist_track_modal.html`: Same pattern as `fill_track.html` for TV
- `album_track_modal.html`: Same pattern for albums
- `fill_track_song.html`: Same pattern as `fill_track_episode.html` for tracks

Track notes are edited from album track rows or the track detail page through
`GET/POST music/track/<track_id>/notes/`. They use the current user's `Music.notes`
and the shared Markdown editor. Notes saves do not create listen history rows;
an unplayed track gets a Planning entry without a listen, and its first listen
completes that entry while preserving its notes.

### Music List (`src/templates/app/media_list.html`)

When `is_artist_list=True`:
- Shows `ArtistTracker` entries (not `Music` entries)
- Uses `artist_grid_items.html` or `artist_table_items.html`
- Mirrors TV show list behavior

### Music Search (`src/templates/app/search_music.html`)

- Dedicated layout for MusicBrainz combined search with sections for artists, albums, and tracks.
- Uses creation buttons that call `create_artist_from_search` / `create_album_from_search`; cover art placeholders swap once details are visited.

## Forms (`src/app/forms.py`)

```python
class MusicForm(MediaForm):
    """Form for Music tracking."""
    
class ArtistTrackerForm(forms.ModelForm):
    """Form for artist tracking modal."""
    class Meta:
        model = ArtistTracker
        fields = ["status", "score", "start_date", "end_date", "notes"]

class AlbumTrackerForm(forms.ModelForm):
    """Form for album tracking modal."""
    class Meta:
        model = AlbumTracker
        fields = ["status", "score", "start_date", "end_date", "notes"]
```

## Admin (`src/app/admin.py`)

Custom admin classes for music models:
- `ArtistAdmin`: Searchable by name, filterable by discography sync status
- `AlbumAdmin`: Searchable by title, filterable by artist and release type
- `TrackAdmin`: Inline editing, filterable by album
- `ArtistTrackerAdmin`: User/artist/status filters
- `AlbumTrackerAdmin`: User/album/status filters
- `MusicReleasePreferenceAdmin`: User/album/release ID filters

## Statistics

- Music is included in the global statistics rollups (`src/app/statistics.py`).
- `get_music_consumption_stats` computes minutes played (not hours), chart data, and top tracks/albums/artists from play history.
- Genre/decade/country rollups come from album → artist metadata (track genres used as fallback); country codes are expanded to full country names for display.
- Durations use track runtime minutes when available; otherwise fall back to album track matches or Item runtime. Minutes are multiplied by play count for aggregate time.
- Statistics cache (`statistics_cache`) and history cache include music so stats/history pages stay fast.

## Performance Optimizations

### Search Speed
- `search_combined()` passes `skip_cover_art=True` to avoid fetching cover art for every result
- Search results show placeholder images; covers load when viewing artist/album pages

### Artist Page Load
- Discography sync skips cover art fetching (`skip_cover_art=True`)
- Cover art loads asynchronously via HTMX endpoint (`prefetch_artist_covers`) and is also prefetched automatically after scrobbles to fill the entire artist catalog.
- Page renders immediately with placeholder images; covers fill in progressively

### Caching
- MusicBrainz API responses cached for 24 hours to 7 days
- Wikipedia data cached for 7 days
- Cover art URLs cached for 7 days
- Failed lookups cached for 1 day to avoid repeated failures

## What's NOT Implemented (Future Phases)

- Calendar events for album releases
- Import/export for music library beyond Plex history import
- Auto-pause for music playback
- Jellyfin/Emby webhook support for music (only Plex webhooks currently support music)
- Last.fm scrobbling support (mentioned in README as "to come")

## Checklist for Music-Related Changes

1. **Adding new music metadata**: Update `musicbrainz.py` provider functions
2. **Changing artist display**: Update `music_artist_detail.html` and `artist_grid_items.html`
3. **Changing album display**: Update `music_album_detail.html` and `album_grid.html`
4. **Adding tracking features**: Mirror TV show/season patterns in views and templates
5. **Performance issues**: Check if cover art is being fetched synchronously; use `skip_cover_art=True`
6. **Missing/wrong bio data**: Check the bio resolution strategy (Last.fm by MBID, then Wikipedia fallbacks) in `get_artist()`
