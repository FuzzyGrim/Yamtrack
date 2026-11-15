ARG PYTHON_IMAGE=python:3.12-alpine3.24@sha256:b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d

FROM ${PYTHON_IMAGE} AS repo_meta

WORKDIR /repo
RUN --mount=type=bind,target=/context,ro python - <<'PY'
from pathlib import Path
from urllib.parse import urlparse

config_path = Path("/context/.git/config")
owner = ""

if config_path.exists():
    origin_url = None
    in_origin = False
    for raw_line in config_path.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith('[remote "origin"]'):
            in_origin = True
            continue
        if line.startswith("[") and in_origin:
            in_origin = False
        if in_origin and line.startswith("url"):
            _, value = line.split("=", 1)
            origin_url = value.strip()
            break

    if origin_url:
        value = origin_url.strip()
        if value.startswith("git@") and ":" in value:
            value = value.split(":", 1)[1]
        parsed = urlparse(value)
        repo_path = parsed.path if parsed.netloc else value
        repo_path = repo_path.strip("/")
        if repo_path.endswith(".git"):
            repo_path = repo_path[:-4]
        if repo_path:
            owner = repo_path.split("/", 1)[0]

Path("/repo_owner").write_text(owner)
PY

FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /uvx /bin/
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /floppy

COPY ./pyproject.toml ./uv.lock ./
COPY ./mcp_server/pyproject.toml ./mcp_server/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-workspace

COPY ./mcp_server ./mcp_server
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable

FROM ${PYTHON_IMAGE}

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Define build argument with default value
ARG VERSION=dev
ARG COMMIT_SHA=unknown
# Set it as an environment variable
ENV VERSION=$VERSION
ENV COMMIT_SHA=$COMMIT_SHA
# A stray environment override (an orchestrator's persisted .env, a leftover
# manual "docker run -e", etc.) can otherwise shadow the ENV above and make
# the running container silently misreport its own build identity, with
# nothing in this image able to tell the difference. Recording the same
# values in a plain file baked into the image gives entrypoint.sh something
# runtime environment variables cannot override, to restore VERSION and
# COMMIT_SHA from before it reports either one.
RUN printf 'VERSION=%s\nCOMMIT_SHA=%s\n' "$VERSION" "$COMMIT_SHA" > /etc/floppy-build-info

# supervisord expands %(ENV_...)s in supervisord.conf and refuses to start if a
# referenced variable is unset, so these need image-level defaults even though
# entrypoint.sh overwrites them from the detected resource tier (issue #521).
ENV FLOPPY_CELERY_ROLE=background
ENV FLOPPY_CELERY_QUEUES=celery
ENV FLOPPY_START_INTERACTIVE_WORKER=true
ENV FLOPPY_START_DISCOVER_WORKER=true
ENV FLOPPY_SUPERVISORD_LOG=AUTO

COPY ./runtime-entrypoint.sh /runtime-entrypoint.sh
COPY ./entrypoint.sh /entrypoint.sh
COPY ./supervisord.conf /etc/supervisord.conf
# Keep one checked-in listener marker. runtime-entrypoint.sh validates the
# configured port and renders the IPv4/IPv6 runtime configs from this template.
COPY ./nginx.conf /etc/nginx/nginx.conf.template

WORKDIR /floppy

# Legacy compat: pre-rename compose files bind-mount ./db to /yamtrack/db.
# Without this symlink such a mount lands on a stale path and the app
# silently creates an empty database.
RUN ln -s /floppy /yamtrack

RUN apk add --no-cache --upgrade krb5-libs nginx shadow zlib\
    # Dependency installation happens in builder; do not ship a second,
    # unused installer (or ensurepip's bundled copy) from the Python base.
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
    && chmod +x /runtime-entrypoint.sh /entrypoint.sh \
    # create user abc for later PUID/PGID mapping
    && useradd -U -M -s /bin/sh abc \
    # Create required nginx directories and set permissions
    && mkdir -p /var/log/nginx \
    && mkdir -p /var/lib/nginx/body

COPY --from=repo_meta /repo_owner /etc/floppy/fork_owner
COPY --from=builder /opt/venv /opt/venv
RUN ln -s /opt/venv /floppy/.venv

# Django app
COPY src ./
RUN SECRET=build-time-placeholder python manage.py collectstatic --noinput \
    && chown -R 1000:1000 /floppy/staticfiles \
    && SECRET=build-time-placeholder python config/static_collection.py --record

# MCP server (mcp/floppy_mcp) — bundled so `docker exec` always runs the
# version shipped with this image, instead of a user's local checkout that
# can silently drift from the app version.
COPY mcp_server ./mcp_server

# Ship compiled bytecode for the standard library, dependencies and app. The
# base image ships none and PYTHONDONTWRITEBYTECODE stops processes saving it,
# so every Python start (each manage.py, workers, Gunicorn) recompiled all its
# imports from source. On a cold or busy disk that alone could exceed the 30 s
# SQLite admission deadline and park a healthy database (#1875). Keep this
# after the last COPY of Python source: a source file newer than its .pyc is
# compiled in memory again, never run stale.
RUN python -m compileall -q -j 0 -x '^/floppy/(\.venv|staticfiles|db)/' \
    /usr/local/lib/python3.12 /opt/venv /floppy

# 8000 remains the documented/default port. FLOPPY_PORT can select a different
# runtime listener; EXPOSE is image metadata and cannot express a dynamic port.
EXPOSE 8000

CMD ["/runtime-entrypoint.sh"]

# A first boot on an empty volume runs every migration before supervisord
# starts nginx and gunicorn, which can take several minutes on slow storage.
# Failures inside the start period are informational only, so give a cold boot
# that budget instead of reporting it unhealthy while migrations are still
# finishing.
HEALTHCHECK --interval=45s --timeout=15s --start-period=300s --retries=5 \
  CMD port="$(cat /tmp/floppy-runtime/server-port 2>/dev/null)" \
      && case "$port" in *[!0-9]*|'') exit 1 ;; esac \
      && wget --no-verbose --tries=1 --spider "http://127.0.0.1:${port}/health/" \
      || exit 1
