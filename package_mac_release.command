#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

APP="dist/AstroFrame.app"
VERSION="$(tr -d '\r\n' < VERSION)"
RELEASE_DIR="release"
STAGE_DIR="$RELEASE_DIR/dmg-staging"
DMG="$RELEASE_DIR/AstroFrame-${VERSION}-macOS-arm64.dmg"
CHECKSUM="$DMG.sha256"

if [[ ! -d "$APP" ]]; then
  echo "AstroFrame.app was not found at $APP"
  echo "Run ./build_mac_app.command first, test the app, then run this script."
  exit 1
fi

BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || true)"
if [[ "$BUNDLE_VERSION" != "1.0 RC1" ]]; then
  echo "Unexpected app bundle version: ${BUNDLE_VERSION:-unknown}"
  echo "Expected: 1.0 RC1"
  exit 1
fi

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR" "$RELEASE_DIR"

# Preserve the app bundle exactly as tested.
ditto "$APP" "$STAGE_DIR/AstroFrame.app"
ln -s /Applications "$STAGE_DIR/Applications"
cp docs/MAC_INSTALL.md "$STAGE_DIR/READ ME FIRST.md"
cp docs/RC1_TESTER_GUIDE.md "$STAGE_DIR/RC1 TESTER GUIDE.md"

rm -f "$DMG" "$CHECKSUM"

hdiutil create \
  -volname "AstroFrame 1.0 RC1" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DMG"

shasum -a 256 "$DMG" > "$CHECKSUM"

rm -rf "$STAGE_DIR"

echo
echo "Created: $DMG"
echo "Checksum: $CHECKSUM"
echo
echo "Code-signature verification:"
codesign --verify --deep --strict --verbose=2 "$APP" || true

echo
echo "Gatekeeper assessment:"
spctl -a -vv "$APP" || true

echo
echo "Important: this script creates the private-test DMG but does not"
echo "Developer ID sign or notarize it. See docs/MAC_INSTALL.md."
