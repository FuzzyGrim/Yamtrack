def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = total_minutes / 60
    minutes = total_minutes % 60
    return f"{int(hours)}:{int(minutes)}"
