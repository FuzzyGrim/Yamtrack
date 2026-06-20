#!/usr/bin/env bash
# One-time setup for a permanent Cloudflare Tunnel hostname (not ephemeral trycloudflare).
#
# Prerequisites:
#   - brew install cloudflared
#   - A domain on Cloudflare (nameservers pointed to Cloudflare)
#   - Docker spine stack listening on localhost:8000
#
# Usage:
#   ./scripts/setup-cloudflare-tunnel.sh api.yourdomain.com

set -euo pipefail

HOSTNAME="${1:-}"
TUNNEL_NAME="${TUNNEL_NAME:-spine}"

if [[ -z "$HOSTNAME" ]]; then
  echo "Usage: $0 <hostname>"
  echo "Example: $0 api.example.com"
  exit 1
fi

if ! command -v cloudflared >/dev/null; then
  echo "Install cloudflared: brew install cloudflared"
  exit 1
fi

CF_DIR="${HOME}/.cloudflared"
mkdir -p "$CF_DIR"

echo "==> Step 1: Log in to Cloudflare (browser opens)"
echo "    Pick the zone that owns ${HOSTNAME}"
cloudflared tunnel login

echo "==> Step 2: Create tunnel '${TUNNEL_NAME}' (skip if it already exists)"
if cloudflared tunnel list 2>/dev/null | grep -q "${TUNNEL_NAME}"; then
  TUNNEL_ID="$(cloudflared tunnel list | awk -v n="${TUNNEL_NAME}" '$0 ~ n {print $1; exit}')"
  echo "    Reusing tunnel ${TUNNEL_NAME} (${TUNNEL_ID})"
else
  cloudflared tunnel create "${TUNNEL_NAME}"
  TUNNEL_ID="$(cloudflared tunnel list | awk -v n="${TUNNEL_NAME}" '$0 ~ n {print $1; exit}')"
fi

CREDS="${CF_DIR}/${TUNNEL_ID}.json"
if [[ ! -f "${CREDS}" ]]; then
  echo "Missing credentials file: ${CREDS}"
  exit 1
fi

echo "==> Step 3: DNS route ${HOSTNAME} -> tunnel"
cloudflared tunnel route dns "${TUNNEL_NAME}" "${HOSTNAME}"

CONFIG="${CF_DIR}/config.yml"
cat > "${CONFIG}" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CREDS}

ingress:
  - hostname: ${HOSTNAME}
    service: http://127.0.0.1:8000
  - service: http_status:404
EOF

echo "==> Wrote ${CONFIG}"
echo ""
echo "==> Step 4: Test (Ctrl+C to stop)"
cloudflared tunnel run "${TUNNEL_NAME}" &
PID=$!
sleep 3
if curl -fsS "https://${HOSTNAME}/api/v1/health/" >/dev/null; then
  echo "OK: https://${HOSTNAME}/api/v1/health/"
else
  echo "WARN: health check failed — is Docker up on port 8000?"
fi
kill "${PID}" 2>/dev/null || true
wait "${PID}" 2>/dev/null || true

PLIST="${HOME}/Library/LaunchAgents/com.spine.cloudflared.plist"
cat > "${PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.spine.cloudflared</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(command -v cloudflared)</string>
    <string>tunnel</string>
    <string>run</string>
    <string>${TUNNEL_NAME}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/spine-cloudflared.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/spine-cloudflared.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.spine.cloudflared" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST}"
launchctl enable "gui/$(id -u)/com.spine.cloudflared"
launchctl kickstart -k "gui/$(id -u)/com.spine.cloudflared"

echo ""
echo "Done."
echo "  Permanent API URL: https://${HOSTNAME}"
echo "  iOS AppConfig.productionAPIBaseURL = \"https://${HOSTNAME}\""
echo "  Tunnel auto-starts on login (LaunchAgent: com.spine.cloudflared)"
echo "  Logs: ${HOME}/Library/Logs/spine-cloudflared.log"
