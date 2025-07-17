"""Celery tasks for app."""
import logging
from django.utils import timezone
from config.celery import app
from app import statistics

logger = logging.getLogger(__name__)


@app.task
def update_daily_statistics(user_id, date_str=None):
    """
    Update statistics for a specific day.
    
    Args:
        user_id: The user to update statistics for
        date_str: Optional date string. If not provided, uses current date
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    try:
        user = User.objects.get(id=user_id)
        
        # Get the date to process
        if date_str:
            day = timezone.datetime.fromisoformat(date_str)
        else:
            day = timezone.now()
            
        # Set time range for the full day
        start_of_day = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timezone.timedelta(days=1)
        
        # Update statistics
        statistics.get_activity_data(
            user=user,
            start_date=start_of_day,
            end_date=end_of_day,
        )
        
        logger.info("Updated statistics for user %s on %s", user, start_of_day.date())
        
    except User.DoesNotExist:
        logger.error("Could not find user with id %s", user_id)
    except Exception as e:
        logger.error("Error updating statistics for user %s: %s", user_id, e) 