#!/bin/zsh
# Build build/Woodshed.app and build/Woodshed-<version>.dmg.
#
#   scripts/build_app.sh                       ad-hoc signed: runs on this Mac only
#   SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" scripts/build_app.sh
#                                              signed for distribution; then run scripts/notarize.sh
set -euo pipefail
cd "$(dirname "$0")/.."

for tool in uv swiftc iconutil codesign hdiutil; do
  command -v "$tool" >/dev/null || {
    case $tool in
      uv) echo "uv is not installed: brew install uv   (or: curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2 ;;
      *)  echo "$tool is missing: install the Xcode Command Line Tools with: xcode-select --install" >&2 ;;
    esac
    exit 1
  }
done
[[ "$(uname -m)" == "arm64" ]] || { echo "Woodshed builds for Apple Silicon only (this Mac is $(uname -m))." >&2; exit 1; }

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
APP="build/Woodshed.app"
DMG="build/Woodshed-$VERSION.dmg"
MODEL="woodshed/models/beat_this_final0.onnx"

[[ -f "$MODEL" ]] || { echo "Exporting the beat model (first build only)…"; uv run python scripts/export_beat_model.py; }

echo "▸ Freezing the Python server"
uv run pyinstaller --noconfirm --clean --onedir --name woodshed-server --log-level WARN \
  --distpath build/dist --workpath build/pyinstaller --specpath build --paths . \
  --add-data "$PWD/web:web" --add-data "$PWD/$MODEL:woodshed/models" --collect-data yt_dlp_ejs \
  --exclude-module torch --exclude-module torchaudio --exclude-module beat_this --exclude-module onnx \
  --exclude-module onnxscript --exclude-module pytest --exclude-module tkinter \
  packaging/server_entry.py

echo "▸ Compiling the app shell"
swiftc -O -target arm64-apple-macos13 -framework Cocoa -framework WebKit -framework Carbon shell/main.swift -o build/Woodshed-shell

echo "▸ Rendering the icon"
rm -rf build/Woodshed.iconset
swift packaging/make_icon.swift build/Woodshed.iconset
iconutil -c icns build/Woodshed.iconset -o build/Woodshed.icns

echo "▸ Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp build/Woodshed-shell "$APP/Contents/MacOS/Woodshed"
cp build/Woodshed.icns "$APP/Contents/Resources/Woodshed.icns"
cp -R build/dist/woodshed-server "$APP/Contents/Resources/server"
sed "s/__VERSION__/$VERSION/g" packaging/Info.plist > "$APP/Contents/Info.plist"

echo "▸ Signing with: $SIGN_IDENTITY"
# Inside-out: every Mach-O in the frozen server first, then the app.
# A real identity gets the hardened runtime and secure timestamps that notarization requires.
# Ad-hoc builds get neither: the hardened runtime's library validation rejects ad-hoc signed
# libraries (they carry no Team ID), so the bundled Python could not load its own dylibs.
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  SIGN_FLAGS=(--force --timestamp=none)
else
  SIGN_FLAGS=(--force --timestamp --options runtime --entitlements packaging/entitlements.plist)
fi
find "$APP/Contents/Resources/server" -type f -print0 | while IFS= read -r -d '' file; do
  if file -b "$file" | grep -q 'Mach-O'; then
    codesign "${SIGN_FLAGS[@]}" -s "$SIGN_IDENTITY" "$file"
  fi
done
codesign "${SIGN_FLAGS[@]}" -s "$SIGN_IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"

echo "▸ Building $DMG"
STAGING=$(mktemp -d)
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Woodshed" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"
[[ "$SIGN_IDENTITY" == "-" ]] || codesign --timestamp -s "$SIGN_IDENTITY" "$DMG"

du -sh "$APP" "$DMG"
echo
echo "Built $APP — drag it into /Applications and open it."
[[ "$SIGN_IDENTITY" == "-" ]] && echo "(Ad-hoc signed: runs on this Mac only. For other Macs, sign with a Developer ID and run scripts/notarize.sh.)"
