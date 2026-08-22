"""Bounded archive transfer and safe restore helpers for machine files."""

import hashlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000


class ArchiveError(ValueError):
    """Raised when an archive is invalid or exceeds transfer policy."""


def root_for_kind(kind, config_root, gcode_root):
    """Return the configured root for a supported archive kind."""
    if kind not in {"config", "gcode"}:
        raise ArchiveError("Archive kind is not supported.")
    return Path(config_root if kind == "config" else gcode_root).expanduser().resolve()


def create_archive(kind, config_root, gcode_root):
    """Create a bounded tar.gz archive and return its bytes plus checksum."""
    root = root_for_kind(kind, config_root, gcode_root)
    temporary = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    temporary.close()
    try:
        with tarfile.open(temporary.name, "w:gz") as archive:
            archive.add(root, arcname=root.name, recursive=True, filter=_archive_filter)
        content = Path(temporary.name).read_bytes()
        if len(content) > MAX_ARCHIVE_BYTES:
            raise ArchiveError("Archive is too large.")
        return content, hashlib.sha256(content).hexdigest()
    finally:
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)


def validate_archive(content, expected_sha256):
    """Verify archive size, checksum, member count, and member paths."""
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("Archive is too large.")
    if hashlib.sha256(content).hexdigest() != expected_sha256.lower():
        raise ArchiveError("Archive checksum does not match content.")
    temporary = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    temporary.write(content)
    temporary.close()
    try:
        with tarfile.open(temporary.name, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ArchiveError("Archive contains too many members.")
            for member in members:
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ArchiveError("Archive contains an unsafe path.")
                if member.issym() or member.islnk() or member.isdev():
                    raise ArchiveError("Archive links and device files are not allowed.")
        return temporary.name
    except Exception:
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)
        raise


def validate_application_archive(content, expected_sha256):
    """Validate an application archive and return its temporary archive path."""
    archive_path = validate_archive(content, expected_sha256)
    invalid_root = False
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name.startswith("/") or member.name.startswith("."):
                invalid_root = True
                break
    if invalid_root:
        os.unlink(archive_path)
        raise ArchiveError("Application archive contains an invalid root path.")
    return archive_path


def restore_archive(kind, content, expected_sha256, config_root, gcode_root, runtime):
    """Safely replace one approved root after LinuxCNC stopped verification."""
    if runtime.is_running():
        raise ArchiveError("LinuxCNC must be stopped before restore.")
    root = root_for_kind(kind, config_root, gcode_root)
    archive_path = validate_archive(content, expected_sha256)
    staging = Path(tempfile.mkdtemp(prefix="cncfusion-restore-"))
    backup = root.parent / f".{root.name}.cncfusion-restore-backup"
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(staging)
        extracted_root = staging / root.name
        if not extracted_root.is_dir():
            raise ArchiveError("Archive root does not match the requested backup kind.")
        if backup.exists():
            shutil.rmtree(backup)
        if root.exists():
            os.replace(root, backup)
        os.replace(extracted_root, root)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if not root.exists() and backup.exists():
            os.replace(backup, root)
        raise
    finally:
        if os.path.exists(archive_path):
            os.unlink(archive_path)
        shutil.rmtree(staging, ignore_errors=True)


def _archive_filter(info):
    """Exclude internal CNCFusion metadata and reject special files."""
    if ".cncfusion-backups" in Path(info.name).parts or info.name.endswith(".cncfusion-approved"):
        return None
    if info.issym() or info.islnk() or info.isdev():
        raise ArchiveError("Special files are not allowed in backups.")
    return info