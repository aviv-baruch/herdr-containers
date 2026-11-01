#!/usr/bin/env python3
"""Update docs and the CI pin after rebuilding the Herdr clone mods."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CLONES = ("spaces", "agents", "settings", "workspace-chrome")


def staged_file(target, content, mode):
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        path.chmod(mode)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def write_all(changes):
    staged = []
    applied = []
    try:
        for target, new_text in changes.items():
            old = target.read_bytes()
            new = new_text.encode("utf-8")
            if new == old:
                continue
            mode = stat.S_IMODE(target.stat().st_mode)
            replacement = staged_file(target, new, mode)
            try:
                backup = staged_file(target, old, mode)
            except BaseException:
                replacement.unlink(missing_ok=True)
                raise
            staged.append((target, replacement, backup))
        try:
            for target, replacement, backup in staged:
                os.replace(replacement, target)
                applied.append((target, backup))
        except OSError:
            for target, backup in reversed(applied):
                os.replace(backup, target)
            raise
    finally:
        for _, replacement, backup in staged:
            replacement.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)


def retarget(root, version, sha):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"invalid version: {version!r}; expected X.Y.Z")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise ValueError("invalid SHA: expected 40 hex characters")
    sha = sha.lower()

    for name in CLONES:
        path = root / f"herdr-containers/mods/{name}-containers/mod.json"
        found = json.loads(path.read_text(encoding="utf-8"))["cloned_from"]["version"]
        if found != version:
            raise ValueError(f"{name}-containers: cloned_from.version is {found}, expected {version}")

    # CI reads this pin; GITHUB_TOKEN may not push edits under .github/workflows.
    pin_path = root / ".github/herdr-app.json"
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    old_version = pin.get("version")
    if not isinstance(old_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", old_version):
        raise ValueError("herdr-app.json: expected a version X.Y.Z")
    pin.update(version=version, sha=sha)

    changes = {pin_path: json.dumps(pin, indent=2) + "\n"}
    mention = re.compile(r"(\b(?:Herdr(?: app)?|herdr-app) v?)" + re.escape(old_version) + r"\b")
    for relative in ("README.md", "herdr-containers/RECIPE.md"):
        path = root / relative
        content = path.read_text(encoding="utf-8")
        if f"Herdr app {old_version}" not in content:
            raise ValueError(f"{relative}: missing Herdr app {old_version} anchor")
        changes[path] = mention.sub(lambda found: found.group(1) + version, content)

    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    section = re.search(r"^## \[Unreleased\]\n(?P<body>.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not section or "### Changed\n\n" not in section.group("body"):
        raise ValueError("CHANGELOG.md: missing Unreleased Changed anchor")
    line = f"- Clones rebuilt for Herdr {version}."
    body = section.group("body")
    if line not in body.splitlines():
        body = body.replace("### Changed\n\n", f"### Changed\n\n{line}\n", 1)
    changes[changelog_path] = changelog[:section.start("body")] + body + changelog[section.end("body"):]
    write_all(changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    try:
        retarget(ROOT, args.version, args.sha)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"retarget-herdr: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
