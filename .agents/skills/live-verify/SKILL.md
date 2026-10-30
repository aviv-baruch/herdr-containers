---
name: live-verify
description: Maintainer checks that the herdr-containers package actually works in Herdr and against real Docker containers. Use before a release, after rebuilding clones, or when changing mod behavior — scratch app-home install, herdr-app check, screenshots, and isolated hc resilience checks.
---

# Live verify

Never touch the person's real core, app home, or running agents without their OK.

## 1. Scratch install (always)

The clones point at shipped assets and overlays (recipe, Watch out), so the scratch home
gets Herdr's shipped mods and frame first, from the same version's defaults:

```sh
S=$(mktemp -d); mkdir -p "$S/layouts" "$S/mods"
cp -R ../herdr-app/defaults/mods/. "$S/mods/"; cp -R ../herdr-app/defaults/frame "$S/frame"
cp herdr-containers/layouts/*.json "$S/layouts/"; cp -R herdr-containers/mods/. "$S/mods/"
for m in containers spaces-containers agents-containers settings-containers workspace-chrome-containers; do
  HERDR_APP_HOME="$S" herdr-app check "$S/mods/$m"
done
rm -rf "$S"
```

All five must print `check passed`. `herdr-app check` needs a display.

## 2. Real app home (only with the person's OK)

Install as in the `herdr-containers` skill, `layout.switch` to `main-containers`, then
`herdr-app shot <file.png>`. The shot is the last drawn frame: ask the person to focus the
Herdr window first, or it may be stale. Check: Containers between Spaces and Agents, group
counts, rows hover, headers don't, footer Settings → Containers.

## 3. Resilience (isolated core)

Use `HC_SESSION=<scratch>` so the real core is untouched, with a throwaway dev container
built from `test/.devcontainer` (`test/sync-feature.sh` first):

- `docker stop` / `start`, `docker restart` → machine unreachable, then connected, same id
- `devcontainer up --remove-existing-container` (with Feature) → same core id
- host core restart → reconnects
- Docker daemon down → needs the person's sudo in their own terminal

Clean up: `hc machine rm`, `HC_SESSION=<scratch> hc server stop`, remove its state dir.
