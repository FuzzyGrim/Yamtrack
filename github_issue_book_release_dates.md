# Incorrect Book Release Dates Displaying as January 1st

## Problem Description

Book release dates from the Hardcover API are displaying incorrectly, showing dates like `1965-01-01`, `1969-01-01`, etc. for all books, even when the actual publication dates are different. Additionally, many release dates show incorrect years.

## Root Cause

1. **Using book-level `release_date` instead of edition-level date**: The code was using `books_by_pk.release_date`, which appears to be unreliable (possibly representing when the book was added to the system or a generic date).

2. **Year-only dates stored as full dates**: When Hardcover only has a year (e.g., "1965"), it's being stored as "1965-01-01", which is misleading to users.

3. **Missing edition date in GraphQL query**: The `default_cover_edition` query was not requesting the `release_date` field, which contains the accurate publication date for that specific edition.

## Expected Behavior

- Release dates should display the actual publication date of the book/edition
- If only a year is available, display just the year (e.g., "1965") instead of "1965-01-01"
- If a full date is available, display it in YYYY-MM-DD format

## Actual Behavior

- All books show release dates ending in "-01-01" (e.g., "1965-01-01", "1969-01-01")
- Many release dates show incorrect years
- Dates appear generic rather than edition-specific

## Solution

### 1. Query edition-level release date

Add `release_date` to the `default_cover_edition` GraphQL query:

```graphql
default_cover_edition {
  edition_format
  isbn_13
  isbn_10
  release_date  # Add this field
  publisher {
    name
  }
}
```

### 2. Prefer edition date over book date

Use the edition's `release_date` if available, otherwise fall back to the book's `release_date`:

```python
# Prefer release_date from default_cover_edition if available, otherwise use book's release_date
default_edition = book_data.get("default_cover_edition")
edition_release_date = default_edition.get("release_date") if default_edition else None
book_release_date = book_data.get("release_date")

# Use edition date if available, otherwise fall back to book date
raw_release_date = edition_release_date or book_release_date
release_date = format_release_date(raw_release_date)
```

### 3. Format dates intelligently

Add a function to detect and format year-only dates:

```python
def format_release_date(release_date):
    """Format release date from Hardcover API.
    
    Hardcover may return:
    - Full date string (YYYY-MM-DD)
    - Just a year (YYYY)
    - None
    
    If it's just a year, return just the year.
    If it's a full date, return it as-is.
    """
    if not release_date:
        return None
    
    # If it's already a string, check the format
    if isinstance(release_date, str):
        # Check if it's just a year (4 digits)
        if len(release_date) == 4 and release_date.isdigit():
            return release_date
        # Check if it's a date with January 1st (likely just a year stored as date)
        if release_date.endswith("-01-01"):
            year = release_date[:4]
            if year.isdigit():
                logger.debug("Release date %s appears to be just a year, returning %s", release_date, year)
                return year
        # Return the date as-is if it's a proper date
        return release_date
    
    # If it's a date object, format it
    if hasattr(release_date, 'year'):
        # If it's January 1st, it's likely just a year
        if release_date.month == 1 and release_date.day == 1:
            return str(release_date.year)
        return release_date.strftime("%Y-%m-%d")
    
    return str(release_date)
```

## Files Changed

- `src/app/providers/hardcover.py`
  - Added `release_date` to `default_cover_edition` GraphQL query
  - Updated date selection logic to prefer edition date
  - Added `format_release_date()` function

## Testing

After applying the fix:
1. Load several book detail pages from Hardcover
2. Verify release dates display correctly (not all showing "-01-01")
3. Verify years are accurate
4. Verify year-only dates display as just the year (e.g., "1965" not "1965-01-01")

## Additional Notes

- The edition-level `release_date` is more accurate because it represents the actual publication date of that specific edition
- The book-level `release_date` may represent when the book was added to Hardcover's system or a generic date
- This fix maintains backward compatibility by falling back to book-level date if edition date is unavailable

