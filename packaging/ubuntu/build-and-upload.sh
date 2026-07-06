#!/usr/bin/env bash
# Build and upload the Ubuntu PPA source package for each target series.
#
# Usage:
#   packaging/ubuntu/build-and-upload.sh <version> [--upload]
#
# Example:
#   packaging/ubuntu/build-and-upload.sh 0.11.0          # build only, source packages left in ./ppa-build
#   packaging/ubuntu/build-and-upload.sh 0.11.0 --upload  # build and dput to the PPA
#
# Requires: devscripts, dpkg-dev, debhelper (build-essential + these on a Debian/Ubuntu host).
# Signs with your default GPG key via debuild; make sure one is configured.

set -euo pipefail

PPA="ppa:yannmasoch/nautilus-my-computer"
SERIES=(noble oracular plucky)

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

for series in "${SERIES[@]}"; do
    revision="1~${series}1"
    echo "== building for $series (revision $revision) =="

    (
        cd "$SRC_DIR"
        dch --distribution "$series" --newversion "${VERSION}-${revision}" \
            "Rebuild for $series." --force-distribution
        debuild -S -sa
    )
done

echo
echo "Source packages ready in $BUILD_DIR"
ls "$BUILD_DIR"/*.changes

if [ "$UPLOAD" = "--upload" ]; then
    for changes in "$BUILD_DIR"/*_source.changes; do
        dput "$PPA" "$changes"
    done
fi
