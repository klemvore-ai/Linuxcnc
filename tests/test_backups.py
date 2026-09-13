import tempfile
import unittest
from pathlib import Path

from agent.backups import BackupError, backup_manifest


class BackupTests(unittest.TestCase):
    def test_manifest_reports_checksums_and_excludes_internal_metadata(self):
        root = Path(tempfile.mkdtemp())
        (root / "machine.ini").write_text("[EMC]\n", encoding="utf-8")
        (root / "custom_postgui.hal").write_text("net custom source => target\n", encoding="utf-8")
        (root / ".linuxcnc-approved").write_text("approved", encoding="utf-8")
        (root / ".linuxcnc-backups").mkdir()
        (root / ".linuxcnc-backups" / "old.ini").write_text("old", encoding="utf-8")

        manifest = backup_manifest("config", root, root / "nc_files")

        self.assertEqual(manifest["file_count"], 2)
        self.assertEqual({item["path"] for item in manifest["files"]}, {"custom_postgui.hal", "machine.ini"})

    def test_rejects_unknown_backup_kind(self):
        with self.assertRaises(BackupError):
            backup_manifest("secret", tempfile.mkdtemp(), tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()