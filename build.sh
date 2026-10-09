#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "  Building HeatWizard for Apple Silicon (M-Series)"
echo "=================================================="

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$PROJECT_DIR/build"
APP_DIR="$BUILD_DIR/HeatWizard.app"

rm -rf "$BUILD_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

echo "-> Compiling Swift Native Core..."
swiftc -O "$PROJECT_DIR/src/HeatWizard/main.swift" \
    -o "$APP_DIR/Contents/MacOS/HeatWizard"

echo "-> Copying Bundle Resources..."
cp "$PROJECT_DIR/src/HeatWizard/Resources/Info.plist" "$APP_DIR/Contents/"
cp "$PROJECT_DIR/src/HeatWizard/Resources/index.html" "$APP_DIR/Contents/Resources/"
cp "$PROJECT_DIR/src/HeatWizard/Resources/BurningIcon.icns" "$APP_DIR/Contents/Resources/"

echo "-> Setting Permissions..."
chmod +x "$APP_DIR/Contents/MacOS/HeatWizard"

echo "=================================================="
echo "  Build Complete: $APP_DIR"
echo "=================================================="
