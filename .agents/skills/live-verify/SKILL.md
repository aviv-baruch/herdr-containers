---
name: live-verify
description: Maintainer checks that the herdr-containers mod actually works in Herdr and against real Docker containers. Use before a release, after rebuilding clones, or when changing mod behavior — scratch app-home install into a user layout, herdr-app check of all five mods, an isolated scratch window and core, screenshots, and hc resilience checks.
---

# Live verify

Never touch the person's real core, app home, window, containers, or running agents without
their OK. Installing into their real app home is the `herdr-containers` skill, with their OK.

## Isolation (read first)

A previous agent leaked a machine into the real core by running one command without the
scratch env. A scratch window or core must never reach the real ones:

- Run **every** `herdr-app` and `hc` command through the scratch wrapper (`"$S/v" hc …`),
  including `herdr-app check`, `status`, `shot` and `do`. A plain `hc` autostarts and talks to
  the real core; a plain `herdr-app do` drives the real window. Shells don't persist between
  tool calls: note `$S`.
- The only plain `hc` call allowed is the real core's read-only `hc machine ls`, before and
  after (the real core is normally running; if not, skip it and say so, since `hc` would start
  it). Never run `hc server status` (it also autostarts) or any `hc server …` on the real
  session.
- The scratch Docker wrapper lists only containers labelled `herdr.verify=1`, so the scratch
  panel can't show or connect the person's containers. Label every throwaway container.

```sh
S=$(mktemp -d /tmp/hv.XXXXXX)   # short path: socket paths are capped at 107 bytes (103 on macOS)
mkdir -m 700 "$S/run"; mkdir -p "$S/home/layouts" "$S/home/mods" "$S/state" "$S/config"
cat > "$S/docker" <<'EOF'
#!/bin/sh
# Scratch Docker: ps lists only throwaway containers; everything else passes through.
if [ "$1" = ps ]; then shift; exec docker ps --filter label=herdr.verify=1 "$@"; fi
exec docker "$@"
EOF
cat > "$S/env" <<EOF
export HERDR_APP_HOME=$S/home HC_SESSION=${S##*/} HC_DEVCONTAINER_DOCKER=$S/docker
export XDG_RUNTIME_DIR=$S/run XDG_STATE_HOME=$S/state XDG_CONFIG_HOME=$S/config
${WAYLAND_DISPLAY:+export WAYLAND_DISPLAY=$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY}
EOF
printf '#!/bin/sh\n. %s/env\nexec "$@"\n' "$S" > "$S/v"
chmod 755 "$S/docker" "$S/v"
hc machine ls --json | python3 -I -c 'import json,sys; print(sorted(m["name"] for m in json.load(sys.stdin)["machines"]))' > "$S/real-machines"
```

The custom `XDG_RUNTIME_DIR` keeps the scratch core off the systemd user manager and away from
the real socket; the scratch `XDG_CONFIG_HOME` keeps a `socket` set in the real
`herdr-core/config.toml` from applying; `WAYLAND_DISPLAY` becomes absolute
(`/run/user/1000/wayland-0`) so the window and `herdr-app check` still find the display. For
podman, make the wrapper call `podman` and restore the real `XDG_RUNTIME_DIR` for it.

## 1. Scratch install (always)

From the repo root. The clones point at shipped assets and overlays (recipe, Watch out), so
the scratch home gets Herdr's shipped mods and frame first, from the same version's defaults,
and a user layout cloned from shipped `main`:

```sh
D=../herdr-app/defaults H=$S/home
cp -R "$D/mods/." "$H/mods/"; cp -R "$D/frame" "$H/frame"
cp "$D/layouts/main.json" "$H/layouts/my-layout.json"
cp -R herdr-containers/mods/. "$H/mods/"
python3 -I -c 'import json,sys; k=json.load(open(sys.argv[1])); [k.setdefault(s, {}).update(v) for s, v in json.load(open(sys.argv[2])).items()]; json.dump(k, open(sys.argv[3], "w"), indent=2)' "$D/keys.json" herdr-containers/keys.json "$H/keys.json"
cat > "$S/fit.py" <<'EOF'
import json, sys
path, swaps = sys.argv[1], sys.argv[2:] == ["swaps"]
layout = json.load(open(path))
def walk(node):
    yield node
    for child in node.get("children", []):
        yield from walk(child)
nodes = {node.get("id"): node for node in walk(layout)}
if "containers" not in nodes:
    lists = nodes["sidebar-lists"]["children"]
    lists.insert([b.get("id") for b in lists].index("spaces") + 1, {"id": "containers", "type": "block",
                 "size": {"weight": 1}, "min": 35, "content": {"mod": "containers"}})
if swaps:  # the footer keeps its props
    for id, mod in (("spaces", "spaces-containers"), ("agents", "agents-containers"),
                    ("sidebar-footer", "workspace-chrome-containers")):
        nodes[id]["content"]["mod"] = mod
json.dump(layout, open(path, "w"), indent=2)
EOF
python3 -I "$S/fit.py" "$H/layouts/my-layout.json"   # adds the block after Spaces
for m in containers spaces-containers agents-containers settings-containers workspace-chrome-containers; do
  "$S/v" herdr-app check "$H/mods/$m"
done
```

All five must print `check passed`. `herdr-app check` needs a display. Then optionally the
swaps: `python3 -I "$S/fit.py" "$H/layouts/my-layout.json" swaps`.

## 2. Scratch window (needs the person's display)

Tell the person a scratch Herdr window will open. Start `"$S/v" herdr-app` in the background
(it starts the scratch core), then:

```sh
"$S/v" herdr-app do layout.switch --name my-layout
"$S/v" herdr-app status            # layout my-layout, errors empty
"$S/v" hc server status            # the scratch core
"$S/v" herdr-app shot "$S/shot.png"
```

The shot is the last drawn frame: ask the person to focus the scratch window first, or it may
be stale. Check: Containers between Spaces and Agents, counts on group headers only, rows
hover, headers don't, Ctrl+Shift+Y and the heading fold it to 35 px; with swaps, footer
Settings → Containers switches work. Rerun `fit.py … swaps` with the window open: the layout
reloads live.

## 3. Resilience (scratch core)

A throwaway dev container from `test/.devcontainer` (`test/sync-feature.sh` first) and a plain
container, both labelled:

```sh
P=$S/project; mkdir "$P"; cp -R test/.devcontainer "$P/"
python3 -I -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["runArgs"]=["--label","herdr.verify=1"]; json.dump(d, open(p, "w"), indent=2)' "$P/.devcontainer/devcontainer.json"
devcontainer up --workspace-folder "$P"
docker run -d --label herdr.verify=1 --name verify-plain alpine sleep infinity
```

Connect them from the scratch panel (or `"$S/v" hc machine add --name <n> --via '<via>' --install`,
`via` from `"$S/v" hc-devcontainer ls --all --json`), then act only on these containers and
check with `"$S/v" hc machine ls`:

- `docker stop` / `start`, `docker restart` → machine unreachable, then connected, same id
- `devcontainer up --workspace-folder "$P" --remove-existing-container` (with Feature) → same
  core id
- scratch core restart (`"$S/v" hc server restart`) → reconnects
- Docker daemon down → needs the person's sudo in their own terminal, and stops their real
  containers too: ask first

## 4. Clean up

Always, even after step 1 alone (`herdr-app check` may have started the scratch core). After
`hc server stop`, run no more `"$S/v" hc`: it would start the scratch core again.

```sh
"$S/v" hc machine ls               # then "$S/v" hc machine rm <name> for each
"$S/v" herdr-app do window.close   # if a scratch window is open
"$S/v" hc server stop
for c in $(docker ps -aq --filter label=herdr.verify=1); do
  docker inspect -f '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}' "$c" >> "$S/volumes"
  docker rm -f "$c"
done
docker volume rm $(cat "$S/volumes")   # the Feature's herdr-core-state-* volume
hc machine ls --json | python3 -I -c 'import json,sys; print(sorted(m["name"] for m in json.load(sys.stdin)["machines"]))' | diff "$S/real-machines" -
grep -lsF "HC_SESSION=${S##*/}" /proc/[0-9]*/environ   # Linux: no scratch core, holder or window left
rm -rf "$S"
```

Also remove the `vsc-*` image `devcontainer up` built for `$P`. The machine diff must be empty:
a new name means a leak into the real core; tell the person and remove it with their OK. If any
command ran without the env, also remove `~/.local/state/herdr-core/<session>` and
`/run/user/$(id -u)/herdr-core/<session>.*` for the scratch session name.
