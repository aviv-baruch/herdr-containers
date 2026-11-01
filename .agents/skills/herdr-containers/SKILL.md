---
name: herdr-containers
description: Install and use the herdr-containers Herdr mod (a Containers panel added to the sidebar of the person's own layout that opens dev containers and other Docker containers as spaces). Use when the person asks to add herdr-containers or herdr.dev/mods/aviv-baruch/herdr-containers, manage agents across dev containers in Herdr, connect a container to Herdr, or troubleshoot it (hc is not installed, Docker isn't running, Repair, Gone, Disconnected, rebuilds).
---

# herdr-containers

A Herdr mod package, `herdr-containers/`: `mods/containers` (the panel, always installed),
four recommended swaps `mods/{spaces,agents,settings,workspace-chrome}-containers` (patched
copies of Herdr's shipped mods), `keys.json`, `RECIPE.md`. No `layouts/` folder: the panel
goes into the layout the person already uses. Requirements: `hc-devcontainer` on the PATH of
both app and core, Docker or podman, herdr-core. Recommended: VS Code with Dev Containers on
the host (its engine is used directly), or `@devcontainers/cli` and Node.js on both PATHs, so
containers start and connect exactly as in VS Code. Optional: the `herdr-core` dev container
Feature, so rebuilt containers keep their identity.

## Install

Follow Herdr's steps for mods other people made (`AGENTS.md` in the app home, "Layouts and
mods other people made"). The app home is
`app_home=${HERDR_APP_HOME:-"${XDG_CONFIG_HOME:-$HOME/.config}/herdr-app"}`.

1. Read `herdr-containers/RECIPE.md` and every file first. Before writing anything, tell the
   person what the mods reach (the recipe's Watch out lists it): `containers` runs
   `hc-devcontainer` (which runs `docker`/`podman` and inspects host containers, labels,
   mounts, and workspaces) and `hc machine ls/add/rm/rename`; `spaces-containers` runs `git`
   in space folders, watches repository folders, and calls `hc`; `settings-containers` reads
   `layouts/*.json`, reads and writes the app home's `settings.json`, and calls `hc`.
   Connecting or repairing a container runs `hc machine add … --install`. A container without
   a compatible Herdr Core (plain containers, and dev containers without the Feature) gets one
   at `~/.local/bin/herdr-core` of the user Herdr connects as, plus an `hc` link if it has
   none, and that core is restarted. hc copies its own binary when the container's OS and CPU
   match the host's; otherwise, as on any macOS host, it first downloads the matching build of
   its version from releases.herdr.dev. No mod makes its own network request. Ask at the same
   time whether they want the swaps (step 5).
2. Install the requirement:
   ```sh
   mkdir -p "$HOME/.local/bin"
   install -m 755 requirements/hc-devcontainer "$HOME/.local/bin/hc-devcontainer"
   ```
   `~/.local/bin` must be on the PATH of both the app and core (restart core after changing
   its PATH). On Windows, install three files into one folder on the PATH of app and core (the
   README's Windows section): `requirements/hc-devcontainer` as `hc-devcontainer.py`, plus
   `requirements/windows/hc-devcontainer` and `hc-devcontainer.cmd`. Check that Python 3 runs
   (`py -3` or `python`), and that Git for Windows' `sh` is on core's PATH. Tell the person
   that released Herdr Core refuses `--via` machines on Windows: the panel lists and starts
   containers, but connecting needs a core that accepts `--via` (or Herdr inside WSL).
3. Copy `herdr-containers/mods/containers/` into `$app_home/mods/`, plus the four
   `*-containers` folders if the person takes the swaps. Leave out `RECIPE.md`, `preview.png`
   and `demo.*`. Never overwrite a mod (theirs or Herdr's): on a clash, rename the new folder
   and every reference to it: its own `mods/<name>/` asset paths, the layout's `content.mod`,
   the key action `containers.toggle` (`<mod>.toggle`), `openOverlay({ mod:
   "settings-containers" … })` in `workspace-chrome-containers`, and `openOverlay({ mod:
   "agents-containers" … })` in `agents-containers`. Block id `containers` and shared keys
   `containers.*` are not mod names: keep them.
4. Add the block to the person's current layout (`herdr-app status` names it): after
   `spaces` in the `sidebar-lists` column of shipped `main` and its clones, or wherever their
   lists live. Keep id `containers`: folding resizes that id.
   ```json
   { "id": "containers", "type": "block", "size": { "weight": 1 }, "min": 35, "content": { "mod": "containers" } }
   ```
   Herdr's shipped layouts are read-only and replaced on every start. If the current one is
   shipped, clone it with the person's OK and edit the clone
   (`herdr-app do layout.clone '{"name":"main","as":"my-layout"}'` switches to it). Layouts
   reload live; no `layout.switch` needed.
5. Offer the three swaps; recommend them. In the same layout set `content.mod` of block
   `spaces` to `spaces-containers`, `agents` to `agents-containers`, and `sidebar-footer`
   (`workspace-chrome`) to `workspace-chrome-containers`. Keep the footer's
   `"props": { "footer": true }`; leave the `brand` block alone. The footer opens
   `settings-containers`, so it is installed too. Without swaps, the Spaces group lingers
   until Spaces' 15 s machine cache expires, disconnected spaces don't dim, Agents has no
   Disconnected group, and footer Settings has no Containers switches (both visibility
   options stay on). Caveat: the swaps are frozen copies of the Herdr version in their
   `mod.json` `cloned_from`; they don't get Herdr's later fixes to Spaces, Agents, Settings or
   chrome until rebuilt and reinstalled. If `herdr-app --version` is newer, say so: offer
   them anyway, skip them, or build them from the recipe.
6. Merge `herdr-containers/keys.json` (`{ "global": { "ctrl-shift-y": "containers.toggle" } }`)
   into `$app_home/keys.json`. Ask before replacing a key they already use.
7. `herdr-app check "$app_home/mods/<name>"` for each copied mod (needs a display), then
   `herdr-app status`: its `errors` must be empty.

## Add a project's dev container

- Open it with VS Code or `devcontainer up`: it appears under **Dev containers**. Click the
  row: it connects and opens a space in the container's workspace folder.
- For rebuild-proof identity, add the Feature. Copy `requirements/feature/herdr-core` to the
  project's `.devcontainer/herdr-core` (never over an existing one), merge
  `"./herdr-core": { "version": "latest" }` into `features` in that `devcontainer.json`, and
  rebuild. The build downloads Herdr Core from releases.herdr.dev (installing `curl` and `tar`
  with apt or apk if missing) and keeps core state in a volume across rebuilds. Without it, a
  rebuilt container gets a new core id and needs **Repair**.

## Troubleshoot

| Symptom | Cause / fix |
|---|---|
| "hc-devcontainer is not installed (put it on PATH)" | The app can't run it. Put it in a folder on the app's PATH; reopen Herdr if you changed that PATH. |
| "… put it and hc-devcontainer.cmd on PATH" (Windows) | cmd can't find the launcher. Install `hc-devcontainer.py` and both `requirements/windows` launchers in one folder on the app's PATH; install Python 3. |
| "This Herdr Core refuses --via machines on Windows" | Released core refuses them on Windows. Use a core that accepts `--via`, or run Herdr inside WSL. |
| "cannot reach … cannot run it" on Windows | Core can't find `sh`. Put Git for Windows' `C:\Program Files\Git\bin` on core's PATH, then restart core. |
| "hc is not installed" right after adding | Core can't find `hc-devcontainer` (sh exits 127). Put it on core's PATH, restart core. |
| Hooks or dotfiles don't run, tools from `.bashrc`/nvm missing in terminals | No engine found, so the helper fell back to `docker exec`. Open VS Code once with Dev Containers (it records its engine), or install `@devcontainers/cli` with Node.js where core sees them, then restart core. Output: `~/.local/state/hc-devcontainer/<container>.log`. |
| Git push asks for credentials | The bridge answers only while Herdr is connected to that container. Check `git config --global --get-all credential.helper` in the container lists the herdr-containers helper, `dev.containers.gitCredentialHelperConfigLocation` isn't `none`, and the host's own `git credential fill` knows that host. |
| No SSH agent in the container | VS Code forwards it from its own server; this package doesn't yet. Use HTTPS (the credential bridge), or mount an agent socket in `devcontainer.json`. |
| "Docker isn't running" | Start Docker. Dev containers stay stopped: use **Start** on the row. |
| Dev container listed under Other containers | It has no folder label (`devcontainer.local_folder` or `vsch.local.folder`) and isn't a `vsc-*` image with devcontainer metadata. Start it with VS Code or `devcontainer up`. |
| Row shows **Repair** | Rebuilt without the Feature (new core id). Click Repair; add the Feature to avoid it. |
| **Gone** group | The container was deleted. **Forget** closes its spaces and removes the machine. |
| Spaces/agents show **Disconnected** | Their container or Docker is down (swaps only); they come back when it is. |
| Spaces group lingers after Disconnect; no Disconnected agents; no Settings → Containers | The swaps aren't installed (Install step 5). |
| Ctrl+Shift+Y does nothing | keys.json not merged, a renamed mod still bound as `containers.toggle`, or the block id isn't `containers`. |
| Links from a container don't open, or `localhost` links don't load | Needs this `hc-devcontainer` on core's PATH and a reconnect; `BROWSER` reaches terminals after core restarts (`hc machine update <machine>`). Forwarding needs `python3`, `bash`, or `nc` in the container. On WSL, links that fail everywhere need `wslu`. |
| Timed out / Herdr hasn't loaded it | Click the row again; Herdr's machine list lags a few seconds. |
