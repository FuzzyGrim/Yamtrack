# Media Imports

Most media import sources can be used directly by entering your username or importing via CSV without any additional configuration. This guide covers only those services that may require special setup for certain scenarios.

!!! note
    The import process matches your data with the IDs from the sources that Yamtrack uses. Each media type uses different sources, so make sure to have them properly configured. See [Media Sources](env-variables.md#media-sources) for more information.

## Trakt

### Public Profile Import

If you have a public Trakt profile, you can import your media by simply entering your public username.

**Optional Configuration:**

| Environment Variable | Description                                                                                                             |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `TRAKT_API`          | Your Trakt Client ID. A default value is provided, but you may want to use your own if you encounter rate limit issues. |

### Private Profile Import (OAuth)

For private Trakt profiles, you'll need to set up OAuth authentication:

1. Create a Trakt application at [Trakt API Apps](https://trakt.tv/oauth/applications)
2. Set the **Redirect URI** to: `https://your_domain.com/import/trakt/private`
3. Configure the following environment variables:

| Environment Variable | Description              |
| -------------------- | ------------------------ |
| `TRAKT_API`          | Your Trakt Client ID     |
| `TRAKT_API_SECRET`   | Your Trakt Client Secret |

---

## Simkl

Create a SIMKL application: <https://simkl.com/settings/developer/new/custom-search/>

### Configuration

| Environment Variable | Description                                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SIMKL_ID`           | Simkl API key for importing media. A default key is provided, but you can obtain your own at [Simkl Developer](https://simkl.com/settings/developer/).       |
| `SIMKL_SECRET`       | Simkl API secret for importing media. A default secret is provided, but you can obtain your own at [Simkl Developer](https://simkl.com/settings/developer/). |

---

## AniList

### Public Profile Import

If you have a public AniList profile, you can import your media by simply entering your username.

### Private Profile Import (OAuth)

For private AniList profiles, you'll need to set up OAuth authentication:

1. Go to <https://anilist.co/settings/developer> and create a new application
2. Set the **Redirect URI** to: `https://your_domain.com/import/anilist/private`
3. Configure the following environment variables:

| Environment Variable | Description                |
| -------------------- | -------------------------- |
| `ANILIST_ID`         | Your AniList Client ID     |
| `ANILIST_SECRET`     | Your AniList Client Secret |

## Steam

### Configuration

Steam import requires a Steam API key and your Steam ID 64.

1. Get your Steam API key at <https://steamcommunity.com/dev/apikey>
2. Configure the following environment variable:

| Environment Variable | Description        |
| -------------------- | ------------------ |
| `STEAM_API_KEY`      | Your Steam API key |

### Import Requirements

- **Steam ID 64**: Instead of your Steam username, you must provide your Steam ID 64. You can find it on your Steam account details page under your username.
- **Profile Visibility**: Your Steam profile must be public if the API key you are using is not linked to the Steam ID you are requesting.

## Yamtrack CSV format

Use this format to bulk-import media into YamTrack. Every row represents a single media instance.

| Column Title   | Required? | Example Value                                  | Notes                                                                                                                                                                           |
| -------------- | --------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| media_id       | No        | 12345                                          | ID of the item on the chosen provider. Must be unique per `(source, media_type)`. Leave blank to let YamTrack fetch the title from the provider automatically with the `title`. |
| source         | **Yes**   | tmdb                                           | One of: `tmdb`, `mal`, `mangaupdates`, `igdb`, `openlibrary`, `hardcover`, `comicvine`, `manual`.                                                                               |
| media_type     | **Yes**   | movie                                          | One of: `tv`, `season`, `episode`, `movie`, `anime`, `manga`, `game`, `book`, `comic`.                                                                                          |
| title          | No        | Inception                                      | Leave blank to let YamTrack fetch the title from the provider automatically with the `media_id`.                                                                                |
| image          | No        | <https://image.tmdb.org/t/p/w500/qmDpIH...jpg> | Public poster/cover URL. Leave blank to auto-fetch from the provider automatically with the `media_id` or `title`.                                                              |
| season_number  | Cond.     | 2                                              | Required when `media_type = season`; also required (together with `episode_number`) when `media_type = episode`.                                                                |
| episode_number | Cond.     | 5                                              | Required when `media_type = episode`; ignored otherwise.                                                                                                                        |
| score          | No        | 8.5                                            | Decimal 0–10 (stored as a 100-point integer internally).                                                                                                                        |
| status         | **Yes**   | Completed                                      | One of: `Completed`, `In progress`, `Planning`, `Paused`, `Dropped`. Capitalisation must match.                                                                                 |
| notes          | No        | Watched in cinema                              | Free-text.                                                                                                                                                                      |
| start_date     | No        | 2023-01-16 03:56:13+00:00                      | Full ISO-8601 **timestamp with timezone** `YYYY-MM-DD HH:MM:SS±HH:MM`.                                                                                                          |
| end_date       | No        | 2023-02-10 22:15:00+00:00                      | Same format as `start_date`.                                                                                                                                                    |
| progress       | No        | 10                                             | Numeric progress (e.g., chapters, pages, minutes). Ignored for tv and season because progress is tracked with episode rows.                                                     |
