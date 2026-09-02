import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lutz_scholz_qgis.gee_service import (
    DEFAULT_PISCO_PRECIP,
    DEFAULT_PISCO_TEMP,
    SOURCE_CATALOG,
    _definition,
    initialize_ee,
    write_climate_csv,
)


class GeeServiceOfflineTests(unittest.TestCase):
    def test_catalog_uses_expected_private_assets_and_units_are_mapped(self):
        self.assertEqual(SOURCE_CATALOG["pisco_p"].collection_id, DEFAULT_PISCO_PRECIP)
        self.assertEqual(SOURCE_CATALOG["pisco_t"].collection_id, DEFAULT_PISCO_TEMP)
        self.assertEqual(SOURCE_CATALOG["pisco_p"].output_bands, ("precipitacion_mm",))
        self.assertEqual(SOURCE_CATALOG["pisco_p"].spatial_reducer, "pixel_mean")
        self.assertIn("temp_min_c", SOURCE_CATALOG["pisco_t"].output_bands)
        self.assertEqual(SOURCE_CATALOG["era5"].frequency, "daily_temperature_mean")
        self.assertEqual(SOURCE_CATALOG["era5"].collection_id, "ECMWF/ERA5_LAND/DAILY_AGGR")
        self.assertEqual(
            SOURCE_CATALOG["era5"].raw_bands,
            ("temperature_2m_min", "temperature_2m_max"),
        )
        self.assertNotIn("total_precipitation_sum", SOURCE_CATALOG["era5"].raw_bands)
        self.assertNotIn("precipitacion_mm", SOURCE_CATALOG["era5"].output_bands)
        self.assertEqual(
            SOURCE_CATALOG["era5"].output_bands,
            ("temp_min_c", "temp_media_c", "temp_max_c"),
        )
        self.assertEqual(SOURCE_CATALOG["chirps"].frequency, "daily")
        self.assertNotIn("persiann", SOURCE_CATALOG)
        self.assertEqual(_definition("pisco_p").spatial_reducer, "pixel_mean")
        self.assertEqual(
            _definition("pisco_p", "projects/example/custom").spatial_reducer,
            "pixel_mean",
        )

    def test_climate_csv_preserves_traceability(self):
        rows = [{
            "fecha": "2020-01-01",
            "precipitacion_mm": 100.5,
            "coverage_pct": 98.2,
            "image_count": 1,
            "fuente": "PISCO precipitacion mensual",
            "metodo": "observado",
        }]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "clima.csv"
            write_climate_csv(rows, str(target))
            with target.open(encoding="utf-8-sig", newline="") as handle:
                saved = list(csv.DictReader(handle))
        self.assertEqual(saved[0]["fuente"], "PISCO precipitacion mensual")
        self.assertEqual(saved[0]["coverage_pct"], "98.2")

    def test_initialize_sets_deadline_after_creating_session(self):
        calls = []

        class FakeData:
            @staticmethod
            def setDeadline(value):
                calls.append(("deadline", value))

        class FakeValue:
            @staticmethod
            def getInfo():
                calls.append(("get_info", None))
                return "conexion_correcta"

        class FakeEe:
            __version__ = "test"
            data = FakeData()

            @staticmethod
            def Initialize(project):
                calls.append(("initialize", project))

            @staticmethod
            def String(value):
                return FakeValue()

        with patch("lutz_scholz_qgis.gee_service._load_ee", return_value=FakeEe()):
            result = initialize_ee("ee-hidrog")

        self.assertEqual(calls[0], ("initialize", "ee-hidrog"))
        self.assertEqual(calls[1], ("deadline", 30000))
        self.assertEqual(result["test"], "conexion_correcta")


if __name__ == "__main__":
    unittest.main()
