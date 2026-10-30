---
name: herdr-containers
description: Install and use the herdr-containers Herdr layout (a Containers section in Herdr's sidebar that opens dev containers and other Docker containers as spaces). Use when the person asks to add herdr-containers, manage agents across dev containers in Herdr, connect a container to Herdr, or troubleshoot it (hc is not installed, Docker isn't running, Repair, Gone, Disconnected, rebuilds).
---

# herdr-containers

A Herdr layout package: `herdr-containers/` (layout + five mods + keys.json + RECIPE.md).
Requirements: `hc-devcontainer` on the PATH of both the app and core, Docker or podman,
herdr-core. Optional: the `herdr-core` dev container Feature, so rebuilt containers keep
their identity.

## Install

Follow Herdr's own steps for layouts other people made (herdr-app `defaults/AGENTS.md`,
"Layouts and mods other people made"):

1. Read `herdr-containers/RECIPE.md` and every file first. Tell the person what the mods
   reach (the recipe's Watch out lists it): `containers` runs `hc-devcontainer` (which runs
   `docker`/`podman` and inspects host containers, labels, mounts, and workspaces) and
   `hc machine ls/add/rm/rename`; `spaces-containers` runs `git` in space folders, watches
   repository folders, and calls `hc`; `settings-containers` reads `layouts/*.json`, reads and
   writes the app home's `settings.json`, and calls `hc`. Connecting or repairing a container
   runs `hc machine add … --install`. A container without a compatible Herdr Core (plain
   containers, and dev containers without the Feature) gets one at `~/.local/bin/herdr-core`
   of the user Herdr connects as, plus an `hc` link if it has none, and that core is
   restarted. hc copies its own binary when the container's OS and CPU match the host's;
   otherwise, as on any macOS host, it first downloads the matching build of its version
   from releases.herdr.dev.
2. Install the requirement:
   ```sh
   mkdir -p "$HOME/.local/bin"
   install -m 755 requirements/hc-devcontainer "$HOME/.local/bin/hc-devcontainer"
   ```
   `~/.local/bin` must be on the PATH of both the app and core (restart core after
   changing its PATH).
3. The app home is `app_home=${HERDR_APP_HOME:-"${XDG_CONFIG_HOME:-$HOME/.config}/herdr-app"}`.
   Copy `herdr-containers/layouts/main-containers.json` into `$app_home/layouts/` and each
   `herdr-containers/mods/<name>/` into `$app_home/mods/`. Never overwrite: on a name clash,
   rename the new one and update the `mod` names in the layout and the mod's own
   `mods/<name>/` paths.
4. Merge `herdr-containers/keys.json` into the person's `keys.json`. Ask before replacing
   a key they already use.
5. `herdr-app check "$app_home/mods/<name>"` for each of the five mods, then
   `herdr-app do layout.switch --name main-containers`.

To keep the person's own layout instead, follow README section C: copy only the `containers`
mod, add its block (id `containers`) to their layout, and offer the clones as optional swaps.

The clones (`*-containers`) are built for the Herdr version in their `cloned_from`. On a
newer Herdr, rebuild them (maintainers: `update-for-herdr` skill) or from the recipe.

## Add a project's dev container

- Open it with VS Code or `devcontainer up` — it appears under **Dev containers**.
- For rebuild-proof identity, add the Feature to `.devcontainer/devcontainer.json`
  (local path: copy `requirements/feature/herdr-core` into `.devcontainer/herdr-core`):
  ```json
  "features": { "./herdr-core": { "version": "latest" } }
  ```
- Click the row: it connects and opens a space in the container's workspace folder.

## Troubleshoot

| Symptom | Cause / fix |
|---|---|
| "hc-devcontainer is not installed (put it on PATH)" | The app can't run it. Put it in a folder on the app's PATH; reopen Herdr if you changed that PATH. |
| "hc is not installed" right after adding | Core can't find `hc-devcontainer` (sh exits 127). Put it on core's PATH, restart core. |
| "Docker isn't running" | Start Docker. Dev containers stay stopped: use **Start** on the row. |
| Row shows **Repair** | Rebuilt without the Feature (new core id). Click Repair; add the Feature to avoid it. |
| **Gone** group | The container was deleted. **Forget** closes its spaces and removes the machine. |
| Spaces/agents show **Disconnected** | Their container or Docker is down; they come back when it is. |
| Timed out / Herdr hasn't loaded it | Click the row again; Herdr's machine list lags a few seconds. |
