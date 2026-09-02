"""Estadistica descriptiva basica de la precipitacion mensual."""

from __future__ import annotations

import math
import statistics

from .model import MonthlyRecord, validate_records


def precipitation_statistics(records):
    validate_records(records)
    years = sorted({record.fecha.year for record in records})
    annual = []
    for year in years:
        values = [record.precipitacion_mm for record in records if record.fecha.year == year]
        annual.append({"anio": year, "precipitacion_anual_mm": sum(values)})
    totals = [row["precipitacion_anual_mm"] for row in annual]
    mean_annual = statistics.fmean(totals)
    std_annual = statistics.stdev(totals) if len(totals) > 1 else 0.0
    monthly = []
    for month in range(1, 13):
        values = [record.precipitacion_mm for record in records if record.fecha.month == month]
        mean = statistics.fmean(values)
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        monthly.append({
            "mes": month, "media_mm": mean, "desviacion_mm": std,
            "cv_porcentaje": 100 * std / mean if mean else None,
            "min_mm": min(values), "max_mm": max(values),
        })
    return {
        "n_meses": len(records), "n_anios": len(years),
        "media_anual_mm": mean_annual, "desviacion_anual_mm": std_annual,
        "cv_anual_porcentaje": 100 * std_annual / mean_annual if mean_annual else None,
        "min_anual_mm": min(totals), "max_anual_mm": max(totals),
        "anual": annual, "mensual": monthly,
    }
