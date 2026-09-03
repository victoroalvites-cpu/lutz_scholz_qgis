import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from lutz_scholz_qgis.reporting import create_word_report
from lutz_scholz_qgis.core import flow_persistence


class ReportingTests(unittest.TestCase):
    def test_report_includes_persistence_and_regulatory_caveat(self):
        rows = [
            {"mes": month, "caudal_simulado_m3s": float(month),
             "caudal_observado_m3s": float(month + 1)}
            for month in range(1, 13)
        ]
        result = {
            "version": "0.1.0",
            "parameters": {"area_km2": 1, "coef_escorrentia": .2, "retencion_mm": 1,
                           "a_dia": .02, "negative_balance_mode": "strict"},
            "calibration_period": (2000, 2000), "validation_period": None,
            "diagnostics": {}, "balance_diagnostics": {},
            "flow_persistence": {"complete": flow_persistence(rows)},
            "run_metadata": {"run_id": "qa_permanencia"},
        }
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "informe.docx"
            create_word_report(result, {}, {}, str(report))
            with ZipFile(report) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("Régimen multimensual, persistencia", document_xml)
        self.assertIn("Weibull", document_xml)
        self.assertIn("Q75", document_xml)
        self.assertIn("Persist. 75 %", document_xml)
        self.assertIn("15 % Q medio", document_xml)
        self.assertIn("caudal simulado por Lutz Sholtz", document_xml)
        self.assertIn("no equivale por sí solo a un caudal ecológico aprobado", document_xml)
        self.assertIn("8. Conclusiones del desempeño", document_xml)

    def test_strict_automatic_report_omits_balance_warning_and_appendix(self):
        result = {
            "version": "0.1.0",
            "parameters": {
                "area_km2": 950.54,
                "coef_escorrentia": 0.17,
                "retencion_mm": 10.9,
                "a_dia": 0.02,
                "negative_balance_mode": "strict",
            },
            "calibration_period": (1990, 2011),
            "validation_period": (2012, 2025),
            "diagnostics": {},
            "balance_diagnostics": {
                "mode": "strict",
                "negative_months": [],
                "clipped_months": 0,
                "clipped_total_mm": 0.0,
                "annual_balance_modified": False,
            },
            "automatic_calibration": {
                "objective": "NSE",
                "trials": 128,
                "physical_balance_enforced": True,
                "coefficient": 0.18,
                "initial_coefficient": 0.17,
                "retention_mm": 12.2,
                "initial_retention_mm": 10.9,
                "a_day": 0.03,
                "initial_a_day": 0.02,
                "calibrated_c": True,
                "search_bounds": {
                    "coefficient": [0.12, 0.22],
                    "retention_mm": [0.0, 200.0],
                    "a_day": [0.005, 0.06],
                },
                "steps_per_axis": 8,
                "refinements": 2,
                "rejected_trials": 7,
                "retention_limit_mm": 13.5,
            },
            "run_metadata": {
                "run_id": "modelacion_20260902_162103",
                "scenario": "escenario_base",
                "created_at": "2026-09-02T16:21:03-05:00",
                "calibration_mode": "automatica",
                "precipitation_source": "PISCO precipitacion mensual (solo precipitacion)",
                "temperature_source": "PISCO T extendida con ERA5-Land corregido (solo temperatura)",
                "split_method": "cronologico_60_40",
                "supply_source": "tabla manual/editada",
                "retention_source": "Manual",
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "informe.docx"
            create_word_report(result, {}, {}, str(report))
            with ZipFile(report) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertNotIn("Diagnóstico de balance", document_xml)
            self.assertNotIn("Anexo A.", document_xml)
            self.assertNotIn("recorte controlado", document_xml.lower())
            self.assertIn("Balance físico estricto", document_xml)
            self.assertIn("Q mensual mayor o igual a cero", document_xml)
            self.assertIn("trazabilidad de la búsqueda", document_xml)
            self.assertIn("Combinaciones rechazadas", document_xml)
            self.assertIn("Convención de PBIAS", document_xml)
            self.assertIn("Indicadores mensuales complementarios", document_xml)
            self.assertIn("Schultz D", document_xml)
            self.assertIn("Identificador de modelación", document_xml)
            self.assertIn("modelacion_20260902_162103", document_xml)
            self.assertIn("Escenario base", document_xml)
            self.assertIn("02/09/2026 16:21:03 (UTC-05:00)", document_xml)
            self.assertIn("Calibración automática", document_xml)
            self.assertIn("PISCO: precipitación mensual", document_xml)
            self.assertIn("PISCO T extendida y corregida con ERA5-Land", document_xml)
            self.assertIn("Cronológica 60/40", document_xml)
            self.assertIn("Tabla manual o editada", document_xml)
            self.assertIn("Conclusiones del desempeño", document_xml)
            self.assertIn("La carpeta de la modelación", document_xml)
            self.assertNotIn(">Corrida<", document_xml)

    def test_conclusions_explain_validation_changes_and_pbias_sign(self):
        result = {
            "version": "0.1.0",
            "parameters": {
                "area_km2": 950.54,
                "coef_escorrentia": 0.18,
                "retencion_mm": 12.2,
                "a_dia": 0.044,
                "negative_balance_mode": "strict",
            },
            "calibration_period": (1990, 2011),
            "validation_period": (2012, 2025),
            "diagnostics": {
                "calibration": {"monthly": {"n": 264, "NSE": 0.682, "LogNSE": 0.560, "KGE": 0.668, "PBIAS_porcentaje": -2.374}},
                "validation": {"monthly": {"n": 168, "NSE": 0.625, "LogNSE": 0.379, "KGE": 0.616, "PBIAS_porcentaje": -25.0, "PBIAS_altos_porcentaje": 14.0}},
            },
            "run_metadata": {"run_id": "qa_conclusiones"},
        }
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "informe.docx"
            create_word_report(result, {}, {}, str(report))
            with ZipFile(report) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("NSE = 0.625", document_xml)
            self.assertIn("KGE mensual de validación", document_xml)
            self.assertIn("requiere cautela al transferirse", document_xml)
            self.assertIn("ajuste de caudales bajos es más débil", document_xml)
            self.assertIn("subestimación global", document_xml)
            self.assertIn("tendencia a sobreestimar", document_xml)


if __name__ == "__main__":
    unittest.main()
