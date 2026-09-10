"""Agent package for public client helpers and private machine-admin services."""

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PRIVATE_AGENT = (_PACKAGE_ROOT.parent / "private" / "agent").resolve()

__path__ = [str(_PACKAGE_ROOT)]
if _PRIVATE_AGENT.exists():
    __path__.append(str(_PRIVATE_AGENT))

__all__ = []
