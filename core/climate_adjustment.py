"""Ajuste y extension de series climaticas mensuales.

El modulo es Python puro para poder probar la correccion PISCO--ERA5 sin
depender de QGIS ni de Earth Engine. La regresion se estima por mes calendario
porque el sesgo de los productos climaticos suele ser estacional.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .model import LutzError


TEMPERATURE_FIELDS = ("temp_min_c", "temp_max_c")
ALL_TEMPERATURE_FIELDS = ("temp_min_c", "temp_media_c", "temp_max_c")
CLIMATE_APPLICATION_MODES = ("temperature", "precipitation", "all")


def _date(value) -> date:
    if isinstance(value, date):
        return value.replace(day=1)
    text = str(value or "").strip()[:10]
    for pattern in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(text, pattern).date().replace(day=1)
        except ValueError:
            continue
    raise LutzError(f"Fecha climatica no reconocida: {value!r}.")


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _index(rows: Iterable[Mapping[str, object]]) -> Dict[date, Mapping[str, object]]:
    result = {}
    for row in rows:
        when = _date(row.get("fecha"))
        if when in result:
            raise LutzError(f"La serie climatica contiene el mes duplicado {when:%Y-%m}.")
        result[when] = row
    return result


def _fit(x: Sequence[float], y: Sequence[float]) -> Dict[str, float]:
    """Ajusta ``y = intercepto + pendiente*x`` y devuelve diagnosticos."""

    if len(x) != len(y) or len(x) < 2:
        raise LutzError("No existen pares suficientes para ajustar la regresion climatica.")
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    sxx = sum((value - mean_x) ** 2 for value in x)
    syy = sum((value - mean_y) ** 2 for value in y)
    sxy = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    if sxx <= 1e-12:
        slope = 1.0
        intercept = mean_y - mean_x
    else:
        slope = sxy / sxx
        intercept = mean_y - slope * mean_x
    predicted = [intercept + slope * value for value in x]
    correlation = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0
    rmse_before = math.sqrt(sum((a - b) ** 2 for a, b in zip(x, y)) / len(x))
    rmse_after = math.sqrt(sum((a - b) ** 2 for a, b in zip(predicted, y)) / len(x))
    bias_before = sum(a - b for a, b in zip(x, y)) / len(x)
    bias_after = sum(a - b for a, b in zip(predicted, y)) / len(x)
    return {
        "n": len(x),
        "intercepto": intercept,
        "pendiente": slope,
        "correlacion": correlation,
        "r2": correlation ** 2,
        "rmse_antes": rmse_before,
        "rmse_despues": rmse_after,
        "sesgo_antes": bias_before,
        "sesgo_despues": bias_after,
    }


def fit_monthly_temperature_adjustment(
    reference_rows: Sequence[Mapping[str, object]],
    candidate_rows: Sequence[Mapping[str, object]],
    fields: Sequence[str] = TEMPERATURE_FIELDS,
    min_pairs: int = 3,
) -> Dict[str, Dict[int, Dict[str, float]]]:
    """Ajusta ERA5 (candidato) contra PISCO (referencia) por mes calendario."""

    reference = _index(reference_rows)
    candidate = _index(candidate_rows)
    common = sorted(set(reference) & set(candidate))
    if not common:
        raise LutzError("PISCO y ERA5 no tienen un periodo comun para correlacionar.")
    models: Dict[str, Dict[int, Dict[str, float]]] = {}
    for field in fields:
        models[field] = {}
        for month in range(1, 13):
            pairs: List[Tuple[float, float]] = []
            for when in common:
                if when.month != month:
                    continue
                ref_value = reference[when].get(field)
                candidate_value = candidate[when].get(field)
                if _finite(ref_value) and _finite(candidate_value):
                    pairs.append((float(candidate_value), float(ref_value)))
            if len(pairs) < min_pairs:
                raise LutzError(
                    f"Solo existen {len(pairs)} pares para {field} en el mes {month}; "
                    f"se requieren al menos {min_pairs}."
                )
            models[field][month] = _fit(
                [pair[0] for pair in pairs], [pair[1] for pair in pairs]
            )
    return models


def extend_pisco_temperature(
    pisco_rows: Sequence[Mapping[str, object]],
    era5_rows: Sequence[Mapping[str, object]],
    min_pairs: int = 3,
) -> Dict[str, object]:
    """Conserva PISCO y completa meses posteriores con ERA5 corregido.

    Tmin y Tmax se corrigen de forma independiente. Tmedia se deriva de ambas
    para mantener siempre ``Tmin <= Tmedia <= Tmax``.
    """

    # Las consultas pueden incluir meses fuera de la disponibilidad de una
    # coleccion. Esos registros vacios no deben desplazar la ultima fecha PISCO
    # ni impedir que ERA5 complete el periodo posterior.
    pisco_valid = [
        row for row in pisco_rows
        if all(_finite(row.get(field)) for field in TEMPERATURE_FIELDS)
    ]
    era5_valid = [
        row for row in era5_rows
        if all(_finite(row.get(field)) for field in TEMPERATURE_FIELDS)
    ]
    pisco = _index(pisco_valid)
    era5 = _index(era5_valid)
    if not pisco:
        raise LutzError("No se cargo la serie PISCO de temperatura.")
    models = fit_monthly_temperature_adjustment(pisco_valid, era5_valid, min_pairs=min_pairs)
    last_reference = max(pisco)
    rows: List[Dict[str, object]] = []
    for when in sorted(set(pisco) | {value for value in era5 if value > last_reference}):
        if when in pisco:
            row = dict(pisco[when])
            row.update({"fecha": when.isoformat(), "fuente": "PISCO", "metodo": "observado"})
            rows.append(row)
            continue
        source = era5.get(when)
        if source is None:
            continue
        corrected = {}
        missing = False
        for field in TEMPERATURE_FIELDS:
            value = source.get(field)
            if not _finite(value):
                missing = True
                break
            model = models[field][when.month]
            corrected[field] = model["intercepto"] + model["pendiente"] * float(value)
        if missing:
            continue
        low = min(corrected["temp_min_c"], corrected["temp_max_c"])
        high = max(corrected["temp_min_c"], corrected["temp_max_c"])
        row = dict(source)
        row.update(
            {
                "fecha": when.isoformat(),
                "temp_min_c": low,
                "temp_media_c": (low + high) / 2.0,
                "temp_max_c": high,
                "fuente": "ERA5-Land",
                "metodo": "corregido_con_PISCO",
            }
        )
        rows.append(row)
    return {
        "rows": rows,
        "models": models,
        "last_reference": last_reference.isoformat(),
        "overlap_months": len(set(pisco) & set(era5)),
    }


def merge_precipitation_temperature(
    precipitation_rows: Sequence[Mapping[str, object]],
    temperature_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    """Une la precipitacion elegida con temperatura por fecha mensual.

    El calendario de salida pertenece a la precipitacion, que es la entrada
    obligatoria del modelo Lutz. La trazabilidad de ambas fuentes se conserva
    en columnas separadas y la cobertura visible corresponde a precipitacion.
    """

    precipitation = _index(precipitation_rows)
    temperature = _index(temperature_rows)
    if not precipitation:
        raise LutzError("No se cargo una serie de precipitacion para combinar.")
    if not temperature:
        raise LutzError("No se genero una serie de temperatura extendida.")
    rows: List[Dict[str, object]] = []
    for when in sorted(precipitation):
        p_row = precipitation[when]
        row = dict(p_row)
        p_source = str(p_row.get("fuente") or p_row.get("source_key") or "Precipitacion")
        row["fecha"] = when.isoformat()
        row["fuente_precipitacion"] = p_source
        row["metodo_precipitacion"] = p_row.get("metodo") or "media_areal"
        t_row = temperature.get(when)
        if t_row is not None:
            for field in ("temp_min_c", "temp_media_c", "temp_max_c"):
                row[field] = t_row.get(field)
            t_source = str(t_row.get("fuente") or "Temperatura")
            t_method = t_row.get("metodo") or "media_areal"
            row["fuente_temperatura"] = t_source
            row["metodo_temperatura"] = t_method
            row["coverage_temperatura_pct"] = t_row.get("coverage_pct")
            row["imagenes_temperatura"] = t_row.get("image_count")
            row["fuente"] = f"P: {p_source} | T: {t_source}"
            row["metodo"] = f"P: {row['metodo_precipitacion']} | T: {t_method}"
        else:
            row["fuente_temperatura"] = ""
            row["metodo_temperatura"] = ""
            row["fuente"] = f"P: {p_source} | T: sin dato"
            row["metodo"] = f"P: {row['metodo_precipitacion']}"
        rows.append(row)
    return rows


def select_climate_variables(
    rows: Sequence[Mapping[str, object]],
    mode: str,
) -> List[Dict[str, object]]:
    """Selecciona que variables GEE se incorporaran al modelo.

    La tabla climatica puede contener simultaneamente precipitacion y
    temperatura (por ejemplo ERA5-Land), pero la aplicacion al modelo debe ser
    explicita. Los campos no seleccionados se dejan vacios para que la capa de
    integracion conserve los valores que ya estaban cargados por fecha.
    """

    selected = str(mode or "").strip().lower()
    if selected not in CLIMATE_APPLICATION_MODES:
        raise LutzError(f"Modo de integracion climatica no reconocido: {mode!r}.")
    output: List[Dict[str, object]] = []
    for source in rows:
        row = dict(source)
        if selected == "temperature":
            row["precipitacion_mm"] = None
        elif selected == "precipitation":
            for field in ALL_TEMPERATURE_FIELDS:
                row[field] = None
        row["modo_integracion"] = selected
        output.append(row)
    return output
