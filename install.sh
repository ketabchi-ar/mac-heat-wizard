#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "  Installing HeatWizard (Apple Silicon M-Series)  "
echo "=================================================="

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_APP_DIR="$HOME/Applications/HeatWizard.app"
BIN_DIR="$HOME/.local/bin"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$HOME/Applications"
mkdir -p "$BIN_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"

# 1. Build
bash "$PROJECT_DIR/build.sh"

# 2. Install App Bundle
echo "-> Installing HeatWizard.app to $TARGET_APP_DIR..."
pkill -f "HeatWizard" 2>/dev/null || true
rm -rf "$TARGET_APP_DIR"
cp -R "$PROJECT_DIR/build/HeatWizard.app" "$TARGET_APP_DIR"

# 3. Install CLI Helpers
echo "-> Installing CLI Tools to $BIN_DIR..."
cp "$PROJECT_DIR/src/cli/mac-heat" "$BIN_DIR/mac-heat"
cp "$PROJECT_DIR/src/cli/mac-heat-scan" "$BIN_DIR/mac-heat-scan"
cp "$PROJECT_DIR/src/daemon/mac-heat-daemon.py" "$BIN_DIR/mac-heat-daemon"
chmod +x "$BIN_DIR/mac-heat" "$BIN_DIR/mac-heat-scan" "$BIN_DIR/mac-heat-daemon"
ln -sf "$TARGET_APP_DIR/Contents/MacOS/HeatWizard" "$BIN_DIR/mac-heat-wizard"

# 4. Install Background Daemon (LaunchAgent)
echo "-> Installing Thermal Spike Daemon..."
PLIST_TARGET="$LAUNCH_AGENTS_DIR/com.ardalan.heat-monitor.plist"
cp "$PROJECT_DIR/src/daemon/com.ardalan.heat-monitor.plist" "$PLIST_TARGET"

launchctl unload "$PLIST_TARGET" 2>/dev/null || true
launchctl load "$PLIST_TARGET" 2>/dev/null || true

echo "=================================================="
echo "  Installation Successful! 🎉"
echo "  • Open App: open ~/Applications/HeatWizard.app"
echo "  • CLI Scanner: mac-heat"
echo "  • Menu Bar Status: Active in top menu bar"
echo "=================================================="
