---
name: release
description: Maintainer release checklist for the herdr-containers mod — changelog, versions, preview and demo, recipe accuracy, commits, push, and publishing as a public herdr-mod on herdr.dev. Use when cutting a release or publishing the package.
---

# Release

1. `sh tools/check.sh` passes; `live-verify` done for this Herdr version (all five mods and
   the sidebar).
2. `CHANGELOG.md`: turn Unreleased into a dated version entry (Keep a Changelog) naming the
   Herdr version the clones target (their `cloned_from.version`). If the clones changed, tell
   people who took the swaps to reinstall them.
3. Versions: bump `requirements/feature/herdr-core/devcontainer-feature.json` `version` if the
   Feature changed (then `test/sync-feature.sh`). The clones' `cloned_from.version` tracks
   Herdr (set by the build); README, RECIPE and CI name the same one (version test).
4. Media in `herdr-containers/`, from neutral demo containers, never real projects (no real
   names, folders, machines, or agents on screen); shoot in a `live-verify` scratch window.
   `preview.png` is a full-window `herdr-app shot` with that window focused (else the frame
   may be stale); `demo.mp4` and `demo.gif` play at 2x speed.
5. Read `herdr-containers/RECIPE.md` against the real files: nothing it claims that the build
   doesn't do; the block, swaps, `keys.json`, rename references, requirements, and the
   programs the mods run are all there. The package holds the five mods, `keys.json`,
   `RECIPE.md`, `preview.png` and `demo.*`, and no `layouts/` folder. README install matches.
6. Commit with lowercase conventional commits (`feat:`, `fix:`, `docs:`, `build:`); push.
7. Publishing — only with the owner's explicit OK. Check with
   `gh repo view aviv-baruch/herdr-containers --json visibility,repositoryTopics`, then
   `gh repo edit aviv-baruch/herdr-containers --add-topic herdr-mod`, adding
   `--remove-topic herdr-layout` if that topic is set and
   `--visibility public --accept-visibility-change-consequences` if the repo is private. It
   appears at herdr.dev/mods/aviv-baruch/herdr-containers within the hour.
