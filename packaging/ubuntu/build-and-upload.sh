#!/usr/bin/env bash
# Build and upload the Ubuntu PPA source package for each target series.
#
# Usage:
#   packaging/ubuntu/build-and-upload.sh <version> [--upload]
#
# Example:
#   packaging/ubuntu/build-and-upload.sh 0.11.1          # build only, source packages left in ./ppa-build
#   packaging/ubuntu/build-and-upload.sh 0.11.1 --upload  # build and dput to the PPA
#
# Requires: devscripts, dpkg-dev, debhelper (build-essential + these on a Debian/Ubuntu host).
# Signs with the GPG key below; override by exporting GPG_KEYID before running.
# debsign otherwise tries to match the Maintainer field in debian/control, which
# only works if that email is a UID on your key.

set -euo pipefail

# Only currently-supported, non-EOL series: resolute (26.04 LTS, GNOME 50)
# and stonking (26.10, in development). oracular/plucky/questing were all
# already obsolete on Launchpad by the time this was written (July 2026) -
# non-LTS Ubuntu releases only get ~9 months of support, so this list needs
# revisiting every time a new series ships.
PPA="ppa:yannmasoch/nautilus-my-computer"
SERIES=(resolute stonking)
GPG_KEYID="${GPG_KEYID:-48A4A06AF4B9B0ED031FCB75E0092153561F1DB8}"

VERSION="${1:?usage: $0 <version> [--upload]}"
UPLOAD="${2:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="$REPO_ROOT/ppa-build"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# devscripts' `debuild`/`dpkg-buildpackage` require the orig tarball to sit
# next to the extracted source tree as <pkg>_<version>.orig.tar.gz.
git -C "$REPO_ROOT" archive --format=tar.gz \
    --prefix="nautilus-my-computer-$VERSION/" \
    -o "$BUILD_DIR/nautilus-my-computer_$VERSION.orig.tar.gz" HEAD

tar -xzf "$BUILD_DIR/nautilus-my-computer_$VERSION.orig.tar.gz" -C "$BUILD_DIR"
SRC_DIR="$BUILD_DIR/nautilus-my-computer-$VERSION"
cp -r "$REPO_ROOT/packaging/ubuntu/debian" "$SRC_DIR/debian"

# The same version is uploaded to every series (the package doesn't differ
# across them) - only the .changes file's Distribution: field differs, since
# that's what Launchpad reads to decide which series pocket to publish into.
# The source package (.dsc/.orig.tar.gz/.debian.tar.xz) must therefore be
# built exactly once and reused byte-for-byte: Launchpad's pool is shared
# across all series in a PPA and rejects re-uploading the same filename with
# different contents, which happens if debian/changelog (baked into
# debian.tar.xz) is edited before every dpkg-source run. So: build the
# source package once, then only regenerate a fresh signed .changes per
# series via dpkg-genchanges (no dpkg-source re-run), pointing at the same
# already-built files.
first_series="${SERIES[0]}"
(
    cd "$SRC_DIR"
    sed -i "1s/) [^;]*;/) ${first_series};/" debian/changelog
    debuild -k"$GPG_KEYID" -S -sa
)

# Derived from debian/changelog rather than hardcoded, so a packaging-only
# revision bump (e.g. 0.11.1-2 for a debian/ fix with no upstream change)
# doesn't also require editing this script.
DEB_VERSION="$(cd "$SRC_DIR" && dpkg-parsechangelog -S Version)"
CHANGES_BASENAME="nautilus-my-computer_${DEB_VERSION}_source.changes"

for series in "${SERIES[@]}"; do
    echo "== packaging for $series =="

    if [ "$series" != "$first_series" ]; then
        (
            cd "$SRC_DIR"
            sed -i "1s/) [^;]*;/) ${series};/" debian/changelog
            dpkg-genchanges -S -sa > "$BUILD_DIR/$CHANGES_BASENAME"
            debsign -k"$GPG_KEYID" "$BUILD_DIR/$CHANGES_BASENAME"
        )
    fi

    mkdir -p "$BUILD_DIR/$series"
    changes="$BUILD_DIR/$CHANGES_BASENAME"
    grep -oP '^ [0-9a-f]{32,64} \d+ \S+ \S+ \K\S+' "$changes" | while read -r f; do
        cp "$BUILD_DIR/$f" "$BUILD_DIR/$series/"
    done
    cp "$changes" "$BUILD_DIR/$series/"

    if [ "$UPLOAD" = "--upload" ]; then
        dput "$PPA" "$changes"
    fi
done

echo
echo "Source packages ready in $BUILD_DIR/<series>/"
find "$BUILD_DIR" -maxdepth 2 -name '*.changes'
