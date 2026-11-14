#!/usr/bin/env bash
# Start or stop a local Floppy web server.
#
# Stops by port listener rather than `pkill -f gunicorn.*config.wsgi`, which
# matches the caller's own command line and can kill the calling shell.
set -uo pipefail

PORT="${FLOPPY_LOCAL_PORT:-8299}"
# Bind all interfaces so a phone on the same network can reach it.
BIND_HOST="${FLOPPY_LOCAL_HOST:-0.0.0.0}"
SETTINGS="${DJANGO_SETTINGS_MODULE:-local_serve}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/../../../../src" && pwd)"
LOGDIR="${FLOPPY_LOCAL_LOGDIR:-/tmp/floppy-local}"
mkdir -p "$LOGDIR"

port_pids() {
  # lsof first: `ss -ltnp` prints nothing at all in some containers (it can't
  # read the socket table), so an ss-only lookup silently finds no listener and
  # reports success while the server keeps running. Ask three ways.
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti:"$PORT" -sTCP:LISTEN 2>/dev/null && return
  fi
  local via_ss
  via_ss="$(ss -ltnp 2>/dev/null | grep ":$PORT" | grep -oP 'pid=\K[0-9]+' | sort -u)"
  if [ -n "$via_ss" ]; then
    echo "$via_ss"
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$'
  fi
}

write_settings() {
  # IS_PROD=False turns off allauth's X-Real-IP requirement (nginx sets it in
  # production) and lets Django serve /static/ itself.
  if [ ! -f "$SRC/local_serve.py" ]; then
    cat > "$SRC/local_serve.py" <<'PY'
"""Local settings: no nginx, so let Django serve /static/."""
from config.settings import *  # noqa: F403

IS_PROD = False
PY
    echo "wrote $SRC/local_serve.py (local only - do not commit)"
  fi
}

cmd_start() {
  local with_worker=0
  [ "${1:-}" = "--with-worker" ] && with_worker=1

  cd "$SRC" || exit 1
  write_settings

  if ! redis-cli ping >/dev/null 2>&1; then
    echo "starting redis on 6379"
    redis-server --port 6379 --daemonize yes --save '' --dir /tmp
  fi

  export DJANGO_SETTINGS_MODULE="$SETTINGS"
  export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
  export DEBUG=False

  # Same probe entrypoint.sh runs, so the process set matches what ships.
  eval "$(python -c 'from config.runtime_profile import emit_env; emit_env()' 2>/dev/null)"
  echo "tier=${FLOPPY_RESOURCE_TIER:-?} gunicorn=${WEB_CONCURRENCY:-?}x${GUNICORN_THREADS:-?}"

  python manage.py migrate --noinput >"$LOGDIR/migrate.log" 2>&1 \
    || { echo "migrate failed, see $LOGDIR/migrate.log"; exit 1; }
  python manage.py collectstatic --noinput >"$LOGDIR/static.log" 2>&1 \
    || echo "WARNING: collectstatic failed; pages will render unstyled"

  if [ "$with_worker" = 1 ]; then
    FLOPPY_PROCESS_ROLE="${FLOPPY_CELERY_ROLE:-background}" \
      celery --app config worker --queues "${FLOPPY_CELERY_QUEUES:-celery}" \
      --hostname "local-celery@%h" --loglevel INFO \
      --without-mingle --without-gossip >"$LOGDIR/celery.log" 2>&1 &
    echo "celery worker started (queues=${FLOPPY_CELERY_QUEUES:-celery})"
  fi

  FLOPPY_PROCESS_ROLE=web nohup gunicorn --bind "$BIND_HOST:$PORT" \
    --config python:config.gunicorn config.wsgi:application \
    >"$LOGDIR/gunicorn.log" 2>&1 &

  # Bounded wait without `timeout`: that is GNU coreutils and is absent on a
  # stock macOS, where it failed as "command not found" and reported the
  # server unhealthy without ever having checked it.
  healthy=""
  for _ in $(seq 1 90); do
    if curl -sf -o /dev/null "http://localhost:$PORT/health/"; then
      healthy=1
      break
    fi
    sleep 1
  done

  if [ -n "$healthy" ]; then
    echo "up on http://localhost:$PORT (logs in $LOGDIR)"
  else
    echo "did not become healthy; see $LOGDIR/gunicorn.log"
    tail -20 "$LOGDIR/gunicorn.log"
    exit 1
  fi
}

cmd_stop() {
  local pids
  pids="$(port_pids)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    sleep 2
    pids="$(port_pids)"
    # shellcheck disable=SC2086
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  fi

  # Confirm rather than assume: a stop that quietly failed is worse than one
  # that says so, because the next start hits an occupied port.
  if curl -sf -o /dev/null --max-time 3 "http://localhost:$PORT/health/" 2>/dev/null; then
    echo "WARNING: still serving on :$PORT - could not identify the listener."
    echo "  Find it with: lsof -ti:$PORT -sTCP:LISTEN"
    echo "  Do NOT use pkill -f 'gunicorn.*config.wsgi' - that pattern matches"
    echo "  your own shell's command line and can kill this session."
    exit 1
  fi
  echo "stopped; nothing serving on :$PORT"
  pkill -f "local-celery@" 2>/dev/null && echo "stopped local celery worker"
  exit 0
}

case "${1:-start}" in
  start) shift 2>/dev/null; cmd_start "${1:-}" ;;
  stop) cmd_stop ;;
  restart) cmd_stop; sleep 2; cmd_start "${2:-}" ;;
  *) echo "usage: serve.sh [start [--with-worker] | stop | restart]"; exit 2 ;;
esac
