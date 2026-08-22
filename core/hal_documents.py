"""Compose LinuxCNC HAL artifacts without overwriting user-authored logic."""

from pathlib import Path


class HALDocumentError(ValueError):
    """Raised when HAL artifact paths or content cannot be handled safely."""


class HALDocumentComposer:
    """Manage separate generated and user-maintained LinuxCNC HAL files."""

    def __init__(self, document_root):
        """Create a composer rooted at one LinuxCNC configuration directory."""
        self.document_root = Path(document_root).expanduser().resolve()

    def artifact_path(self, filename):
        """Return a safe path for one supported HAL artifact filename."""
        path = Path(filename)
        if path.name != filename or path.suffix.lower() != ".hal":
            raise HALDocumentError("HAL artifact must be a filename ending in .hal.")
        return self.document_root / path

    def compose(self, core_hal, blueprint_hal, postgui_hal=None):
        """Return independent HAL artifact contents for deployment or export."""
        if not isinstance(core_hal, str) or not isinstance(blueprint_hal, str):
            raise HALDocumentError("Generated HAL content must be text.")
        if postgui_hal is None:
            postgui_hal = "# User-maintained post-GUI HAL logic\n"
        if not isinstance(postgui_hal, str):
            raise HALDocumentError("Post-GUI HAL content must be text.")
        return {
            "core.hal": self._ensure_trailing_newline(core_hal),
            "blueprint.hal": self._ensure_trailing_newline(blueprint_hal),
            "custom_postgui.hal": self._ensure_trailing_newline(postgui_hal),
        }

    def compose_blueprint(self, blueprint_hal):
        """Normalize one generated visual blueprint without touching custom HAL."""
        if not isinstance(blueprint_hal, str) or not blueprint_hal.strip():
            raise HALDocumentError("Generated blueprint HAL content is required.")
        return self._ensure_trailing_newline(blueprint_hal)

    def load_postgui(self):
        """Read existing user-maintained post-GUI logic without modifying it."""
        path = self.artifact_path("custom_postgui.hal")
        if not path.is_file():
            return "# User-maintained post-GUI HAL logic\n"
        return path.read_text(encoding="utf-8")

    def write(self, artifacts):
        """Write composed artifacts, creating only the configured root."""
        self.document_root.mkdir(parents=True, exist_ok=True)
        written = []
        for filename, content in artifacts.items():
            path = self.artifact_path(filename)
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append(path)
        return written

    @staticmethod
    def _ensure_trailing_newline(content):
        """Normalize artifact endings for stable diffs and LinuxCNC parsing."""
        return content if content.endswith("\n") else f"{content}\n"