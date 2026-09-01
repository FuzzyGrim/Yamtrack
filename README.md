# Yamtrack — Personal Custom Build

This repository is my customized fork of **Yamtrack**, a self-hosted media tracker.

The original Yamtrack project is maintained by FuzzyGrim:

**Original project:** https://github.com/FuzzyGrim/Yamtrack

For the original application's documentation, installation instructions, features, screenshots, support, and development information, please use the upstream Yamtrack repository and documentation.

## About this fork

This fork exists to maintain a small set of personal changes on top of Yamtrack without automatically following every upstream release.

The `custom-release` branch is the customized version intended for my self-hosted installation.

Updates are deliberately manual and selective. When I decide to adopt a newer upstream Yamtrack release, the custom behavior is reviewed and ported onto the newer code rather than automatically applying every upstream update.

## Custom changes

### Cross-account tracking visibility

Search results can show when the same media is already being tracked by other accounts on the local Yamtrack installation.

Compared with upstream Yamtrack, which enriches search results with the current user's own tracking state, this custom version also looks up the local accounts tracking each result.

The search interface displays:

- **Tracked by N account(s)** when one or more accounts track the media.
- The usernames of the accounts tracking that media.
- The information in both grid and list search-result layouts.
- Correct account tracking for seasons by including the season number when identifying media.
- Usernames de-duplicated and sorted case-insensitively.

This is useful on a multi-user Yamtrack installation for quickly seeing whether another local account already has a movie, show, season, book, game, or other supported media in its tracking library.

## Docker image

The customized Docker image is published to GitHub Container Registry as:

```yaml
image: ghcr.io/dragonmaster1748/Yamtrack-my-custom-01:custom-release
```

The image is built from the `custom-release` branch through a manually triggered GitHub Actions workflow. Building an image does not automatically update this fork from upstream Yamtrack.

## Branches

- **`custom-release`** — my customized Yamtrack source and the source used for the custom Docker image.
- **`dev`** — upstream/reference branch used when preparing future updates.

## Updating the custom version

Future upstream updates are intentionally handled manually:

1. Choose an upstream Yamtrack version worth adopting.
2. Compare the new upstream code with the current customized version.
3. Reimplement or port the custom behavior while preserving upstream changes.
4. Test the customized build.
5. Build and publish a new `custom-release` Docker image.
6. Deploy the tested image to the self-hosted installation.

The goal is to keep a stable personal build rather than automatically following every upstream release.

## Additional implementation notes

More detailed notes about the customization and the areas of the Yamtrack code it modifies are available in [`CUSTOM_CHANGES.md`](CUSTOM_CHANGES.md).

## License and upstream attribution

This repository is a fork of [FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack) and retains the upstream project's **GNU Affero General Public License v3.0 (AGPL-3.0)**.

Yamtrack itself and the majority of this codebase originate from the upstream Yamtrack project. This repository documents only the additional personal modifications maintained in this fork.
