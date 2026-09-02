"""Calculos climaticos auxiliares del complemento."""

from __future__ import annotations

import calendar
import math
from dataclasses import replace
from datetime import date, timedelta
from typing import Dict, List, Sequence

from .model import LutzError, MonthlyRecord


def extraterrestrial_radiation_mj_m2_day(latitude_degrees: float, day: date) -> float:
    """Radiacion extraterrestre diaria FAO-56 para una fecha y latitud."""

    if not math.isfinite(latitude_degrees) or abs(latitude_degrees) > 90:
        raise LutzError("Latitud invalida para Hargreaves-Samani.")
    latitude = math.radians(latitude_degrees)
    julian = day.timetuple().tm_yday
    dr = 1 + 0.033 * math.cos(2 * math.pi * julian / 365)
    declination = 0.409 * math.sin(2 * math.pi * julian / 365 - 1.39)
    argument = max(-1.0, min(1.0, -math.tan(latitude) * math.tan(declination)))
    sunset_angle = math.acos(argument)
    gsc = 0.0820
    return (24 * 60 / math.pi) * gsc * dr * (
        sunset_angle * math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.sin(sunset_angle)
    )


def hargreaves_monthly_mm(
    month: date,
    temp_mean_c: float,
    temp_max_c: float,
    temp_min_c: float,
    latitude_degrees: float,
) -> float:
    """ET0 mensual por Hargreaves-Samani, compatible con el codigo R 2.4."""

    values = (temp_mean_c, temp_max_c, temp_min_c)
    if not all(math.isfinite(value) for value in values):
        raise LutzError("Hargreaves-Samani requiere temperaturas completas.")
    if temp_max_c < temp_min_c:
        raise LutzError("Temp_max debe ser mayor o igual a Temp_min.")
    days = calendar.monthrange(month.year, month.month)[1]
    middle = date(month.year, month.month, 1) + timedelta(days=(days - 1) // 2)
    ra_mm = 0.408 * extraterrestrial_radiation_mj_m2_day(latitude_degrees, middle)
    et0_day = 0.0023 * (temp_mean_c + 17.8) * math.sqrt(max(temp_max_c - temp_min_c, 0)) * ra_mm
    return max(et0_day, 0.0) * days


def apply_hargreaves(records: Sequence[MonthlyRecord], latitude_degrees: float):
    """Devuelve nuevos registros conservando P y Q e incorporando ET0."""

    output = []
    for record in records:
        if None in (record.temp_media_c, record.temp_max_c, record.temp_min_c):
            raise LutzError(
                f"Faltan temperaturas para {record.fecha:%Y-%m}; Hargreaves requiere Tmin, Tmedia y Tmax."
            )
        etp = hargreaves_monthly_mm(
            record.fecha,
            float(record.temp_media_c),
            float(record.temp_max_c),
            float(record.temp_min_c),
            latitude_degrees,
        )
        output.append(replace(record, etp_mm=etp))
    return output


def summarize_etp(records: Sequence[MonthlyRecord]) -> Dict[str, List[Dict[str, object]]]:
    """Construye los cuadros mensual y anual de evapotranspiracion."""

    monthly: List[Dict[str, object]] = []
    by_year: Dict[int, List[float]] = {}
    for record in sorted(records, key=lambda item: item.fecha):
        value = record.etp_mm
        monthly.append(
            {
                "fecha": record.fecha.isoformat(),
                "temp_min_c": record.temp_min_c,
                "temp_media_c": record.temp_media_c,
                "temp_max_c": record.temp_max_c,
                "etp_mm": value,
            }
        )
        if value is not None and math.isfinite(float(value)):
            by_year.setdefault(record.fecha.year, []).append(float(value))

    years = sorted({record.fecha.year for record in records})
    annual: List[Dict[str, object]] = []
    for year in years:
        values = by_year.get(year, [])
        annual.append(
            {
                "anio": year,
                "meses_validos": len(values),
                "etp_total_mm": sum(values) if values else None,
                "etp_media_mensual_mm": (sum(values) / len(values)) if values else None,
            }
        )
    return {"mensual": monthly, "anual": annual}
