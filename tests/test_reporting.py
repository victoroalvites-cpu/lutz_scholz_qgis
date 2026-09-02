import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from lutz_scholz_qgis.reporting import create_word_report


class ReportingTests(unittest.TestCase):
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
            },
            "run_metadata": {
                "run_id": "qa_estricto",
                "calibration_mode": "automatica",
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


if __name__ == "__main__":
    unittest.main()
