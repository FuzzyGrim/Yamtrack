# Environment Variables

This page outlines the environment variables used in the YamTrack project.

## Media Sources

| Name            | Notes                                                                                                                                                                                                                                                 |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TMDB_API`      | The Movie Database API key for movies and TV shows. A default key is provided.                                                                                                                                                                        |
| `TMDB_NSFW`     | Default to `False`. Set to `True` to include adult content in TV and movie searches.                                                                                                                                                                  |
| `TMDB_LANG`     | TMDB metadata language. Uses a language code in ISO 639-1 (e.g., `en`). Also supports a country code in ISO 3166-1 (e.g., `en-US`). Metadata is cached for a few hours in Redis. You may need to clear the cache to see the new language immediately. |
| `MAL_API`       | MyAnimeList API key for anime and manga. A default key is provided.                                                                                                                                                                                   |
| `MAL_NSFW`      | Default to `False`. Set to `True` to include adult content in anime and manga searches from MyAnimeList.                                                                                                                                              |
| `MU_NSFW`       | Default to `False`. Set to `True` to include adult content in manga searches from MangaUpdates.                                                                                                                                                       |
| `IGDB_ID`       | IGDB API key for games. A default key is provided, but it's recommended to get your own as it has a low rate limit.                                                                                                                                   |
| `IGDB_SECRET`   | IGDB API secret for games. A default value is provided, but it's recommended to get your own as it has a low rate limit.                                                                                                                              |
| `IGDB_NSFW`     | Default to `False`. Set to `True` to include adult content in game searches.                                                                                                                                                                          |
| `HARDCOVER_API` | Hardcover API key for books. A default key is provided, but it's recommended to get your own as it has a low rate limit.                                                                                                                              |
| `COMICVINE_API` | ComicVine API key for comics. A default key is provided, but it's recommended to get your own as it has a low rate limit.                                                                                                                             |

## Media Import

See [media-imports](media-imports.md).

## Redis and Django Settings

| Name               | Notes                                                                                                                                                                                                |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `REDIS_URL`        | Default to `redis://localhost:6379`. Set this to your Redis server URL, in the format of `redis://{service}:{port}`. If using a custom network you may need to use `redis://{container_nam}:{port}`. |
| `CELERY_REDIS_URL` | Default to the value of `REDIS_URL`. Set this to your Redis server URL for Celery if you need a different value than `REDIS_URL`.                                                                    |
| `REDIS_PREFIX`     | Optional prefix for Redis keys and channels to enable isolation when sharing a Redis instance across multiple applications. Useful for ACL-based permission control.                                 |
| `SECRET`           | [Secret key](https://docs.djangoproject.com/en/stable/ref/settings/#secret-key) used for cryptographic signing. Should be a random string.                                                           |
| `URLS`             | Shortcut to set both the `CSRF` and `ALLOWED_HOSTS` settings. Comma-separated list of URLs (e.g., `https://yamtrack.mydomain.com`).                                                                  |
| `ALLOWED_HOSTS`    | Comma-separated list of host/domain names that this Django site can serve (e.g., `yamtrack.mydomain.com`). Default to `*` for all hosts.                                                             |
| `CSRF`             | Comma-separated list of trusted origins for `POST` requests when using reverse proxies (e.g., `https://yamtrack.mydomain.com`).                                                                      |
| `REGISTRATION`     | Default to `True`. Set to `False` to disable user registration.                                                                                                                                      |
| `DEBUG`            | Default to `False`. Set to `True` for debugging.                                                                                                                                                     |
| `ADMIN_ENABLED`    | Default to `False`. Set to `True` to enable the Django admin interface.                                                                                                                              |
| `TRACK_TIME`       | Default to `True`. Set to `False` to disable time tracking in Yamtrack.                                                                                                                              |

## User and System Configuration

| Name                            | Notes                                                                                                                                                                 |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PUID`                          | User ID for the app. Default to `1000`.                                                                                                                               |
| `PGID`                          | Group ID for the app. Default to `1000`.                                                                                                                              |
| `TZ`                            | Timezone (e.g., `Europe/Berlin`). Default to `UTC`.                                                                                                                   |
| `WEB_CONCURRENCY`               | Number of web server processes. Default to `1`.                                                                                                                       |
| `SOCIAL_PROVIDERS`              | Comma-separated list of social authentication providers to enable (e.g., `allauth.socialaccount.providers.openid_connect,allauth.socialaccount.providers.github`).    |
| `SOCIALACCOUNT_PROVIDERS`       | JSON configuration for social providers. See the [Docs](social-auth.md) for an OIDC configuration example.                                                            |
| `ACCOUNT_DEFAULT_HTTP_PROTOCOL` | Protocol for social providers. If your `redirect_uri` in OIDC config is `https`, set this to `https`. Default is determined based on your `CSRF` settings.            |
| `ACCOUNT_LOGOUT_REDIRECT_URL`   | Absolute URL to redirect users after logout. Useful for OpenID Connect providers to ensure complete logout from the external authentication provider.                 |
| `SOCIALACCOUNT_ONLY`            | Default to `False`. Set to `True` to disable local authentication when using social authentication only.                                                              |
| `REDIRECT_LOGIN_TO_SSO`         | Default to `False`. Set to `True` to automatically redirect (using JavaScript) to the SSO provider when there's only one available. Useful for single sign-on setups. |

## Celery Health Check

| Name                              | Notes                                                                                                           |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `HEALTHCHECK_CELERY_PING_TIMEOUT` | Default to `1`. Increases the timeout for the health check ping to Celery. This is useful for slow connections. |

## PostgreSQL Environment Variables (YamTrack Container)

| Name          | Notes                                                                                                    |
| ------------- | -------------------------------------------------------------------------------------------------------- |
| `DB_HOST`     | The hostname or IP address of the PostgreSQL server. If not set, SQLite is used as the default database. |
| `DB_PORT`     | The port number on which the PostgreSQL server is listening.                                             |
| `DB_NAME`     | The name of the database to connect to.                                                                  |
| `DB_USER`     | The username used to authenticate with the PostgreSQL server.                                            |
| `DB_PASSWORD` | The password for the specified user.                                                                     |

**Note:** Check the example `docker-compose.postgres.yml` in the root directory of the repo for a PostgreSQL configuration example.

### External PostgreSQL database with SSL (YamTrack Container)

| Name               | Notes                                                                                                                                                                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DB_SSL_MODE`      | Determines whether or with what priority a secure SSL TCP/IP connection will be negotiated with the server. See [the official documentation](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNECT-SSLMODE).               |
| `DB_SSL_CERT_MODE` | Determines whether a client certificate may be sent to the server, and whether the server is required to request one. See [the official documentation](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNECT-SSLCERTMODE). |

## Docker Secrets Support

YamTrack supports reading sensitive configuration values from Docker secrets files. The following environment variables can alternatively be provided as secrets:

| Environment Variable      | Secret File Equivalent         |
| ------------------------- | ------------------------------ |
| `SECRET`                  | `SECRET_FILE`                  |
| `DB_NAME`                 | `DB_NAME_FILE`                 |
| `DB_USER`                 | `DB_USER_FILE`                 |
| `DB_PASSWORD`             | `DB_PASSWORD_FILE`             |
| `TMDB_API`                | `TMDB_API_FILE`                |
| `MAL_API`                 | `MAL_API_FILE`                 |
| `IGDB_ID`                 | `IGDB_ID_FILE`                 |
| `IGDB_SECRET`             | `IGDB_SECRET_FILE`             |
| `HARDCOVER_API`           | `HARDCOVER_API_FILE`           |
| `COMICVINE_API`           | `COMICVINE_API_FILE`           |
| `TRAKT_API`               | `TRAKT_API_FILE`               |
| `SIMKL_ID`                | `SIMKL_ID_FILE`                |
| `SIMKL_SECRET`            | `SIMKL_SECRET_FILE`            |
| `SOCIALACCOUNT_PROVIDERS` | `SOCIALACCOUNT_PROVIDERS_FILE` |

## Host under subpath

| Name       | Notes                                                                                                                  |
| ---------- | ---------------------------------------------------------------------------------------------------------------------- |
| `BASE_URL` | To host YamTrack under a subpath like `https://example.com/yamtrack`, set this to `/yamtrack`, without trailing slash. |

## Self-signed certificates

| Name                 | Notes                                                                                                                                                                                                                                                                 |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `REQUESTS_CA_BUNDLE` | Path to a custom CA certificate bundle file for SSL verification. Useful for self-hosted authentication providers with self-signed certificates (e.g., `/etc/ssl/certs/ca-certificates.crt`). This requires the CA certificate to be present in the host's CA bundle. |
