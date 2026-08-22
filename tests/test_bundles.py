import tempfile
import unittest
from pathlib import Path

from agent.bundles import BundleError, approve_bundle, bundle_status


class BundleTests(unittest.TestCase):
    def test_status_reports_files_and_restart_requirement(self):
        root = Path(tempfile.mkdtemp())
        bundle = root / "TestMill"
        bundle.mkdir()
        (bundle / "TestMill.ini").write_text("[EMC]\n", encoding="utf-8")
        (bundle / "TestMill.hal").write_text("loadrt hostmot2\n", encoding="utf-8")

        status = bundle_status(root, "TestMill")

        self.assertTrue(status["complete"])
        self.assertFalse(status["approved"])
        self.assertTrue(status["restart_required"])
        self.assertEqual({item["name"] for item in status["files"]}, {"TestMill.ini", "TestMill.hal"})

    def test_approval_requires_complete_bundle_and_persists(self):
        root = Path(tempfile.mkdtemp())
        bundle = root / "TestMill"
        bundle.mkdir()
        (bundle / "TestMill.ini").write_text("[EMC]\n", encoding="utf-8")
        with self.assertRaises(BundleError):
            approve_bundle(root, "TestMill")
        (bundle / "TestMill.hal").write_text("loadrt hostmot2\n", encoding="utf-8")

        status = approve_bundle(root, "TestMill")

        self.assertTrue(status["approved"])

    def test_rejects_nested_bundle_names(self):
        with self.assertRaises(BundleError):
            bundle_status(tempfile.mkdtemp(), "../unsafe")


if __name__ == "__main__":
    unittest.main()