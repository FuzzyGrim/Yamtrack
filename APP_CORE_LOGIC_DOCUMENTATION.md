# Spine/YamTrack - Complete Application Logic Documentation

## Table of Contents
1. [Application Overview](#application-overview)
2. [Data Models & Architecture](#data-models--architecture)
3. [Media Types & Sources](#media-types--sources)
4. [User System & Preferences](#user-system--preferences)
5. [Media Tracking System](#media-tracking-system)
6. [TV Shows, Seasons & Episodes](#tv-shows-seasons--episodes)
7. [Books & Reading Sessions](#books--reading-sessions)
8. [Diary System](#diary-system)
9. [Calendar & Events](#calendar--events)
10. [Statistics & Analytics](#statistics--analytics)
11. [Lists & Collections](#lists--collections)
12. [Search & Discovery](#search--discovery)
13. [API Providers & Integrations](#api-providers--integrations)
14. [Notifications](#notifications)
15. [Hall of Fame](#hall-of-fame)
16. [Key User Flows](#key-user-flows)

---

## Application Overview

**Spine** (formerly YamTrack) is a comprehensive media tracking application that allows users to track their consumption of various types of media including:
- TV Shows (with seasons and episodes)
- Movies
- Anime
- Manga
- Games
- Books
- Comics

The application provides:
- Progress tracking with status management
- Calendar/event system for release dates
- Diary entries for logging consumption
- Statistics and analytics
- Custom lists and collections
- Hall of Fame (favorites)
- Notifications for new releases
- Import/export capabilities

---

## Data Models & Architecture

### Core Models

#### Item Model
The `Item` model is the central entity representing any media item in the system.

**Key Fields:**
- `media_id` (CharField): Unique identifier from the source (e.g., TMDB ID, MAL ID)
- `source` (CharField): Which provider the item comes from (TMDB, MAL, IGDB, etc.)
- `media_type` (CharField): Type of media (tv, movie, anime, manga, game, book, comic, season, episode)
- `title` (CharField): Display title
- `image` (URLField): Poster/cover image URL
- `season_number` (PositiveIntegerField, nullable): For seasons/episodes
- `episode_number` (PositiveIntegerField, nullable): For episodes
- `poster_accent_color` (CharField): Extracted accent color from poster
- `total_pages` (PositiveIntegerField, nullable): For books

**Constraints:**
- Unique constraints ensure no duplicate items (handles seasons/episodes specially)
- Season items must have season_number but no episode_number
- Episode items must have both season_number and episode_number
- Non-TV media cannot have season/episode numbers

**Key Methods:**
- `generate_manual_id()`: Creates sequential IDs for manual entries
- `fetch_releases()`: Triggers calendar event fetching for the item

#### Media Model (Abstract Base)
All media tracking models inherit from this abstract base class.

**Key Fields:**
- `item` (ForeignKey to Item): The media item being tracked
- `user` (ForeignKey to User): The user tracking this media
- `score` (DecimalField, 0-10): User's rating
- `progress` (PositiveIntegerField): Current progress (episodes watched, pages read, etc.)
- `progressed_at` (MonitorField): Auto-updated when progress changes
- `status` (CharField): Current status (Completed, In Progress, Planning, Paused, Dropped)
- `start_date` (DateTimeField, nullable): When user started consuming
- `end_date` (DateTimeField, nullable): When user finished consuming
- `notes` (TextField): User notes
- `created_at` (DateTimeField): When tracking entry was created

**Key Methods:**
- `process_progress()`: Validates and updates progress, auto-completes if max reached
- `process_status()`: Handles status changes, triggers calendar updates
- `increase_progress()`: Increment progress by 1
- `decrease_progress()`: Decrement progress by 1
- `mark_consumed()`: Mark as completed with proper date handling
- `formatted_score`: Property returning score as int if 10.0 or 0.0, else decimal
- `formatted_progress`: Property returning progress as string

**History Tracking:**
- Uses `simple_history` for full audit trail
- Excludes certain fields from history (item, user, created_at, etc.)

#### Media Type Models
Each media type has its own model inheriting from Media:

- **BasicMedia**: Base for simple types (Anime, Manga, Movie, Comic)
- **TV**: Special handling for TV shows with seasons
- **Season**: Tracks individual seasons of TV shows
- **Episode**: Individual episode tracking (separate model, not Media subclass)
- **Game**: Special progress handling (minutes played)
- **Book**: Reading session tracking

---

## Media Types & Sources

### Media Types
1. **TV** (`tv`): TV shows (tracked at season level)
2. **Season** (`season`): Individual seasons
3. **Episode** (`episode`): Individual episodes
4. **Movie** (`movie`): Movies
5. **Anime** (`anime`): Anime series
6. **Manga** (`manga`): Manga series
7. **Game** (`game`): Video games
8. **Book** (`book`): Books
9. **Comic** (`comic`): Comic books/series

### Sources (Data Providers)
1. **TMDB** (`tmdb`): The Movie Database - Movies, TV shows
2. **MAL** (`mal`): MyAnimeList - Anime, Manga
3. **MangaUpdates** (`mangaupdates`): Manga database
4. **IGDB** (`igdb`): Internet Game Database - Games
5. **OpenLibrary** (`openlibrary`): Books
6. **Hardcover** (`hardcover`): Books
7. **ComicVine** (`comicvine`): Comics
8. **Manual** (`manual`): User-created entries

---

## User System & Preferences

### User Model
Extends Django's AbstractUser with extensive preference tracking.

**Media Type Preferences:**
- Each media type has: `{type}_enabled`, `{type}_layout`, `{type}_sort`, `{type}_status`
- Layout options: Grid or Table
- Sort options: Score, Title, Progress, Start Date, End Date
- Status filter: All, Completed, In Progress, Planning, Paused, Dropped

**Home Page Preferences:**
- `home_sort`: How to sort in-progress items on home
  - Upcoming: By next release event
  - Recent: By last progress update
  - Completion: By completion percentage
  - Episodes Left: By remaining episodes
  - Title: Alphabetically

**Other Preferences:**
- `last_search_type`: Last media type searched
- `calendar_layout`: Grid or List view
- `lists_sort`: How to sort custom lists
- `list_detail_sort`: How to sort items within lists
- `hide_from_search`: Whether to hide from search results

**Notification Settings:**
- `notification_urls`: Apprise URLs for notifications
- `notification_excluded_items`: Items to exclude from notifications
- `release_notifications_enabled`: Receive release notifications
- `daily_digest_enabled`: Receive daily digest

**Profile:**
- `bio`: User bio (max 500 chars)
- `pronouns`: Preferred pronouns
- `location`: User location
- `profile_picture`: Profile image

**Hall of Fame:**
- One favorite item per media type: `hof_tv`, `hof_movie`, `hof_anime`, `hof_manga`, `hof_game`, `hof_book`, `hof_comic`

**Integration:**
- `token`: API token for external integrations
- `plex_usernames`: For Plex webhook matching

**Key Methods:**
- `update_preference(field_name, new_value)`: Updates preference if valid
- `get_enabled_media_types()`: Returns list of enabled types
- `get_active_media_types()`: Includes season if TV is enabled
- `get_hall_of_fame_items()`: Returns all HOF items
- `set_hall_of_fame_item(media_type, item)`: Sets HOF item
- `get_import_tasks()`: Returns import history and schedules

---

## Media Tracking System

### Status Management

**Status Values:**
- **Completed**: Finished consuming
- **In Progress**: Currently consuming
- **Planning**: Plan to consume
- **Paused**: Temporarily stopped
- **Dropped**: Stopped consuming permanently

**Status Transitions:**
- When status changes to Completed:
  - Progress is set to max_progress
  - End date is set to current time
  - For TV: All remaining seasons/episodes are created and marked completed
  - For Books: Final reading session is created
- When status changes to Dropped:
  - For TV: All in-progress seasons are marked dropped
  - For Books: All in-progress sessions are marked dropped
- When status changes to In Progress:
  - For TV: Next available season is started if none in progress
  - For Books: Reading session is created if none exists

### Progress Tracking

**Progress Calculation:**
- **Movies**: Binary (0 or 1)
- **TV Shows**: Sum of episodes watched across all seasons (excluding season 0)
- **Seasons**: Highest episode number watched (handles repeats)
- **Episodes**: Binary (watched or not)
- **Anime/Manga**: Episode/chapter number
- **Games**: Minutes played
- **Books**: Pages read
- **Comics**: Issue number

**Progress Updates:**
- `increase_progress()`: Increments by 1 (or 30 minutes for games)
- `decrease_progress()`: Decrements by 1 (or 30 minutes for games)
- Progress is validated against `max_progress` from provider metadata
- If progress reaches max_progress, status auto-changes to Completed

**Max Progress Calculation:**
- **Movies**: Always 1
- **TV Shows**: Sum of released episodes (from events)
- **Books**: From `item.total_pages`
- **Other**: From calendar events (content_number)

### Media Manager

The `MediaManager` provides sophisticated querying:

**Key Methods:**
- `get_media_list()`: Get filtered, sorted, searched media list
  - Filters by status
  - Searches by title
  - Sorts by various criteria
  - Handles deduplication (only latest instance per item)
  - Applies appropriate prefetch_related for performance

- `get_in_progress()`: Get in-progress items for home page
  - Annotates with max_progress
  - Annotates with next_event
  - Sorts by user preference
  - Limits results per type

- `get_media()`: Get single media instance
- `get_media_prefetch()`: Get with prefetch_related applied
- `filter_media()`: Filter by media_id, source, season/episode
- `annotate_max_progress()`: Calculates max_progress for items

**Sorting Logic:**
- **TV Shows**: Special handling for start_date, end_date, progress (aggregates from seasons/episodes)
- **Seasons**: Special handling for start_date, end_date, progress (aggregates from episodes)
- **Generic**: Standard field sorting with null handling

---

## TV Shows, Seasons & Episodes

### TV Show Model

**Special Properties:**
- `progress`: Calculated from sum of all season progress (excluding season 0)
- `last_watched`: Returns "SxxExx" format of latest watched episode
- `progressed_at`: Latest episode watched date
- `start_date`: Earliest episode watched date
- `end_date`: Latest episode watched date

**Status Management:**
- When TV status changes to Completed:
  - `_completed()`: Creates all remaining seasons and episodes, marks them completed
  - Only processes seasons with episodes > 0
  - Uses bulk operations for performance

- When TV status changes to Dropped:
  - `_mark_in_progress_seasons_as_dropped()`: Marks all in-progress seasons as dropped

- When TV status changes to In Progress:
  - `_start_next_available_season()`: Finds next unwatched season and starts it
  - If all existing seasons watched, fetches next season from provider

### Season Model

**Relationships:**
- `related_tv`: ForeignKey to TV show
- `episodes`: Related episodes (via Episode.related_season)

**Special Properties:**
- `progress`: Highest episode number watched (handles repeats by counting instances)
- `progressed_at`: Latest episode watched date
- `start_date`: Earliest episode watched date
- `end_date`: Latest episode watched date

**Status Management:**
- When season status changes to Completed:
  - `get_remaining_eps()`: Creates all remaining episodes
  - Only creates episodes that have aired (checks air_date)
  - Uses bulk operations

- When season status changes to Dropped:
  - If TV show is not dropped, TV show is marked as dropped

- When season status changes to In Progress:
  - If TV show is not in progress, TV show is marked as in progress

**Episode Management:**
- `increase_progress()`: Watches next episode (finds next from provider metadata)
- `decrease_progress()`: Unwatches current episode
- `watch(episode_number, end_date)`: Creates episode instance
- `unwatch(episode_number)`: Deletes latest episode instance
- `get_episode_item()`: Gets or creates Item for episode

**Auto-Season Progression:**
- When last episode of season is watched, season is marked completed
- If not the last season, next season is automatically started
- If it's the last season, TV show is marked completed

### Episode Model

**Fields:**
- `item`: ForeignKey to Item (episode item)
- `related_season`: ForeignKey to Season
- `end_date`: When episode was watched
- `created_at`: When episode entry was created

**Behavior:**
- Multiple instances allowed (for rewatches)
- When episode is created:
  - If it's the last episode of season, season is marked completed
  - If season was just completed and it's the last season, TV show is marked completed
  - If season was just completed and not last season, next season is started
  - If season is not in progress, it's marked in progress
  - If TV show is not in progress, it's marked in progress

---

## Books & Reading Sessions

### Book Model

**Special Fields:**
- `completion_diary_entry`: OneToOne link to DiaryEntry when book completed
- `completed_manually`: Boolean flag for manual completion

**Reading Session System:**
- Books use `BookSession` model for detailed tracking
- Each session can track:
  - `pages_read`: Number of pages
  - `percentage_read`: Percentage complete
  - `status`: Session status
  - `start_date`, `end_date`: Session dates
  - `notes`: Session notes

**Progress Snapshot:**
- `progress_snapshot`: Cached snapshot of latest reading progress
- Combines data from:
  - In-progress session (preferred)
  - Completed session (fallback)
  - Stored progress (if higher)
- Handles conversion between pages and percentage

**Status Management:**
- When status changes to Completed:
  - `_completed()`: Creates final reading session if none exists
  - Sets progress to total_pages if available

- When status changes to Dropped:
  - `_mark_in_progress_sessions_as_dropped()`: Marks all in-progress sessions as dropped

- When status changes to In Progress:
  - `_start_reading()`: Creates reading session if none exists

**Progress Logging:**
- `log_reading_session(progress_type, progress_value, notes)`: Logs reading progress
  - `progress_type`: "percentage" or "pages"
  - Converts between pages and percentage automatically
  - Updates or creates in-progress session
  - Updates book progress

**Properties:**
- `has_progress`: Whether any progress data exists
- `get_max_progress()`: Returns total_pages

### BookSession Model

**Fields:**
- `related_book`: ForeignKey to Book
- `pages_read`: Pages read in session
- `percentage_read`: Percentage read (0-100)
- `status`: Session status
- `start_date`, `end_date`: Session dates
- `notes`: Session notes

**Behavior:**
- When session status changes to Completed:
  - Book status is set to Completed
  - Book end_date is set

- When session status changes to In Progress:
  - Book status is set to In Progress
  - Book start_date is set if not already set

---

## Diary System

### DiaryEntry Model

**Purpose:** Log consumption of media with ratings, reviews, and metadata.

**Fields:**
- `item`: ForeignKey to Item (movie, TV, season, book, or game)
- `user`: ForeignKey to User
- `consumed_at`: DateTime when consumed
- `rating`: Decimal (0-10)
- `review`: Text review
- `liked`: Boolean favorite flag
- `is_rewatch`: Boolean rewatch flag
- `progress_snapshot`: JSON field storing progress at time of logging
  - For games: Stores playtime in minutes and formatted playtime
- `tags`: ManyToMany to Tag model

**Properties:**
- `rewatch_count`: Number of times this item was logged before this entry

**Validation:**
- Only allowed for: movies, TV shows, seasons, books, games
- Validated in `clean()` method

### Tag System

**Tag Model:**
- `name`: Unique tag name (lowercased, trimmed)
- `usage_count`: Number of times tag is used
- Auto-increments/decrements when tags added/removed

**DiaryEntryTag Model:**
- Through model for many-to-many relationship
- Tracks when tag was added to entry

**Tag Management:**
- Tags are normalized (lowercase, trimmed)
- Usage count is maintained automatically
- Tags can be added/removed from entries
- Tags are suggested based on usage count

### Diary Service Functions

**create_diary_entry():**
- Creates diary entry with all metadata
- Optionally marks media as consumed (`auto_mark_consumed`)
- For games: Captures playtime snapshot
- Adds tags to entry
- Queues statistics update

**mark_consumed():**
- Marks media as consumed without creating diary entry
- Sets end_date
- Queues statistics update

**update_diary_entry_tags():**
- Replaces all tags on an entry
- Clears existing tags, adds new ones

---

## Calendar & Events

### Event Model

**Purpose:** Track release dates and upcoming content for media items.

**Fields:**
- `item`: ForeignKey to Item
- `content_number`: Integer (episode number, chapter number, etc.) - nullable
- `datetime`: DateTime of release
- `notification_sent`: Boolean flag for notifications

**Constraints:**
- Unique constraint on (item, content_number)
- Unique constraint on (item) when content_number is null

**Properties:**
- `readable_content_number`: Formatted content number (e.g., "Ep 5")
- `is_sentinel_time`: Check if time is sentinel (no specific time)
- `is_max_datetime`: Check if datetime is sentinel datetime
- `display_time`: Formatted time string (empty if sentinel)

**Sentinel Time:**
- Used when exact release time is unknown
- Time: 11:59:59.999999
- Date: 9999-12-31 (for max datetime)
- Events with sentinel time are sorted last

### Event Manager

**get_user_events(user, first_day, last_day):**
- Gets all events for user in date range
- Filters by enabled media types
- Excludes items with inactive status (Paused, Dropped)
- For TV shows: Complex logic to exclude seasons after first dropped season
- Sorts with sentinel time last

**sort_with_sentinel_last():**
- Annotates events with sentinel flag
- Sorts by date, then sentinel flag, then time

### Calendar Processing

**fetch_releases():**
- Main entry point for calendar updates
- Processes items and creates/updates events
- Cleans up invalid events
- Returns status message

**process_items():**
- Categorizes items by type
- Calls appropriate processor:
  - `process_tv()`: For TV shows
  - `process_season()`: For seasons
  - `process_comic()`: For comics
  - `process_other()`: For other types

**TV Show Processing:**
- Fetches TV metadata with seasons
- Creates events for each season's episodes
- Handles special cases (season 0, specials)
- Only processes seasons with episodes > 0

**Season Processing:**
- Fetches season metadata
- Creates events for each episode
- Uses episode air_date for datetime

**Comic Processing:**
- Fetches comic issues
- Creates events for each issue
- Handles special date formats

**Other Media Processing:**
- Fetches metadata
- Extracts release date from details
- Creates single event (or content_number event for series)

**Event Cleanup:**
- Removes events for items no longer tracked
- Removes events for content that doesn't exist anymore

**Filtering Logic:**
- Only processes items that need updates:
  - Items with no events
  - Items with future events
  - Comics with events within last year
  - TV shows with seasons that haven't all aired

---

## Statistics & Analytics

### Statistics Module

**get_user_media(user, start_date, end_date):**
- Gets all media items for user in date range
- Returns media dict and count dict
- Handles special cases for TV/episodes
- Caches episode queries for performance

**get_media_type_distribution(media_count):**
- Calculates percentage distribution across media types
- Returns data for pie chart

**get_score_distribution(user_media):**
- Calculates score distribution (0-10)
- Identifies top-rated items
- Returns data for charts

**get_status_distribution(user_media):**
- Calculates status distribution
- Returns counts per status

**get_status_pie_chart_data(status_distribution):**
- Formats data for pie chart visualization

**get_timeline(user_media):**
- Creates timeline of media consumption
- Sorts by start_date
- Groups by date for visualization

**get_activity_data(user, start_date, end_date):**
- Creates activity heatmap data
- Uses historical records for accuracy
- Calculates:
  - Daily activity counts
  - Activity levels (0-4 based on count)
  - Most active day of week
  - Current streak
  - Longest streak
- Formats as calendar weeks
- Includes month labels

**Activity Levels:**
- Level 0: 0 items
- Level 1: 1 item
- Level 2: 2-3 items
- Level 3: 4-5 items
- Level 4: 6+ items

**Historical Data:**
- Uses `simple_history` records
- Streams data to keep memory usage low
- Buckets by date
- Handles timezone conversion

**Daily Statistics Updates:**
- Celery task: `update_daily_statistics`
- Triggered when diary entries created or media marked consumed
- Updates activity data for specific date
- Runs asynchronously

---

## Lists & Collections

### CustomList Model

**Purpose:** User-created collections of media items.

**Fields:**
- `name`: List name
- `description`: Optional description
- `owner`: User who created list
- `collaborators`: ManyToMany to User
- `items`: ManyToMany to Item (through CustomListItem)

**Permissions:**
- `user_can_view()`: Owner or collaborator
- `user_can_edit()`: Owner or collaborator
- `user_can_delete()`: Owner only

**Properties:**
- `image`: First item's image (or default)

### CustomListItem Model

**Through Model:**
- `item`: ForeignKey to Item
- `custom_list`: ForeignKey to CustomList
- `date_added`: Auto timestamp

**Constraints:**
- Unique constraint on (item, custom_list)

### List Management

**get_user_lists(user):**
- Returns lists user owns or collaborates on
- Prefetches related data for performance
- Orders items by date_added

**get_user_lists_with_item(user, item):**
- Returns lists with membership status
- Annotates with `has_item` boolean
- Used for UI to show which lists contain item

---

## Search & Discovery

### Search Functionality

**media_search():**
- Searches across multiple providers
- Supports pagination
- Supports secondary source selection
- Returns search results with metadata

**Provider-Specific Search:**
- **TMDB**: Movies and TV shows
- **MAL**: Anime and Manga
- **MangaUpdates**: Manga (alternative)
- **IGDB**: Games
- **OpenLibrary**: Books
- **Hardcover**: Books (alternative)
- **ComicVine**: Comics

**Search Results:**
- Includes: title, image, media_id, source
- Paginated (24 per page default)
- Cached for performance

### Media Details

**media_details():**
- Shows full details for media item
- Fetches metadata from provider
- Shows user's tracking instances
- Displays diary entries
- Shows MDBList ratings (for movies/TV)
- Calculates poster accent color
- Handles custom posters

**season_details():**
- Shows season-specific details
- Displays episodes with watched status
- Shows diary entries for season
- Handles custom season posters

### Poster Selection

**Custom Posters:**
- Users can select custom posters for items
- Stored in `CustomPosterPreference` model
- Per-user, per-item
- Used in UI instead of default poster

**Poster Selection Modals:**
- `poster_selection_modal`: For movies/TV/anime/manga
- `season_poster_selection_modal`: For seasons
- `game_poster_selection_modal`: For games
- `book_cover_selection_modal`: For books

**Poster Accent Color:**
- Extracted from poster image
- Stored in `Item.poster_accent_color`
- Used for UI theming
- Computed on-demand or cached

---

## API Providers & Integrations

### Provider System

**Provider Services:**
- Centralized service layer (`app.providers.services`)
- Rate limiting via Redis
- Error handling and retries
- Caching for performance

**Rate Limiting:**
- Per-provider limits:
  - TMDB: 5 requests/second
  - MAL: 30 requests/minute
  - IGDB: 3 requests/second
  - ComicVine: 190 requests/hour
  - OpenLibrary: 20 requests/minute
  - Hardcover: 55 requests/minute

**Provider Functions:**
- `get_media_metadata()`: Fetches full metadata
- `search()`: Searches provider database
- `api_request()`: Low-level API call with error handling

### Provider-Specific Details

**TMDB:**
- Movies, TV shows, seasons, episodes
- Supports NSFW filtering
- Language support
- Image URLs

**MAL:**
- Anime and manga
- Supports NSFW filtering
- OAuth authentication

**IGDB:**
- Games
- OAuth authentication
- Supports NSFW filtering

**OpenLibrary:**
- Books
- Free API
- ISBN lookup

**Hardcover:**
- Books
- GraphQL API
- Bearer token auth

**ComicVine:**
- Comics
- API key auth
- Rate limited

**MangaUpdates:**
- Manga database
- Web scraping
- Supports NSFW filtering

### Manual Provider

**Manual Entries:**
- Users can create custom items
- No external API calls
- Full control over metadata
- Supports all media types
- Can link to parent TV/season

**Manual ID Generation:**
- Sequential IDs per media type
- Format: "1", "2", "3", etc.
- Stored as string

---

## Notifications

### Notification System

**Apprise Integration:**
- Uses Apprise for multi-platform notifications
- Supports: Email, Discord, Slack, Telegram, Pushover, etc.
- Configured via `notification_urls` field

### Release Notifications

**send_releases():**
- Checks for events released in last 30 minutes
- Filters by user preferences
- Excludes user-specified items
- Sends notifications via Apprise
- Marks events as notified

**Notification Content:**
- Title: "🔔 YamTrack: New Releases Available! 🔔"
- Lists all recent releases
- Includes media type and content number
- Links to media details

### Daily Digest

**send_daily_digest():**
- Sends digest of today's releases
- Runs at configured hour (default 8 AM)
- Only for users with digest enabled
- Groups by media type

### Notification Preferences

**User Settings:**
- `release_notifications_enabled`: Toggle release notifications
- `daily_digest_enabled`: Toggle daily digest
- `notification_urls`: Apprise URLs
- `notification_excluded_items`: Items to exclude

---

## Hall of Fame

### Hall of Fame System

**Purpose:** Allow users to select one favorite item per media type.

**Fields on User Model:**
- `hof_tv`: Favorite TV show
- `hof_movie`: Favorite movie
- `hof_anime`: Favorite anime
- `hof_manga`: Favorite manga
- `hof_game`: Favorite game
- `hof_book`: Favorite book
- `hof_comic`: Favorite comic

**Methods:**
- `get_hall_of_fame_items()`: Returns all HOF items as dict
- `get_hall_of_fame_item(media_type)`: Gets HOF item for type
- `set_hall_of_fame_item(media_type, item)`: Sets HOF item
- `clear_hall_of_fame_item(media_type)`: Clears HOF item
- `get_hall_of_fame_count()`: Counts how many are set

**UI:**
- Search interface for finding items
- Toggle to set/clear HOF item
- Display on user profile

---

## Key User Flows

### Adding Media

1. **Search:**
   - User searches for media
   - Selects media type and provider
   - Enters search query
   - Views paginated results

2. **Select Item:**
   - Clicks on search result
   - Views media details page
   - Sees metadata, ratings, etc.

3. **Track Media:**
   - Clicks "Track" or "Add to List"
   - Modal opens with tracking form
   - User sets: status, score, notes
   - Media instance created

4. **Manual Entry:**
   - User creates custom item
   - Fills in title, image, metadata
   - For seasons/episodes: Links to parent
   - Item created with manual source

### Tracking Progress

1. **Update Progress:**
   - From home page: Click +/- buttons
   - From details page: Update progress field
   - Progress validated against max
   - Status auto-updates if max reached

2. **TV Show Progress:**
   - Watch episode: Creates Episode instance
   - Season progress updates automatically
   - TV show progress updates automatically
   - Next season starts when current completes

3. **Book Progress:**
   - Log reading session
   - Enter pages or percentage
   - Session created/updated
   - Book progress updates

### Diary Entry

1. **Create Entry:**
   - From media details or list
   - Click "Log" or "Add Diary Entry"
   - Modal opens with form
   - Enter: date, rating, review, tags
   - Optionally mark as consumed
   - Entry created

2. **Edit Entry:**
   - From diary list or details
   - Click edit
   - Update fields
   - Save changes

3. **Delete Entry:**
   - From diary list or details
   - Click delete
   - Confirm deletion
   - Entry removed

### Calendar

1. **View Calendar:**
   - Navigate to calendar page
   - Select month/year
   - View events in grid or list
   - Click event to view media details

2. **Reload Calendar:**
   - Click "Reload Calendar"
   - System fetches latest releases
   - Events updated
   - User notified of results

3. **Download Calendar:**
   - Generate iCal file
   - Includes all user events
   - Can import into calendar apps

### Statistics

1. **View Statistics:**
   - Navigate to statistics page
   - Select date range (or "All Time")
   - View charts and graphs:
     - Media type distribution
     - Score distribution
     - Status distribution
     - Timeline
     - Activity heatmap

2. **Activity Tracking:**
   - Automatically tracked via history
   - Updates when:
     - Media status changes
     - Progress updates
     - Diary entries created
   - Shows daily activity levels

### Lists

1. **Create List:**
   - Navigate to lists page
   - Click "Create List"
   - Enter name and description
   - List created

2. **Add to List:**
   - From media details
   - Click "Add to List"
   - Select lists to add to
   - Item added

3. **Manage Lists:**
   - Add collaborators
   - Reorder items
   - Remove items
   - Delete list

---

## Technical Implementation Details

### Caching

**Cache Strategy:**
- Metadata cached for 24 hours
- Cache key format: `{source}_{media_type}_{media_id}`
- Cache invalidation on sync
- Redis backend in production

### Background Tasks

**Celery Tasks:**
- `reload_calendar`: Fetches release dates
- `send_release_notifications`: Sends notifications
- `send_daily_digest`: Sends daily digest
- `update_daily_statistics`: Updates statistics

**Task Scheduling:**
- Calendar reload: Every 24 hours
- Release notifications: Every 10 minutes
- Daily digest: Configurable hour (default 8 AM)

### History Tracking

**simple_history:**
- Full audit trail for all media changes
- Excludes certain fields (item, user, created_at)
- Used for statistics and activity tracking
- Cascade delete on media deletion

### Performance Optimizations

**Query Optimization:**
- Prefetch_related for related objects
- Select_related for foreign keys
- Window functions for deduplication
- Bulk operations for batch updates
- Streaming for large datasets

**Database:**
- PostgreSQL in production
- SQLite for development
- Connection pooling
- Indexes on frequently queried fields

### Error Handling

**Provider Errors:**
- Rate limiting: Automatic retry with backoff
- API errors: Logged and user notified
- Timeout handling: 120 second timeout
- Fallback to cached data when possible

---

## API Endpoints Summary

### Media Management
- `GET /`: Home page
- `GET /media/<type>`: Media list
- `GET /search`: Search media
- `GET /details/<source>/<type>/<id>/<title>`: Media details
- `POST /media/save`: Save media
- `POST /media/delete`: Delete media
- `POST /progress/<type>/<id>`: Update progress

### TV/Season/Episode
- `POST /tv/start/<source>/<type>/<id>`: Start tracking TV
- `POST /tv/watch/<source>/<type>/<id>`: Mark TV watched
- `POST /season/watch/<source>/<type>/<id>/<season>`: Mark season watched
- `POST /episode/watch/<source>/<type>/<id>/<season>/<episode>`: Watch episode

### Books
- `POST /book/progress/log/<source>/<id>`: Log reading progress
- `POST /book/mark_read/<source>/<id>`: Mark book read
- `POST /book/start/<source>/<id>`: Start reading

### Diary
- `POST /diary/add/<source>/<type>/<id>`: Add diary entry
- `GET /diary`: Diary list
- `GET /diary/edit/<id>`: Edit diary entry
- `DELETE /diary/delete/<id>`: Delete diary entry

### Calendar
- `GET /calendar`: Calendar view
- `POST /calendar/reload`: Reload calendar
- `GET /calendar/download/<token>`: Download iCal

### Statistics
- `GET /statistics`: Statistics page

### Lists
- `GET /lists`: Lists page
- `POST /lists/create`: Create list
- `POST /lists/<id>/add`: Add item to list

### Hall of Fame
- `GET /hof/search`: Search for HOF item
- `POST /hof/toggle`: Toggle HOF item

---

## Data Flow Examples

### Adding a TV Show

1. User searches for "Breaking Bad"
2. System queries TMDB API
3. Results displayed with metadata
4. User clicks on result
5. System fetches full TV metadata (cached)
6. User clicks "Track"
7. System creates:
   - Item (if not exists)
   - TV instance for user
   - Season instances for existing seasons
   - Episode instances for watched episodes (if any)
8. System triggers calendar fetch
9. Calendar events created for future episodes
10. User redirected to TV details page

### Watching an Episode

1. User on season details page
2. User clicks "Watch" on episode
3. System:
   - Gets or creates Episode Item
   - Creates Episode instance with end_date = now
4. Episode.save() triggers:
   - Season progress recalculated
   - If last episode: Season marked completed
   - If season completed and not last: Next season started
   - If last season: TV show marked completed
   - TV show status updated if needed
5. Calendar events updated
6. Statistics queued for update
7. UI refreshed

### Creating Diary Entry

1. User on movie details page
2. User clicks "Log"
3. Modal opens with form
4. User enters: date, rating, review, tags
5. User submits
6. System:
   - Creates DiaryEntry
   - Adds tags (creating if needed)
   - If auto_mark_consumed: Marks movie as completed
   - Queues statistics update
7. User redirected to diary or details page

---

## Important Notes for iOS Development

### Authentication
- Uses Django Allauth
- Supports social authentication
- Session-based auth
- CSRF protection required

### API Considerations
- Most endpoints are POST (not RESTful)
- CSRF token required for POST requests
- HTMX headers used for partial updates
- JSON responses for AJAX calls

### Data Synchronization
- Calendar events update daily
- Metadata cached for 24 hours
- Progress updates are immediate
- Statistics update asynchronously

### Media Types Handling
- TV shows tracked at season level
- Episodes are separate instances (allows rewatches)
- Books use reading sessions
- Games track playtime in minutes

### Status Workflows
- Status changes trigger cascading updates
- TV shows manage seasons automatically
- Books manage sessions automatically
- Progress validation prevents invalid states

### Performance
- Use prefetch_related for related data
- Paginate large lists
- Cache metadata when possible
- Stream large datasets

### Error Handling
- Provider APIs may fail
- Rate limiting may occur
- Network timeouts possible
- Always have fallback UI states

---

This document provides a comprehensive overview of the Spine/YamTrack application. Use this as a reference when building the iOS app to ensure all functionality is properly implemented.

