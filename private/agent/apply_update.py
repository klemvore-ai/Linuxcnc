"""Apply a previously staged LinuxCNC update with local administrator approval."""

import argparse
import ast
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent.archive_transfer import ArchiveError, validate_application_archive
REQUIRED_BASE_FILES = {"main.py", "requirements.txt", "agent/__init__.py", "agent/__main__.py"}
LAUNCHER_NAME = "linuxcnc-launcher.sh"
DEFAULT_STATUS_PATH = "/var/lib/linuxcnc/updates/last-apply.json"


class UpdateApplyError(RuntimeError):
    """Report whether a failed update transaction restored the prior release."""

    def __init__(self, message, rollback_performed=False):
        super().__init__(message)
        self.rollback_performed = rollback_performed


def _verify_application_contents(archive_path):
    """Reject a staged archive that cannot boot LinuxCNC after replacement."""
    with tarfile.open(archive_path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    if not REQUIRED_BASE_FILES.issubset(names):
        raise ArchiveError("Application archive is missing required LinuxCNC files.")
    if "agent/status.py" not in names and "private/agent/status.py" not in names:
        raise ArchiveError("Application archive is missing agent status metadata.")


def _parse_status_file(status_file):
    if not status_file.is_file():
        return {}
    try:
        module = ast.parse(status_file.read_text(encoding="utf-8"))
        values = {}
        for node in module.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"AGENT_VERSION", "CAPABILITIES"}:
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        return values
    except Exception:
        return {}


def _release_metadata(replacement):
    """Read version and capability constants from the validated replacement source tree."""
    values = _parse_status_file(replacement / "private" / "agent" / "status.py")
    if "AGENT_VERSION" not in values or "CAPABILITIES" not in values:
        values.update(_parse_status_file(replacement / "agent" / "status.py"))

    version = values.get("AGENT_VERSION")
    capabilities = values.get("CAPABILITIES")
    if not isinstance(version, str) or not isinstance(capabilities, (tuple, list)) or not all(isinstance(item, str) for item in capabilities):
        raise ArchiveError("Application archive release metadata is invalid: Invalid release metadata.")
    return {"agent_version": version, "capabilities": list(capabilities)}


def _write_launcher(application_root):
    """Create a stable launcher that is independent of the caller's directory."""
    launcher = application_root / LAUNCHER_NAME
    launcher.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "cd /opt/LinuxCNC\n"
        "exec /opt/LinuxCNC/.venv/bin/python /opt/LinuxCNC/main.py \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)


def _write_status(status_path, payload):
    """Persist a bounded update result for the read-only status endpoint."""
    status = dict(payload)
    status["recorded_at"] = datetime.now(timezone.utc).isoformat()
    destination = Path(status_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(status, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, destination)


def apply_update(archive_path, application_root, status_path=DEFAULT_STATUS_PATH):
    """Atomically replace the application tree while preserving its virtual environment."""
    archive_path = Path(archive_path)
    application_root = Path(application_root)
    if not archive_path.is_file():
        raise ArchiveError("No staged application update is available.")
    content = archive_path.read_bytes()
    import hashlib
    archive_sha256 = hashlib.sha256(content).hexdigest()
    temporary_archive = validate_application_archive(content, archive_sha256)
    _verify_application_contents(temporary_archive)
    staging_parent = application_root.parent
    staging = Path(tempfile.mkdtemp(prefix=".linuxcnc-update-", dir=staging_parent))
    replacement = staging / application_root.name
    backup = application_root.with_name(f".{application_root.name}.previous")
    virtual_environment = application_root / ".venv"
    replacement_environment = replacement / ".venv"
    moved_environment = False
    try:
        replacement.mkdir()
        with tarfile.open(temporary_archive, "r:gz") as archive:
            archive.extractall(replacement, filter="data")
        release_metadata = _release_metadata(replacement)
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(application_root, backup)
        try:
            backup_environment = backup / ".venv"
            if backup_environment.is_dir():
                os.replace(backup_environment, replacement_environment)
                moved_environment = True
            os.replace(replacement, application_root)
            _write_launcher(application_root)
        except Exception as error:
            rollback_performed = False
            try:
                active_environment = application_root / ".venv"
                if moved_environment and active_environment.is_dir():
                    os.replace(active_environment, backup / ".venv")
                if application_root.exists():
                    shutil.rmtree(application_root)
                os.replace(backup, application_root)
                rollback_performed = True
            except Exception as rollback_error:
                raise UpdateApplyError(
                    f"Update failed and rollback failed: {rollback_error}",
                    rollback_performed=False,
                ) from error
            raise UpdateApplyError(str(error), rollback_performed=rollback_performed) from error
        shutil.rmtree(backup)
        archive_path.unlink()
        _write_status(
            status_path,
            {
                "status": "applied",
                "application_root": str(application_root),
                "archive_sha256": archive_sha256,
                **release_metadata,
            },
        )
    finally:
        if os.path.exists(temporary_archive):
            os.unlink(temporary_archive)
        shutil.rmtree(staging, ignore_errors=True)


def main():
    """Apply the staged update only when run locally by an administrator."""
    parser = argparse.ArgumentParser(description="Apply a staged LinuxCNC update")
    parser.add_argument("--archive", default=None)
    parser.add_argument("--application-root", default=None)
    parser.add_argument("--status-path", default=DEFAULT_STATUS_PATH)
    parser.add_argument("--staging-dir", default=None)
    parser.add_argument("--agent-root", default=None)
    args = parser.parse_args()
    archive_path = args.archive or (str(Path(args.staging_dir) / "linuxcnc-update.tar.gz") if args.staging_dir else "/var/lib/linuxcnc/updates/linuxcnc-update.tar.gz")
    application_root = args.application_root or args.agent_root or "/opt/LinuxCNC"
    try:
        apply_update(archive_path, application_root, args.status_path)
    except Exception as error:
        _write_status(
            args.status_path,
            {
                "status": "rolled_back" if getattr(error, "rollback_performed", False) else "failed",
                "error": str(error)[:1024],
            },
        )
        raise


if __name__ == "__main__":
    main()