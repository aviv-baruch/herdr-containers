# Herdr Containers

Add a Containers sidebar to Herdr's main workspace with dev container connections, recovery actions, and visibility settings.

![herdr-containers](herdr-containers/preview.png)

The screenshot is due before publication.

## What it does

- Adds **Dev containers**, **Other containers**, and **Gone** groups between Spaces and Agents, with counts on the group headers.
- Opens running dev containers as spaces; offers Start for stopped ones and inline confirmation before connecting a plain container.
- Keeps saved machines visible through Docker outages, and offers Repair, Disconnect, and Forget where appropriate.
- Adds **Settings → Containers** visibility switches in the sidebar footer. Herdr's original layout remains available.

## Requirements

- Herdr app 0.1.10 and its core (`hc`). The four cloned mods track this version.
- `requirements/hc-devcontainer` on the PATH seen by both app and core.
- Docker, or a Docker-compatible `podman` command on that host (`HC_DEVCONTAINER_DOCKER=podman`). A plain container needs `sh` to connect.
- Optional: the [Herdr Core Feature](requirements/feature/herdr-core/README.md) to preserve core state across dev container rebuilds.

## Install

### A. Ask an agent

Once published, ask: **“Add herdr.dev/layouts/aviv-baruch/herdr-containers.”** Until then, point the agent at this repo's `herdr-containers/` folder. It should follow Herdr's “Layouts and mods other people made” steps, starting with the [recipe](herdr-containers/RECIPE.md) and every package file. It must disclose external access, avoid overwriting names, ask before replacing a used key, check each mod, and switch the layout.

### B. Manual

Read the [recipe](herdr-containers/RECIPE.md) and inspect the package files. Its mods run `hc`, `hc-devcontainer`, and `git`, inspect Docker containers and workspace files, and the cloned Settings reads and writes the app home's `settings.json`. Connecting or repairing a container runs `hc machine add … --install`. A container without a compatible Herdr Core (plain containers, and dev containers without the Feature) gets one at `~/.local/bin/herdr-core` of the user Herdr connects as, plus an `hc` link if it has none, and that core is restarted. hc copies its own binary when the container's OS and CPU match the host's; otherwise, as on any macOS host, it first downloads the matching build of its version from releases.herdr.dev. See the recipe for the full access list.

Herdr's app home is `$HERDR_APP_HOME`, else `$XDG_CONFIG_HOME/herdr-app`, else `~/.config/herdr-app`. Check that `layouts/main-containers.json` and all five mod names are unused in that home. If any name is taken, rename the **new** layout or mod and every reference to it before copying; never overwrite an existing name. Then copy:

```sh
app_home=${HERDR_APP_HOME:-"${XDG_CONFIG_HOME:-$HOME/.config}/herdr-app"}
(
  for source in herdr-containers/layouts/*.json herdr-containers/mods/*; do
    case "$source" in
      */layouts/*) target="$app_home/layouts/${source##*/}" ;;
      *) target="$app_home/mods/${source##*/}" ;;
    esac
    if [ -e "$target" ] || [ -L "$target" ]; then
      printf 'Name clash: %s; nothing copied\n' "$target" >&2
      exit 1
    fi
  done
  mkdir -p "$app_home/layouts" "$app_home/mods"
  cp herdr-containers/layouts/main-containers.json "$app_home/layouts/"
  for mod in herdr-containers/mods/*; do cp -R "$mod" "$app_home/mods/"; done
)
```

Merge `herdr-containers/keys.json` into the app home's `keys.json`, adding `layout:main-containers` and its `ctrl-shift-y` binding. Ask before replacing a key already in use. Do not copy the recipe or preview into the app home. Check all five mods, then switch:

```sh
for name in containers spaces-containers agents-containers settings-containers workspace-chrome-containers; do
  herdr-app check "$app_home/mods/$name"
done
herdr-app do layout.switch --name main-containers
herdr-app status
```

Use the adjusted names in those commands if a clash required renaming. `herdr-app check` needs a display. To install the host helper:

```sh
mkdir -p "$HOME/.local/bin"
install -m 755 requirements/hc-devcontainer "$HOME/.local/bin/hc-devcontainer"
hc-devcontainer ls --all --json
```

Restart core if its PATH changed. For the optional Feature, from this repo root:

```sh
project=/path/to/project
mkdir -p "$project/.devcontainer"
test ! -e "$project/.devcontainer/herdr-core" && test ! -L "$project/.devcontainer/herdr-core"
cp -R requirements/feature/herdr-core "$project/.devcontainer/herdr-core"
```

Add `"features": { "./herdr-core": { "version": "latest" } }` to that project's `devcontainer.json`, then rebuild. Its [README](requirements/feature/herdr-core/README.md) explains state and version handling.

## Using it

Click a running dev container to connect and open its workspace. A stopped dev container offers **Start**; a plain container asks for **Connect** inline. **Repair** reconnects a rebuilt unreachable machine. **Gone → Forget** removes a saved machine whose container has disappeared. **× → Disconnect** closes its spaces and removes the machine while the container keeps running.

Click a group header to collapse its rows while retaining the count. Click the Containers header, or press **Ctrl+Shift+Y**, to fold the whole block. The sidebar footer's **Settings → Containers** controls visibility; palette and keyboard Settings open Herdr's original settings screen. The list refreshes every 5 seconds while open and active.

## Behavior and resilience

| Case | Behavior |
| --- | --- |
| Stop/start | Stopped dev containers stay listed; **Start** runs `docker start`. A stopped plain container with a machine stays listed too. The machine reconnects in about 2–4 s with the same core id. Agents running in the container die when it stops. |
| Docker daemon restart | Dev containers stay stopped without a restart policy; use **Start**. The machine reconnects in about 2–4 s with the same id. |
| Rebuild with Feature | The preserved state volume keeps the same core id; Herdr reconnects automatically. |
| Rebuild without Feature | Use **Repair** on the unreachable machine row. It adds the container under a spare name first, then removes the old machine and renames the new one, so a failed add leaves the old machine untouched. It opens a new space if needed. |
| Pause | **Paused** replaces the connected dot; unpause the container before opening it. |
| Deleted container | Once Docker reports the container gone, its saved machine appears under **Gone**; a connected machine never does. **Forget** immediately hides its Spaces group, closes its spaces, and removes the machine. |
| Disconnect | Click the **×** on an added container row, then confirm **Disconnect**. The row shows **Disconnecting…** and the Spaces group disappears immediately. Herdr closes its spaces first; the machine connection is removed in the background once they are gone (at most 5 s later). The container keeps running. A removal error restores the row and shows the error. |
| Docker down | Herdr marks the machine unreachable within about 2 s. Containers shows one “Docker isn't running” line and saved container machines as **Disconnected**. Spaces dims their rows and marks their machine groups disconnected; Agents puts their agents in a **Disconnected** group. After a daemon restart, use **Start** for stopped dev containers. |
| Host core restart | Machines reconnect. |
| Duplicate folder | The newest running container wins; the connector warns on stderr. |

The workspace follows the closest bind mount containing the local folder. Otherwise it uses the container's `WorkingDir`; `/` or an empty value leaves the space folder unset. Remote terminals use that user's `HOME` if available, or the core's current directory. The connector uses plain `docker exec -u USER`, so `remoteEnv` and `userEnvProbe` from `devcontainer.json` do not apply.

## For agents

Read [AGENTS.md](AGENTS.md). Task skills are in [`.agents/skills`](.agents/skills); `.claude/skills` points there.

## Maintaining

Run `sh tools/check.sh`. Its drift test compares a fresh build against the package using `../herdr-app/defaults`; CI requires that reference. For a new Herdr release, inspect its defaults, rebuild clones through the builder, repair failed anchors in `tools/patch-mods.py`, run the checks, and live-verify in a scratch app home:

```sh
python3 -I tools/build-package.py --source ../herdr-app/defaults
sh tools/check.sh
```

The builder does not change `RECIPE.md` or `preview.png`. Update both as needed and bump the release version in the changelog. The Feature version is in `requirements/feature/herdr-core/devcontainer-feature.json`; the layout's `cloned_from` tracks Herdr.

## License

Apache-2.0. Copyright 2026 Aviv Baruch. See [LICENSE](LICENSE). SPDX-License-Identifier: Apache-2.0.
