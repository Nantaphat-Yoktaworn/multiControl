#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
mkdir -p data

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PORT="${MULTICONTROL_PORT:-8765}"
HOST_IP="${MULTICONTROL_LAN_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
HOST_IP="${HOST_IP:-localhost}"

docker compose up -d --build
echo ""
echo "✅ MultiControl Central Hub & Layout Studio is running!"
echo "   • Web UI:                http://${HOST_IP}:${PORT}"
if [ -n "${MULTICONTROL_TAILSCALE_IP:-}" ]; then
  echo "   • Tailscale VPN Web UI:  http://${MULTICONTROL_TAILSCALE_IP}:${PORT}"
fi
echo "   • macOS 1-Line Install:  curl -fsSL http://${HOST_IP}:${PORT}/install.sh | bash"
echo "   • Windows PowerShell:    irm http://${HOST_IP}:${PORT}/install.ps1 | iex"
