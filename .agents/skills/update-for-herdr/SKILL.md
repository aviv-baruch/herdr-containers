---
name: update-for-herdr
description: Maintainer workflow to rebuild the herdr-containers mod for a new Herdr release. Use when Herdr was updated, the daily rebuild workflow opened a "build: rebuild clones for herdr X.Y.Z" PR or a "patch anchor" issue, the drift test fails, a patch anchor in tools/patch-mods.py no longer matches, or the *-containers clones need regenerating.
---

# Update for a new Herdr release

The four clones (`spaces-`, `agents-`, `settings-`, `workspace-chrome-containers`) are
generated from Herdr's shipped mods; their `mod.json` `cloned_from.version` is the Herdr
version the package targets. Never hand-edit them. The package is a mod: the builder writes
`mods/` and `keys.json`, never a layout.

Normally the daily `.github/workflows/rebuild.yml` does steps 3, 5 and 6 when
herdrdev/herdr-app tags a release: it opens the PR `build: rebuild clones for herdr X.Y.Z`,
or, if a patch anchor fails, the issue `Rebuild for Herdr X.Y.Z failed: patch anchor`. Check
out its branch and use the steps below to review it (step 1 still applies: the drift test and
live-verify read `../herdr-app/defaults`). Never merge before live-verify. Without a PR, do
every step.

1. Get the new herdr-app sources at the release tag. The builder default and the drift test
   both read `../herdr-app/defaults`, so that checkout must sit at the tag. It is the
   maintainer's working copy: check it is clean first and ask before moving it.
   ```sh
   git -C ../herdr-app status --short   # must print nothing
   git -C ../herdr-app fetch --tags && git -C ../herdr-app checkout <tag>
   ```
2. Read what changed in the new defaults: shipped `spaces`, `agents`, `frame/settings` and
   `workspace-chrome`; `layouts/main.json` (the docs put the block after `spaces` in
   `sidebar-lists` and swap `spaces`, `agents` and `sidebar-footer`); and `AGENTS.md`,
   "Layouts and mods other people made".
3. Rebuild the package:
   ```sh
   python3 -I tools/build-package.py --source ../herdr-app/defaults
   ```
   From an installed app home (no `Cargo.toml`), add `--source-version X.Y.Z`.
4. If a patch anchor fails, the build stops, prints `patch-mods: <clone>: patch anchor …`
   with the anchor, and leaves the package unchanged. Read the new shipped mod, fix the
   anchor (exactly-once match) in `tools/patch-mods.py`, add or adjust its test in
   `test/test_spaces_patch.py`, rebuild.
5. Retarget the docs, CI and changelog:
   ```sh
   python3 -I tools/retarget-herdr.py --version X.Y.Z --sha "$(git -C ../herdr-app rev-parse '<tag>^{commit}')"
   ```
   It sets the CI checkout of herdr-app (SHA and `# vX.Y.Z`), the Herdr version in README.md
   and RECIPE.md, and adds `Clones rebuilt for Herdr X.Y.Z.` under Unreleased. Then
   `grep -rn '<old version>' --exclude-dir=.git .` for anything left (AGENTS.md, skills; not
   CHANGELOG history). The version test in `test/test_build_package.py` fails until they
   match the built `cloned_from`.
6. Run `sh tools/check.sh` (tests, syntax, JSON, drift test, whitespace).
7. Read the RECIPE against the new build (behavior inherited from shipped mods, such as the
   15 s Spaces cache, may have changed).
8. Live-verify (`live-verify` skill), then release (`release` skill). The changelog tells
   people who took the swaps to reinstall them: installed copies are frozen and miss this
   rebuild until replaced (compare first if they customized them).
