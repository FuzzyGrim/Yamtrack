# Time Left Sort Feature for TV Shows

## Overview

This feature adds a new sorting option called "Time Left" for TV shows in the media list and media grid views. The sorting algorithm prioritizes shows that are closest to completion (100%) but not yet complete, helping users identify which shows they're closest to finishing.

## How It Works

### Sorting Logic

The "Time Left" sort uses the following algorithm:

1. **Calculate Episodes Left**: For each TV show, calculate `total_episodes - episodes_watched`
2. **Calculate Actual Time Left**: Sum the runtime of all unwatched episodes
3. **Prioritize Least Time**: Shows with **least actual watch time remaining** appear first
4. **Defer Completed Shows**: 100% completed shows (0 time left) appear at the end
5. **Fallback Sorting**: If runtime data unavailable, fall back to episode count sorting

### Example Sorting Order

Given these TV shows with their progress and runtime:
- Percy Jackson: 8/10 episodes (2 episodes left) - 2 × 45min = 1h 30m left
- 100 Humans: 6/8 episodes (2 episodes left) - 2 × 30min = 1h left  
- The Last of Us: 9/16 episodes (7 episodes left) - 7 × 60min = 7h left
- Kitchen Nightmares: 58/92 episodes (34 episodes left) - 34 × 45min = 25h 30m left
- Breaking Bad: 62/62 episodes (0 episodes left - completed)

The "Time Left" sort would order them as:
1. 100 Humans (1h left - least time remaining)
2. Percy Jackson (1h 30m left - second least time)
3. The Last of Us (7h left)
4. Kitchen Nightmares (25h 30m left)
5. Breaking Bad (0h left - completed, appears last)

### Enhanced Time-Based Sorting

The current implementation now factors in actual episode runtime for more accurate time-based sorting:

- **Before**: 2 episodes left = higher priority than 3 episodes left
- **Now**: 2 short episodes (30 min each) = 1 hour left vs 1 long episode (90 min) = 1.5 hours left
- **Result**: Shows with less actual watch time remaining will appear first, regardless of episode count

### Implementation Details

The time calculation works by:

1. **Accessing Season Metadata**: Fetches the first season of each TV show to access episode runtime data
2. **Episode Runtime Extraction**: Gets individual episode runtime values from the season response
3. **Average Calculation**: Calculates the average episode runtime for the season
4. **Time Calculation**: Multiplies episodes left by average episode runtime
5. **Human-Readable Format**: Converts total minutes to "Xh Ym" format
6. **Smart Fallback**: Falls back to episode count if runtime data unavailable

**Data Source**: The runtime information comes from the same season metadata that powers the TV Seasons media details page, ensuring consistency and accuracy.

**Why This Approach**: TV shows often have consistent episode lengths within a season, so using the first season's average runtime provides a good estimate for the entire show.

### Column Layout Optimization

When using "Time Left" sorting:

- **Progress Column**: Automatically hidden to save space
- **Episodes Left Column**: Shows remaining episode count
- **Time Left Column**: Enhanced width (`w-24` class) to prevent text wrapping
- **Result**: Better readability for longer time values like "25h 30m"

### Pagination and Sorting Fix

**Problem Identified**: The original implementation was sorting only the current page (32 items) instead of the entire dataset, causing:
- Shows with similar time values to cluster together on each page
- Inconsistent sorting patterns across pages
- "Chunked" appearance where time values would grow properly, then reset with 0-time shows

**Solution Implemented**: 
- **Sort First**: Apply time-based sorting to the entire dataset before pagination
- **Paginate Second**: Split the properly sorted list into pages
- **Result**: Consistent time-based sorting across all pages, with shows properly ordered from least time remaining to most

**Technical Details**:
- Convert queryset to list for custom sorting
- Calculate time_left for all shows in the dataset
- Sort entire list by actual time values
- Apply pagination to the sorted list
- Each page now shows a proper slice of the globally sorted data

### Enhanced Time Calculation Fallbacks

**Problem Identified**: Some shows were falling back to "X ep" format instead of actual time calculations, and some time calculations seemed inaccurate.

**Solution Implemented**: Multi-tier fallback strategy for runtime data:

1. **Primary**: Individual episode runtimes from season metadata (most accurate)
2. **Fallback 1**: TV show runtime from metadata (parsed from formatted strings like "45m" or "1h 30m")
3. **Fallback 2**: Industry standard episode lengths based on source:
   - TMDB shows: 30 minutes (typical for 22-45 minute episodes)
   - MyAnimeList: 23 minutes (typical for anime episodes)
4. **Emergency Fallback**: Standard runtime when all else fails

**Runtime Parsing**: Enhanced parsing logic to handle TMDB's formatted runtime strings:
- "45m" → 45 minutes
- "1h 30m" → 90 minutes
- "2h 15m" → 135 minutes

**Result**: More consistent time calculations across all TV shows, with fewer fallbacks to "X ep" format.

### Season 0 Bypass and Runtime Validation

**Problem Identified**: Many shows were using "season 0" data which often contains:
- Special episodes and OVAs with different runtime characteristics
- Behind-the-scenes content
- Pilot episodes
- Movies or extended content

This led to unrealistic episode runtimes like:
- **Rent-a-Girlfriend**: 1.5 minutes per episode (should be ~23 minutes)
- **Ted Lasso**: 5 minutes per episode (should be ~30 minutes)  
- **Severance**: 7 minutes per episode (should be ~45-60 minutes)

**Solution Implemented**:

1. **Season Selection Priority**: 
   - **First Choice**: Season 1 or higher (regular TV episodes)
   - **Fallback**: Season 0 only if no other seasons exist

2. **Runtime Validation**: 
   - **Valid Range**: 15-90 minutes per episode
   - **Unrealistic Detection**: Automatically triggers fallback when runtime < 15min or > 90min
   - **Smart Fallback**: Falls back to TV show runtime or industry standards

3. **Improved Accuracy**:
   - Eliminates most unrealistic time calculations
   - Provides more consistent episode length estimates
   - Better handling of shows with special content in season 0

### Enhanced Sorting Logic with Status Priority

**Problem Identified**: The original sorting only considered time remaining, which could mix dropped shows with actively watched shows, making it harder to focus on shows you're actually planning to finish.

**Solution Implemented**: Multi-tier sorting that respects user intent:

1. **Active Shows First**: All non-dropped shows sorted by time remaining (least to most)
2. **Dropped Shows Second**: Shows with "Dropped" status appear after active shows, still sorted by time remaining
3. **Completed Shows Last**: 100% completed shows appear at the very bottom

**Sort Order**:
```
1. Shows with LEAST time remaining (actively watching/planning)
2. Shows with MORE time remaining (actively watching/planning)  
3. Shows with MOST time remaining (actively watching/planning)
4. Shows with Dropped status (sorted by time remaining)
5. 100% completed shows (at the very bottom)
```

**Benefits**:
- **Focus on Active Content**: Shows you're actively watching appear first
- **Logical Progression**: Easy to see what's almost done vs. what needs more time
- **Dropped Shows Accessible**: Dropped shows are still visible and sorted by time, but don't compete with active shows
- **Trakt Consistency**: Follows the same pattern as Trakt's Progress view

**Technical Implementation**: 
- Status field accessed via `media.status` (Media model) not `media.item.status` (Item model)
- Large offset (1 million minutes) added to dropped show sort keys to ensure proper positioning
- Maintains time-based sorting within each status group

## Implementation Details

### Files Modified

1. **`src/users/models.py`**
   - Added `TIME_LEFT = "time_left", "Time Left"` to `MediaSortChoices`

2. **`src/app/models.py`**
   - Added `time_left` case in `_sort_tv_media_list()` method
   - Returns queryset as-is for custom Python sorting
   - **Fixed Progress Display**: Enhanced `formatted_progress` property to show full format (e.g., "275 / 340")

3. **`src/app/views.py`**
   - Added custom sorting logic in `media_list()` view
   - Implements the time_left sorting algorithm after `max_progress` annotation

4. **Template Updates**
   - Updated `media_table_items.html` to use enhanced `formatted_progress`
   - Updated `media_card.html` to use enhanced `formatted_progress`
   - Updated `media_details.html` to use enhanced `formatted_progress`
   - Updated `progress_changer.html` to use enhanced `formatted_progress`

5. **Database Migration**
   - Created `users.0038_add_time_left_sort_option` migration
   - Updates all sort fields to include "time_left" option
   - Updates database constraints accordingly

### Key Components

#### Sort Key Function
```python
def time_left_sort_key(media):
    if not hasattr(media, 'max_progress') or media.max_progress == 0:
        return 0
    
    progress_percentage = (media.progress / media.max_progress) * 100
    
    # If 100% complete, put at the end
    if progress_percentage >= 100:
        return -1
    
    # Otherwise, sort by how close to 100% (higher percentage first)
    return progress_percentage
```

#### View Integration
The sorting is applied in the `media_list` view after the `annotate_max_progress` method is called, ensuring that all TV shows have their `max_progress` properly calculated before sorting.

## Progress Display Fix

### Issue Identified

During implementation, it was discovered that the Progress column was only displaying single digits (e.g., "275") instead of the expected full format (e.g., "275 / 340"). This was caused by two issues:

1. **Template Display Issue**: The `formatted_progress` property in the base `Media` class only returned the progress number as a string
2. **Root Cause - Empty Events Table**: The `max_progress` was showing as 0 because the events table was completely empty, preventing the calculation of total episode counts

### Solution Implemented

#### 1. Enhanced `formatted_progress` Property

The `formatted_progress` property was enhanced to automatically include the `max_progress` when available:

```python
@property
def formatted_progress(self):
    """Return the progress of the media in a formatted string."""
    if hasattr(self, 'max_progress') and self.max_progress is not None:
        return f"{self.progress} / {self.max_progress}"
    return str(self.progress)
```

#### 2. Fallback Metadata Retrieval

Since the events table was empty, a fallback mechanism was implemented to get episode counts directly from TV show metadata:

```python
# If no events were found, try to get max_progress from metadata
if tv.max_progress == 0:
    try:
        from app.providers import services
        metadata = services.get_media_metadata(
            MediaTypes.TV.value,
            tv.item.media_id,
            tv.item.source
        )
        tv.max_progress = metadata.get("max_progress", 0)
    except Exception:
        # If metadata retrieval fails, keep max_progress as 0
        tv.max_progress = 0
```

### Template Updates

All templates were updated to use the enhanced `formatted_progress` property instead of manually concatenating progress and max_progress:

- **Before**: `{{ media.formatted_progress }} / {{ media.max_progress }}`
- **After**: `{{ media.formatted_progress }}`

### Files Updated

- `
```
**Example Display:**
```
Title                    | Progress | Episodes Left | Time Left
100 Humans              | 6/8      | 2             | 1h 13m
Percy Jackson           | 8/10     | 2             | 1h 30m
The Last of Us          | 9/16     | 7             | 7h 0m
Kitchen Nightmares      | 58/92    | 34            | 25h 30m
```

**Note**: Time Left now shows actual watch time in clean "Xh Ym" format when episode runtime data is available from TMDB. This provides much more accurate sorting than episode count alone.

### Episodes Left and Time Left Columns

When using the "Time Left" sort option, two additional columns appear to help users understand their progress:

#### Episodes Left Column
- **Calculation**: `max_progress - progress`
- **Display**: Only shown when `current_sort == "time_left"` and `media_type == MediaTypes.TV.value`
- **Purpose**: Shows the number of episodes remaining to complete each show

#### Time Left Column
- **Current Implementation**: Shows actual watch time remaining based on episode runtime
- **Calculation**: Sums the runtime of all unwatched episodes
- **Display Format**: Human-readable time (e.g., "2h 30m", "45m", "1h 15m")
- **Fallback**: If runtime data unavailable, shows episode count (e.g., "3 ep")
- **Purpose**: Provides accurate time-based view of completion progress

#### Progress Column Behavior
- **Hidden during Time Left sort**: The Progress column is automatically hidden when using "Time Left" sorting
- **Space optimization**: This gives more width to the Time Left column for better readability
- **No information loss**: The Episodes Left column provides the same progress information in count format
- **Automatic restoration**: Progress column reappears when switching to other sort options

### TV Season Details Card Runtime Calculation

**Overview**: The TV Season details card displays key information including runtime calculations that are essential for the time-left sort functionality.

**Data Flow**:
1. **Season Details View** (`season_details` in `views.py`):
   - Fetches metadata using `services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])`
   - Calls `tmdb.process_seasons()` to process the raw API response
   - Passes processed metadata to the template

2. **TMDB Provider Processing** (`process_season` in `tmdb.py`):
   - Iterates through all episodes in the season
   - Collects individual episode runtimes from `episode["runtime"]`
   - Calculates total runtime by summing all episode runtimes
   - Calculates average runtime by dividing total by episode count
   - Formats durations using `get_readable_duration()` function

3. **Template Rendering** (`media_details.html`):
   - Loops through `media.details.items` to display each detail
   - Uses `no_underscore` filter to convert keys like "first_air_date" to "FIRST AIR DATE"
   - Displays values directly from the processed metadata

**Runtime Calculation Details**:
- **Individual Episode Runtime**: Each episode's `runtime` field from TMDB API (in minutes)
- **Average Runtime**: `sum(runtimes) / len(runtimes)` formatted as "48m" or "1h 30m"
- **Total Runtime**: `sum(runtimes)` formatted as "8h 5m" for the entire season
- **Episode Count**: `len(episodes)` showing total number of episodes

**Key Functions**:
- `get_readable_duration(duration)`: Converts minutes to "Xh Ym" format
- `get_start_date(air_date)`: Extracts season start date from first episode
- `get_end_date(response)`: Extracts season end date from last episode's air date

**Example Output**:
```
Details
FIRST AIR DATE: 2015-06-24
LAST AIR DATE: 2015-09-02  
EPISODES: 10
RUNTIME: 48m
TOTAL RUNTIME: 8h 5m
Source: The Movie Database
```

**Integration with Time-Left Sort**:
- The average runtime (48m) is used as the base calculation for remaining episodes
- Total runtime (8h 5m) provides context for the full season duration
- Episode count (10) determines how many episodes remain unwatched
- These values feed into the time-left calculations: `episodes_left × avg_runtime = time_left`

**Data Source**: The Movie Database (TMDB) API provides episode-level runtime data, which is more accurate than show-level estimates and enables precise time calculations for the sorting functionality.

### Runtime Data Storage and Caching

**Important**: The runtime calculations are NOT performed every time the media details page is loaded. Instead, they are calculated once and stored in Django's cache system.

**Storage Location**: Django Redis Cache
- **Cache Backend**: `django_redis.cache.RedisCache`
- **Cache Timeout**: 24 hours (86,400 seconds)
- **Cache Keys**: 
  - TV Show: `tmdb_tv_{media_id}`
  - Season: `tmdb_season_{media_id}_{season_number}`

**Caching Strategy**:
1. **First Request**: When a season is accessed for the first time:
   - TMDB API call fetches raw episode data
   - `process_season()` calculates runtimes and formats data
   - Results stored in Redis cache with 24-hour TTL

2. **Subsequent Requests**: For the next 24 hours:
   - Data served directly from cache
   - No API calls or recalculations
   - Instant page loads with pre-computed runtime data

3. **Cache Expiration**: After 24 hours:
   - Cache entry expires
   - Next request triggers fresh API call and recalculation
   - New data cached for another 24 hours

**Cache Implementation** (from `tmdb.py`):
```python
# Check cache first
season_cache_key = f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}"
season_data = cache.get(season_cache_key)

if season_data:
    # Use cached data
    data[f"season/{season_number}"] = season_data
else:
    # Fetch from API and cache
    season_data = process_season(response[season_key])
    cache.set(season_cache_key, season_data)
```

**Benefits**:
- **Performance**: No API calls on repeat visits
- **Cost Efficiency**: Reduces TMDB API usage
- **User Experience**: Fast page loads with cached data
- **Data Consistency**: Same runtime values shown across multiple visits

**Manual Refresh**: Users can manually sync metadata via the sync button, which:
- Deletes the cached data
- Triggers fresh API calls
- Recalculates all runtime values
- Updates the cache with new data

**Database Storage**: Runtime data is NOT stored in the database models. The `Item`, `Season`, and `Episode` models only store:
- Basic metadata (title, image, season/episode numbers)
- User progress and status
- Watch history and dates

All runtime calculations remain in the cache layer for performance optimization.

### Cache-Based Time-Left Sort Optimization

**Problem Identified**: The original time-left sort implementation was making individual API calls for each TV show in the media list, causing:
- **Rate Limiting**: TMDB API rate limits exceeded with large lists
- **Slow Performance**: Each show required a separate API request
- **Inconsistent Results**: Some shows failed to load due to API limits

**Solution Implemented**: Leverage cached runtime data instead of making API calls

**Optimization Strategy**:
1. **Primary**: Check season cache first (`tmdb_season_{media_id}_1`)
2. **Fallback**: Try other common seasons (2, 3, 4, 5) if season 1 not cached
3. **Secondary**: Use TV show cache (`tmdb_tv_{media_id}`) for runtime data
4. **Final**: Industry standard episode lengths as emergency fallback

**Cache Key Pattern**:
```python
# Season cache keys (most accurate)
season_cache_key = f"tmdb_season_{media_id}_1"  # Season 1
season_cache_key = f"tmdb_season_{media_id}_2"  # Season 2
# etc.

# TV show cache key (fallback)
tv_cache_key = f"tmdb_tv_{media_id}"
```

**Performance Benefits**:
- **Zero API Calls**: Uses only cached data for runtime calculations
- **Instant Sorting**: No network delays or rate limiting
- **Consistent Results**: Same runtime values across multiple page loads
- **Scalable**: Works efficiently with lists of any size

**Implementation Details**:
```python
# Check cached season runtime data first (most efficient)
season_cache_key = f"tmdb_season_{media.item.media_id}_1"
cached_season_data = cache.get(season_cache_key)

if cached_season_data and cached_season_data.get("details", {}).get("runtime"):
    # Use cached runtime data - no API call needed!
    cached_runtime = cached_season_data["details"]["runtime"]
    runtime_minutes = parse_cached_runtime(cached_runtime)
    total_time_left = media.episodes_left * runtime_minutes
    media.time_left = format_time_left(total_time_left)
```

**Fallback Chain**:
1. **Season 1 Cache** → Most accurate episode-level runtime
2. **Other Seasons Cache** → Alternative season data
3. **TV Show Cache** → Show-level runtime estimate
4. **Industry Standards** → TMDB: 30min, MAL: 23min

**Result**: Time-left sort now works efficiently without API rate limiting, providing fast and accurate sorting based on cached runtime data.