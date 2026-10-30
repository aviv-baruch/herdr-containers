#!/usr/bin/env python3
"""Clone installed Herdr mods and apply small, anchored sidebar patches."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def once(source, old, new):
    count = source.count(old)
    if count != 1:
        raise ValueError(f"patch anchor matched {count} times: {old[:70]!r}")
    return source.replace(old, new, 1)


def spaces(source):
    source = once(source, "    this.machines = [];\n",
                  '    this.machines = [];\n    this.lastContainers = JSON.stringify([shared("containers.removing") ?? [], shared("containers.down") ?? []]);\n')
    source = once(source, "  checkNew(cx) {\n    const now = Date.now();\n",
                  '  checkNew(cx) {\n    const containers = JSON.stringify([shared("containers.removing") ?? [], shared("containers.down") ?? []]);\n'
                  '    if (containers !== this.lastContainers) {\n      this.lastContainers = containers;\n      this.refreshMachines(cx);\n    }\n    const now = Date.now();\n')
    source = once(source, "  grouped(t, collapsed) {\n    const rows = this.ordered();\n",
                  '  grouped(t, collapsed) {\n    const removing = new Set(shared("containers.removing") ?? []);\n'
                  '    const down = new Set(shared("containers.down") ?? []);\n'
                  '    const rows = this.ordered().filter(r => !removing.has(r.w.machine));\n')
    source = once(source,
                  '    const remote = [...new Set([...rows.map(r => r.w.machine).filter(Boolean), ...this.machines.map(m => m.name)])].sort();\n',
                  '    const remote = [...new Set([...rows.map(r => r.w.machine).filter(Boolean), ...this.machines.map(m => m.name)])].filter(m => !removing.has(m)).sort();\n')
    source = once(source, '      const connected = !m || machine?.state === "connected";\n',
                  '      const connected = !down.has(m) && (!m || machine?.state === "connected");\n')
    source = once(source, '(machine?.state ?? "asleep").replace(/_/g, " ")',
                  '(down.has(m) ? "disconnected" : machine?.state ?? "asleep").replace(/_/g, " ")')
    source = once(source, '    const on = w.active;\n',
                  '    const on = w.active;\n    const disconnected = (shared("containers.down") ?? []).includes(w.machine);\n')
    source = once(source, '      .when(on, (el) => el.bg(t.selected))\n',
                  '      .when(on, (el) => el.bg(t.selected))\n      .when(disconnected, (el) => el.text_color(t.dim))\n')
    source = once(source, '.max_w(150).truncate().text_color(t.foreground).child(w.name)',
                  '.max_w(150).truncate().text_color(disconnected ? t.dim : t.foreground).child(w.name)')
    source = once(source, '.rounded(5).bg(fill).items_center().justify_center().text_size(10.5).font_bold().text_color("#ffffff")',
                  '.rounded(5).bg(disconnected ? t.raised : fill).items_center().justify_center().text_size(10.5).font_bold().text_color(disconnected ? t.dim : "#ffffff")')
    source = once(source, '      .child(div().text_color(w.active ? t.foreground : t.muted).child(String(i + 1)))',
                  '      .child(div().text_color((shared("containers.down") ?? []).includes(w.machine) ? t.dim : w.active ? t.foreground : t.muted).child(String(i + 1)))')
    source = once(source, '      .child(div().text_color(tone(t, w.status)).child(glyph(w.status)));',
                  '      .child(div().text_color((shared("containers.down") ?? []).includes(w.machine) ? t.dim : tone(t, w.status)).child(glyph(w.status)));')
    source = once(source, '  gitTail(g, tree, name, t) {',
                  '  gitTail(g, tree, name, t, disconnected = false) {')
    source = once(source, '.when(g.ahead > 0, (el) => el.child(div().flex_none().text_color(t.ansi2).child(`\\u2191${g.ahead}`)))',
                  '.when(g.ahead > 0, (el) => el.child(div().flex_none().text_color(disconnected ? t.dim : t.ansi2).child(`\\u2191${g.ahead}`)))')
    source = once(source, '.when(g.behind > 0, (el) => el.child(div().flex_none().text_color(t.ansi1).child(`\\u2193${g.behind}`)))',
                  '.when(g.behind > 0, (el) => el.child(div().flex_none().text_color(disconnected ? t.dim : t.ansi1).child(`\\u2193${g.behind}`)))')
    source = once(source, 'g ? this.gitTail(g, tree, w.name, t) : div().flex_1()',
                  'g ? this.gitTail(g, tree, w.name, t, disconnected) : div().flex_1()')
    return source


def agents(source):
    source = once(source, '    let list = agents();\n',
                  '    let list = agents();\n    const down = new Set(shared("containers.down") ?? []);\n'
                  '    const spaceMachine = new Map(spaces().map((s) => [s.id, s.machine]));\n'
                  '    const disconnected = (a) => down.has(spaceMachine.get(a.space));\n')
    source = once(source, '    const color = t[a.status] ?? t.dim;\n',
                  '    if (a.status === "disconnected") return div().flex_none().text_size(10.5).text_color(t.dim).child("disconnected");\n'
                  '    const color = t[a.status] ?? t.dim;\n')
    source = once(source, '      .child(this.status(t, { status: st }, compact));\n',
                  '      .child(this.status(t, { status: s.disconnected ? "disconnected" : st }, compact));\n')
    source = once(source, '.text_color(on ? t.foreground : t.muted).child(k.name || k.template || k.program?.name || k.address)',
                  '.text_color(s.disconnected ? t.dim : on ? t.foreground : t.muted).child(k.name || k.template || k.program?.name || k.address)')
    source = once(source, '      return [r, ...subs.map((s) => this.subRow(t, s, shown, byPriority))];\n',
                  '      return [r, ...subs.map((s) => this.subRow(t, { ...s, disconnected: disconnected(a) }, shown, byPriority))];\n')
    source = once(source, '      const sts = subs.map((s) => kidStatus(s.t, this.listed));\n',
                  '      const sts = disconnected(a) ? [] : subs.map((s) => kidStatus(s.t, this.listed));\n')
    source = once(source, '    const rankOf = (a) => urgency.get(a.address);\n',
                  '    const rankOf = (a) => disconnected(a) ? 0 : urgency.get(a.address);\n')
    source = once(source, '    const rest = list.filter((a) => !this.pins.includes(a.address));\n',
                  '    const rest = list.filter((a) => !this.pins.includes(a.address) && !disconnected(a));\n'
                  '    const away = list.filter(disconnected);\n')
    source = once(source, '    const pinned = this.pins.map((p) => list.find((a) => a.address === p)).filter(Boolean);\n',
                  '    const pinned = this.pins.map((p) => list.find((a) => a.address === p && !disconnected(a))).filter(Boolean);\n')
    source = once(source, '    list = [...pinned, ...rest];\n',
                  '    list = [...pinned, ...rest, ...away];\n')
    source = once(source, '      const pic = icon(a.program, 16, t.foreground);\n',
                  '      const pic = icon(a.program, 16, disconnected(a) ? t.dim : t.foreground);\n')
    source = once(source, '          .child(div().flex_none().max_w(full && more ? 120 : 200).truncate().text_color(idle ? t.muted : t.foreground).child(name))\n',
                  '          .child(div().flex_none().max_w(full && more ? 120 : 200).truncate().text_color(disconnected(a) ? t.dim : idle ? t.muted : t.foreground).child(name))\n')
    source = once(source, '        .child(this.status(t, a, byPriority && !isPinned));\n',
                  '        .child(this.status(t, disconnected(a) ? { status: "disconnected" } : a, byPriority && !isPinned));\n')
    source = once(source, '    // A group\'s header; it folds the group. Folded, it says how many and the most urgent state.\n',
                  '    if (away.length) groups.push({ id: "disconnected", label: "Disconnected", status: null, members: away });\n\n'
                  '    // A group\'s header; it folds the group. Folded, it says how many and the most urgent state.\n')
    source = once(source, '      const top = g.members.reduce((m, a) => ((RANK[a.status] ?? 0) > (RANK[m] ?? 0) ? a.status : m), null);\n',
                  '      const top = g.id === "disconnected" ? null : g.members.reduce((m, a) => ((RANK[a.status] ?? 0) > (RANK[m] ?? 0) ? a.status : m), null);\n')
    return source


def settings(source):
    source = once(source, 'const I = (name) => `frame/settings/${name}.svg`;',
                  'const I = (name) => `mods/settings-containers/${name}.svg`;')
    source = once(source, 'props, closeOverlay } from "herdr";',
                  'props, closeOverlay, setShared } from "herdr";')
    source = once(source, '  { id: "machines", title: "Machines", icon: "server" },\n',
                  '  { id: "machines", title: "Machines", icon: "server" },\n'
                  '  { id: "containers", title: "Containers", icon: "server" },\n')
    source = once(source, '  // Alerts ---------------------------------------------------------------\n',
                  '  // Containers -----------------------------------------------------------\n\n'
                  '  containers(t) {\n'
                  '    const choice = (key, label) => {\n'
                  '      const on = shared(`containers.${key}`) ?? true;\n'
                  '      return this.row(t, label, "", this.toggle(t, `st-containers-${key}`, on, () => setShared(`containers.${key}`, !on)));\n'
                  '    };\n'
                  '    return v_flex().w_full().child(this.heading(t, "Containers"))\n'
                  '      .child(choice("showOther", "Show other containers"))\n'
                  '      .child(choice("showStopped", "Show stopped dev containers"));\n'
                  '  }\n\n  // Alerts ---------------------------------------------------------------\n')
    source = once(source, '                      : this.section === "alerts"\n',
                  '                      : this.section === "containers"\n'
                  '                        ? this.containers(t)\n'
                  '                      : this.section === "alerts"\n')
    return source


def chrome(source):
    source = once(source, 'shared, setShared, updates, layout } from "herdr";',
                  'shared, setShared, updates, layout, openOverlay } from "herdr";')
    source = once(source, '() => run("app.settings"))',
                  '() => openOverlay({ mod: "settings-containers", kind: "dialog", width: 880, props: { section: null } }))')
    return source


PATCHES = {
    "spaces-containers": (spaces, "spaces"),
    "agents-containers": (agents, "agents"),
    "settings-containers": (settings, "frame-settings"),
    "workspace-chrome-containers": (chrome, "workspace-chrome"),
}


def rewrite(text, old, new):
    text = text.replace(f"mods/{old}/", f"mods/{new}/")
    text = text.replace(f'"mods/{old}"', f'"mods/{new}"')
    text = text.replace(f"'mods/{old}'", f"'mods/{new}'")
    for key in ("mod", '"mod"', "'mod'"):
        for space in ("", " "):
            for quote in ('"', "'"):
                text = text.replace(f"{key}:{space}{quote}{old}{quote}", f"{key}:{space}{quote}{new}{quote}")
    return text


def clone(source, target, version):
    patch, original = PATCHES[target.name]
    if not source.is_dir():
        raise ValueError(f"installed {original} mod is missing: {source}")
    match = re.fullmatch(r"herdr-app (.+)", version)
    if not match:
        raise ValueError(f"unexpected herdr-app version: {version!r}")
    with tempfile.TemporaryDirectory(prefix=f".{target.name}-", dir=target.parent) as scratch:
        staged = Path(scratch) / target.name
        shutil.copytree(source, staged)
        for path in staged.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            rewritten = rewrite(text, source.name, target.name)
            if rewritten != text:
                path.write_text(rewritten, encoding="utf-8")
        metadata = staged / "mod.json"
        meta = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
        if not isinstance(meta, dict):
            raise ValueError("mod.json must be an object")
        meta["cloned_from"] = {"name": original, "version": match.group(1)}
        metadata.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        main = staged / "main.js"
        main.write_text(patch(main.read_text(encoding="utf-8")), encoding="utf-8")
        if target.exists():
            target.rename(Path(scratch) / "old")
        staged.rename(target)


def main():
    source, target = map(Path, sys.argv[1:3])
    try:
        version = sys.argv[3] if len(sys.argv) > 3 else subprocess.run(
            ["herdr-app", "--version"], capture_output=True, text=True, check=True).stdout.strip()
        clone(source, target, version)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        if target.exists():
            shutil.rmtree(target)
        print(f"Warning: {target.name} clone unavailable ({error}); using shipped mod", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
