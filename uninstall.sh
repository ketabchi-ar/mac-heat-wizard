#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "  Uninstalling HeatWizard                         "
echo "=================================================="

pkill -f "HeatWizard" 2>/dev/null || true
pkill -f "mac-heat-daemon" 2>/dev/null || true

PLIST_TARGET="$HOME/Library/LaunchAgents/com.ardalan.heat-monitor.plist"
if [ -f "$PLIST_TARGET" ]; then
    echo "-> Unloading LaunchAgent..."
    launchctl unload "$PLIST_TARGET" 2>/dev/null || true
    rm -f "$PLIST_TARGET"
fi

echo "-> Removing App & CLI shortcuts..."
rm -rf "$HOME/Applications/HeatWizard.app"
rm -f "$HOME/.local/bin/mac-heat"
rm -f "$HOME/.local/bin/mac-heat-wizard"
rm -f "$HOME/.local/bin/mac-heat-scan"
rm -f "$HOME/.local/bin/mac-heat-daemon"

echo "HeatWizard has been completely removed."
