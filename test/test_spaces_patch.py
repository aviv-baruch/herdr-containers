import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "app-mod/patch-mods.py"
DEFAULTS = ROOT.parent / "herdr-app/defaults"
CASES = (
    ("spaces", "spaces-containers", "mods/spaces", "  grouped(t, collapsed) {", 'shared("containers.down")'),
    ("agents", "agents-containers", "mods/agents", "    let list = agents();", 'label: "Disconnected"'),
    ("frame-settings", "settings-containers", "frame/settings", 'const I = (name) => `frame/settings/${name}.svg`;', 'Show stopped dev containers'),
    ("workspace-chrome", "workspace-chrome-containers", "mods/workspace-chrome", '() => run("app.settings"))', 'mod: "settings-containers"'),
)


@unittest.skipUnless(all((DEFAULTS / rel).is_dir() for _, _, rel, _, _ in CASES),
                     f"needs shipped mods under {DEFAULTS}")
class PatchTests(unittest.TestCase):
    def run_patcher(self, source, target):
        return subprocess.run([sys.executable, "-I", str(PATCHER), str(source), str(target),
                               "herdr-app 1.2.3"], capture_output=True, text=True)

    def test_reference_applies_and_is_valid_js(self):
        for original, clone, rel, anchor, result in CASES:
            reference = DEFAULTS / rel
            with self.subTest(clone=clone), tempfile.TemporaryDirectory(dir=ROOT / "test") as temp:
                source = Path(temp) / reference.name
                target = Path(temp) / clone
                shutil.copytree(reference, source)
                target.mkdir()
                (target / "old.txt").write_text("old clone")
                run = self.run_patcher(source, target)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual([p.name for p in Path(temp).iterdir() if p.name.startswith(".")], [])
                self.assertEqual({p.relative_to(source) for p in source.rglob("*")},
                                 {p.relative_to(target) for p in target.rglob("*")})
                self.assertIn(result, (target / "main.js").read_text())
                self.assertEqual(json.loads((target / "mod.json").read_text())["cloned_from"],
                                 {"name": original, "version": "1.2.3"})
                for path in target.rglob("*.js"):
                    checked = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
                if clone == "settings-containers":
                    self.assertIn("mods/settings-containers/${name}.svg", (target / "main.js").read_text())

    def test_mangled_anchor_refuses_and_removes_clone(self):
        for original, clone, rel, anchor, result in CASES:
            reference = DEFAULTS / rel
            with self.subTest(clone=clone), tempfile.TemporaryDirectory(dir=ROOT / "test") as temp:
                source = Path(temp) / reference.name
                target = Path(temp) / clone
                shutil.copytree(reference, source)
                main = source / "main.js"
                self.assertIn(anchor, main.read_text())
                main.write_text(main.read_text().replace(anchor, "MANGLED", 1))
                target.mkdir()
                (target / "old.txt").write_text("old clone")
                run = self.run_patcher(source, target)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn(f"Warning: {clone} clone unavailable", run.stderr)
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
