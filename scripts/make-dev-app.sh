#!/usr/bin/env bash
# Wraps the locally built Gothic2Notr into a minimal macOS app bundle, so it shows up as a regular
# app (Dock name and icon, screenshots, UI automation). The bundle links to the build output: rebuilds
# are picked up. The launcher runs the game from inside this bundle when it exists.
#
#   scripts/make-dev-app.sh
#   open -n "OpenGothic/build/opengothic/OpenGothic Dev.app" --args -g "/path/to/Gothic II" -devmode
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/OpenGothic/build/opengothic"
APP="$OUT/OpenGothic Dev.app"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
ln -sf "../../../Gothic2Notr" "$APP/Contents/MacOS/Gothic2Notr"
# OpenGothic's own icon (a Windows .ico holding one 246x256 PNG), squared and turned into .icns
if [ -f "$ROOT/OpenGothic/icon.ico" ]; then
  TMP="$(mktemp -d)"
  sips -s format png "$ROOT/OpenGothic/icon.ico" --out "$TMP/icon.png" >/dev/null
  sips -p 256 256 "$TMP/icon.png" --out "$TMP/square.png" >/dev/null
  sips -s format icns "$TMP/square.png" --out "$APP/Contents/Resources/OpenGothic.icns" >/dev/null
  rm -rf "$TMP"
fi
cat >"$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>OpenGothic Dev</string>
  <key>CFBundleDisplayName</key><string>OpenGothic Dev</string>
  <key>CFBundleIdentifier</key><string>dev.gothic-ai.opengothic</string>
  <key>CFBundleExecutable</key><string>Gothic2Notr</string>
  <key>CFBundleIconFile</key><string>OpenGothic</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Push-to-talk speech input</string>
</dict>
</plist>
PLIST
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
echo "$APP"
