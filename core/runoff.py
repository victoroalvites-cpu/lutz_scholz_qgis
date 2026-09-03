"""Estimacion explicable del coeficiente de escorrentia del modelo Lutz Sholtz."""

from __future__ import annotations

import calendar
import math

from .model import LutzError


def _selected(records, years):
    start, end = years
    if start > end:
        raise LutzError("El periodo para estimar C es invalido.")
    return [record for record in records if start <= record.fecha.year <= end]


def _complete_annual_means(records, fields):
    grouped = {}
    for record in records:
        grouped.setdefault(record.fecha.year, []).append(record)
    annual = []
    for year, rows in sorted(grouped.items()):
        months = {row.fecha.month for row in rows}
        if months != set(range(1, 13)):
            continue
        values = {}
        valid = True
        for field in fields:
            field_values = [getattr(row, field) for row in rows]
            if any(value is None or not math.isfinite(float(value)) for value in field_values):
                valid = False
                break
            values[field] = field_values
        if valid:
            annual.append((year, values))
    if not annual:
        raise LutzError("No existen años completos con las variables requeridas para estimar C.")
    return annual


def _bounded(raw):
    value = min(1.0, max(0.0, float(raw)))
    warning = ""
    if not math.isclose(value, raw, rel_tol=0, abs_tol=1e-12):
        warning = f"El valor bruto C={raw:.5f} quedo fuera de [0, 1] y se limito a {value:.5f}."
    return value, warning


def estimate_c_turc(records, years):
    """Calcula Turc clasico con P anual media y T media del periodo."""

    annual = _complete_annual_means(_selected(records, years), ("precipitacion_mm", "temp_media_c"))
    precipitation = sum(sum(values["precipitacion_mm"]) for _, values in annual) / len(annual)
    temperature = sum(sum(values["temp_media_c"]) / 12.0 for _, values in annual) / len(annual)
    if precipitation <= 0:
        raise LutzError("La precipitacion anual debe ser positiva para calcular C por Turc.")
    temperature_factor = 300.0 + 25.0 * temperature + 0.05 * temperature ** 3
    if temperature_factor <= 0:
        raise LutzError("El coeficiente termico L de Turc no es positivo.")
    deficit = precipitation / math.sqrt(0.9 + (precipitation / temperature_factor) ** 2)
    raw = (precipitation - deficit) / precipitation
    coefficient, bounded_warning = _bounded(raw)
    warnings = [
        "Turc clasico es una referencia; en la sierra sur puede presentar sesgo por temperaturas bajas."
    ]
    if bounded_warning:
        warnings.append(bounded_warning)
    return {
        "method": "Turc clasico",
        "years": years,
        "complete_years": len(annual),
        "precipitation_annual_mm": precipitation,
        "temperature_annual_c": temperature,
        "etp_annual_mm": None,
        "temperature_factor": temperature_factor,
        "deficit_mm": deficit,
        "coefficient_raw": raw,
        "coefficient": coefficient,
        "warning": " ".join(warnings),
    }


def estimate_c_southern_region(records, years):
    """Ecuacion regional de la sierra sur basada en P y ETP Hargreaves."""

    annual = _complete_annual_means(_selected(records, years), ("precipitacion_mm", "etp_mm"))
    precipitation = sum(sum(values["precipitacion_mm"]) for _, values in annual) / len(annual)
    etp = sum(sum(values["etp_mm"]) for _, values in annual) / len(annual)
    if precipitation <= 0 or etp <= 0:
        raise LutzError("P y ETP anuales deben ser positivas para estimar C regional.")
    raw = 3.16e12 * precipitation ** -0.571 * etp ** -3.686
    coefficient, bounded_warning = _bounded(raw)
    deficit = precipitation * (1.0 - coefficient)
    warnings = [
        "La ecuacion regional debe usarse solo en cuencas comparables de la sierra sur."
    ]
    if bounded_warning:
        warnings.append(bounded_warning)
    return {
        "method": "Regional sierra sur",
        "years": years,
        "complete_years": len(annual),
        "precipitation_annual_mm": precipitation,
        "temperature_annual_c": None,
        "etp_annual_mm": etp,
        "temperature_factor": None,
        "deficit_mm": deficit,
        "coefficient_raw": raw,
        "coefficient": coefficient,
        "warning": " ".join(warnings),
    }


def estimate_c_observed(records, years, area_km2):
    """Obtiene C como lamina observada anual dividida por precipitacion anual."""

    if not math.isfinite(float(area_km2)) or float(area_km2) <= 0:
        raise LutzError("El area debe ser positiva para calcular C observado.")
    annual = _complete_annual_means(
        _selected(records, years), ("precipitacion_mm", "caudal_observado_m3s")
    )
    precipitation_values = []
    runoff_values = []
    for year, values in annual:
        precipitation_values.append(sum(values["precipitacion_mm"]))
        volume_m3 = 0.0
        for month, discharge in enumerate(values["caudal_observado_m3s"], start=1):
            days = calendar.monthrange(year, month)[1]
            volume_m3 += float(discharge) * days * 86400.0
        runoff_values.append(volume_m3 / (float(area_km2) * 1_000_000.0) * 1000.0)
    precipitation = sum(precipitation_values) / len(precipitation_values)
    runoff = sum(runoff_values) / len(runoff_values)
    if precipitation <= 0:
        raise LutzError("La precipitacion anual debe ser positiva para calcular C observado.")
    raw = runoff / precipitation
    coefficient, bounded_warning = _bounded(raw)
    warnings = [
        "C observado supone que Q representa toda la salida de la cuenca; revise derivaciones y trasvases."
    ]
    if bounded_warning:
        warnings.append(bounded_warning)
    return {
        "method": "Observado Q/P",
        "years": years,
        "complete_years": len(annual),
        "precipitation_annual_mm": precipitation,
        "temperature_annual_c": None,
        "etp_annual_mm": None,
        "temperature_factor": None,
        "deficit_mm": precipitation - runoff,
        "runoff_observed_mm": runoff,
        "coefficient_raw": raw,
        "coefficient": coefficient,
        "warning": " ".join(warnings),
    }
