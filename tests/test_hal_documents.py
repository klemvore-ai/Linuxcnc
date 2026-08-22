import tempfile
import unittest
from pathlib import Path

from core.hal_documents import HALDocumentComposer, HALDocumentError


class HALDocumentTests(unittest.TestCase):
    def test_compose_keeps_user_postgui_content_separate(self):
        composer = HALDocumentComposer(tempfile.mkdtemp())

        artifacts = composer.compose("core", "blueprint", "net custom source => target")

        self.assertEqual(artifacts["core.hal"], "core\n")
        self.assertEqual(artifacts["blueprint.hal"], "blueprint\n")
        self.assertEqual(artifacts["custom_postgui.hal"], "net custom source => target\n")

    def test_write_creates_independent_artifacts(self):
        root = tempfile.mkdtemp()
        composer = HALDocumentComposer(root)

        composer.write(composer.compose("core", "blueprint", "custom"))

        self.assertEqual((Path(root) / "core.hal").read_text(encoding="utf-8"), "core\n")
        self.assertEqual((Path(root) / "blueprint.hal").read_text(encoding="utf-8"), "blueprint\n")
        self.assertEqual((Path(root) / "custom_postgui.hal").read_text(encoding="utf-8"), "custom\n")

    def test_existing_postgui_content_can_be_loaded_for_regeneration(self):
        root = tempfile.mkdtemp()
        postgui_path = Path(root) / "custom_postgui.hal"
        postgui_path.write_text("# operator wiring\nnet custom source => target\n", encoding="utf-8")
        composer = HALDocumentComposer(root)

        self.assertEqual(composer.load_postgui(), postgui_path.read_text(encoding="utf-8"))

    def test_blueprint_is_normalized_as_a_separate_generated_artifact(self):
        composer = HALDocumentComposer(tempfile.mkdtemp())

        blueprint = composer.compose_blueprint("net generated source => target")

        self.assertEqual(blueprint, "net generated source => target\n")
        self.assertNotIn("custom_postgui", blueprint)

    def test_empty_blueprint_is_rejected(self):
        composer = HALDocumentComposer(tempfile.mkdtemp())

        with self.assertRaises(HALDocumentError):
            composer.compose_blueprint("\n")

    def test_rejects_nested_or_non_hal_artifact_names(self):
        composer = HALDocumentComposer(tempfile.mkdtemp())

        with self.assertRaises(HALDocumentError):
            composer.artifact_path("../unsafe.hal")
        with self.assertRaises(HALDocumentError):
            composer.artifact_path("machine.ini")


if __name__ == "__main__":
    unittest.main()