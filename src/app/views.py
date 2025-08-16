import logging

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import prefetch_related_objects
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import helpers, history_processor
from app import statistics as stats
from app.forms import EpisodeForm, ManualItemForm, get_form_class
from app.models import TV, BasicMedia, Item, MediaTypes, Season, Sources, Status
from app.providers import manual, services, tmdb
from app.templatetags import app_tags
from users.models import HomeSortChoices, MediaSortChoices, MediaStatusChoices

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress."""
    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    media_type_to_load = request.GET.get("load_media_type")
    items_limit = 14

    list_by_type = BasicMedia.objects.get_in_progress(
        request.user,
        sort_by,
        items_limit,
        media_type_to_load,
    )

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        context = {
            "media_list": list_by_type.get(media_type_to_load, []),
        }
        return render(request, "app/components/home_grid.html", context)

    context = {
        "list_by_type": list_by_type,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
        "items_limit": items_limit,
    }
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST["operation"]

    media = BasicMedia.objects.get_media_prefetch(
        request.user,
        media_type,
        instance_id,
    )

    if operation == "increase":
        media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

    if media_type == MediaTypes.SEASON.value:
        # clear prefetch cache to get the updated episodes
        media.refresh_from_db()
        prefetch_related_objects([media], "episodes")

    context = {
        "media": media,
    }
    return render(
        request,
        "app/components/progress_changer.html",
        context,
    )


@require_GET
def media_list(request, media_type):
    """Return the media list page."""
    layout = request.user.update_preference(
        f"{media_type}_layout",
        request.GET.get("layout"),
    )
    sort_filter = request.user.update_preference(
        f"{media_type}_sort",
        request.GET.get("sort"),
    )
    status_filter = request.user.update_preference(
        f"{media_type}_status",
        request.GET.get("status"),
    )
    search_query = request.GET.get("search", "")
    page = request.GET.get("page", 1)

    # Prepare status filter for database query
    if not status_filter:
        status_filter = MediaStatusChoices.ALL

    # Get media list with filters applied
    media_queryset = BasicMedia.objects.get_media_list(
        user=request.user,
        media_type=media_type,
        status_filter=status_filter,
        sort_filter=sort_filter,
        search=search_query,
    )

    # Apply custom sorting for time_left BEFORE pagination
    print(f"DEBUG: sort_filter = '{sort_filter}', media_type = '{media_type}'")
    if sort_filter == "time_left" and media_type == MediaTypes.TV.value:
        # Convert queryset to list for custom sorting
        media_list = list(media_queryset)
        
        # Annotate max_progress for the entire list
        BasicMedia.objects.annotate_max_progress(
            media_list,
            media_type,
        )
        
        # Calculate episodes_left and time_left fields for the entire list
        for media in media_list:
            if hasattr(media, 'max_progress') and media.max_progress > 0:
                media.episodes_left = media.max_progress - media.progress
                
                # Calculate actual time_left based on episode runtime
                try:
                    from app.providers import services
                    
                    # Get the first season to access episode runtime data
                    tv_metadata = services.get_media_metadata(
                        MediaTypes.TV.value,
                        media.item.media_id,
                        media.item.source
                    )
                    
                    # Debug: Print what we got from the TV API
                    print(f"DEBUG: {media.item.title} - TV Metadata keys: {list(tv_metadata.keys()) if tv_metadata else 'None'}")
                    
                    # Try to get season data to access episode runtime
                    seasons = tv_metadata.get("related", {}).get("seasons", [])
                    if seasons:
                        # Skip season 0 (often contains specials/OVAs with different runtime characteristics)
                        # Start with season 1, fall back to season 0 only if no other seasons exist
                        first_season = None
                        for season in seasons:
                            season_num = season.get("season_number", 0)
                            if season_num > 0:  # Prefer season 1 or higher
                                first_season = season_num
                                break
                        
                        # If no season > 0 found, use the first available season
                        if first_season is None and seasons:
                            first_season = seasons[0].get("season_number", 1)
                        
                        print(f"DEBUG: {media.item.title} - Fetching season {first_season} for runtime data")
                        
                        # Fetch season metadata
                        season_metadata = services.get_media_metadata(
                            MediaTypes.SEASON.value,
                            media.item.media_id,
                            media.item.source,
                            [first_season]
                        )
                        
                        print(f"DEBUG: {media.item.title} - Season metadata keys: {list(season_metadata.keys()) if season_metadata else 'None'}")
                        
                        # Get episode runtime data from season
                        episodes = season_metadata.get("episodes", [])
                        if episodes:
                            # Calculate average episode runtime
                            runtimes = []
                            for episode in episodes:
                                if episode.get("runtime"):
                                    runtimes.append(episode["runtime"])
                            
                            if runtimes:
                                avg_runtime = sum(runtimes) / len(runtimes)
                                print(f"DEBUG: {media.item.title} - Average episode runtime: {avg_runtime:.1f} minutes")
                                
                                # Validate that the runtime makes sense
                                # TV episodes should typically be between 15-90 minutes
                                # If runtime is unrealistic, fall back to other methods
                                if avg_runtime < 15 or avg_runtime > 90:
                                    print(f"DEBUG: {media.item.title} - Unrealistic episode runtime ({avg_runtime:.1f}min), falling back to TV show runtime")
                                    # Try to use TV show runtime from tv_metadata
                                    tv_runtime = tv_metadata.get("details", {}).get("runtime")
                                    if tv_runtime:
                                        # Parse formatted runtime like "45m" or "1h 30m"
                                        try:
                                            runtime_minutes = 0
                                            if 'h' in tv_runtime:
                                                hours_part = tv_runtime.split('h')[0]
                                                runtime_minutes += int(hours_part) * 60
                                            
                                            if 'm' in tv_runtime:
                                                minutes_part = tv_runtime.split('m')[0]
                                                if 'h' in tv_runtime:
                                                    # Extract minutes after hours (e.g., "1h 30m" -> " 30")
                                                    minutes_part = minutes_part.split('h')[-1].strip()
                                                runtime_minutes += int(minutes_part)
                                            
                                            if runtime_minutes > 0:
                                                print(f"DEBUG: {media.item.title} - Using TV show runtime: {tv_runtime} ({runtime_minutes} minutes)")
                                                total_time_left = media.episodes_left * runtime_minutes
                                                hours = int(total_time_left // 60)
                                                minutes = int(total_time_left % 60)
                                                if hours > 0:
                                                    media.time_left = f"{hours}h {minutes}m"
                                                else:
                                                    media.time_left = f"{minutes}m"
                                            else:
                                                raise ValueError("Invalid runtime format")
                                        except (ValueError, IndexError):
                                            # If parsing fails, use industry standard
                                            if media.item.source == "tmdb":
                                                standard_runtime = 30
                                            elif media.item.source == "mal":
                                                standard_runtime = 23
                                            else:
                                                standard_runtime = 30
                                            
                                            print(f"DEBUG: {media.item.title} - Failed to parse TV runtime, using standard {standard_runtime}min episodes")
                                            total_time_left = media.episodes_left * standard_runtime
                                            hours = int(total_time_left // 60)
                                            minutes = int(total_time_left % 60)
                                            if hours > 0:
                                                media.time_left = f"{hours}h {minutes}m"
                                            else:
                                                media.time_left = f"{minutes}m"
                                    else:
                                        # Use industry standard episode length
                                        if media.item.source == "tmdb":
                                            standard_runtime = 30
                                        elif media.item.source == "mal":
                                            standard_runtime = 23
                                        else:
                                            standard_runtime = 30
                                        
                                        print(f"DEBUG: {media.item.title} - No TV runtime available, using standard {standard_runtime}min episodes")
                                        total_time_left = media.episodes_left * standard_runtime
                                        hours = int(total_time_left // 60)
                                        minutes = int(total_time_left % 60)
                                        if hours > 0:
                                            media.time_left = f"{hours}h {minutes}m"
                                        else:
                                            media.time_left = f"{minutes}m"
                                else:
                                    # Runtime is realistic, use it for calculation
                                    total_time_left = media.episodes_left * avg_runtime
                                    
                                    # Convert minutes to hours and minutes with clean formatting
                                    hours = int(total_time_left // 60)
                                    minutes = int(total_time_left % 60)
                                    if hours > 0:
                                        media.time_left = f"{hours}h {minutes}m"
                                    else:
                                        media.time_left = f"{minutes}m"
                                    print(f"DEBUG: {media.item.title} - Calculated time_left: {media.time_left} (avg runtime: {avg_runtime:.1f}min × {media.episodes_left} episodes)")
                            else:
                                # No episode runtimes available, fall back to TV show runtime
                                tv_runtime = tv_metadata.get("details", {}).get("runtime")
                                if tv_runtime:
                                    # Parse formatted runtime like "45m" or "1h 30m"
                                    try:
                                        runtime_minutes = 0
                                        if 'h' in tv_runtime:
                                            hours_part = tv_runtime.split('h')[0]
                                            runtime_minutes += int(hours_part) * 60
                                        
                                        if 'm' in tv_runtime:
                                            minutes_part = tv_runtime.split('m')[0]
                                            if 'h' in tv_runtime:
                                                # Extract minutes after hours (e.g., "1h 30m" -> " 30")
                                                minutes_part = minutes_part.split('h')[-1].strip()
                                            runtime_minutes += int(minutes_part)
                                        
                                        if runtime_minutes > 0:
                                            print(f"DEBUG: {media.item.title} - No episode runtimes, using TV show runtime: {tv_runtime} ({runtime_minutes} minutes)")
                                            total_time_left = media.episodes_left * runtime_minutes
                                            hours = int(total_time_left // 60)
                                            minutes = int(total_time_left % 60)
                                            if hours > 0:
                                                media.time_left = f"{hours}h {minutes}m"
                                            else:
                                                media.time_left = f"{minutes}m"
                                        else:
                                            raise ValueError("Invalid runtime format")
                                    except (ValueError, IndexError):
                                        # If parsing fails, fall back to standard runtime
                                        print(f"DEBUG: {media.item.title} - Failed to parse TV runtime '{tv_runtime}', using standard runtime")
                                        if media.item.source == "tmdb":
                                            standard_runtime = 30
                                        elif media.item.source == "mal":
                                            standard_runtime = 23
                                        else:
                                            standard_runtime = 30
                                        
                                        total_time_left = media.episodes_left * standard_runtime
                                        hours = int(total_time_left // 60)
                                        minutes = int(total_time_left % 60)
                                        if hours > 0:
                                            media.time_left = f"{hours}h {minutes}m"
                                        else:
                                            media.time_left = f"{minutes}m"
                                else:
                                    # Fallback 2: Use industry standard episode length based on source
                                    if media.item.source == "tmdb":
                                        # TMDB shows are typically 22-45 minutes
                                        standard_runtime = 30  # 30 minutes as default
                                    elif media.item.source == "mal":
                                        # Anime episodes are typically 22-24 minutes
                                        standard_runtime = 23
                                    else:
                                        standard_runtime = 30
                                    
                                    print(f"DEBUG: {media.item.title} - No runtime data available, using standard {standard_runtime}min episodes")
                                    total_time_left = media.episodes_left * standard_runtime
                                    hours = int(total_time_left // 60)
                                    minutes = int(total_time_left % 60)
                                    if hours > 0:
                                        media.time_left = f"{hours}h {minutes}m"
                                    else:
                                        media.time_left = f"{minutes}m"
                        else:
                            # No episodes in season, fall back to TV show runtime
                            tv_runtime = tv_metadata.get("details", {}).get("runtime")
                            if tv_runtime:
                                # Parse formatted runtime like "45m" or "1h 30m"
                                try:
                                    runtime_minutes = 0
                                    if 'h' in tv_runtime:
                                        hours_part = tv_runtime.split('h')[0]
                                        runtime_minutes += int(hours_part) * 60
                                    
                                    if 'm' in tv_runtime:
                                        minutes_part = tv_runtime.split('m')[0]
                                        if 'h' in tv_runtime:
                                            # Extract minutes after hours (e.g., "1h 30m" -> " 30")
                                            minutes_part = minutes_part.split('h')[-1].strip()
                                        runtime_minutes += int(minutes_part)
                                    
                                    if runtime_minutes > 0:
                                        print(f"DEBUG: {media.item.title} - No episodes in season, using TV show runtime: {tv_runtime} ({runtime_minutes} minutes)")
                                        total_time_left = media.episodes_left * runtime_minutes
                                        hours = int(total_time_left // 60)
                                        minutes = int(total_time_left % 60)
                                        if hours > 0:
                                            media.time_left = f"{hours}h {minutes}m"
                                        else:
                                            media.time_left = f"{minutes}m"
                                    else:
                                        raise ValueError("Invalid runtime format")
                                except (ValueError, IndexError):
                                    # If parsing fails, fall back to standard runtime
                                    print(f"DEBUG: {media.item.title} - Failed to parse TV runtime '{tv_runtime}', using standard runtime")
                                    if media.item.source == "tmdb":
                                        standard_runtime = 30
                                    elif media.item.source == "mal":
                                        standard_runtime = 23
                                    else:
                                        standard_runtime = 30
                                    
                                    total_time_left = media.episodes_left * standard_runtime
                                    hours = int(total_time_left // 60)
                                    minutes = int(total_time_left % 60)
                                    if hours > 0:
                                        media.time_left = f"{hours}h {minutes}m"
                                    else:
                                        media.time_left = f"{minutes}m"
                            else:
                                # Fallback 2: Use industry standard episode length
                                if media.item.source == "tmdb":
                                    standard_runtime = 30
                                elif media.item.source == "mal":
                                    standard_runtime = 23
                                else:
                                    standard_runtime = 30
                                
                                print(f"DEBUG: {media.item.title} - No season data, using standard {standard_runtime}min episodes")
                                total_time_left = media.episodes_left * standard_runtime
                                hours = int(total_time_left // 60)
                                minutes = int(total_time_left % 60)
                                if hours > 0:
                                    media.time_left = f"{hours}h {minutes}m"
                                else:
                                    media.time_left = f"{minutes}m"
                    else:
                        # No seasons found, fall back to TV show runtime
                        tv_runtime = tv_metadata.get("details", {}).get("runtime")
                        if tv_runtime:
                            # Parse formatted runtime like "45m" or "1h 30m"
                            try:
                                runtime_minutes = 0
                                if 'h' in tv_runtime:
                                    hours_part = tv_runtime.split('h')[0]
                                    runtime_minutes += int(hours_part) * 60
                                
                                if 'm' in tv_runtime:
                                    minutes_part = tv_runtime.split('m')[0]
                                    if 'h' in tv_runtime:
                                        # Extract minutes after hours (e.g., "1h 30m" -> " 30")
                                        minutes_part = minutes_part.split('h')[-1].strip()
                                    runtime_minutes += int(minutes_part)
                                
                                if runtime_minutes > 0:
                                    print(f"DEBUG: {media.item.title} - No seasons found, using TV show runtime: {tv_runtime} ({runtime_minutes} minutes)")
                                    total_time_left = media.episodes_left * runtime_minutes
                                    hours = int(total_time_left // 60)
                                    minutes = int(total_time_left % 60)
                                    if hours > 0:
                                        media.time_left = f"{hours}h {minutes}m"
                                    else:
                                        media.time_left = f"{minutes}m"
                                else:
                                    raise ValueError("Invalid runtime format")
                            except (ValueError, IndexError):
                                # If parsing fails, fall back to standard runtime
                                print(f"DEBUG: {media.item.title} - Failed to parse TV runtime '{tv_runtime}', using standard runtime")
                                if media.item.source == "tmdb":
                                    standard_runtime = 30
                                elif media.item.source == "mal":
                                    standard_runtime = 23
                                else:
                                    standard_runtime = 30
                                
                                total_time_left = media.episodes_left * standard_runtime
                                hours = int(total_time_left // 60)
                                minutes = int(total_time_left % 60)
                                if hours > 0:
                                    media.time_left = f"{hours}h {minutes}m"
                                else:
                                    media.time_left = f"{minutes}m"
                        else:
                            # Fallback 2: Use industry standard episode length
                            if media.item.source == "tmdb":
                                standard_runtime = 30
                            elif media.item.source == "mal":
                                standard_runtime = 23
                            else:
                                standard_runtime = 30
                            
                            print(f"DEBUG: {media.item.title} - No metadata available, using standard {standard_runtime}min episodes")
                            total_time_left = media.episodes_left * standard_runtime
                            hours = int(total_time_left // 60)
                            minutes = int(total_time_left % 60)
                            if hours > 0:
                                media.time_left = f"{hours}h {minutes}m"
                            else:
                                media.time_left = f"{minutes}m"
                        
                except Exception as e:
                    # If metadata retrieval fails, use industry standard episode length as fallback
                    print(f"ERROR calculating time_left for {media.item.title}: {e}")
                    
                    # Last resort: use industry standard episode length
                    if media.item.source == "tmdb":
                        standard_runtime = 30
                    elif media.item.source == "mal":
                        standard_runtime = 23
                    else:
                        standard_runtime = 30
                    
                    print(f"DEBUG: {media.item.title} - Using emergency fallback: {standard_runtime}min episodes")
                    total_time_left = media.episodes_left * standard_runtime
                    hours = int(total_time_left // 60)
                    minutes = int(total_time_left % 60)
                    if hours > 0:
                        media.time_left = f"{hours}h {minutes}m"
                    else:
                        media.time_left = f"{minutes}m"
            else:
                media.episodes_left = 0
                media.time_left = "0 ep"
        
        # Define sorting function
        def time_left_sort_key(media):
            if not hasattr(media, 'max_progress') or media.max_progress == 0:
                return float('inf')  # Put shows with no max_progress at the end
            
            # Calculate episodes left
            episodes_left = media.max_progress - media.progress
            
            # If 100% complete (0 episodes left), put at the very end
            if episodes_left <= 0:
                return float('inf')  # This will sort to the very end
            
            # Check if show is dropped
            is_dropped = media.status == "Dropped"
            
            # Try to use actual time_left for sorting if available
            if hasattr(media, 'time_left') and media.time_left and not media.time_left.endswith(' ep'):
                # Parse time format like "2h 30m" or "45m"
                try:
                    time_str = media.time_left
                    total_minutes = 0
                    
                    if 'h' in time_str:
                        hours_part = time_str.split('h')[0]
                        total_minutes += int(hours_part) * 60
                    
                    if 'm' in time_str:
                        minutes_part = time_str.split('m')[0]
                        if 'h' in time_str:
                            # Extract minutes after hours (e.g., "2h 30m" -> " 30")
                            minutes_part = minutes_part.split('h')[-1].strip()
                        total_minutes += int(minutes_part)
                    
                    # Add a large offset for dropped shows so they appear after active shows
                    # but before 100% completed shows
                    if is_dropped:
                        total_minutes += 1000000  # 1 million minutes = ~694 days
                    
                    return total_minutes
                except (ValueError, IndexError):
                    # If parsing fails, fall back to episodes left
                    if is_dropped:
                        return episodes_left + 1000000  # Add offset for dropped shows
                    return episodes_left
            
            # Fallback: use episodes left for sorting
            if is_dropped:
                return episodes_left + 1000000  # Add offset for dropped shows
            return episodes_left
        
        # Debug: Print sorting information
        print(f"\n=== Time Left Sorting Debug ===")
        print(f"Total TV shows to sort: {len(media_list)}")
        print(f"Sorting by: LEAST time remaining first, then dropped shows, then 100% completed shows last")
        print()
        
        # Calculate sort keys for all media before sorting
        for media in media_list:
            media.sort_key = time_left_sort_key(media)
        
        # Debug: Show status values to understand what we're actually reading
        print(f"Status Field Debug:")
        status_counts = {}
        for media in media_list[:20]:  # Check first 20 shows
            status = media.status
            if status not in status_counts:
                status_counts[status] = 0
            status_counts[status] += 1
            print(f"  {media.item.title[:30]:<30} | Status: '{status}' | Type: {type(status)}")
        
        print(f"Status Counts: {status_counts}")
        print()
        
        # Sort the entire list by time left
        media_list = sorted(media_list, key=lambda m: m.sort_key, reverse=False)
        
        # Debug: Show sorting summary
        active_shows = [m for m in media_list if m.status != "Dropped" and hasattr(m, 'episodes_left') and m.episodes_left > 0]
        dropped_shows = [m for m in media_list if m.status == "Dropped" and hasattr(m, 'episodes_left') and m.episodes_left > 0]
        completed_shows = [m for m in media_list if hasattr(m, 'episodes_left') and m.episodes_left <= 0]
        
        print(f"Sorting Summary:")
        print(f"  Active Shows: {len(active_shows)}")
        print(f"  Dropped Shows: {len(dropped_shows)}")
        print(f"  Completed Shows: {len(completed_shows)}")
        print()
        
        # Debug: Print first 10 sorted results
        for i, media in enumerate(media_list[:10]):
            progress_pct = (media.progress / media.max_progress * 100) if media.max_progress > 0 else 0
            episodes_left = media.episodes_left
            time_left = media.time_left
            status = media.status
            status_indicator = "✅ COMPLETED" if episodes_left <= 0 else f"🔄 {status.upper()}"
            print(f"{i+1:2d}. {media.item.title[:30]:<30} | {media.progress:2d}/{media.max_progress:2d} ({progress_pct:5.1f}%) | Episodes Left: {episodes_left:2d} | Time Left: {time_left:>8} | Status: {status:>10} | Sort Key: {media.sort_key:>12} | {status_indicator}")
        
        # Also show some dropped shows and completed shows for verification
        print(f"\n=== Sample Dropped Shows ===")
        dropped_shows = [m for m in media_list if m.status == "Dropped" and hasattr(m, 'episodes_left') and m.episodes_left > 0][:5]
        for i, media in enumerate(dropped_shows):
            progress_pct = (media.progress / media.max_progress * 100) if media.max_progress > 0 else 0
            episodes_left = media.episodes_left
            time_left = media.time_left
            print(f"     {media.item.title[:30]:<30} | {media.progress:2d}/{media.max_progress:2d} ({progress_pct:5.1f}%) | Episodes Left: {episodes_left:2d} | Time Left: {time_left:>8} | Status: {media.status} | Sort Key: {media.sort_key}")
        
        print(f"\n=== Sample Completed Shows ===")
        completed_shows = [m for m in media_list if hasattr(m, 'episodes_left') and m.episodes_left <= 0][:5]
        for i, media in enumerate(completed_shows):
            progress_pct = (media.progress / media.max_progress * 100) if media.max_progress > 0 else 0
            print(f"     {media.item.title[:30]:<30} | {media.progress:2d}/{media.max_progress:2d} ({progress_pct:5.1f}%) | Status: {media.status} | Sort Key: {media.sort_key}")
        
        # Now paginate the sorted list
        items_per_page = 32
        paginator = Paginator(media_list, items_per_page)
        media_page = paginator.get_page(page)
        
        print(f"=== After Pagination ===")
        print(f"Page {page} of {paginator.num_pages}")
        print(f"Items on this page: {len(media_page.object_list)}")
        print("=" * 80)
        
    else:
        # Standard pagination for non-time_left sorts
        items_per_page = 32
        paginator = Paginator(media_queryset, items_per_page)
        media_page = paginator.get_page(page)

        BasicMedia.objects.annotate_max_progress(
            media_page.object_list,
            media_type,
        )

    context = {
        "media_type": media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "current_layout": layout,
        "layout_class": ".media-grid" if layout == "grid" else ".media-table",
        "current_sort": sort_filter,
        "current_status": status_filter,
        "sort_choices": MediaSortChoices.choices,
        "status_choices": MediaStatusChoices.choices,
    }

    # Handle HTMX requests for partial updates
    if request.headers.get("HX-Request"):
        # Check if this is a pagination request (has page parameter)
        is_pagination = request.GET.get("page") and request.GET.get("page") != "1"
        
        # Changing from empty list to a status with items
        if request.headers.get("HX-Target") == "empty_list":
            response = HttpResponse()
            response["HX-Redirect"] = reverse("medialist", args=[media_type])
            return response
        
        if layout == "grid":
            template_name = "app/components/media_grid_items.html"
        else:
            # For table layout: pagination requests get just rows, sort/filter changes get full table
            if is_pagination:
                template_name = "app/components/media_table_items.html"
            else:
                template_name = "app/components/media_table_complete.html"
    else:
        template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_GET
def media_search(request):
    """Return the media search page."""
    media_type = request.user.update_preference(
        "last_search_type",
        request.GET["media_type"],
    )
    query = request.GET["q"]
    page = int(request.GET.get("page", 1))
    layout = request.GET.get("layout", "grid")

    # only receives source when searching with secondary source
    source = request.GET.get("source")

    data = services.search(media_type, query, page, source)

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id, source)
    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    if source == Sources.MANUAL.value:
        season_metadata["episodes"] = manual.process_episodes(
            season_metadata,
            episodes_in_db,
        )
    else:
        season_metadata["episodes"] = tmdb.process_episodes(
            season_metadata,
            episodes_in_db,
        )

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )

    score = float(request.POST.get("score"))
    media.score = score
    media.save()
    logger.info(
        "%s score updated to %s",
        media,
        score,
    )

    return JsonResponse(
        {
            "success": True,
            "score": score,
        },
    )


@require_POST
def sync_metadata(request, source, media_type, media_id, season_number=None):
    """Refresh the metadata for a media item."""
    if source == Sources.MANUAL.value:
        msg = "Manual items cannot be synced."
        messages.error(request, msg)
        return HttpResponse(
            msg,
            status=400,
            headers={"HX-Redirect": request.POST.get("next", "/")},
        )

    cache_key = f"{source}_{media_type}_{media_id}"
    if media_type == MediaTypes.SEASON.value:
        cache_key += f"_{season_number}"

    ttl = cache.ttl(cache_key)
    logger.debug("%s - Cache TTL for: %s", cache_key, ttl)

    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        msg = "The data was recently synced, please wait a few seconds."
        messages.error(request, msg)
        logger.error(msg)
    else:
        deleted = cache.delete(cache_key)
        logger.debug("%s - Old cache deleted: %s", cache_key, deleted)

        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        title = metadata["title"]
        if season_number:
            title += f" - Season {season_number}"

        if media_type == MediaTypes.SEASON.value:
            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )

            # Create a dictionary of existing episodes keyed by episode number
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []
            episode_count = 0

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)
                    episode_count += 1

            logger.info(
                "Found %s existing episodes to update for %s",
                episode_count,
                title,
            )

            if episodes_to_update:
                updated_count = Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )
                logger.info(
                    "Successfully updated %s episodes for %s",
                    updated_count,
                    title,
                )

        item.fetch_releases(delay=False)

        msg = f"{title} was synced to {Sources(source).label} successfully."
        messages.success(request, msg)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=204,
            headers={
                "HX-Redirect": request.POST["next"],
            },
        )
    return helpers.redirect_back(request)


@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    instance_id = request.GET.get("instance_id")
    if instance_id:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    elif request.GET.get("is_create"):
        media = None
    else:
        # no specific instance, try to find the first one
        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
            season_number=season_number,
        )
        media = user_medias.first()
        if media:
            instance_id = media.id

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "instance_id": instance_id,
    }

    if media:
        title = media.item
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        title = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form = get_form_class(media_type)(instance=media, initial=initial_data)

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")

    if instance_id:
        instance = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_json())
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    f"{field.replace('_', ' ').title()}: {error}",
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]

    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    if media:
        media.delete()
        logger.info("%s deleted successfully.", media)
    else:
        logger.warning("The %s was already deleted before.", media_type)

    return helpers.redirect_back(request)


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]

    form = EpisodeForm(request.POST)
    if not form.is_valid():
        logger.error("Form validation failed: %s", form.errors)
        return HttpResponseBadRequest("Invalid form data")

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        tv_with_seasons_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_with_seasons_metadata["title"],
                "image": season_metadata["image"],
            },
        )
        related_season = Season.objects.create(
            item=item,
            user=request.user,
            score=None,
            status=Status.IN_PROGRESS.value,
            notes="",
        )

        logger.info("%s did not exist, it was created successfully.", related_season)

    related_season.watch(episode_number, form.cleaned_data["end_date"])

    return helpers.redirect_back(request)


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    if request.method == "GET":
        media_types = MediaTypes.values
        return render(request, "app/create_entry.html", {"media_types": media_types})

    # Process the form submission
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect("create_entry")

    # Try to save the item
    try:
        item = form.save()
    except IntegrityError:
        # Handle duplicate item
        media_name = form.cleaned_data["title"]
        if form.cleaned_data.get("season_number"):
            media_name += f" - Season {form.cleaned_data['season_number']}"
        if form.cleaned_data.get("episode_number"):
            media_name += f" - Episode {form.cleaned_data['episode_number']}"

        logger.exception("%s already exists in the database.", media_name)
        messages.error(request, f"{media_name} already exists in the database.")
        return redirect("create_entry")

    # Prepare and validate the media form
    updated_request = request.POST.copy()
    updated_request.update({"source": item.source, "media_id": item.media_id})
    media_form = get_form_class(item.media_type)(updated_request)

    if not media_form.is_valid():
        # Handle media form validation errors
        logger.error(media_form.errors.as_json())
        helpers.form_error_messages(media_form, request)

        # Delete the item since the media creation failed
        item.delete()
        logger.info("%s was deleted due to media form validation failure", item)
        return redirect("create_entry")

    # Save the media instance
    media_form.instance.user = request.user
    media_form.instance.item = item

    # Handle relationships based on media type
    if item.media_type == MediaTypes.SEASON.value:
        media_form.instance.related_tv = form.cleaned_data["parent_tv"]
    elif item.media_type == MediaTypes.EPISODE.value:
        media_form.instance.related_season = form.cleaned_data["parent_season"]

    media_form.save()

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect("create_entry")


@require_GET
def search_parent_tv(request):
    """Return the search results for parent TV shows."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for TV shows with query: %s",
        request.user.username,
        query,
    )

    parent_tvs = TV.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.TV.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_tv.html",
        {"results": parent_tvs, "query": query},
    )


@require_GET
def search_parent_season(request):
    """Return the search results for parent seasons."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for seasons with query: %s",
        request.user.username,
        query,
    )

    parent_seasons = Season.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.SEASON.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_season.html",
        {"results": parent_seasons, "query": query},
    )


@require_GET
def history_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the history page for a media item."""
    user_medias = BasicMedia.objects.filter_media(
        request.user,
        media_id,
        media_type,
        source,
        season_number=season_number,
        episode_number=episode_number,
    )

    total_medias = user_medias.count()
    timeline_entries = []
    for index, media in enumerate(user_medias, start=1):
        if history := media.history.all():
            media_entry_number = total_medias - index + 1
            timeline_entries.extend(
                history_processor.process_history_entries(
                    history,
                    media_type,
                    media_entry_number,
                ),
            )
    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "timeline": timeline_entries,
            "total_medias": total_medias,
            "return_url": request.GET["return_url"],
        },
    )


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    try:
        historical_model = apps.get_model(
            app_label="app",
            model_name=f"historical{media_type.lower()}",
        )

        historical_model.objects.get(
            history_id=history_id,
            history_user=request.user,
        ).delete()

        logger.info(
            "Deleted history record %s",
            str(history_id),
        )

        # Return empty 200 response - the element will be removed by HTMX
        return HttpResponse()

    except historical_model.DoesNotExist:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)


@require_GET
def statistics(request):
    """Return the statistics page."""
    # Set default date range to last year
    timeformat = "%Y-%m-%d"
    today = timezone.localdate()
    one_year_ago = today.replace(year=today.year - 1)

    # Get date parameters with defaults
    start_date_str = request.GET.get("start-date") or one_year_ago.strftime(timeformat)
    end_date_str = request.GET.get("end-date") or today.strftime(timeformat)

    if start_date_str == "all" and end_date_str == "all":
        start_date = None
        end_date = None
    else:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if start_date and end_date:
            # Convert to datetime with timezone awareness
            start_date = timezone.make_aware(
                datetime.combine(start_date, datetime.min.time()),
            )

            # End date should be end of day
            end_date = timezone.make_aware(
                datetime.combine(end_date, datetime.max.time()),
            )

    # Get all user media data in a single operation
    user_media, media_count = stats.get_user_media(
        request.user,
        start_date,
        end_date,
    )

    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
    )
    score_distribution, top_rated = stats.get_score_distribution(user_media)
    status_distribution = stats.get_status_distribution(user_media)
    status_pie_chart_data = stats.get_status_pie_chart_data(
        status_distribution,
    )
    timeline = stats.get_timeline(user_media)

    activity_data = stats.get_activity_data(request.user, start_date, end_date)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "top_rated": top_rated,
        "status_distribution": status_distribution,
        "status_pie_chart_data": status_pie_chart_data,
        "timeline": timeline,
    }

    return render(request, "app/statistics.html", context)
