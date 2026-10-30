#!/usr/bin/env python3
"""Build the shareable layout from one Herdr app source."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "herdr-app/defaults"
CONTAINERS = ROOT / "herdr-containers/mods/containers"
CLONES = (
    ("mods/spaces", "spaces-containers"),
    ("mods/agents", "agents-containers"),
    ("frame/settings", "settings-containers"),
    ("mods/workspace-chrome", "workspace-chrome-containers"),
)


def source_version(source, supplied):
    manifest = source.parent / "Cargo.toml" if source.name == "defaults" else source / "Cargo.toml"
    found = None
    if manifest.is_file():
        with manifest.open("rb") as stream:
            found = tomllib.load(stream).get("package", {}).get("version")
    if found and supplied and found != supplied:
        raise ValueError(f"source is {found}, not {supplied}")
    version = found or supplied
    if not version or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("source has no version; pass --source-version for an installed app home")
    return version


def find(node, wanted):
    if node.get("id") == wanted:
        return node
    for child in node.get("children", []):
        found = find(child, wanted)
        if found is not None:
            return found
    return None


def layout_from(source, version):
    tree = json.loads((source / "layouts/main.json").read_text(encoding="utf-8"))
    lists = find(tree, "sidebar-lists")
    if lists is None:
        raise ValueError("main layout has no sidebar-lists")
    children = lists["children"]
    positions = [i for i, block in enumerate(children) if block.get("id") == "spaces"]
    if len(positions) != 1 or positions[0] + 1 >= len(children) or children[positions[0] + 1].get("id") != "agents":
        raise ValueError("main layout no longer has adjacent spaces and agents blocks")
    i = positions[0]
    for block, original, replacement in (
        (children[i], "spaces", "spaces-containers"),
        (children[i + 1], "agents", "agents-containers"),
    ):
        if block.get("content", {}).get("mod") != original:
            raise ValueError(f"{block['id']} no longer uses {original}")
        block["content"]["mod"] = replacement
    children.insert(i + 1, {
        "id": "containers", "type": "block", "size": {"weight": 1}, "min": 35,
        "content": {"mod": "containers"},
    })
    footer = find(tree, "sidebar-footer")
    brand = find(tree, "brand")
    if footer is None or footer.get("content", {}).get("mod") != "workspace-chrome":
        raise ValueError("main layout footer no longer uses workspace-chrome")
    if brand is None or brand.get("content", {}).get("mod") != "workspace-chrome":
        raise ValueError("main layout brand no longer uses workspace-chrome")
    footer["content"]["mod"] = "workspace-chrome-containers"
    tree["cloned_from"] = {"name": "main", "version": version}
    return tree


def build(source, output, version):
    if not CONTAINERS.is_dir():
        raise ValueError(f"missing containers mod: {CONTAINERS}")
    for relative, _ in CLONES:
        if not (source / relative).is_dir():
            raise ValueError(f"missing source mod: {source / relative}")
    tree = layout_from(source, version)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".herdr-containers-", dir=output.parent) as scratch:
        staged = Path(scratch)
        (staged / "layouts").mkdir()
        (staged / "mods").mkdir()
        shutil.copytree(CONTAINERS, staged / "mods/containers")
        patcher = ROOT / "tools/patch-mods.py"
        for relative, name in CLONES:
            # patch-mods prints its own error, naming the clone and the anchor.
            patched = subprocess.run([sys.executable, "-I", str(patcher), str(source / relative),
                                      str(staged / "mods" / name), f"herdr-app {version}"])
            if patched.returncode != 0:
                raise ValueError(f"{name} could not be built from {source / relative}; {output} was not changed")
        (staged / "layouts/main-containers.json").write_text(
            json.dumps(tree, indent=2) + "\n", encoding="utf-8")
        (staged / "keys.json").write_text(json.dumps({
            "layout:main-containers": {"ctrl-shift-y": "containers.toggle"},
        }, indent=2) + "\n", encoding="utf-8")
        output.mkdir(parents=True, exist_ok=True)
        for relative in ("layouts/main-containers.json", "keys.json", "mods/containers",
                         *(f"mods/{name}" for _, name in CLONES)):
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            shutil.move(str(staged / relative), str(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Herdr defaults directory or installed app home")
    parser.add_argument("--source-version", help="version of an installed app home without Cargo.toml")
    parser.add_argument("--output", type=Path, default=ROOT / "herdr-containers",
                        help="layout package directory")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if not (source / "layouts/main.json").is_file() and (source / "defaults/layouts/main.json").is_file():
        source /= "defaults"
    try:
        build(source, args.output.expanduser().resolve(), source_version(source, args.source_version))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"build-package: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
