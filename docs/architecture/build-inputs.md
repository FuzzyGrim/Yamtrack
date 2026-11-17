# Build inputs outside the lockfiles

`uv.lock` and `package-lock.json` do not cover the browser files committed under
`src/static/` or the files the installer downloads. This page is the inventory
and the update boundary for those inputs.

## Vendored browser libraries

`src/static/vendor-manifest.json` is the machine-readable record: name, version,
license, upstream repository, source artifact, SHA-256 and size of every
vendored file. `app.tests.test_static_libraries.VendorManifestTests` fails when a
file changes without its record, or when a new `*.min.*` file appears in
`js/libraries/` or `css/libraries/` without one. ZXing additionally keeps its
license and `PROVENANCE.txt` beside the bundle (the pattern for a library that
needs a full source/tag record).

| Library | Version | License | File | SHA-256 |
|---|---|---|---|---|
| Alpine.js | 3.14.9 | MIT | `js/libraries/alpinejs-3.14.9.min.js` | `3ed1eed252488921…` |
| Chart.js | 4.4.9 | MIT | `js/libraries/chartjs-4.4.9.min.js` | `de315773454b6076…` |
| chartjs-plugin-datalabels | 2.0.0 | MIT | `js/libraries/chartjs-datalabels-2.0.0.min.js` | `4605ba20245c1e0c…` |
| htmx | 2.0.4 | 0BSD | `js/libraries/htmx-2.0.4.min.js` | `e209dda5c8235479…` |
| jQuery | 3.7.1 | MIT | `js/libraries/jquery-3.7.1.min.js` | `fc9a93dd241f6b04…` |
| lazysizes | 5.3.2 | MIT | `js/libraries/lazysizes-5.3.2.min.js` | `3d9120fa621da6d6…` |
| marked | 12.0.2 | MIT | `js/libraries/marked-12.0.2.min.js` | `15fabce5b65898b3…` |
| Select2 (JS) | 4.1.0-rc.0 | MIT | `js/libraries/select2-4.1.0.min.js` | `f7244fff610595b9…` |
| Select2 (CSS) | 4.1.0-rc.0 | MIT | `css/libraries/select2-4.1.0.min.css` | `cda4a81c187015d9…` |
| SortableJS | 1.15.3 | MIT | `js/libraries/sortablejs-1.15.3.min.js` | `72aa2c4f9f7cb2b8…` |
| @zxing/library | 0.21.3 | Apache-2.0 | `js/libraries/zxing-library-0.21.3.min.js` | `7b1b6776f9dedf2e…` |
| Roboto Flex (variable font) | unrecorded | OFL-1.1 | `fonts/roboto-flex.woff2` | `10e8c746c710eea5…` |

Roboto Flex's upstream release was not recorded when it was vendored; the digest
pins today's bytes only.

### Updating a vendored library

1. Pick an exact upstream release; read its notes and check its advisories
   (GitHub Security Advisories for the upstream repository, `npm audit` on the
   package at that version). Existing pins are not upgraded just to clear a
   low-impact advisory; record the decision in the PR.
2. Replace the file with a versioned name, taken from the registry tarball or the
   release asset named in `source_artifact`.
3. Update `vendor-manifest.json` (version, file, sha256, size) and the template
   reference together.
4. Run `scripts/test.sh app.tests.test_static_libraries`, then lint.

## Installer and bootstrap downloads

| Input | Pin | Verified |
|---|---|---|
| Floppy source (`scripts/install.sh`) | branch by default; `--commit <40-hex SHA>` for an immutable install | `HEAD` must equal the SHA after checkout; a resume refuses a different commit |
| uv (`scripts/install/source_common.sh`) | version `0.12.3` | SHA-256 of `https://astral.sh/uv/<version>/install.sh` is checked before it runs; override with `FLOPPY_UV_INSTALLER_SHA256`, otherwise a non-default version runs unverified with a warning |
| Docker image | `Dockerfile` base images and uv image are `@sha256:` pinned | by the container runtime |
| Python dependencies | `uv sync --locked` | hashes in `uv.lock` |

Immutable source install:

```bash
bash floppy-install.sh --commit <full-commit-sha>
```

Bump the uv version, `UV_INSTALLER_SHA256` and `pyproject.toml` together.

### Remaining trust

- HTTPS and the certificate authorities for github.com and astral.sh. The
  bootstrap script itself is fetched over HTTPS and cannot verify itself; pin a
  copy of it if that matters.
- The uv installer script is verified, but it then downloads the uv binary,
  which the script checks against its own embedded digest.
- OS package repositories and Docker's apt repository/GPG key
  (`scripts/install/docker.sh`), Homebrew's installer on macOS, and the Tailwind
  CLI are not pinned or digest-checked.

## npm audit exceptions

CI's two npm audits (the Expo proof at level high, the frontend build at any
level) run through `scripts/npm_audit_gate.mjs`. They fail on every advisory
except the ones listed in `.github/npm-audit-exceptions.json`. Each entry names
one advisory, the reason it does not reach users, who approved it and an expiry
date; after that date the audit fails again until the entry is removed or
renewed. Add an entry only when no fixed release exists and the owner has
approved it, and remove it as soon as the lockfiles pick up a fix.
