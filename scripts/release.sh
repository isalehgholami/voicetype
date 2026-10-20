#!/usr/bin/env bash
# Release helper:  scripts/release.sh 0.1.0
# 1) checks tests+lint  2) bumps version  3) tags  4) updates the AUR PKGBUILD checksums (after you push the tag).
set -euo pipefail
VER="${1:?usage: release.sh X.Y.Z}"
cd "$(dirname "$0")/.."

python -m ruff check voicetype tests scripts spikes
python -m pytest -q

sed -i "s/^version = \".*\"/version = \"$VER\"/" pyproject.toml
sed -i "s/^__version__ = \".*\"/__version__ = \"$VER\"/" voicetype/__init__.py
sed -i "s/^pkgver=.*/pkgver=$VER/" packaging/aur/PKGBUILD

git add -A && git commit -m "Release $VER"
git tag -a "v$VER" -m "VoiceType $VER"
echo "Now: git push && git push --tags   (the tag tarball must exist before the next step)"
read -r -p "Pushed? Press Enter to update the AUR checksums… "
( cd packaging/aur && updpkgsums && makepkg --printsrcinfo > .SRCINFO )
echo "Copy packaging/aur/{PKGBUILD,.SRCINFO} and voicetype.install into your AUR git repo and push it."
