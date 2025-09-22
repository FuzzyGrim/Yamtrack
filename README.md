# Yamtrack Mod (Community Fork)

* **Repository**: https://github.com/omfgnuts/yamtrack-mod
* **Upstream (original project):** https://github.com/FuzzyGrim/Yamtrack

This repository is a personal fork of Yamtrack, a self-hosted media tracker for movies, TV shows, anime, manga, video games, books, and comics.

>**Disclaimer**: I’m a **technical writer** first and an **inexperienced developer**. I rely heavily on AI-assisted tools and I loosely follow the coding guidelines from the official repository. I’ll do my best to clean up the code and align with upstream over time, but there’s no guarantee it will fully happen. My focus is TV shows and movies, so Game and Manga and Anime tracking are not priorities for this fork and may lag behind or be left untouched at all.

## Purpose of this fork:

* Cherry-pick selected upstream PRs when they are stable and align with this fork’s direction.
* Accept community contributions that make sense for the fork.
* Add personal tweaks/UX changes I find useful.

I’m not the main developer of Yamtrack. Prefer the upstream repository for core bugs, support, and documentation. You can open issues here for fork-specific requests, but I'm not really a good developer.

## What’s different in this fork as of now

### Added

* Global Time-Spent Statistics: 
  * A consolidated view of time spent across movies, TV, games, books, and more. (See screenshot in this repo.)
* Cast & Crew Strips on Media Details:
  * Horizontally scrollable strips under the description to surface actors, roles/characters, and crew for quick scanning.

**Focus**: I’m prioritizing TV shows and movies.

### Planned changes

* Clickable people (actors/directors): Open a filmography view showing titles they starred in or worked on.
* Selectable genres: Select a genre to list all titles you’ve watched in that category along with recommendations.
* User reviews: Fetch reviews from TheTVDB (and possibly other sources).
* Budget & revenue: Display production budget and box office revenue when available.
* Easy trailer access: Add a prominent “Watch Trailer” action on media pages.

Upstream alignment:
* I’ll continue to cherry-pick useful PRs and sync with upstream periodically; expect occasional gaps while features stabilize.

If you have a feature request you’d like to see in this fork, feel free to open an issue here with details.

## Screenshots

* Global Time-Spent Statistics:
<img width="1251" height="213" alt="image" src="https://github.com/user-attachments/assets/2d3db468-ef50-4ff5-adef-d6ae75813f34" />

* Cast & Crew Strips:
<img width="1182" height="924" alt="image" src="https://github.com/user-attachments/assets/842817fa-fba0-4bd0-9c20-8bd397c919c0" />

## Demo

Use the official [demo](https://github.com/FuzzyGrim/Yamtrack) provided by the upstream project:

## Installation, Deployment, Testing, and Environment

Refer to the [upstream documentation](https://github.com/FuzzyGrim/Yamtrack) for installation, deployment, testing, and environment variables.

This fork aims to remain deployable with the same methods as upstream. If you encounter a fork-specific issue, let me know—but issues reproducible on upstream should be reported upstream.

## Contributing & Feature Requests:
* Feature requests for this fork are welcome. Open an issue with a clear problem statement and acceptance criteria.
* Pull requests that improve or stabilize fork-specific features are appreciated.
* For core functionality or larger changes that benefit everyone, please contribute directly to upstream first. I’ll cherry-pick or rebase those changes here when appropriate.

## Support & Issues:
* For general support, bugs, or documentation, use the upstream issue tracker:
https://github.com/FuzzyGrim/Yamtrack
* You can open issues in this fork for fork-specific features, but it may not be the most effective path since I’m not the main maintainer.

## License

This fork follows the AGPL-3.0 license, same as the upstream project. See LICENSE for details.

## Acknowledgments

Huge thanks to @FuzzyGrim and all upstream contributors. This fork stands on their work.
