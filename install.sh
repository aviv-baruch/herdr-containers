#!/bin/sh
set -eu

force=0
case ${1-} in
  '') ;;
  --force) force=1 ;;
  *) printf 'usage: %s [--force]\n' "$0" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  printf 'usage: %s [--force]\n' "$0" >&2
  exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin="$HOME/.local/bin"
app_home=${HERDR_APP_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/herdr-app}
mod="$app_home/mods/containers"
layout="$app_home/layouts/main-containers.json"

if { [ ! -e "$layout" ] || [ "$force" -eq 1 ]; } && [ ! -f "$app_home/layouts/main.json" ]; then
  printf '%s is missing; start herdr-app once to install its main layout\n' "$app_home/layouts/main.json" >&2
  exit 1
fi
if [ ! -f "$app_home/keys.json" ]; then
  printf '%s is missing; start herdr-app once to install its keys\n' "$app_home/keys.json" >&2
  exit 1
fi

# Through a hidden file and a rename: the app reloads mods on save, and a dial may be reading the helper.
put() {
  tmp="$(dirname -- "$2")/.$(basename -- "$2").new"
  cp "$1" "$tmp"
  chmod "$3" "$tmp"
  mv -f "$tmp" "$2"
}
# Best effort: check needs a display; without one it is skipped.
check() {
  { [ -n "${WAYLAND_DISPLAY-}${DISPLAY-}" ] && command -v herdr-app >/dev/null 2>&1; } || return 0
  out=$(herdr-app check "$1" 2>&1) && return 0
  printf 'Warning: herdr-app check %s failed:\n%s\n' "$1" "$out" >&2
  return 1
}
# herdr-app do prints its error on stdout.
app_do() {
  out=$(herdr-app do "$1" "$2") || { printf 'herdr-app do %s failed: %s\n' "$1" "$out" >&2; exit 1; }
}

mkdir -p "$bin" "$mod" "$app_home/layouts"
put "$root/bin/hc-devcontainer" "$bin/hc-devcontainer" 755
put "$root/app-mod/containers/main.js" "$mod/main.js" 644
put "$root/app-mod/containers/mod.json" "$mod/mod.json" 644
check "$mod" || true
clone_mod() {
  source=$1
  name=$2
  fallback=$3
  target="$app_home/mods/$name"
  if python3 -I "$root/app-mod/patch-mods.py" "$source" "$target" && check "$target"; then
    printf '%s\n' "$name"
  else
    rm -rf "$target"
    printf '%s\n' "$fallback"
  fi
}
spaces_mod=$(clone_mod "$app_home/mods/spaces" spaces-containers spaces)
agents_mod=$(clone_mod "$app_home/mods/agents" agents-containers agents)
settings_mod=$(clone_mod "$app_home/frame/settings" settings-containers frame-settings)
chrome_mod=workspace-chrome
if [ "$settings_mod" = settings-containers ]; then
  chrome_mod=$(clone_mod "$app_home/mods/workspace-chrome" workspace-chrome-containers workspace-chrome)
else
  rm -rf "$app_home/mods/workspace-chrome-containers"
fi

if [ ! -e "$layout" ] || [ "$force" -eq 1 ]; then
  python3 -I - "$app_home" "$spaces_mod" "$agents_mod" "$chrome_mod" <<'PY'
import json
import os
import pathlib
import re
import subprocess
import sys
import time

home = pathlib.Path(sys.argv[1])
source = home / "layouts/main.json"
target = home / "layouts/main-containers.json"
if not source.is_file():
    raise SystemExit(f"{source} is missing; start herdr-app once to install its main layout")
version = subprocess.run(["herdr-app", "--version"], capture_output=True, text=True, check=True).stdout.strip()
match = re.fullmatch(r"herdr-app (.+)", version)
if not match:
    raise SystemExit(f"unexpected herdr-app version: {version!r}")
tree = json.loads(source.read_text(encoding="utf-8"))
sidebar = next(child for child in tree["children"] if child.get("id") == "sidebar")
lists = next(child for child in sidebar["children"] if child.get("id") == "sidebar-lists")
children = lists["children"]
spaces = next(i for i, child in enumerate(children) if child.get("id") == "spaces")
if children[spaces + 1].get("id") != "agents":
    raise SystemExit("main layout no longer has adjacent spaces and agents blocks")
children[spaces]["content"]["mod"] = sys.argv[2]
children[spaces + 1]["content"]["mod"] = sys.argv[3]
children.insert(spaces + 1, {
    "id": "containers", "type": "block", "size": {"weight": 1}, "min": 35,
    "content": {"mod": "containers"},
})
tree["cloned_from"] = {"name": "main", "version": match.group(1)}
def find(node, name):
    if node.get("id") == name:
        return node
    for child in node.get("children", []):
        found = find(child, name)
        if found:
            return found
    return None
footer = find(tree, "sidebar-footer")
if footer and footer.get("content", {}).get("mod") == "workspace-chrome":
    footer["content"]["mod"] = sys.argv[4]
tmp = target.with_name(f".{target.name}.new")
tmp.write_text(json.dumps(tree, indent=2) + "\n", encoding="utf-8")
if target.exists():  # --force: keep theirs aside, as Herdr's layout.reset does
    stamp = int(time.time())
    aside = next(p for i in range(1, 1000) if not (p := target.with_name(
        f"{target.name}.old-{stamp}" + (f"-{i}" if i > 1 else ""))).exists())
    target.rename(aside)
    print(f"Kept the old layout as {aside}")
os.replace(tmp, target)
PY
fi

python3 -I - "$layout" "$spaces_mod" "$agents_mod" "$chrome_mod" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
tree = json.loads(path.read_text(encoding="utf-8"))
def find(node, name):
    if node.get("id") == name:
        return node
    for child in node.get("children", []):
        found = find(child, name)
        if found:
            return found
    return None
block = find(tree, "containers")
if block is None:
    raise SystemExit("main-containers layout has no containers block")
spaces = find(tree, "spaces")
if spaces is None:
    raise SystemExit("main-containers layout has no spaces block")
changed = False
# Collapsed is 35 px; a lower minimum someone set is theirs.
if block.get("min") is None or block["min"] > 35:
    block["min"] = 35
    changed = True
# Only ours: a block pointed at their own Spaces clone stays.
if spaces.get("content", {}).get("mod") in ("spaces", "spaces-containers") and spaces["content"]["mod"] != sys.argv[2]:
    spaces["content"]["mod"] = sys.argv[2]
    changed = True
for name, mods, wanted in (
    ("agents", ("agents", "agents-containers"), sys.argv[3]),
    ("sidebar-footer", ("workspace-chrome", "workspace-chrome-containers"), sys.argv[4]),
):
    item = find(tree, name)
    if item and item.get("content", {}).get("mod") in mods and item["content"]["mod"] != wanted:
        item["content"]["mod"] = wanted
        changed = True
if changed:
    tmp = path.with_name(f".{path.name}.new")
    tmp.write_text(json.dumps(tree, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
PY

python3 -I - "$app_home/keys.json" <<'PY'
import json
import os
import pathlib
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
keys = json.loads(path.read_text(encoding="utf-8"))
shortcut = "ctrl-shift-y"
action = "containers.toggle"
old = "layout.toggle:containers"
# Commands reach shown mods only, so bind where the block shows; global would swallow the key elsewhere.
scope = "layout:main-containers"
changed = False
for section in keys.values():
    if not isinstance(section, dict):
        continue
    for key, value in section.items():
        if value == old:
            section[key] = action
            changed = True
if isinstance(keys.get("global"), dict) and keys["global"].get(shortcut) == action:
    del keys["global"][shortcut]
    changed = True
sections = [section for section in keys.values() if isinstance(section, dict)]
if not any(action in section.values() for section in sections):
    if any(shortcut in (keys.get(name) or {}) for name in ("global", "terminal", scope)):
        print(f"{shortcut} is already bound; add {action} to keys.json yourself", file=sys.stderr)
    else:
        keys.setdefault(scope, {})[shortcut] = action
        changed = True
if changed:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        json.dump(keys, out, indent=2)
        out.write("\n")
        name = out.name
    os.chmod(name, path.stat().st_mode & 0o777)
    os.replace(name, path)
PY

if herdr-app status >/dev/null 2>&1; then
  app_do layout.switch '{"name":"main-containers"}'
  app_do layout.show '{"id":"containers"}'
  printf 'Installed Containers sidebar and switched to main-containers.\n'
else
  python3 -I - "$app_home/shared.json" <<'PY'
import json
import os
import pathlib
import tempfile
import sys

path = pathlib.Path(sys.argv[1])
values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
values["layout"] = "main-containers"
with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
    json.dump(values, out)
    out.write("\n")
    name = out.name
os.replace(name, path)
PY
  printf 'Installed Containers sidebar. Open Herdr to use it.\n'
  python3 -I - "$app_home/layouts/.state/main-containers.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("nodes", {}).get("containers", {}).get("hidden") is True:
    print("Containers is hidden; open Herdr and run install.sh again")
PY
fi
