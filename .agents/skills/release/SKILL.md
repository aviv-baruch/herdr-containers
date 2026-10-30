---
name: release
description: Maintainer release checklist for herdr-containers — changelog, versions, preview screenshot, recipe accuracy, push, and publishing as a public herdr-layout on herdr.dev. Use when cutting a release or publishing the package.
---

# Release

1. `sh tools/check.sh` passes; `live-verify` done for this Herdr version.
2. `CHANGELOG.md`: new entry (Keep a Changelog), naming the Herdr version the clones target.
3. Versions: bump `requirements/feature/herdr-core/devcontainer-feature.json` `version` if the
   Feature changed; the layout's `cloned_from.version` tracks Herdr (set by the build).
4. Refresh `herdr-containers/preview.png` (a `herdr-app shot` of the sidebar, window focused).
5. Read `herdr-containers/RECIPE.md` against the real files: nothing it claims that the
   build doesn't do; requirements and the programs the mods run are disclosed.
6. Commit with lowercase conventional commits; push.
7. Publishing — only with the person's explicit OK: make the repo public and add the topic
   `herdr-layout`
   (`gh repo edit --visibility public --accept-visibility-change-consequences --add-topic herdr-layout`).
   It appears at herdr.dev/layouts within the hour.
