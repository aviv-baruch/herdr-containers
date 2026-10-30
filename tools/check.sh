#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
fail() { printf 'check: %s failed\n' "$1" >&2; exit 1; }

python3 -I -B -m unittest discover -s test || fail 'Python tests (including package drift)'
node --test test/*.cjs || fail 'Node tests'

find herdr-containers requirements -type f -name '*.js' -exec sh -c '
    for file do
        node --check "$file" || { printf "check: JavaScript syntax failed: %s\n" "$file" >&2; exit 1; }
    done
' sh {} + || fail 'JavaScript syntax'

find tools requirements test -type f -name '*.sh' -exec sh -c '
    for file do
        sh -n "$file" || { printf "check: shell syntax failed: %s\n" "$file" >&2; exit 1; }
    done
' sh {} + || fail 'shell syntax'

find . -path './.git' -prune -o -type f -name '*.json' -exec python3 -I -B -c '
import json
import sys

for name in sys.argv[1:]:
    try:
        with open(name, encoding="utf-8") as stream:
            json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"check: invalid JSON: {name}: {error}", file=sys.stderr)
        sys.exit(1)
' {} + || fail 'JSON validity'

# Whitespace in every file git would add, tracked or not: a throwaway index of
# intent-to-add entries makes `git diff --check` see whole files.
index=$(mktemp -d)
trap 'rm -rf "$index"' EXIT
GIT_INDEX_FILE="$index/index" git add --intent-to-add --all . || fail 'whitespace index'
GIT_INDEX_FILE="$index/index" git diff --check || fail 'whitespace (git diff --check)'
printf 'check: passed\n'
