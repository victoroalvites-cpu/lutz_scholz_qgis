import json
import tempfile
import unittest
from pathlib import Path

from lutz_scholz_qgis.project_utils import (
    PROJECT_FILE,
    PROJECT_FOLDERS,
    ensure_project_structure,
)


class ProjectUtilsTests(unittest.TestCase):
    def test_creates_expected_project_structure_and_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root, folders, config_path = ensure_project_structure(Path(folder) / "cuenca")
            self.assertTrue(root.is_dir())
            self.assertEqual(set(folders), set(PROJECT_FOLDERS))
            self.assertTrue(all(path.is_dir() for path in folders.values()))
            self.assertEqual(config_path.name, PROJECT_FILE)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["folders"], PROJECT_FOLDERS)

    def test_reapplying_preserves_creation_time_and_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root, folders, config_path = ensure_project_structure(Path(folder) / "cuenca")
            marker = folders["input"] / "serie.csv"
            marker.write_text("fecha,precipitacion_mm\n", encoding="utf-8")
            original_created_at = json.loads(
                config_path.read_text(encoding="utf-8")
            )["created_at"]
            _, _, second_config = ensure_project_structure(root)
            payload = json.loads(second_config.read_text(encoding="utf-8"))
            self.assertEqual(payload["created_at"], original_created_at)
            self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
