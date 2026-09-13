"""Safe manifest generation for machine configuration and G-code backups."""

import hashlib
from pathlib import Path


class BackupError(ValueError):
    """Raised when a backup kind or root is invalid."""


def backup_manifest(kind, config_root, gcode_root):
    """Return bounded file metadata for one approved backup area."""
    if kind not in {"config", "gcode"}:
        raise BackupError("Backup kind is not supported.")
    root = Path(config_root if kind == "config" else gcode_root).expanduser().resolve(strict=False)
    files = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".linuxcnc-backups" in path.parts or path.name == ".linuxcnc-approved":
                continue
            if len(files) >= 1000:
                raise BackupError("Backup contains too many files.")
            content = path.read_bytes()
            files.append({
                "path": path.relative_to(root).as_posix(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
    return {"kind": kind, "root": str(root), "files": files, "file_count": len(files)}