"""Tab package for public GUI tabs and private machine-admin screens."""

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PRIVATE_TABS = (_PACKAGE_ROOT.parent / "private" / "tabs").resolve()

__path__ = [str(_PACKAGE_ROOT)]
if _PRIVATE_TABS.exists():
    __path__.append(str(_PRIVATE_TABS))

__all__ = []
