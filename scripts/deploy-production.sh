#!/usr/bin/env bash
set -euo pipefail

branch="${1:-}"
repo_dir="$HOME/projects/spine"

if [[ -z "$branch" ]]; then
  echo "Usage: $0 <branch>" >&2
  exit 2
fi

if [[ "$branch" == -* ]] || ! git check-ref-format --branch "$branch" >/dev/null 2>&1; then
  echo "Invalid branch: $branch" >&2
  exit 2
fi

cd "$repo_dir"

if [[ ! -f .env.production ]]; then
  echo "Missing $repo_dir/.env.production" >&2
  exit 1
fi

compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
build_log="$(mktemp -t spine-deploy-build.XXXXXX)"
trap 'rm -f "$build_log"' EXIT

build_was_all_cached() {
  awk '
    /^#[0-9]+ \[/ {
      id = $1
      if ($0 !~ /\[internal\]/ && $0 !~ /exporting/ && $0 !~ /importing/ && $0 !~ /resolving/) {
        step[id] = 1
      }
    }
    $2 == "CACHED" && step[$1] { cached++ }
    $2 == "DONE" && step[$1] { done++ }
    END { exit !(cached > 0 && done == 0) }
  ' "$1"
}

git fetch origin
if git show-ref --verify --quiet "refs/heads/$branch"; then
  git checkout "$branch"
else
  git checkout --track "origin/$branch"
fi
git pull --ff-only origin "$branch"

export DOCKER_BUILDKIT=1
export BUILDKIT_PROGRESS=plain

"${compose[@]}" up -d --build 2>&1 | tee "$build_log"

if build_was_all_cached "$build_log"; then
  echo "Build used only cached layers; rebuilding app with --no-cache."
  "${compose[@]}" build --no-cache app
  "${compose[@]}" up -d
fi

"${compose[@]}" exec -T app python manage.py migrate
"${compose[@]}" exec -T app python manage.py shell -c 'from django.core.cache import cache; cache.clear()'

curl --fail --show-error --silent https://api.spine-api.com/api/v1/health/
echo
