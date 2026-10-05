# API

!!! warning
    The API is still in development and may change at any time.

The Yamtrack's API is reachable at `/api/v1/`.

## Integrated Swagger docs

The integrated Swagger documentation is enabled if `DEBUG` is active, or if `SPECTACULAR_ENABLE_SERVE` is set to `True`.

When enabled, the schema (`/api/v1/schema/`) and the Swagger documentation (`/api/v1/docs/`) are publicly accessible, without authentication.

Currently the schema version is the version of the API, and it's independent from the Yamtrack version.

## Authentication

Authentication is required for most API endpoints. You can authenticate using the following methods:

### Bearer token authentication

Include a valid token in the `Authorization` header of your requests:

```bash
curl -H "Authorization: Bearer <your_token>" http://localhost:8000/api/v1/your_endpoint/
```

### API key authentication

Include a valid token in the `X-API-Key` header of your requests:

```bash
curl -H "X-API-Key: <your_api_key>" http://localhost:8000/api/v1/your_endpoint/
```

### Getting your token

You can get your token using the web interface. Go to the `Integrations` section of the `Settings` page.

### Authentication errors

Requests with a missing or invalid token return a `401 Unauthorized` response, while `403 Forbidden` is returned when the authenticated user can't access a resource (e.g. another user's list).

## Endpoints

You can check the available endpoints with examples using the integrated Swagger documentation at `/api/v1/docs/`, or at [Endpoints](endpoints.md).

## Data formats

### Media status

The media status is represented as an integer, both in requests and responses, and in the `status` query parameter:

| Value | Status      |
| ----- | ----------- |
| `0`   | Planning    |
| `1`   | In progress |
| `2`   | Paused      |
| `3`   | Completed   |
| `4`   | Dropped     |

### Item ID

The `item_id` field identifies a media item across providers, in the same form used by the media endpoints paths:

- `<media_type>/<source>/<media_id>` for most media types (e.g. `movie/tmdb/603`)
- `tv/<source>/<media_id>/<season_number>` for seasons (e.g. `tv/tmdb/1396/1`)
- `tv/<source>/<media_id>/<season_number>/<episode_number>` for episodes (e.g. `tv/tmdb/1396/1/2`)

Seasons and episodes also have a `parent_id` field, with the `item_id` of their show/season.

### Pagination

Endpoints that return many results accept the `limit` (default `20`, maximum `200`) and `offset` (default `0`) query parameters, and return the results in this form:

```json
{
  "pagination": {
    "total": 42,
    "limit": 20,
    "offset": 20,
    "next": "http://localhost:8000/api/v1/media/?limit=20&offset=40",
    "previous": "http://localhost:8000/api/v1/media/?limit=20&offset=0"
  },
  "results": [...]
}
```

### Sorting

Endpoints that return media accept a `sort` query parameter in the form `<field>_asc` or `<field>_desc` (e.g. `?sort=start_date_desc`). The direction suffix is optional and defaults to ascending. An unsupported sort returns a `400 Bad Request` response.

### Excluding media types

The `/api/v1/media/` endpoint accepts an `exclude` query parameter, with a comma separated list of media types to leave out of the results (e.g. `?exclude=movie,game`).

## Errors

Error responses always contain a `detail` message. Validation errors also contain an `errors` object with the messages for each invalid field:

```json
{
  "detail": "Invalid request: name.",
  "errors": {
    "name": ["This field is required."]
  }
}
```

### Debugging

To get detailed error messages from the API, set the `DEBUG` environment variable to `True`: server errors will also include the exception message in the `debug` field.

## Next steps

These are some possible next additions to the API, in no particular order:

- Batch operations (e.g., bulk create, update, delete)
- Webhooks managements
- Field filtering (e.g., `?field=title,score`)
- Rate limiting
- Expand filters
- Expand sorting
- Administration endpoints (e.g., user management, system settings, token management)
- Provider integrations
- Statistics endpoint
