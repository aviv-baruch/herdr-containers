import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "herdr-app/defaults"
PACKAGE = ROOT / "herdr-containers"
BUILDER = ROOT / "tools/build-package.py"

spec = importlib.util.spec_from_file_location("build_package", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
READY = (SOURCE.parent / "Cargo.toml").is_file() and all(
    (SOURCE / relative).is_dir() for relative, _ in builder.CLONES
)


def files(folder):
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*")
            if path.is_file() and str(path.relative_to(folder)) not in {"RECIPE.md", "preview.png", "demo.mp4", "demo.gif"}}


def app_home(temp):
    home = Path(temp) / "app-home"
    home.mkdir()
    for relative, _ in builder.CLONES:
        shutil.copytree(SOURCE / relative, home / relative)
    return home


class BuildPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not READY:
            message = f"needs Herdr mods and Cargo.toml at {SOURCE}"
            if os.environ.get("CI"):
                raise AssertionError(message)
            raise unittest.SkipTest(message)

    def test_generated_package_has_no_drift_and_keeps_recipe(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "test") as temp:
            generated = Path(temp) / "herdr-containers"
            generated.mkdir()
            (generated / "RECIPE.md").write_text("keep recipe")
            (generated / "preview.png").write_bytes(b"keep preview")
            result = subprocess.run([sys.executable, "-I", str(BUILDER), "--source", str(SOURCE),
                                     "--output", str(generated)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((generated / "RECIPE.md").read_text(), "keep recipe")
            self.assertEqual((generated / "preview.png").read_bytes(), b"keep preview")
            self.assertFalse((generated / "layouts").exists())
            self.assertEqual(json.loads((generated / "keys.json").read_text()),
                             {"global": {"ctrl-shift-y": "containers.toggle"}})
            self.assertEqual(files(generated), files(PACKAGE))

    def test_installed_app_home_with_source_version(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "test") as temp:
            home = app_home(temp)
            output = Path(temp) / "package"
            version = builder.source_version(SOURCE, None)
            result = subprocess.run([sys.executable, "-I", str(BUILDER), "--source", str(home),
                                     "--source-version", version, "--output", str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((output / "layouts").exists())
            self.assertEqual((output / "keys.json").read_bytes(), (PACKAGE / "keys.json").read_bytes())
            for _, name in builder.CLONES:
                self.assertEqual((output / "mods" / name / "mod.json").read_bytes(),
                                 (PACKAGE / "mods" / name / "mod.json").read_bytes())

    def test_failed_anchor_names_clone_and_leaves_package_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "test") as temp:
            home = app_home(temp)
            main = home / "mods/spaces/main.js"
            main.write_text(main.read_text().replace("  grouped(t, collapsed) {", "MANGLED", 1))
            output = Path(temp) / "package"
            shutil.copytree(PACKAGE, output)
            before = files(output)
            result = subprocess.run([sys.executable, "-I", str(BUILDER), "--source", str(home),
                                     "--source-version", "1.2.3", "--output", str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("patch-mods: spaces-containers: patch anchor matched 0 times: '  grouped(t, collapsed) {",
                          result.stderr)
            self.assertIn("build-package: spaces-containers could not be built", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("non-zero exit status", result.stderr)
            self.assertEqual(files(output), before)
            self.assertEqual([p.name for p in Path(temp).iterdir() if p.name.startswith(".")], [])


class TargetVersionTests(unittest.TestCase):
    # The builder writes cloned_from on each clone; docs and CI must agree.
    def test_docs_and_ci_name_the_built_herdr_version(self):
        versions = set()
        for _, name in builder.CLONES:
            meta = json.loads((PACKAGE / "mods" / name / "mod.json").read_text())
            versions.add(meta["cloned_from"]["version"])
        self.assertEqual(len(versions), 1)
        version = versions.pop()
        mention = re.compile(r"\b(?:Herdr(?: app)?|herdr-app) v?(\d+\.\d+\.\d+)")
        docs = [ROOT / "README.md", ROOT / "AGENTS.md", PACKAGE / "RECIPE.md"]
        for path in docs:
            for found in mention.findall(path.read_text(encoding="utf-8")):
                self.assertEqual(found, version, path.relative_to(ROOT))
        for path in (ROOT / "README.md", PACKAGE / "RECIPE.md"):
            self.assertIn(f"Herdr app {version}", path.read_text(encoding="utf-8"), path.relative_to(ROOT))
        pin = json.loads((ROOT / ".github/herdr-app.json").read_text(encoding="utf-8"))
        self.assertEqual(pin["version"], version)
        self.assertRegex(pin["sha"], r"^[0-9a-f]{40}$")
        # GITHUB_TOKEN cannot push workflow edits, so the rebuild PR must not need any.
        ci = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        self.assertNotRegex(ci, r"ref: [0-9a-fA-F]{40}")
        self.assertIn(".github/herdr-app.json", ci)


if __name__ == "__main__":
    unittest.main()
