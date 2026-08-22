import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from agent.archive_transfer import ArchiveError, create_archive, restore_archive, validate_archive, validate_application_archive


class RuntimeStopped:
    """Runtime double representing a stopped LinuxCNC host."""

    def is_running(self):
        return False


class RuntimeRunning:
    """Runtime double representing a running LinuxCNC host."""

    def is_running(self):
        return True


class ArchiveTransferTests(unittest.TestCase):
    def test_create_archive_reports_checksum_and_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "machine.ini").write_text("[EMC]\n", encoding="utf-8")

        content, checksum = create_archive("config", root, root / "nc_files")

        self.assertEqual(checksum, hashlib.sha256(content).hexdigest())
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            self.assertIn("machine.ini", {Path(member.name).name for member in archive.getmembers()})

    def test_validate_archive_rejects_checksum_mismatch(self):
        with self.assertRaises(ArchiveError):
            validate_archive(b"not-an-archive", "0" * 64)

    def test_validate_archive_rejects_traversal_member(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            member = tarfile.TarInfo("../unsafe.ini")
            member.size = 4
            archive.addfile(member, io.BytesIO(b"test"))
        content = output.getvalue()

        with self.assertRaises(ArchiveError):
            validate_archive(content, hashlib.sha256(content).hexdigest())

    def test_restore_requires_stopped_linuxcnc(self):
        root = Path(tempfile.mkdtemp())
        with self.assertRaises(ArchiveError):
            restore_archive("config", b"content", "0" * 64, root, root / "nc_files", RuntimeRunning())

    def test_application_archive_accepts_normal_relative_files(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            member = tarfile.TarInfo("agent/server.py")
            member.size = 6
            archive.addfile(member, io.BytesIO(b"server"))
        content = output.getvalue()

        archive_path = validate_application_archive(content, hashlib.sha256(content).hexdigest())

        self.assertTrue(Path(archive_path).is_file())
        Path(archive_path).unlink()

    def test_application_archive_rejects_hidden_or_absolute_root(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            member = tarfile.TarInfo(".hidden/server.py")
            member.size = 6
            archive.addfile(member, io.BytesIO(b"server"))
        content = output.getvalue()

        with self.assertRaises(ArchiveError):
            validate_application_archive(content, hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()