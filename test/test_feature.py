import filecmp
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FeatureTests(unittest.TestCase):
    def test_fixture_copy_matches_feature(self):
        # test/sync-feature.sh refreshes it.
        compared = filecmp.dircmp(ROOT / "requirements/feature/herdr-core", ROOT / "test/.devcontainer/herdr-core")
        self.assertEqual((compared.left_only, compared.right_only, compared.diff_files), ([], [], []))
        _, mismatch, errors = filecmp.cmpfiles(compared.left, compared.right, compared.common_files, shallow=False)
        self.assertEqual((mismatch, errors), ([], []))


if __name__ == "__main__":
    unittest.main()
