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
READY = (SOURCE / "layouts/main.json").is_file() and (SOURCE.parent / "Cargo.toml").is_file()

spec = importlib.util.spec_from_file_location("build_package", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def files(folder):
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*")
            if path.is_file() and str(path.relative_to(folder)) not in {"RECIPE.md", "preview.png"}}


def app_home(temp):
    home = Path(temp) / "app-home"
    (home / "layouts").mkdir(parents=True)
    shutil.copy2(SOURCE / "layouts/main.json", home / "layouts/main.json")
    for relative, _ in builder.CLONES:
        shutil.copytree(SOURCE / relative, home / relative)
    return home


class BuildPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not READY:
            message = f"needs Herdr defaults and Cargo.toml at {SOURCE}"
            if os.environ.get("CI"):
                raise AssertionError(message)
            raise unittest.SkipTest(message)

    def test_layout_order_references_and_source_version(self):
        version = builder.source_version(SOURCE, None)
        main = json.loads((SOURCE / "layouts/main.json").read_text())
        built = builder.layout_from(SOURCE, version)
        lists = builder.find(built, "sidebar-lists")["children"]
        self.assertEqual([item["id"] for item in lists], ["spaces", "containers", "agents"])
        self.assertEqual([(item["content"]["mod"]) for item in lists],
                         ["spaces-containers", "containers", "agents-containers"])
        self.assertEqual(lists[1]["size"], {"weight": 1})
        self.assertEqual(lists[1]["min"], 35)
        self.assertEqual(builder.find(built, "sidebar-footer")["content"]["mod"],
                         "workspace-chrome-containers")
        self.assertEqual(builder.find(built, "brand")["content"]["mod"], "workspace-chrome")
        self.assertEqual(built["cloned_from"], {"name": "main", "version": version})
        self.assertEqual(builder.find(main, "brand"), builder.find(built, "brand"))

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
            self.assertEqual((output / "layouts/main-containers.json").read_bytes(),
                             (PACKAGE / "layouts/main-containers.json").read_bytes())
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
    # The builder writes cloned_from; docs and CI must name the same Herdr.
    def test_docs_and_ci_name_the_built_herdr_version(self):
        layout = json.loads((PACKAGE / "layouts/main-containers.json").read_text())
        version = layout["cloned_from"]["version"]
        for _, name in builder.CLONES:
            meta = json.loads((PACKAGE / "mods" / name / "mod.json").read_text())
            self.assertEqual(meta["cloned_from"]["version"], version, name)
        mention = re.compile(r"\b(?:Herdr(?: app)?|herdr-app) v?(\d+\.\d+\.\d+)")
        docs = [ROOT / "README.md", ROOT / "AGENTS.md", PACKAGE / "RECIPE.md",
                *sorted((ROOT / ".agents/skills").glob("*/SKILL.md"))]
        for path in docs:
            for found in mention.findall(path.read_text(encoding="utf-8")):
                self.assertEqual(found, version, path.relative_to(ROOT))
        for path in (ROOT / "README.md", PACKAGE / "RECIPE.md"):
            self.assertIn(f"Herdr app {version}", path.read_text(encoding="utf-8"), path.relative_to(ROOT))
        ci = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"# v(\d+\.\d+\.\d+)", ci), [version])


if __name__ == "__main__":
    unittest.main()
