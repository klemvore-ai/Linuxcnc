"""Status and approval tracking for staged LinuxCNC configuration bundles."""

import hashlib
import json
from pathlib import Path


class BundleError(ValueError):
    """Raised when a bundle identifier or bundle operation is invalid."""


def _bundle_directory(config_root, bundle_name):
    """Resolve one single-directory bundle below the approved config root."""
    if not isinstance(bundle_name, str) or not bundle_name or Path(bundle_name).name != bundle_name:
        raise BundleError("Bundle name must be a single directory name.")
    root = Path(config_root).expanduser().resolve(strict=False)
    directory = (root / bundle_name).resolve(strict=False)
    if root not in directory.parents:
        raise BundleError("Bundle is outside the approved configuration root.")
    return directory


def bundle_status(config_root, bundle_name):
    """Return staged files, checksums, completeness, approval, and restart state."""
    directory = _bundle_directory(config_root, bundle_name)
    files = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in {".ini", ".hal", ".tbl", ".var"}:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                files.append({"name": path.name, "sha256": digest, "size": path.stat().st_size})
    names = {item["name"] for item in files}
    approval_path = directory / ".linuxcnc-approved"
    return {
        "bundle": bundle_name,
        "files": files,
        "complete": any(name.endswith(".ini") for name in names) and any(name.endswith(".hal") for name in names),
        "approved": approval_path.is_file(),
        "restart_required": any(name.endswith((".ini", ".hal")) for name in names),
    }


def approve_bundle(config_root, bundle_name):
    """Persist explicit operator approval for an existing, complete bundle."""
    directory = _bundle_directory(config_root, bundle_name)
    status = bundle_status(config_root, bundle_name)
    if not status["complete"]:
        raise BundleError("A bundle requires at least one INI and one HAL file.")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".linuxcnc-approved").write_text(
        json.dumps({"bundle": bundle_name, "approved": True}),
        encoding="utf-8",
    )
    return bundle_status(config_root, bundle_name)