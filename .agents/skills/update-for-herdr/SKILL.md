---
name: update-for-herdr
description: Maintainer workflow to rebuild the herdr-containers package for a new Herdr release. Use when Herdr was updated, the drift test fails, a patch anchor in tools/patch-mods.py no longer matches, or the *-containers clones need regenerating.
---

# Update for a new Herdr release

The four clones (`spaces-`, `agents-`, `settings-`, `workspace-chrome-containers`) and the
layout are generated from Herdr's shipped sources. Never hand-edit them.

1. Get the new herdr-app sources at the release tag. The builder default and the drift test
   both read `../herdr-app/defaults`, so that checkout must sit at the tag. It is the
   maintainer's working copy: check it is clean first and ask before moving it.
   ```sh
   git -C ../herdr-app status --short   # must print nothing
   git -C ../herdr-app fetch --tags && git -C ../herdr-app checkout <tag>
   ```
2. Rebuild the package:
   ```sh
   python3 -I tools/build-package.py --source ../herdr-app/defaults
   ```
3. If a patch anchor fails, the build stops, prints `patch-mods: <clone>: patch anchor …`
   with the anchor, and leaves the package unchanged. Read the new shipped mod, fix the
   anchor (exactly-once match) in `tools/patch-mods.py`, add or adjust its test in
   `test/test_spaces_patch.py`, rebuild.
4. Update the Herdr version everywhere it is named: the CI checkout of herdr-app in
   `.github/workflows/check.yml` (SHA and `# v<version>` comment), README.md, RECIPE.md
   (Built from, Watch out), and any skill or AGENTS.md mention. Find them with
   `grep -rn '<old version>' --exclude-dir=.git .` (not CHANGELOG history). The version
   test in `test/test_build_package.py` fails until they match the built `cloned_from`.
5. Run `sh tools/check.sh` (tests, syntax, JSON, drift test, whitespace).
6. Live-verify (`live-verify` skill), then release (`release` skill): CHANGELOG entry
   naming the Herdr version.
