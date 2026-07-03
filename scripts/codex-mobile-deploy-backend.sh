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

git checkout "$branch"
git fetch origin
git pull

docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
docker compose --env-file .env.production -f docker-compose.production.yml exec app python manage.py shell -c "from django.core.cache import cache; cache.clear()"
