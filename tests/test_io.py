import unittest
import tempfile
from pathlib import Path

from lutz_scholz_qgis.io_utils import read_lutz_xlsx, read_monthly_csv, write_results


class InputTests(unittest.TestCase):
    def test_result_export_contains_scaled_metrics_and_manifest(self):
        metric = {"n": 12, "NSE": 0.8, "PBIAS_porcentaje": -2.0}
        diagnostics = {
            "complete": {"monthly": metric, "annual": None, "regime": metric,
                         "scatter": {"n": 12, "R2": 0.9}, "monthly_by_calendar_month": {}},
            "calibration": {"monthly": metric, "annual": None, "regime": metric,
                            "scatter": {"n": 12, "R2": 0.9}, "monthly_by_calendar_month": {}},
        }
        result = {
            "version": "0.5.0",
            "rows": [{"fecha": "2000-01-01", "anio": 2000, "mes": 1,
                      "caudal_observado_m3s": 1.0, "caudal_simulado_m3s": 1.1}],
            "parameters": {"area_km2": 1.0}, "retention": {}, "pe_average_mm": [],
            "q_average_mm": [], "regression": {}, "calibration_period": (2000, 2000),
            "validation_period": None, "diagnostics": diagnostics,
            "precipitation_statistics": {},
            "run_metadata": {"run_id": "corrida_prueba", "validation_recalibrates": False},
        }
        with tempfile.TemporaryDirectory() as folder:
            outputs = write_results(result, folder)
            metrics_text = Path(outputs["metrics"]).read_text(encoding="utf-8-sig")
            manifest_text = Path(outputs["manifest"]).read_text(encoding="utf-8")
        self.assertIn("periodo,escala,indicador,valor", metrics_text)
        self.assertIn("complete,monthly,NSE,0.8", metrics_text)
        self.assertIn('"validation_recalibrates": false', manifest_text)

    def test_example_csv_is_complete(self):
        source = Path(__file__).parents[1] / "examples" / "serie_mensual_ejemplo.csv"
        records = read_monthly_csv(str(source))
        self.assertEqual(len(records), 72)
        self.assertEqual(records[0].fecha.isoformat(), "1990-01-01")
        self.assertEqual(records[-1].fecha.isoformat(), "1995-12-01")
        self.assertTrue(all(record.caudal_observado_m3s is not None for record in records))

    def test_semicolon_csv_accepts_caudal_obs_m3s_alias(self):
        content = (
            "Fecha;Precipitacion_mm;Caudal_obs_m3s\n"
            "1/01/1990;19.58;1.38\n"
            "1/02/1990;8.36;1.52\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "datos.csv"
            source.write_text(content, encoding="utf-8")
            records = read_monthly_csv(str(source))
        self.assertEqual(len(records), 2)
        self.assertAlmostEqual(records[0].caudal_observado_m3s, 1.38)
        self.assertAlmostEqual(records[1].caudal_observado_m3s, 1.52)

    def test_csv_can_load_q_only_before_precipitation_is_added_from_gee(self):
        content = (
            "Fecha;Precipitacion_mm;Caudal_obs_m3s\n"
            "1/01/1990;;1.38\n"
            "1/02/1990;;1.52\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "caudales.csv"
            source.write_text(content, encoding="utf-8")
            records = read_monthly_csv(str(source))
        self.assertTrue(all(record.precipitacion_mm is None for record in records))
        self.assertTrue(all(record.caudal_observado_m3s is not None for record in records))

    def test_excel_template_is_read_without_external_dependencies(self):
        source = Path(__file__).parents[1] / "templates" / "Plantilla_Entradas_Lutz_Scholz_QGIS_v0.1.xlsx"
        project = read_lutz_xlsx(str(source))
        self.assertEqual(len(project.records), 24)
        self.assertEqual(len(project.retention_rows), 12)
        self.assertEqual(len(project.components), 3)
        self.assertEqual(project.config["metodo_r"], "manual")
        self.assertEqual(project.config["retencion_manual_mm"], 15)
        self.assertTrue(all(record.caudal_observado_m3s is not None for record in project.records))


if __name__ == "__main__":
    unittest.main()
