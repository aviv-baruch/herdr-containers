# Herdr Containers

A Herdr mod that puts your Docker dev containers in the sidebar: click one and Herdr opens a space inside it, so your terminals and agents run in the container with its own shell and tools. It adds a Containers panel to the layout you already use, with Start, Repair, Disconnect and Forget, and keeps saved connections visible while Docker is down.

![Connecting two dev containers, running git status and curl inside them, folding the section](herdr-containers/demo.gif)

The demo plays at 2x speed ([MP4](herdr-containers/demo.mp4)). The still is [preview.png](herdr-containers/preview.png).

## What it does

- Adds **Dev containers**, **Other containers**, and **Gone** groups between Spaces and Agents, with counts on the group headers.
- Opens running dev containers as spaces; offers Start for stopped ones and inline confirmation before connecting a plain container.
- Keeps saved machines visible through Docker outages, and offers Repair, Disconnect, and Forget where appropriate.
- With optional swaps, adds **Settings → Containers** visibility switches in the sidebar footer.

## Requirements

- Herdr app 0.1.10 and its core (`hc`). The four cloned mods track this version.
- `requirements/hc-devcontainer` on the PATH seen by both app and core.
- Recommended, so dev containers open exactly as in VS Code: VS Code with its Dev Containers extension on this host (the helper runs the extension's own engine with VS Code's Electron; nothing else to install), or the [Dev Containers CLI](https://github.com/devcontainers/cli) (`npm install -g @devcontainers/cli`, which needs Node.js) on the PATH seen by both app and core. Without either, the helper uses plain `docker exec`. See [VS Code parity](#vs-code-parity).
- Docker, or a Docker-compatible `podman` command on that host (`HC_DEVCONTAINER_DOCKER=podman`). A plain container needs `sh` to connect.
- On Windows: Python 3 (the `py` launcher, or `python`), the two launchers in `requirements/windows/`, and for connecting, Git for Windows' `sh` and a Herdr Core that accepts `--via` there. See [Windows](#windows).
- Optional: the [Herdr Core Feature](requirements/feature/herdr-core/README.md) to preserve core state across dev container rebuilds.

## Install

### A. Ask an agent

Give the agent this exact line:

> Read ~/.config/herdr-app/AGENTS.md, then add this mod: herdr.dev/mods/aviv-baruch/herdr-containers

The agent should read the [recipe](herdr-containers/RECIPE.md) and package files, disclose external access, resolve name and key clashes, add the block to your current layout, and check each installed mod. Until the mod appears on herdr.dev, point it at this repo's `herdr-containers/` folder.

### B. Manual

Read the [recipe](herdr-containers/RECIPE.md) and inspect the five mods first. They run `hc`, `hc-devcontainer`, and `git`, inspect Docker containers and workspace files, and cloned Settings reads and writes the app home's `settings.json`. Connection or repair may install Herdr Core in the container or download a matching build from `releases.herdr.dev`. The recipe lists all external access.

Install the helper from this repository on a PATH seen by both the app and core. Restart core if its PATH changed.

```sh
mkdir -p "$HOME/.local/bin"
install -m 755 requirements/hc-devcontainer "$HOME/.local/bin/hc-devcontainer"
hc-devcontainer ls --all --json
```

On Windows, see [Windows](#windows) for the helper instead.

Herdr's app home is `$HERDR_APP_HOME`, else `$XDG_CONFIG_HOME/herdr-app`, else `~/.config/herdr-app`. Check for name clashes before copying. Rename a new mod and all its references if a name is taken; never overwrite an existing mod. The recipe's Watch out lists references inside code. From the repo root:

```sh
app_home=${HERDR_APP_HOME:-"${XDG_CONFIG_HOME:-$HOME/.config}/herdr-app"}
(
  for mod in herdr-containers/mods/*; do
    target="$app_home/mods/${mod##*/}"
    if [ -e "$target" ] || [ -L "$target" ]; then
      printf 'Name clash: %s; nothing copied\n' "$target" >&2
      exit 1
    fi
  done
  mkdir -p "$app_home/mods"
  for mod in herdr-containers/mods/*; do cp -R "$mod" "$app_home/mods/"; done
)
```

Herdr replaces its shipped layouts (`main`, `terminals`, `win98`) on every start, so edit a layout of your own. If you use a shipped one, clone it first; this switches to the clone:

```sh
herdr-app do layout.clone '{"name":"main","as":"my-layout"}'
```

Add this block after Spaces in the `sidebar-lists` column of `$app_home/layouts/<your-layout>.json` (or another suitable column). Keep id `containers` even if you rename the mod: folding resizes that id. Use the renamed mod name in `content.mod` if needed.

```json
{ "id": "containers", "type": "block", "size": { "weight": 1 }, "min": 35, "content": { "mod": "containers" } }
```

Merge [keys.json](herdr-containers/keys.json) into the app home's `keys.json`: add `"ctrl-shift-y": "containers.toggle"` under `"global"`. Ask before replacing a key already in use. If you renamed `containers`, use `<mod>.toggle`. The block also folds by clicking its header.

For full behavior, make these three edits to blocks in the same layout. The four clones are recommended; the `containers` mod works alone.

| Block id | Change its `content.mod` from | To |
| --- | --- | --- |
| `spaces` | `spaces` | `spaces-containers` |
| `agents` | `agents` | `agents-containers` |
| `sidebar-footer` | `workspace-chrome` | `workspace-chrome-containers` |

In shipped `main`, those three blocks become:

```json
[
  { "id": "spaces", "type": "block", "size": { "weight": 1 }, "min": 90,
    "content": { "mod": "spaces-containers" } },
  { "id": "agents", "type": "block", "size": { "weight": 1 }, "min": 90,
    "content": { "mod": "agents-containers" } },
  { "id": "sidebar-footer", "type": "block", "size": { "px": 69 },
    "content": { "mod": "workspace-chrome-containers", "props": { "footer": true } } }
]
```

Edit the existing blocks in place; their positions do not change. The footer opens the installed `settings-containers` mod. Keep its `"props": { "footer": true }`. Without the swaps, the shipped Spaces group can linger for 15 s after disconnect, unreachable spaces do not dim, Agents has no Disconnected group, and footer Settings has no Containers switches; both visibility options stay on. The swaps are copies of Herdr app 0.1.10's shipped mods, so compare before replacing an older or customized version. They need rebuilding and reinstalling to receive fixes from later Herdr releases.

Check each installed mod and the app status. Herdr reloads layout edits live; no layout switch is needed. `herdr-app check` needs a display.

```sh
for name in containers spaces-containers agents-containers settings-containers workspace-chrome-containers; do
  herdr-app check "$app_home/mods/$name"
done
herdr-app status
```

Use adjusted names if you renamed a mod. Leave `RECIPE.md`, `preview.png`, and `demo.*` out of the app home.

### Optional: the Herdr Core Feature

For the optional Feature, from this repo root:

```sh
project=/path/to/project
mkdir -p "$project/.devcontainer"
test ! -e "$project/.devcontainer/herdr-core" && test ! -L "$project/.devcontainer/herdr-core"
cp -R requirements/feature/herdr-core "$project/.devcontainer/herdr-core"
```

Add `"features": { "./herdr-core": { "version": "latest" } }` to that project's `devcontainer.json`, then rebuild. Its [README](requirements/feature/herdr-core/README.md) explains state and version handling. The Feature installs `curl` and `tar` with apt or apk if needed.

### Windows

Windows runs neither shebangs nor files without an extension, so the helper comes with two launchers, and the Python helper is installed as `hc-devcontainer.py` next to them:

- `hc-devcontainer.cmd`: the app runs `cmd /d /c hc-devcontainer …` to list, start and check containers.
- `hc-devcontainer`: a POSIX sh script. Core runs a connection as `sh -c '<via> "$1"'`, and on Windows that `sh` comes from Git for Windows; the launcher starts Python itself instead of relying on a `python3` shebang.

Both start the helper with `py -3`, else `python`. Install all three into one folder on the PATH of both app and core, for example the folder that holds `hc`. In PowerShell, from the repo root:

```powershell
$bin = "$env:LOCALAPPDATA\Programs\herdr-core"
Copy-Item requirements\hc-devcontainer "$bin\hc-devcontainer.py"
Copy-Item requirements\windows\hc-devcontainer, requirements\windows\hc-devcontainer.cmd $bin
hc-devcontainer ls --all --json
```

To connect, core needs `sh` on its PATH: add Git for Windows' `C:\Program Files\Git\bin`, then restart core. Core also has to accept `--via` machines on Windows, and released Herdr Core (through the version this package targets) refuses them. Until a core that accepts them ships, the panel lists, starts and checks containers on Windows, and **Connect** and **Repair** say that core refused. In the meantime you can connect by running Herdr inside WSL.

Docker Desktop labels dev containers with Windows folders (`c:\Users\…`) and reports their mounts as `/run/desktop/mnt/host/c/…`, `/host_mnt/c/…` or `/mnt/c/…`. The helper treats all of these as the same folder, ignoring case, on Windows and in WSL.

## Using it

Click a running dev container to connect and open its workspace. A stopped dev container offers **Start**; a plain container asks for **Connect** inline. **Repair** reconnects a rebuilt unreachable machine. **Gone → Forget** removes a saved machine whose container has disappeared. **× → Disconnect** closes its spaces and removes the machine while the container keeps running.

Click a group header to collapse its rows while retaining the count. Click the Containers header, or press **Ctrl+Shift+Y**, to fold the whole block. The sidebar footer's **Settings → Containers** controls visibility; palette and keyboard Settings open Herdr's original settings screen. The list refreshes every 5 seconds while open and active.

## Behavior and resilience

| Case | Behavior |
| --- | --- |
| Stop/start | Stopped dev containers stay listed; **Start** runs `devcontainer up` like VS Code's open (or `docker start` without the CLI); lifecycle commands follow on connect. A stopped plain container with a machine stays listed too. The machine reconnects in about 2–4 s with the same core id. Agents running in the container die when it stops. |
| Docker daemon restart | Dev containers stay stopped without a restart policy; use **Start**. The machine reconnects in about 2–4 s with the same id. |
| Rebuild with Feature | The preserved state volume keeps the same core id; Herdr reconnects automatically. |
| Rebuild without Feature | Use **Repair** on the unreachable machine row. It adds the container under a spare name first, then removes the old machine and renames the new one, so a failed add leaves the old machine untouched. It opens a new space if needed. |
| Pause | **Paused** replaces the connected dot; unpause the container before opening it. |
| Deleted container | Once Docker reports the container gone, its saved machine appears under **Gone**; a connected machine never does. **Forget** immediately hides its Spaces group, closes its spaces, and removes the machine. |
| Disconnect | Click the **×** on an added container row, then confirm **Disconnect**. The row shows **Disconnecting…** and the Spaces group disappears immediately. Herdr closes its spaces first; the machine connection is removed in the background once they are gone (at most 5 s later). The container keeps running. A removal error restores the row and shows the error. |
| Docker down | Herdr marks the machine unreachable within about 2 s. Containers shows one “Docker isn't running” line and saved container machines as **Disconnected**. Spaces dims their rows and marks their machine groups disconnected; Agents puts their agents in a **Disconnected** group. After a daemon restart, use **Start** for stopped dev containers. |
| Host core restart | Machines reconnect. |
| Duplicate folder | The newest running container wins; the connector warns on stderr. |

The space folder follows the closest bind mount containing the local folder. Otherwise it uses the container's `WorkingDir`; `/` or an empty value leaves the space folder unset. Remote terminals use that user's `HOME` if available, or the core's current directory. Like VS Code's terminal, it starts the user's passwd shell (interactive, not a `-l` login shell).

### VS Code parity

`hc-devcontainer` runs VS Code's own open sequence, read from the Dev Containers extension (0.471.0), through the Dev Containers CLI engine. It picks the engine in this order:

1. `HC_DEVCONTAINER_CLI`, a command line (empty means always `docker exec`).
2. VS Code's own: the extension's bundled `dist/spec-node/devContainersSpecCLI.js`, run by VS Code's Electron with `ELECTRON_RUN_AS_NODE=1`, exactly as VS Code's `devcontainer` command runs it. The extension records both paths under `globalStorage/ms-vscode-remote.remote-containers/cli-bin/` in VS Code's User folder each time it activates, so this is always the version your VS Code uses. `HC_DEVCONTAINER_VSCODE_USER` names another User folder (for example `~/.config/Code - Insiders/User`).
3. A `devcontainer` command on PATH from `@devcontainers/cli`. VS Code's **Install devcontainer CLI** command offers only `open` and `build`, so the helper never runs it itself; it uses the engine it names instead.

Each connection, including core's reconnects, is one VS Code "open":

1. `devcontainer up --expect-existing-container --skip-post-create`: runs `initializeCommand` on the host and starts the container (for Compose, its services). It uses the container's own `devcontainer.local_folder` and `devcontainer.config_file` labels and never builds or replaces a container.
2. Copies the host's `~/.ssh/known_hosts` into the remote user's home if it has none and has `ssh`.
3. Lifecycle commands with `REMOTE_CONTAINERS=true`, as VS Code splits them: `onCreateCommand` and `updateContentCommand` (through `waitFor`) block, then the connection opens and the rest runs in the background. Every command runs once when it should: create commands once per container, `postStartCommand` once per start, `postAttachCommand` on every connection.
4. Personalization, at the same point as VS Code (after `postCreateCommand`): dotfiles from your VS Code settings (`dotfiles.repository`, `dotfiles.installCommand`, `dotfiles.targetPath`). Then, unless `dev.containers.copyGitConfig` is off, the host's `~/.gitconfig` and `~/.config/git/config` are copied where the container has none, VS Code's Git config cleanup runs, and the `gpg.ssh.allowedSignersFile` is copied.
5. In the background, unless `dev.containers.githubCLILoginWithToken` is off: if the container has `gh` and it is logged out, it is logged in with the host's `gh auth token`.
6. The connection runs through `devcontainer exec`: `remoteUser`, `userEnvProbe` (a login interactive shell by default, so `.bashrc`, `.profile`, nvm and similar apply), `remoteEnv` with all its variables, `REMOTE_CONTAINERS=true`, and the workspace folder. Herdr Core and every terminal and agent it starts inherit that.

Settings come from `settings.json` in VS Code's User folder (`~/.config/Code/User` on Linux, `~/Library/Application Support/Code/User` on macOS). Without one, VS Code's defaults apply. Output goes to `${XDG_STATE_HOME:-~/.local/state}/hc-devcontainer/<container>.log`, rewritten on each connection. **Start** is step 1 alone; the rest follows when the container connects. Plain containers get steps 2, 4 (Git config only), 5 and 6, as VS Code's **Attach to Running Container** does.

`devcontainer.json` is read from the host on each connection, as VS Code reads it on each open, so `remoteEnv` edits apply on the next reconnect without a rebuild. As in VS Code, `waitFor` and other values merged into the container's metadata at build time need a rebuild. If that file is gone from this host, or `up` fails, the helper still connects with `devcontainer exec --container-id`, using the container's metadata; a warning goes to stderr.

What VS Code does that this cannot, and why:

| VS Code | Why not here |
| --- | --- |
| Git credential helper, SSH and GPG agent forwarding, the Wayland socket | VS Code serves these from its own server in the container, which reaches back to the host over its connection. Herdr Core's connection can't carry them, and this package must not change Herdr or core. The links and ports relay below shows a separate `docker exec` can, but these run the other way (container to host) and would need a listener inside the container; not done yet. Meanwhile use `gh auth` (step 5) for HTTPS Git, or mount an SSH agent socket in `mounts`. |
| `forwardPorts` forwarded before anything listens | Ports are forwarded once the container listens on them, as VS Code's automatic forwarding does (below). |
| Keeps `core.editor = code` in the copied Git config | That works only with VS Code's `code` command in the container; here the cleanup removes it like any other missing editor. |
| Stops the container when its last window closes (`shutdownAction`) | Herdr runs long-lived agents, and **Disconnect** deliberately leaves the container running. |
| Builds a missing container, and offers to rebuild when the config changes | The panel opens existing containers only; rebuild with VS Code or `devcontainer up`, then use **Repair**. |
| Per-container attach configs for **Attach to Running Container** (in VS Code's private storage) | Not read; plain containers use their image metadata. |
| Extensions, VS Code server and machine settings | Editor-only. |

Without an engine, or when it cannot run (for example `node` missing from core's PATH), the helper falls back to `docker exec`: no lifecycle commands, personalization or `userEnvProbe`, but `remoteEnv` is applied (values pass through the environment, never the command line). On Windows the helper always uses `docker exec`, since the CLI's `devcontainer.cmd` shim would make `cmd` re-parse the connection line. With the CLI, `remoteEnv` values appear on the `docker exec` command line it runs, as they do with VS Code's.

Links work like VS Code's. While Herdr is connected, the container's `BROWSER` points to a helper that opens each http(s) URL with the host's browser (`wslview` or `explorer.exe` on WSL, `xdg-open` on Linux, `open` on macOS, `explorer.exe` on Windows), unless its config sets `BROWSER` itself. Ports the container listens on (localhost or all interfaces) are forwarded to the same port on the host's `127.0.0.1`, so clicked `http://localhost:3000` links and login callbacks reach the container. A port already taken on the host is forwarded to a free one, which only links opened through `BROWSER` follow. Forwarding runs over `docker exec` and needs `python3`, `bash`, or `nc` in the container. A core that was already running keeps its old environment, so `BROWSER` arrives after the core restarts (restart the container or run `hc machine update <machine>`). On WSL, if links outside containers fail too, install `wslu` so the app's `xdg-open` reaches Windows.

## For agents

Read [AGENTS.md](AGENTS.md). Task skills are in [`.agents/skills`](.agents/skills); `.claude/skills` points there.
