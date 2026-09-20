#!/bin/zsh
# Notarize and staple the DMG so it opens without Gatekeeper warnings on other Macs.
#
# One-time setup (needs a paid Apple Developer account):
#   1. Create a "Developer ID Application" certificate (Xcode ▸ Settings ▸ Accounts ▸ Manage
#      Certificates ▸ +) — the "Apple Development" certificate cannot sign for distribution.
#   2. Create an app-specific password at account.apple.com, then store it in the keychain:
#        xcrun notarytool store-credentials woodshed-notary --apple-id <you@example.com> --team-id <TEAMID>
#
# Each release:
#   SIGN_IDENTITY="Developer ID Application: <Name> (<TEAMID>)" scripts/build_app.sh
#   scripts/notarize.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${NOTARY_PROFILE:-woodshed-notary}"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
DMG="build/Woodshed-$VERSION.dmg"

[[ -f "$DMG" ]] || { echo "No $DMG — run scripts/build_app.sh first." >&2; exit 1; }
if ! codesign -dvv "$DMG" 2>&1 | grep -q "Authority=Developer ID Application"; then
  echo "$DMG is not signed with a Developer ID; Apple will reject it. Rebuild with SIGN_IDENTITY set." >&2
  exit 1
fi

xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$DMG"
spctl --assess --type open --context context:primary-signature -v "$DMG"
echo "Notarized and stapled: $DMG"
