"""Core package for CNCFusion public runtime and private machine-admin support."""

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PRIVATE_CORE = (_PACKAGE_ROOT.parent / "private" / "core").resolve()

__path__ = [str(_PACKAGE_ROOT)]
if _PRIVATE_CORE.exists():
    __path__.append(str(_PRIVATE_CORE))

__all__ = []
