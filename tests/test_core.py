import math
import unittest
import calendar
from datetime import date

from lutz_scholz_qgis.core.model import (
    LutzError,
    ModelParameters,
    MonthlyRecord,
    RetentionConfig,
    depth_to_discharge,
    effective_precipitation_year,
    metrics,
    average_year,
    run_model,
)
from lutz_scholz_qgis.core import (
    apply_hargreaves,
    calculate_retention_components,
    regional_supply,
    select_k_by_criteria,
    calibrate_retention_and_a,
    calibrate_parameters,
    estimate_c_observed,
    estimate_c_southern_region,
    estimate_c_turc,
    extend_pisco_temperature,
    merge_precipitation_temperature,
    precipitation_statistics,
    select_climate_variables,
    summarize_etp,
    chronological_observed_split,
)

DEFAULT_POSITIONS = (0, 0, 0, 1, 2, 3, 4, 5, 6, 0, 0, 0)
DEFAULT_SUPPLY = (.5, .3, .2, 0, 0, 0, 0, 0, 0, 0, 0, 0)


class LutzCoreTests(unittest.TestCase):
    def test_negative_average_year_has_strict_and_traced_clip_modes(self):
        pe = [0.1] * 12
        retention = {
            "gasto_mm": [0.0] * 12,
            "abastecimiento_mm": [1.0] + [0.0] * 11,
        }
        with self.assertRaises(LutzError):
            average_year(pe, retention, "strict")
        diagnostics = {}
        values = average_year(pe, retention, "controlled_clip", diagnostics)
        self.assertEqual(values[0], 0.0)
        self.assertEqual(diagnostics["clipped_months"], 1)
        self.assertAlmostEqual(diagnostics["clipped_total_mm"], 0.9)

    def test_invalid_initial_balance_does_not_abort_automatic_search(self):
        pattern = [160, 190, 170, 80, 30, 10, 5, 5, 15, 40, 80, 130]
        climate = [
            MonthlyRecord(date(year, month, 1), float(precipitation))
            for year in range(2000, 2004)
            for month, precipitation in enumerate(pattern, start=1)
        ]
        retention = RetentionConfig(DEFAULT_POSITIONS, DEFAULT_SUPPLY)
        truth = run_model(
            climate,
            ModelParameters(950.54, .20, 5.0, a_dia=.02, negative_balance_mode="strict"),
            retention, (2000, 2003),
        )
        observed = [
            MonthlyRecord(record.fecha, record.precipitacion_mm, row["caudal_simulado_m3s"])
            for record, row in zip(climate, truth["rows"])
        ]
        base = ModelParameters(
            950.54, .10, 200.0, a_dia=.02,
            negative_balance_mode="controlled_clip",
        )
        best = calibrate_parameters(
            observed, base, retention, (2000, 2003), None,
            (0.0, 20.0), (.01, .03), (.05, .30), 4, "NSE", False,
        )
        self.assertIsNone(best["initial_result"])
        self.assertIn("láminas negativas", best["initial_error"])
        self.assertIsNotNone(best["result"])
        self.assertTrue(best["physical_balance_enforced"])
        self.assertEqual(best["result"]["parameters"]["negative_balance_mode"], "strict")
        self.assertFalse(best["result"]["balance_diagnostics"]["annual_balance_modified"])

    def test_automatic_calibration_rejects_bounds_without_physical_solution(self):
        records = [
            MonthlyRecord(date(year, month, 1), 1.0, 1.0)
            for year in range(2000, 2002)
            for month in range(1, 13)
        ]
        retention = RetentionConfig(DEFAULT_POSITIONS, DEFAULT_SUPPLY)
        base = ModelParameters(
            950.54, .10, 150.0, a_dia=.02,
            negative_balance_mode="controlled_clip",
        )
        with self.assertRaisesRegex(LutzError, "Q mensual mayor o igual a cero"):
            calibrate_parameters(
                records, base, retention, (2000, 2001), None,
                (100.0, 200.0), (.01, .03), (.05, .30), 4, "NSE", False,
            )

    def test_chronological_split_uses_complete_observed_years_at_60_40(self):
        records = []
        for year in range(1990, 2026):
            for month in range(1, 13):
                observed = None if (year == 2000 and month == 12) else 1.0
                records.append(MonthlyRecord(date(year, month, 1), 10.0, observed))
        split = chronological_observed_split(records)
        self.assertEqual(split["method"], "cronologico_60_40")
        self.assertEqual(split["calibration_period"], (1990, 2011))
        self.assertEqual(split["validation_period"], (2012, 2025))
        self.assertEqual(len(split["calibration_years"]), 21)
        self.assertEqual(len(split["validation_years"]), 14)
        self.assertEqual(split["excluded_years"], [2000])

    def test_effective_precipitation_closes_annual_mass(self):
        precipitation = [160, 190, 170, 80, 30, 10, 5, 5, 15, 40, 80, 130]
        result = effective_precipitation_year(precipitation, 0.17)
        self.assertAlmostEqual(sum(result["values"]), 0.17 * sum(precipitation), places=8)
        self.assertTrue(all(0 <= pe <= p for pe, p in zip(result["values"], precipitation)))

    def test_depth_to_discharge_matlab_calendar(self):
        calculated = depth_to_discharge(1.0, 1.0, 2000, 1, True)
        expected = 1000.0 / (30 * 86400.0)
        self.assertAlmostEqual(calculated, expected, places=12)

    def test_metrics_are_perfect_for_equal_series(self):
        result = metrics([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertAlmostEqual(result["NSE"], 1.0)
        self.assertAlmostEqual(result["LogNSE"], 1.0)
        self.assertAlmostEqual(result["KGE"], 1.0)
        self.assertAlmostEqual(result["Correlacion"], 1.0)
        self.assertAlmostEqual(result["RMSE"], 0.0)
        self.assertAlmostEqual(result["PBIAS_porcentaje"], 0.0)
        self.assertAlmostEqual(result["PBIAS_altos_porcentaje"], 0.0)

    def test_complete_model_returns_monthly_rows_and_balances(self):
        pattern = [160, 190, 170, 80, 30, 10, 5, 5, 15, 40, 80, 130]
        records = []
        for year in range(1990, 1996):
            factor = 0.90 + 0.04 * (year - 1990)
            for month, precipitation in enumerate(pattern, start=1):
                records.append(MonthlyRecord(date(year, month, 1), precipitation * factor, 1.0 + precipitation / 40.0))
        parameters = ModelParameters(950.54, 0.17, 15.0, k=0.034, compatible_matlab=True)
        retention = RetentionConfig(
            (0, 0, 0, 1, 2, 3, 4, 5, 6, 0, 0, 0),
            (0.50, 0.30, 0.20, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        )
        result = run_model(records, parameters, retention, (1990, 1994), (1995, 1995))
        self.assertEqual(len(result["rows"]), 72)
        self.assertAlmostEqual(result["retention"]["error_balance_mm"], 0.0, places=8)
        self.assertIsNotNone(result["metrics_calibration"])
        self.assertIsNotNone(result["metrics_validation"])

    def test_hargreaves_adds_monthly_etp(self):
        record = MonthlyRecord(date(2001, 1, 1), 100, 2.0, 7, 13, 20)
        calculated = apply_hargreaves([record], -12)[0]
        self.assertGreater(calculated.etp_mm, 0)
        self.assertEqual(calculated.caudal_observado_m3s, 2.0)

    def test_retention_components_reproduces_reference_case(self):
        result = calculate_retention_components(1020, (
            {"type": "acuifero", "area_km2": 15, "slope_fraction": .05},
            {"type": "lagunas_pantanos", "area_km2": 10},
            {"type": "nevados", "area_km2": 5},
        ))
        self.assertAlmostEqual(result["volume_total_mmc"], 11.6625, places=8)
        self.assertAlmostEqual(result["retention_mm"], 11.4338235294118, places=8)

    def test_regional_supply_and_k_recommendation(self):
        self.assertAlmostEqual(sum(regional_supply("ancash_santa")["fractions"]), 1.0)
        selected = select_k_by_criteria(950, 30, 13.5, "desconocido", "puna_poco_desarrollada", "bajo")
        self.assertEqual(selected["option"], "muy_rapido")
        self.assertAlmostEqual(selected["K"], .034)

    def test_automatic_calibration_uses_observed_flows(self):
        source = __import__("pathlib").Path(__file__).parents[1] / "examples" / "serie_mensual_ejemplo.csv"
        from lutz_scholz_qgis.io_utils import read_monthly_csv
        records = read_monthly_csv(str(source))
        parameters = ModelParameters(950.54, .17, 15, k=.034)
        retention = RetentionConfig(DEFAULT_POSITIONS, DEFAULT_SUPPLY)
        best = calibrate_retention_and_a(records, parameters, retention, (1990, 1994), (1995, 1995), (0, 80), (.008, .04), 4)
        self.assertEqual(best["trials"], 32)
        self.assertIsNotNone(best["result"]["metrics_calibration"])

    def test_turc_estimation_uses_complete_annual_precipitation_and_temperature(self):
        records = [
            MonthlyRecord(date(2000, month, 1), 100.0, temp_media_c=10.0)
            for month in range(1, 13)
        ]
        result = estimate_c_turc(records, (2000, 2000))
        factor = 300 + 25 * 10 + 0.05 * 10 ** 3
        deficit = 1200 / math.sqrt(0.9 + (1200 / factor) ** 2)
        self.assertAlmostEqual(result["temperature_factor"], factor)
        self.assertAlmostEqual(result["coefficient"], (1200 - deficit) / 1200)

    def test_southern_regional_estimation_uses_hargreaves_etp(self):
        records = [
            MonthlyRecord(date(2000, month, 1), 100.0, etp_mm=80.0)
            for month in range(1, 13)
        ]
        result = estimate_c_southern_region(records, (2000, 2000))
        expected = 3.16e12 * 1200 ** -0.571 * 960 ** -3.686
        self.assertAlmostEqual(result["coefficient"], expected)

    def test_observed_coefficient_converts_discharge_to_annual_depth(self):
        area = 100.0
        records = []
        for month in range(1, 13):
            days = calendar.monthrange(2000, month)[1]
            discharge = (20.0 / 1000.0 * area * 1_000_000.0) / (days * 86400.0)
            records.append(MonthlyRecord(date(2000, month, 1), 100.0, discharge))
        result = estimate_c_observed(records, (2000, 2000), area)
        self.assertAlmostEqual(result["runoff_observed_mm"], 240.0)
        self.assertAlmostEqual(result["coefficient"], 0.2)

    def test_optional_c_calibration_moves_coefficient_toward_observed_volume(self):
        pattern = [160, 190, 170, 80, 30, 10, 5, 5, 15, 40, 80, 130]
        climate = [
            MonthlyRecord(date(year, month, 1), precipitation * (1 + .02 * (year - 2000)))
            for year in range(2000, 2005)
            for month, precipitation in enumerate(pattern, start=1)
        ]
        retention = RetentionConfig(DEFAULT_POSITIONS, DEFAULT_SUPPLY)
        true_parameters = ModelParameters(950.54, .30, 15.0, a_dia=.02)
        truth = run_model(climate, true_parameters, retention, (2000, 2004))
        observed = [
            MonthlyRecord(record.fecha, record.precipitacion_mm, row["caudal_simulado_m3s"])
            for record, row in zip(climate, truth["rows"])
        ]
        base = ModelParameters(950.54, .15, 15.0, a_dia=.02)
        best = calibrate_parameters(
            observed, base, retention, (2000, 2004), None,
            (10, 20), (.015, .025), (.10, .40), 5, "Combinado", True,
        )
        self.assertTrue(best["calibrated_c"])
        self.assertLess(abs(best["coefficient"] - .30), abs(.15 - .30))
        self.assertEqual(best["trials"], 60)

    def test_precipitation_statistics_has_annual_and_monthly_summaries(self):
        records = [MonthlyRecord(date(year, month, 1), float(month)) for year in (2000, 2001) for month in range(1, 13)]
        result = precipitation_statistics(records)
        self.assertEqual(result["n_anios"], 2)
        self.assertEqual(len(result["mensual"]), 12)
        self.assertAlmostEqual(result["media_anual_mm"], 78.0)

    def test_pisco_temperature_extension_corrects_era5_by_calendar_month(self):
        pisco, era5 = [], []
        for year in range(2000, 2006):
            for month in range(1, 13):
                candidate_min = 4.0 + month * 0.25 + (year - 2000) * 0.1
                candidate_max = candidate_min + 9.0
                era5.append({
                    "fecha": date(year, month, 1).isoformat(),
                    "temp_min_c": candidate_min,
                    "temp_media_c": candidate_min + 4.5,
                    "temp_max_c": candidate_max,
                })
                pisco.append({
                    "fecha": date(year, month, 1).isoformat(),
                    "temp_min_c": 1.5 + 1.1 * candidate_min,
                    "temp_media_c": 2.0 + 1.05 * (candidate_min + 4.5),
                    "temp_max_c": 2.5 + 1.0 * candidate_max,
                })
        for month in range(1, 13):
            value = 5.0 + month * 0.25
            era5.append({
                "fecha": date(2006, month, 1).isoformat(),
                "temp_min_c": value,
                "temp_media_c": value + 4.5,
                "temp_max_c": value + 9.0,
            })
        pisco.append({
            "fecha": "2007-01-01",
            "temp_min_c": None,
            "temp_media_c": None,
            "temp_max_c": None,
        })
        result = extend_pisco_temperature(pisco, era5)
        extended = [row for row in result["rows"] if row["fecha"].startswith("2006-")]
        self.assertEqual(len(extended), 12)
        self.assertTrue(all(row["metodo"] == "corregido_con_PISCO" for row in extended))
        self.assertTrue(all(row["temp_min_c"] <= row["temp_media_c"] <= row["temp_max_c"] for row in extended))
        self.assertGreater(result["models"]["temp_min_c"][1]["correlacion"], .99)
        self.assertEqual(result["last_reference"], "2005-12-01")

    def test_combined_climate_preserves_selected_precipitation(self):
        precipitation = [{
            "fecha": "2001-01-01",
            "precipitacion_mm": 123.4,
            "fuente": "CHIRPS Daily",
            "coverage_pct": 97.5,
            "image_count": 31,
        }]
        temperature = [{
            "fecha": "2001-01-01",
            "temp_min_c": 4.0,
            "temp_media_c": 9.0,
            "temp_max_c": 14.0,
            "fuente": "ERA5-Land",
            "metodo": "corregido_con_PISCO",
        }]
        result = merge_precipitation_temperature(precipitation, temperature)
        self.assertEqual(result[0]["precipitacion_mm"], 123.4)
        self.assertEqual(result[0]["temp_media_c"], 9.0)
        self.assertEqual(result[0]["fuente_precipitacion"], "CHIRPS Daily")
        self.assertEqual(result[0]["fuente_temperatura"], "ERA5-Land")
        self.assertEqual(result[0]["coverage_pct"], 97.5)

    def test_variable_selection_keeps_era5_temperature_without_precipitation(self):
        rows = [{
            "fecha": "2001-01-01",
            "precipitacion_mm": 222.0,
            "temp_min_c": 3.0,
            "temp_media_c": 8.0,
            "temp_max_c": 14.0,
        }]
        temperature = select_climate_variables(rows, "temperature")
        precipitation = select_climate_variables(rows, "precipitation")
        self.assertIsNone(temperature[0]["precipitacion_mm"])
        self.assertEqual(temperature[0]["temp_media_c"], 8.0)
        self.assertEqual(precipitation[0]["precipitacion_mm"], 222.0)
        self.assertIsNone(precipitation[0]["temp_media_c"])

    def test_etp_summary_contains_monthly_rows_and_annual_totals(self):
        records = [
            MonthlyRecord(date(2001, month, 1), 100.0, etp_mm=float(month))
            for month in range(1, 13)
        ]
        summary = summarize_etp(records)
        self.assertEqual(len(summary["mensual"]), 12)
        self.assertEqual(summary["anual"][0]["meses_validos"], 12)
        self.assertAlmostEqual(summary["anual"][0]["etp_total_mm"], 78.0)
        self.assertAlmostEqual(summary["anual"][0]["etp_media_mensual_mm"], 6.5)


if __name__ == "__main__":
    unittest.main()
