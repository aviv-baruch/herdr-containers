import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/retarget-herdr.py"
OLD = json.loads((ROOT / "herdr-containers/mods/spaces-containers/mod.json").read_text())["cloned_from"]["version"]
NEW = ".".join((*OLD.split(".")[:2], str(int(OLD.split(".")[2]) + 1)))
OLD_SHA = re.search(r"ref: ([0-9a-f]{40}) # v" + re.escape(OLD),
                    (ROOT / ".github/workflows/check.yml").read_text()).group(1)
SHA = "a" * 40
FILES = ("README.md", "herdr-containers/RECIPE.md", ".github/workflows/check.yml", "CHANGELOG.md")
CLONES = ("spaces", "agents", "settings", "workspace-chrome")


def fixture(root):
    for relative in FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    for name in CLONES:
        path = root / f"herdr-containers/mods/{name}-containers/mod.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads((ROOT / path.relative_to(root)).read_text())
        data["cloned_from"]["version"] = NEW
        path.write_text(json.dumps(data))


def snapshot(root):
    return {relative: (root / relative).read_bytes() for relative in FILES}


def invoke(root, version=NEW, sha=SHA):
    script = root / "tools/retarget-herdr.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TOOL, script)
    return subprocess.run([sys.executable, "-I", str(script), "--version", version, "--sha", sha],
                          capture_output=True, text=True)


class RetargetHerdrTests(unittest.TestCase):
    def test_updates_docs_ci_and_unreleased_changelog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            before_ci = (root / ".github/workflows/check.yml").read_text()
            result = invoke(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Herdr app {NEW}", (root / "README.md").read_text())
            self.assertIn(f"Herdr app {NEW}", (root / "herdr-containers/RECIPE.md").read_text())
            self.assertNotIn(f"Herdr app {OLD}", (root / "README.md").read_text())
            self.assertNotIn(f"Herdr app {OLD}", (root / "herdr-containers/RECIPE.md").read_text())
            ci = (root / ".github/workflows/check.yml").read_text()
            self.assertEqual(ci, before_ci.replace(f"{OLD_SHA} # v{OLD}", f"{SHA} # v{NEW}"))
            self.assertIn(f"- Clones rebuilt for Herdr {NEW}.", (root / "CHANGELOG.md").read_text())
            after = snapshot(root)
            self.assertEqual(invoke(root).returncode, 0)
            self.assertEqual(snapshot(root), after)

    def test_invalid_arguments_and_missing_anchors_leave_every_file_unchanged(self):
        for change, version, sha, expected in (
            (None, "1.2", SHA, "invalid version"),
            (None, NEW, "short", "invalid SHA"),
            ("missing ci", NEW, SHA, "check.yml"),
            ("missing readme", NEW, SHA, "README.md"),
            ("missing recipe", NEW, SHA, "RECIPE.md"),
            ("missing changelog", NEW, SHA, "CHANGELOG.md"),
            ("wrong clone", NEW, SHA, "spaces-containers"),
        ):
            with self.subTest(change=change, version=version, sha=sha):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    fixture(root)
                    if change == "missing ci":
                        path = root / ".github/workflows/check.yml"
                        path.write_text(path.read_text().replace(f" # v{OLD}", " # no version"))
                    elif change == "missing readme":
                        path = root / "README.md"
                        path.write_text(path.read_text().replace(f"Herdr app {OLD}", "Herdr app unknown"))
                    elif change == "missing recipe":
                        path = root / "herdr-containers/RECIPE.md"
                        path.write_text(path.read_text().replace(f"Herdr app {OLD}", "Herdr app unknown"))
                    elif change == "missing changelog":
                        path = root / "CHANGELOG.md"
                        path.write_text(path.read_text().replace("### Changed", "### Different"))
                    elif change == "wrong clone":
                        path = root / "herdr-containers/mods/spaces-containers/mod.json"
                        data = json.loads(path.read_text())
                        data["cloned_from"]["version"] = OLD
                        path.write_text(json.dumps(data))
                    before = snapshot(root)
                    result = invoke(root, version, sha)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertEqual(snapshot(root), before)

    def test_write_failure_rolls_back_all_files(self):
        spec = importlib.util.spec_from_file_location("retarget_herdr", TOOL)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            before = snapshot(root)
            replace = tool.os.replace
            calls = 0

            def fail_second_replace(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated write failure")
                return replace(source, target)

            with mock.patch.object(tool.os, "replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    tool.retarget(root, NEW, SHA)
            self.assertEqual(snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
