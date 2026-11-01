#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
mkdir -p data
docker compose up -d --build
echo ""
echo "✅ MultiControl Central Hub & Layout Studio is running!"
echo "   • Local LAN Web UI:      http://192.168.1.100:8765"
echo "   • Tailscale VPN Web UI:  http://100.64.0.10:8765"
echo "   • macOS 1-Line Install:  curl -fsSL http://192.168.1.100:8765/install.sh | bash"
echo "   • Windows PowerShell:    irm http://192.168.1.100:8765/install.ps1 | iex"
