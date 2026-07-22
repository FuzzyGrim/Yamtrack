<!-- --8<-- [start:docs-index-intro] -->

# Yamtrack

![App Tests](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/app-tests.yml/badge.svg)
![Docker Image](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/docker-image.yml/badge.svg)
![CodeFactor](https://www.codefactor.io/repository/github/fuzzygrim/yamtrack/badge)
![Codecov](https://codecov.io/github/FuzzyGrim/Yamtrack/branch/dev/graph/badge.svg?token=PWUG660120)
![GitHub](https://img.shields.io/badge/license-AGPL--3.0-blue)

Yamtrack is a self hosted media tracker for movies, tv shows, anime, manga, video games, books, comics, and board games.

<!-- --8<-- [end:docs-index-intro] -->

## 📚 Documentation

The full documentation is available at [fuzzygrim.github.io/Yamtrack](https://fuzzygrim.github.io/Yamtrack/).

<!-- --8<-- [start:docs-index-body] -->

## 🚀 Demo

You can try the app at [yamtrack.fuzzygrim.com](https://yamtrack.fuzzygrim.com) using the username `demo` and password `demo`.

## ✨ Features

- 🎬 Track movies, tv shows, anime, manga, games, books, comics, and board games.
- 📺 Track each season of a tv show individually and episodes watched.
- ⭐ Save score, status, progress, repeats (rewatches, rereads...), start and end dates, or write a note.
- 📈 Keep a tracking history with each action with a media, such as when you added it, when you started it, when you started watching it again, etc.
- ✏️ Create custom media entries, for niche media that cannot be found by the supported APIs.
- 📂 Create personal lists to organize your media for any purpose, add other members to collaborate on your lists.
- 📅 Keep up with your upcoming media with a calendar, which can be subscribed to in external applications using a iCalendar (.ics) URL.
- 🔔 Receive notifications of upcoming releases via Apprise (supports Discord, Telegram, ntfy, Slack, email, and many more).
- 🐳 Easy deployment with Docker via docker-compose with SQLite or PostgreSQL.
- 👥 Multi-users functionality allowing individual accounts with personalized tracking.
- 🔑 Flexible authentication options including OIDC and 100+ social providers (Google, GitHub, Discord, etc.) via django-allauth.
- 🦀 Integration with [Jellyfin](https://jellyfin.org/), [Plex](https://plex.tv/) and [Emby](https://emby.media/) to automatically track new media watched.
- 📥 Import from [Trakt](https://trakt.tv/), [Simkl](https://simkl.com/), [MyAnimeList](https://myanimelist.net/), [AniList](https://anilist.co/) and [Kitsu](https://kitsu.app/) with support for periodic automatic imports.
- 📊 Export all your tracked media to a CSV file and import it back.

## 📱 Screenshots

| Homepage                                                                                       | Calendar                                                                                    |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/homepage.png?v2" alt="Homepage" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/calendar.png" alt="calendar" /> |

| Media List Grid                                                                                    | Media List Table                                                                                     |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/medialist_grid.png" alt="List Grid" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/medialist_table.png" alt="List Table" /> |

| Media Details                                                                                         | Tracking                                                                                    |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/media_details.png" alt="Media Details" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/tracking.png" alt="Tracking" /> |

| Season Details                                                                                          | Tracking Episodes                                                                                            |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/season_details.png" alt="Season Details" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/tracking_episode.png" alt="Tracking Episodes" /> |

| Lists                                                                                 | Statistics                                                                                      |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/lists.png" alt="Lists" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/statistics.png" alt="Statistics" /> |

| Create Manual Entries                                                                                         | Import Data                                                                                       |
| ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/create_custom.png" alt="Create Manual Entries" /> | <img src="https://cdn.fuzzygrim.com/file/fuzzygrim/yamtrack/import_data.png" alt="Import Data" /> |

## 🐳 Installing with Docker

Download the default `docker-compose.yml` file from the repository, update the environment values, and start Yamtrack:

```bash
docker compose up -d
```

The default Compose file uses SQLite, which is enough for most personal installs. For full SQLite, PostgreSQL, and reverse proxy setup instructions, see the [Setup documentation](https://fuzzygrim.github.io/Yamtrack/release/setup/).

## 💻 Development

Development instructions are available in the [Development documentation](https://fuzzygrim.github.io/Yamtrack/release/development/).

## 💪 Support the Project

There are many ways you can support Yamtrack's development:

### ⭐ Star the Project

The simplest way to show your support is to star the repository on GitHub. It helps increase visibility and shows appreciation for the work.

### 🐛 Bug Reports

Found a bug? Open an [issue](https://github.com/FuzzyGrim/Yamtrack/issues) on GitHub with detailed steps to reproduce it. Quality bug reports are incredibly valuable for improving stability.

### 💡 Feature Suggestions

Have ideas for new features? Share them through [GitHub issues](https://github.com/FuzzyGrim/Yamtrack/issues). Your feedback helps shape the future of Yamtrack.

### 🧪 Contributing

Pull requests are welcome! Whether it's fixing typos, improving documentation, or adding new features, your contributions help make Yamtrack better for everyone.

### ☕ Donate

If you'd like to support the project financially:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/fuzzygrim)
