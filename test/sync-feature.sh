#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
dest="$root/test/.devcontainer/herdr-core"
rm -rf "$dest"
cp -R "$root/feature/herdr-core" "$dest"
printf 'Copied Feature to %s\n' "$dest"
