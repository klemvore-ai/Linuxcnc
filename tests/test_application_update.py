import json
import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.apply_update import apply_update
from agent.linuxcnc_runtime import LinuxCNCRuntime


class ApplicationUpdateTests(unittest.TestCase):
    def test_staging_copies_validated_archive_across_filesystems(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "validated.tar.gz"
            archive.write_bytes(b"archive")
            staging_root = root / "updates"
            runtime = LinuxCNCRuntime()

            with patch("agent.archive_transfer.validate_application_archive", return_value=str(archive)):
                result = runtime.stage_application_update(b"archive", "a" * 64, str(staging_root))

            self.assertTrue(result["staged"])
            self.assertEqual((staging_root / "cncfusion-update.tar.gz").read_bytes(), b"archive")
            self.assertFalse(archive.exists())

    def test_update_status_reports_staged_archive_and_last_apply_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "cncfusion-update.tar.gz"
            archive.write_bytes(b"archive")
            (root / "last-apply.json").write_text('{"status":"applied"}', encoding="utf-8")

            status = LinuxCNCRuntime().application_update_status(root)

            self.assertTrue(status["staged"])
            self.assertEqual(status["staged_size"], len(b"archive"))
            self.assertEqual(status["last_apply"]["status"], "applied")

    def test_deployment_preflight_reports_application_and_update_prerequisites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = root / "CNCFusion"
            python = application / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            staging = root / "updates"
            staging.mkdir()

            preflight = LinuxCNCRuntime().deployment_preflight(staging, application)

            self.assertEqual(
                preflight,
                {
                    "application_root": True,
                    "python": True,
                    "update_directory": True,
                    "update_writable": True,
                },
            )

    def test_apply_update_replaces_application_and_preserves_virtual_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = root / "CNCFusion"
            application.mkdir()
            (application / "main.py").write_text("old", encoding="utf-8")
            environment = application / ".venv"
            environment.mkdir()
            (environment / "marker").write_text("keep", encoding="utf-8")
            source = root / "release"
            source.mkdir()
            (source / "main.py").write_text("new", encoding="utf-8")
            (source / "requirements.txt").write_text("", encoding="utf-8")
            agent = source / "agent"
            agent.mkdir()
            (agent / "__init__.py").write_text("", encoding="utf-8")
            (agent / "__main__.py").write_text("", encoding="utf-8")
            (agent / "status.py").write_text('AGENT_VERSION = "9.9.9"\nCAPABILITIES = ("replacement_capability",)\n', encoding="utf-8")
            archive = root / "cncfusion-update.tar.gz"
            with tarfile.open(archive, "w:gz") as update:
                update.add(source / "main.py", arcname="main.py")
                update.add(source / "requirements.txt", arcname="requirements.txt")
                update.add(agent / "__init__.py", arcname="agent/__init__.py")
                update.add(agent / "__main__.py", arcname="agent/__main__.py")
                update.add(agent / "status.py", arcname="agent/status.py")
            expected_archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

            with patch("agent.apply_update.shutil.copytree") as copytree:
                apply_update(archive, application, root / "last-apply.json")

            self.assertEqual((application / "main.py").read_text(encoding="utf-8"), "new")
            self.assertEqual((application / ".venv" / "marker").read_text(encoding="utf-8"), "keep")
            launcher = application / "cncfusion-launcher.sh"
            self.assertTrue(launcher.is_file())
            self.assertIn("exec /opt/CNCFusion/.venv/bin/python", launcher.read_text(encoding="utf-8"))
            self.assertFalse(archive.exists())
            copytree.assert_not_called()
            apply_status = json.loads((root / "last-apply.json").read_text(encoding="utf-8"))
            self.assertEqual(apply_status["status"], "applied")
            self.assertEqual(apply_status["agent_version"], "9.9.9")
            self.assertEqual(apply_status["capabilities"], ["replacement_capability"])
            self.assertEqual(apply_status["archive_sha256"], expected_archive_sha256)
            self.assertIn(
                "recorded_at",
                apply_status,
            )
            self.assertFalse((root / "last-apply.json.tmp").exists())

    def test_apply_update_rejects_incomplete_application_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = root / "CNCFusion"
            application.mkdir()
            placeholder = root / "placeholder"
            placeholder.write_text("placeholder", encoding="utf-8")
            archive = root / "cncfusion-update.tar.gz"
            with tarfile.open(archive, "w:gz") as update:
                update.add(placeholder, arcname="placeholder")

            with self.assertRaisesRegex(Exception, "missing required CNCFusion files"):
                apply_update(archive, application)

    def test_apply_update_restores_previous_release_after_post_swap_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = root / "CNCFusion"
            application.mkdir()
            (application / "main.py").write_text("old", encoding="utf-8")
            environment = application / ".venv"
            environment.mkdir()
            (environment / "marker").write_text("keep", encoding="utf-8")
            source = root / "release"
            source.mkdir()
            (source / "main.py").write_text("new", encoding="utf-8")
            (source / "requirements.txt").write_text("", encoding="utf-8")
            agent = source / "agent"
            agent.mkdir()
            (agent / "__init__.py").write_text("", encoding="utf-8")
            (agent / "__main__.py").write_text("", encoding="utf-8")
            (agent / "status.py").write_text('AGENT_VERSION = "9.9.9"\nCAPABILITIES = ()\n', encoding="utf-8")
            archive = root / "cncfusion-update.tar.gz"
            with tarfile.open(archive, "w:gz") as update:
                update.add(source / "main.py", arcname="main.py")
                update.add(source / "requirements.txt", arcname="requirements.txt")
                update.add(agent / "__init__.py", arcname="agent/__init__.py")
                update.add(agent / "__main__.py", arcname="agent/__main__.py")
                update.add(agent / "status.py", arcname="agent/status.py")

            with patch("agent.apply_update._write_launcher", side_effect=OSError("launcher write failed")):
                with self.assertRaisesRegex(Exception, "launcher write failed"):
                    apply_update(archive, application, root / "last-apply.json")

            self.assertEqual((application / "main.py").read_text(encoding="utf-8"), "old")
            self.assertEqual((application / ".venv" / "marker").read_text(encoding="utf-8"), "keep")
            self.assertTrue(archive.exists())

    def test_workspace_packaging_includes_required_application_files(self):
        workspace_root = Path(__file__).resolve().parent.parent
        self.assertTrue((workspace_root / "main.py").is_file())
        self.assertTrue((workspace_root / "requirements.txt").is_file())
        self.assertTrue((workspace_root / "agent" / "__init__.py").is_file())
        self.assertTrue((workspace_root / "agent" / "__main__.py").is_file())
        self.assertTrue((workspace_root / "agent" / "status.py").is_file())
