#!/usr/bin/env bash
# Build the macOS reference input field into .build/ (the directory is ignored by git):
#   .build/refinput-macos       the executable
#   .build/RefInput.app         the same executable in an application bundle with a bundle identifier, which
#                               the emitter's focus_back and the runner's front-window check need
set -eu
cd "$(dirname "$0")"
if ! command -v swiftc >/dev/null 2>&1; then
  echo "build.sh: swiftc not found (install the Xcode command line tools)" >&2
  exit 2
fi
BUNDLE_ID="${REFINPUT_BUNDLE_ID:-local.ime-observation.refinput}"
# The same form the emitter accepts for focus_app (kit/emit/backend_macos.py); LC_ALL=C keeps the ranges ASCII.
LC_ALL=C
BUNDLE_RE='^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$'
if ! [[ "${BUNDLE_ID}" =~ ${BUNDLE_RE} ]]; then
  echo "build.sh: REFINPUT_BUNDLE_ID must start with a letter or digit and use letters, digits, '.' and '-' only" \
       "(at most 128 characters)" >&2
  exit 2
fi
mkdir -p .build
swiftc -O RefInput.swift -o .build/refinput-macos
APP=".build/RefInput.app"
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS"
cp .build/refinput-macos "${APP}/Contents/MacOS/refinput-macos"
cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
<key>CFBundleName</key><string>RefInput</string>
<key>CFBundleExecutable</key><string>refinput-macos</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
</dict></plist>
PLIST
if command -v codesign >/dev/null 2>&1; then
  codesign --force --sign - "${APP}" >/dev/null 2>&1 || echo "build.sh: ad-hoc signing failed (the bundle is still usable locally)" >&2
fi
echo "built: $(pwd)/.build/refinput-macos"
echo "built: $(pwd)/${APP} (bundle identifier ${BUNDLE_ID})"
