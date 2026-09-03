import tempfile
import unittest
from pathlib import Path

from lutz_scholz_qgis.plotting import create_diagnostic_plots


class PlottingTests(unittest.TestCase):
    def test_monthly_series_contains_visible_date_ticks(self):
        rows = []
        for year in (2000, 2001):
            for month in range(1, 13):
                rows.append({
                    "fecha": f"{year:04d}-{month:02d}-01",
                    "anio": year,
                    "mes": month,
                    "caudal_simulado_m3s": float(month),
                    "caudal_observado_m3s": float(month) * .9,
                })
        result = {
            "rows": rows,
            "parameters": {"area_km2": 1, "coef_escorrentia": .2, "retencion_mm": 1, "a_dia": .02},
            "calibration_period": (2000, 2000),
            "validation_period": (2001, 2001),
            "run_metadata": {
                "run_id": "corrida_20260902_200624",
                "calibration_mode": "automatica",
                "split_method": "cronologico_60_40",
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            paths = create_diagnostic_plots(result, folder)
            svg = Path(paths["serie_mensual"]).read_text(encoding="utf-8")
            calibration_svg = Path(paths["serie_mensual_calibracion"]).read_text(encoding="utf-8")
            validation_svg = Path(paths["serie_mensual_validacion"]).read_text(encoding="utf-8")
            panel = Path(paths["panel_diagnostico"]).read_text(encoding="utf-8")
            annual = Path(paths["caudal_anual"]).read_text(encoding="utf-8")
            scatter = Path(paths["dispersion"]).read_text(encoding="utf-8")
            summary = Path(paths["resumen"]).read_text(encoding="utf-8")
            persistence = Path(paths["permanencia"]).read_text(encoding="utf-8")
        self.assertIn("2000-01", svg)
        self.assertIn("2001-12", svg)
        self.assertGreaterEqual(svg.count('class="tick"'), 7)
        self.assertIn("serie_mensual_calibracion", paths)
        self.assertIn("serie_mensual_validacion", paths)
        self.assertIn("Calibracion", calibration_svg)
        self.assertIn("Validacion", validation_svg)
        self.assertIn("panel_diagnostico", paths)
        self.assertIn("Diagnostico hidrologico", panel)
        self.assertIn("Caudal anual ponderado", panel)
        self.assertIn("ponderado por dias", annual)
        self.assertIn("Qsim =", scatter)
        self.assertIn("R2=", scatter)
        self.assertIn("Identificador de modelación", summary)
        self.assertIn("modelacion_20260902_200624", summary)
        self.assertIn("Modalidad: Calibración automática", summary)
        self.assertIn("División temporal: Cronológica 60/40", summary)
        self.assertNotIn("Corrida:", summary)
        self.assertIn("Curva de permanencia", persistence)
        self.assertIn("Fuente=simulado", persistence)
        self.assertIn("Q75=", persistence)
        self.assertIn("permanencia_calibracion", paths)
        self.assertIn("permanencia_validacion", paths)


if __name__ == "__main__":
    unittest.main()
