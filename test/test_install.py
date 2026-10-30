import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")


def heredoc(argument):
    pattern = rf'python3 -I - "{re.escape(argument)}" <<\'PY\'\n(.*?)\nPY'
    return re.search(pattern, SCRIPT, re.S).group(1)


def run_python(source, path, *args, env=None):
    return subprocess.run([sys.executable, "-I", "-", str(path), *args], input=source,
                          text=True, capture_output=True, check=True, env=env)


class InstallTests(unittest.TestCase):
    def main_layout(self, home):
        (home / "layouts").mkdir()
        (home / "layouts/main.json").write_text(json.dumps({"children": [{
            "id": "sidebar", "children": [{"id": "sidebar-lists", "children": [
                {"id": "spaces", "content": {"mod": "spaces"}},
                {"id": "agents", "content": {"mod": "agents"}}]},
                {"id": "sidebar-footer", "content": {"mod": "workspace-chrome"}},
                {"id": "brand", "content": {"mod": "workspace-chrome"}}]}]}))
        binary = home / "herdr-app"
        binary.write_text("#!/bin/sh\nprintf 'herdr-app 1.2.3\\n'\n")
        binary.chmod(0o755)
        return {**os.environ, "PATH": f"{home}:{os.environ['PATH']}"}

    def test_new_layout_uses_spaces_clone(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            home = Path(temp)
            env = self.main_layout(home)
            run_python(heredoc('$app_home" "$spaces_mod" "$agents_mod" "$chrome_mod'), home,
                       "spaces-containers", "agents-containers", "workspace-chrome-containers", env=env)
            layout = json.loads((home / "layouts/main-containers.json").read_text())
            blocks = layout["children"][0]["children"][0]["children"]
            self.assertEqual([block["id"] for block in blocks], ["spaces", "containers", "agents"])
            self.assertEqual(blocks[0]["content"]["mod"], "spaces-containers")
            self.assertEqual(blocks[2]["content"]["mod"], "agents-containers")
            self.assertEqual(layout["children"][0]["children"][1]["content"]["mod"], "workspace-chrome-containers")
            self.assertEqual(layout["children"][0]["children"][2]["content"]["mod"], "workspace-chrome")
            self.assertEqual(layout["cloned_from"], {"name": "main", "version": "1.2.3"})
            self.assertEqual(sorted(p.name for p in (home / "layouts").iterdir()), ["main-containers.json", "main.json"])

    def test_force_keeps_the_old_layout_aside(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            home = Path(temp)
            env = self.main_layout(home)
            (home / "layouts/main-containers.json").write_text('{"mine": true}')
            for _ in range(2):
                run_python(heredoc('$app_home" "$spaces_mod" "$agents_mod" "$chrome_mod'), home,
                           "spaces", "agents", "workspace-chrome", env=env)
            kept = sorted(p for p in (home / "layouts").iterdir() if ".old-" in p.name)
            self.assertEqual(len(kept), 2)
            self.assertEqual(json.loads(kept[0].read_text()), {"mine": True})
            self.assertIn("containers", (home / "layouts/main-containers.json").read_text())

    def test_shortcut_moves_to_the_layout_and_preserves_unrelated_binding(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            path = Path(temp) / "keys.json"
            path.write_text(json.dumps({"global": {"ctrl-shift-y": "layout.toggle:containers",
                                                  "ctrl-a": "other.action"}}))
            script = heredoc("$app_home/keys.json")
            run_python(script, path)
            expected = {"global": {"ctrl-a": "other.action"},
                        "layout:main-containers": {"ctrl-shift-y": "containers.toggle"}}
            self.assertEqual(json.loads(path.read_text()), expected)
            run_python(script, path)
            self.assertEqual(json.loads(path.read_text()), expected)
            path.write_text(json.dumps({"global": {"ctrl-shift-y": "containers.toggle"}}))
            run_python(script, path)
            self.assertEqual(json.loads(path.read_text()),
                             {"global": {}, "layout:main-containers": {"ctrl-shift-y": "containers.toggle"}})

    def test_shortcut_does_not_replace_unrelated_binding(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            path = Path(temp) / "keys.json"
            path.write_text(json.dumps({"global": {"ctrl-shift-y": "other.action"}}))
            result = run_python(heredoc("$app_home/keys.json"), path)
            self.assertIn("already bound", result.stderr)
            self.assertEqual(json.loads(path.read_text()), {"global": {"ctrl-shift-y": "other.action"}})

    def test_existing_layout_minimum_and_spaces_mod(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            path = Path(temp) / "layout.json"
            path.write_text(json.dumps({"id": "root", "children": [
                {"id": "containers", "min": 90}, {"id": "spaces", "min": 90,
                 "content": {"mod": "spaces"}}]}))
            script = heredoc('$layout" "$spaces_mod" "$agents_mod" "$chrome_mod')
            run_python(script, path, "spaces-containers", "agents-containers", "workspace-chrome-containers")
            children = json.loads(path.read_text())["children"]
            self.assertEqual(children[0]["min"], 35)
            self.assertEqual(children[1]["min"], 90)
            self.assertEqual(children[1]["content"]["mod"], "spaces-containers")
            run_python(script, path, "spaces", "agents", "workspace-chrome")
            self.assertEqual(json.loads(path.read_text())["children"][1]["content"]["mod"], "spaces")
            # Their own choices stay: a lower minimum, their own Spaces clone.
            path.write_text(json.dumps({"id": "root", "children": [
                {"id": "containers", "min": 31}, {"id": "spaces", "content": {"mod": "my-spaces"}}]}))
            before = path.read_text()
            run_python(script, path, "spaces-containers", "agents-containers", "workspace-chrome-containers")
            self.assertEqual(path.read_text(), before)

    def test_existing_layout_updates_only_our_clone_blocks(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            path = Path(temp) / "layout.json"
            path.write_text(json.dumps({"id": "root", "children": [
                {"id": "containers", "min": 35},
                {"id": "agents", "content": {"mod": "agents"}},
                {"id": "sidebar-footer", "content": {"mod": "workspace-chrome"}},
                {"id": "brand", "content": {"mod": "workspace-chrome"}},
                {"id": "spaces", "content": {"mod": "my-spaces"}},
            ]}))
            script = heredoc('$layout" "$spaces_mod" "$agents_mod" "$chrome_mod')
            run_python(script, path, "spaces", "agents-containers", "workspace-chrome-containers")
            children = json.loads(path.read_text())["children"]
            self.assertEqual([c.get("content", {}).get("mod") for c in children],
                             [None, "agents-containers", "workspace-chrome-containers", "workspace-chrome", "my-spaces"])
            run_python(script, path, "spaces", "agents", "workspace-chrome")
            children = json.loads(path.read_text())["children"]
            self.assertEqual([c.get("content", {}).get("mod") for c in children],
                             [None, "agents", "workspace-chrome", "workspace-chrome", "my-spaces"])

    def test_hidden_notice_only_when_needed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            path = Path(temp) / "state.json"
            source = heredoc("$app_home/layouts/.state/main-containers.json")
            self.assertEqual(run_python(source, path).stdout, "")
            path.write_text(json.dumps({"nodes": {"containers": {"hidden": False}}}))
            self.assertEqual(run_python(source, path).stdout, "")
            path.write_text(json.dumps({"nodes": {"containers": {"hidden": True}}}))
            self.assertIn("open Herdr and run install.sh again", run_python(source, path).stdout)


if __name__ == "__main__":
    unittest.main()
