# API

!!! warning
    The API is still in development and may change at any time.

The Yamtrack's API is reachable at `/api/v1/`.

## Integrated Swagger docs

The integrated Swagger documentation is enabled if `DEBUG` is active, or if `SPECTACULAR_ENABLE_SERVE` is set to `True`.

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

## Endpoints

You can check the available endpoints with examples using the integrated Swagger documentation at `/api/v1/docs/`, or at [Endpoints](endpoints.md).

## Debugging

To get detailed error messages from the API, set the `DEBUG` environment variable to `True`.

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
