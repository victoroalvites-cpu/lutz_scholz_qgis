"""Nucleo numerico puro Python del metodo clasico de Lutz Scholz.

El modulo no importa QGIS ni librerias externas. Esto permite comprobar las
ecuaciones con ``unittest`` y reutilizarlas en otras interfaces.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..version import PLUGIN_VERSION


MONTHS = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
MATLAB_DAYS = (30, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

# Filas I-VII; columnas a0...a5 para PE = sum(ai * P**i).
PE_COEFFICIENTS = (
    (-1.8357e-2, -1.8497e-2, 1.1055e-3, -1.2045e-5, 1.4396e-7, -2.8497e-10),
    (-2.1387e-2, 1.3576e-1, -2.2963e-3, 4.3489e-5, -8.9009e-8, -8.7917e-11),
    (1.6317e-2, 2.2729e-1, -3.9068e-3, 1.1840e-4, -7.0402e-7, 1.3612e-9),
    (5.3963e-2, 3.4755e-2, 1.1230e-2, -6.4489e-5, 1.7956e-7, -1.8796e-10),
    (-7.8846e-2, 7.6535e-3, 2.0849e-2, -2.1900e-4, 1.1073e-6, -2.1312e-9),
    (-1.0373e-2, 1.3534e-1, 1.8204e-2, -1.8055e-4, 8.4914e-7, -1.5249e-9),
    (-4.2424e-2, 2.2718e-1, 1.9645e-2, -2.2200e-4, 1.1467e-6, -2.2070e-9),
)
PE_NAMES = ("I", "II", "III", "IV", "V", "VI", "VII")
PE_UPPER_LIMITS = (177.8, 152.4, 127.0, 101.6, 76.2, 50.8, 25.4)
PE_UPPER_LOSSES = (120.6, 86.4, 59.7, 33.0, 20.4, 15.4, 10.4)


class LutzError(ValueError):
    """Error de validacion o calculo comprensible para el usuario."""


@dataclass(frozen=True)
class MonthlyRecord:
    fecha: date
    precipitacion_mm: Optional[float]
    caudal_observado_m3s: Optional[float] = None
    temp_min_c: Optional[float] = None
    temp_media_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    etp_mm: Optional[float] = None


@dataclass(frozen=True)
class ModelParameters:
    area_km2: float
    coef_escorrentia: float
    retencion_mm: float
    k: Optional[float] = None
    a_dia: Optional[float] = None
    compatible_matlab: bool = True
    negative_balance_mode: str = "strict"


@dataclass(frozen=True)
class RetentionConfig:
    posiciones_gasto: Tuple[float, ...]
    fracciones_abastecimiento: Tuple[float, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LutzError(message)


def validate_records(records: Sequence[MonthlyRecord]) -> None:
    _require(bool(records), "No se encontraron registros mensuales.")
    previous = None
    for index, record in enumerate(records):
        _require(record.fecha.day == 1, "Cada fecha debe representar el primer dia del mes.")
        _require(
            record.precipitacion_mm is not None,
            f"Falta precipitacion para {record.fecha:%Y-%m}. Complete la serie desde Excel/CSV o Clima GEE.",
        )
        _require(math.isfinite(record.precipitacion_mm), "La precipitacion contiene valores no numericos.")
        _require(record.precipitacion_mm >= 0, "La precipitacion no puede ser negativa.")
        if record.caudal_observado_m3s is not None:
            _require(math.isfinite(record.caudal_observado_m3s), "El caudal observado contiene valores no numericos.")
            _require(record.caudal_observado_m3s >= 0, "El caudal observado no puede ser negativo.")
        for name, value in (
            ("temperatura minima", record.temp_min_c),
            ("temperatura media", record.temp_media_c),
            ("temperatura maxima", record.temp_max_c),
            ("ETP", record.etp_mm),
        ):
            if value is not None:
                _require(math.isfinite(value), f"La {name} contiene valores no numericos.")
        if record.temp_min_c is not None and record.temp_max_c is not None:
            _require(record.temp_max_c >= record.temp_min_c, "Temp_max debe ser mayor o igual a Temp_min.")
        if record.etp_mm is not None:
            _require(record.etp_mm >= 0, "La ETP no puede ser negativa.")
        if previous is not None:
            expected_year = previous.year + (1 if previous.month == 12 else 0)
            expected_month = 1 if previous.month == 12 else previous.month + 1
            _require(
                (record.fecha.year, record.fecha.month) == (expected_year, expected_month),
                f"La serie tiene un mes faltante o duplicado cerca del registro {index + 1}.",
            )
        previous = record.fecha
    _require(records[0].fecha.month == 1, "La serie debe iniciar en enero.")
    _require(records[-1].fecha.month == 12, "La serie debe terminar en diciembre.")
    _require(len(records) % 12 == 0, "La serie debe contener años completos.")


def _year_matrix(records: Sequence[MonthlyRecord], attribute: str) -> Tuple[List[int], List[List[Optional[float]]]]:
    years = sorted({record.fecha.year for record in records})
    matrix: List[List[Optional[float]]] = []
    for year in years:
        row = [getattr(record, attribute) for record in records if record.fecha.year == year]
        _require(len(row) == 12, f"El año {year} no contiene 12 meses.")
        matrix.append(row)
    return years, matrix


def pe_curves(precipitation_mm: float) -> List[float]:
    _require(math.isfinite(precipitation_mm) and precipitation_mm >= 0, "Precipitacion invalida.")
    curves = []
    for coefficients, upper, loss in zip(PE_COEFFICIENTS, PE_UPPER_LIMITS, PE_UPPER_LOSSES):
        value = sum(coefficient * precipitation_mm ** power for power, coefficient in enumerate(coefficients))
        if precipitation_mm > upper:
            value = precipitation_mm - loss
        curves.append(value)
    return curves


def _bounded_mass(values: Sequence[float], limits: Sequence[float], target: float) -> List[float]:
    result = [min(max(float(value), 0.0), float(limit)) for value, limit in zip(values, limits)]
    _require(-1e-10 <= target <= sum(limits) + 1e-10, "La masa anual de PE no es fisicamente alcanzable.")
    for _ in range(100):
        residual = target - sum(result)
        if abs(residual) <= 1e-10:
            break
        if residual > 0:
            capacity = [max(limit - value, 0.0) for value, limit in zip(result, limits)]
            total = sum(capacity)
            _require(total > 1e-10, "No existe capacidad para completar la PE anual.")
            result = [min(value + residual * available / total, limit) for value, available, limit in zip(result, capacity, limits)]
        else:
            removable = [max(value, 0.0) for value in result]
            total = sum(removable)
            _require(total > 1e-10, "No existe PE removible para cerrar el balance anual.")
            result = [max(value - (-residual) * available / total, 0.0) for value, available in zip(result, removable)]
    _require(abs(sum(result) - target) <= 1e-8, "No se pudo cerrar el balance anual de PE.")
    return result


def effective_precipitation_year(precipitation: Sequence[float], coefficient: float) -> Dict[str, object]:
    _require(len(precipitation) == 12, "Cada año debe contener 12 precipitaciones.")
    p = [float(value) for value in precipitation]
    _require(all(math.isfinite(value) and value >= 0 for value in p), "Precipitacion anual invalida.")
    _require(math.isfinite(coefficient) and 0 <= coefficient <= 1, "C debe estar entre 0 y 1.")
    annual = sum(p)
    if annual == 0:
        return {"values": [0.0] * 12, "lower": "I", "upper": "I", "weight": 0.0, "mass_error": 0.0}

    monthly_curves = [pe_curves(value) for value in p]
    annual_coefficients = [sum(row[index] for row in monthly_curves) / annual for index in range(7)]
    order = sorted(range(7), key=lambda index: annual_coefficients[index])
    ordered_coefficients = [annual_coefficients[index] for index in order]

    if coefficient <= ordered_coefficients[0]:
        lower = upper = 0
        weight = 0.0
    elif coefficient >= ordered_coefficients[-1]:
        lower = upper = 6
        weight = 0.0
    else:
        upper = next(index for index, value in enumerate(ordered_coefficients) if value >= coefficient)
        lower = upper - 1
        denominator = ordered_coefficients[upper] - ordered_coefficients[lower]
        _require(abs(denominator) > 1e-15, "Dos curvas de PE tienen igual coeficiente anual.")
        weight = (coefficient - ordered_coefficients[lower]) / denominator

    if lower == upper:
        raw = [row[order[lower]] for row in monthly_curves]
    else:
        raw = [
            (1 - weight) * row[order[lower]] + weight * row[order[upper]]
            for row in monthly_curves
        ]
    target = coefficient * annual
    values = _bounded_mass(raw, p, target)
    return {
        "values": values,
        "lower": PE_NAMES[order[lower]],
        "upper": PE_NAMES[order[upper]],
        "weight": weight,
        "mass_error": sum(values) - target,
    }


def effective_precipitation_series(matrix: Sequence[Sequence[float]], coefficient: float) -> Tuple[List[List[float]], List[Dict[str, object]]]:
    values, details = [], []
    for row in matrix:
        result = effective_precipitation_year(row, coefficient)
        values.append(list(result["values"]))
        details.append({key: value for key, value in result.items() if key != "values"})
    return values, details


def depletion_coefficient(area_km2: float, k: float) -> float:
    _require(math.isfinite(area_km2) and area_km2 > 0, "El area debe ser positiva.")
    _require(math.isfinite(k), "K debe ser numerico.")
    return -0.00252 * math.log(area_km2) + k


def retention_balance(parameters: ModelParameters, config: RetentionConfig) -> Dict[str, object]:
    positions = list(config.posiciones_gasto)
    supply = list(config.fracciones_abastecimiento)
    _require(len(positions) == 12 and len(supply) == 12, "Gasto y abastecimiento deben tener 12 valores.")
    _require(all(math.isfinite(value) and value >= 0 for value in positions), "Posiciones de gasto invalidas.")
    _require(all(math.isfinite(value) for value in supply), "Fracciones de abastecimiento invalidas.")
    _require(parameters.retencion_mm >= 0, "La retencion debe ser no negativa.")
    if parameters.retencion_mm > 0:
        _require(abs(sum(supply) - 1.0) <= 1e-8, f"El abastecimiento debe sumar 1. Suma actual: {sum(supply):.8f}.")
    a_day = parameters.a_dia
    if a_day is None:
        _require(parameters.k is not None, "Debe proporcionar K o a_dia.")
        a_day = depletion_coefficient(parameters.area_km2, float(parameters.k))
    _require(math.isfinite(a_day) and a_day > 0, "El coeficiente de agotamiento a debe ser positivo.")
    b0 = math.exp(-a_day * 30.0)
    raw = [b0 ** position if position > 0 else 0.0 for position in positions]
    _require(sum(raw) > 0 or parameters.retencion_mm == 0, "Debe existir al menos un mes de gasto.")
    normalized = [value / sum(raw) for value in raw] if sum(raw) else raw
    spending = [parameters.retencion_mm * value for value in normalized]
    supply_mm = [parameters.retencion_mm * value for value in supply]
    error = sum(spending) - sum(supply_mm)
    _require(abs(error) <= 1e-8, "El balance de retencion no cierra.")
    return {
        "a_dia": a_day,
        "b0_mensual": b0,
        "factores_gasto": normalized,
        "gasto_mm": spending,
        "abastecimiento_mm": supply_mm,
        "error_balance_mm": error,
    }


def average_year(
    pe_average: Sequence[float],
    retention: Dict[str, object],
    negative_mode: str = "strict",
    diagnostics: Optional[Dict[str, object]] = None,
) -> List[float]:
    """Cierra el año promedio y documenta cualquier recorte aplicado.

    ``strict`` conserva el control fisico tradicional y detiene la modelacion.
    ``controlled_clip`` permite reproducir el flujo de calculo que recorta a
    cero, pero deja trazabilidad mensual y cuantifica la masa modificada.
    """

    _require(negative_mode in ("strict", "controlled_clip"), "Modo de balance negativo no reconocido.")
    q = [
        float(pe) + float(spending) - float(supply)
        for pe, spending, supply in zip(pe_average, retention["gasto_mm"], retention["abastecimiento_mm"])
    ]
    negative_rows = []
    for index, value in enumerate(q):
        if value < -1e-10:
            negative_rows.append({
                "mes": MONTHS[index],
                "mes_numero": index + 1,
                "valor_original_mm": value,
                "precipitacion_efectiva_mm": float(pe_average[index]),
                "gasto_mm": float(retention["gasto_mm"][index]),
                "abastecimiento_mm": float(retention["abastecimiento_mm"][index]),
                "recorte_mm": -value,
            })
    if negative_rows and negative_mode == "strict":
        detail = ", ".join(f"{row['mes']}={row['valor_original_mm']:.4f} mm" for row in negative_rows)
        raise LutzError(
            "El año promedio contiene láminas negativas "
            f"({detail}). Revise C, R, K y abastecimiento, o use el recorte controlado con advertencia."
        )
    if diagnostics is not None:
        diagnostics.update({
            "mode": negative_mode,
            "negative_months": negative_rows,
            "clipped_months": len(negative_rows) if negative_mode == "controlled_clip" else 0,
            "clipped_total_mm": sum(row["recorte_mm"] for row in negative_rows)
            if negative_mode == "controlled_clip" else 0.0,
            "annual_balance_modified": bool(negative_rows and negative_mode == "controlled_clip"),
        })
    return [max(value, 0.0) for value in q]


def retention_limit_for_nonnegative_balance(
    pe_average: Sequence[float], retention: Dict[str, object]
) -> Optional[Dict[str, object]]:
    """Estima el R maximo compatible con Q mensual no negativo."""

    factors = retention.get("factores_gasto", ())
    supply = retention.get("abastecimiento_mm", ())
    current_r = sum(float(value) for value in supply)
    if current_r <= 0 or len(factors) != 12 or len(supply) != 12:
        return None
    supply_fractions = [float(value) / current_r for value in supply]
    candidates = []
    for index, (pe, spending_fraction, supply_fraction) in enumerate(
        zip(pe_average, factors, supply_fractions)
    ):
        net_fraction = supply_fraction - float(spending_fraction)
        if net_fraction > 1e-12:
            candidates.append((float(pe) / net_fraction, index))
    if not candidates:
        return None
    value, index = min(candidates, key=lambda item: item[0])
    return {"retencion_maxima_mm": max(0.0, value), "mes_limitante": MONTHS[index], "mes_numero": index + 1}


def _solve_3x3(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> List[float]:
    augmented = [list(row) + [float(value)] for row, value in zip(matrix, vector)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        _require(abs(augmented[pivot][column]) > 1e-14, "La regresion de Markov es singular.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [value - factor * base for value, base in zip(augmented[row], augmented[column])]
    return [augmented[row][3] for row in range(3)]


def fit_markov(q_average: Sequence[float], pe_average: Sequence[float]) -> Dict[str, float]:
    _require(len(q_average) == 12 and len(pe_average) == 12, "Markov requiere 12 meses.")
    previous = [q_average[-1]] + list(q_average[:-1])
    design = [[1.0, float(prev), float(pe)] for prev, pe in zip(previous, pe_average)]
    xtx = [[sum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)]
    xty = [sum(row[i] * value for row, value in zip(design, q_average)) for i in range(3)]
    b1, b2, b3 = _solve_3x3(xtx, xty)
    fitted = [b1 + b2 * prev + b3 * pe for prev, pe in zip(previous, pe_average)]
    residuals = [observed - predicted for observed, predicted in zip(q_average, fitted)]
    sse = sum(value * value for value in residuals)
    mean_q = sum(q_average) / 12.0
    sst = sum((value - mean_q) ** 2 for value in q_average)
    _require(sst > 0, "El año promedio no tiene variabilidad suficiente.")
    return {"B1": b1, "B2": b2, "B3": b3, "S": math.sqrt(sse / 9.0), "R2": max(0.0, min(1.0, 1.0 - sse / sst))}


def generate_depth(pe_matrix: Sequence[Sequence[float]], regression: Dict[str, float], initial_depth: float) -> List[List[float]]:
    previous = float(initial_depth)
    output = []
    for row in pe_matrix:
        result_row = []
        for pe in row:
            current = regression["B1"] + regression["B2"] * previous + regression["B3"] * float(pe)
            current = max(current, 0.0)
            result_row.append(current)
            previous = current
        output.append(result_row)
    return output


def days_for_month(year: int, month: int, matlab_compatible: bool) -> int:
    return MATLAB_DAYS[month - 1] if matlab_compatible else calendar.monthrange(year, month)[1]


def depth_to_discharge(depth_mm: float, area_km2: float, year: int, month: int, matlab_compatible: bool) -> float:
    days = days_for_month(year, month, matlab_compatible)
    return depth_mm * area_km2 * 1000.0 / (days * 86400.0)


def metrics(observed: Sequence[Optional[float]], simulated: Sequence[float]) -> Optional[Dict[str, Optional[float]]]:
    pairs = [(float(obs), float(sim)) for obs, sim in zip(observed, simulated) if obs is not None and math.isfinite(obs) and math.isfinite(sim)]
    if len(pairs) < 3:
        return None
    obs = [pair[0] for pair in pairs]
    sim = [pair[1] for pair in pairs]
    mean_obs = sum(obs) / len(obs)
    denominator = sum((value - mean_obs) ** 2 for value in obs)
    if denominator <= 0:
        return None
    nse = 1.0 - sum((o - s) ** 2 for o, s in pairs) / denominator
    log_nse = None
    if all(o > 0 and s > 0 for o, s in pairs):
        log_obs = [math.log(value) for value in obs]
        log_sim = [math.log(value) for value in sim]
        mean_log = sum(log_obs) / len(log_obs)
        log_den = sum((value - mean_log) ** 2 for value in log_obs)
        if log_den > 0:
            log_nse = 1.0 - sum((o - s) ** 2 for o, s in zip(log_obs, log_sim)) / log_den
    mean_sim = sum(sim) / len(sim)
    covariance = sum((o - mean_obs) * (s - mean_sim) for o, s in pairs)
    spread = math.sqrt(sum((o - mean_obs) ** 2 for o in obs) * sum((s - mean_sim) ** 2 for s in sim))
    correlation = covariance / spread if spread > 0 else None
    std_obs = math.sqrt(sum((value - mean_obs) ** 2 for value in obs) / len(obs))
    std_sim = math.sqrt(sum((value - mean_sim) ** 2 for value in sim) / len(sim))
    kge = None
    if correlation is not None and std_obs > 0 and mean_obs != 0:
        alpha = std_sim / std_obs
        beta = mean_sim / mean_obs
        kge = 1.0 - math.sqrt((correlation - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2)
    sorted_observed = sorted(obs)
    high_threshold = sorted_observed[max(0, math.ceil(0.75 * len(sorted_observed)) - 1)]
    high_pairs = [(o, s) for o, s in pairs if o >= high_threshold]
    high_observed_sum = sum(o for o, _ in high_pairs)
    maximum_observed = max(obs)
    schultz = (
        200.0 * sum(abs(s - o) * o for o, s in pairs)
        / (len(pairs) * maximum_observed ** 2)
        if maximum_observed > 0 else None
    )
    return {
        "n": len(pairs),
        "NSE": nse,
        "LogNSE": log_nse,
        "KGE": kge,
        "Correlacion": correlation,
        "RMSE": math.sqrt(sum((o - s) ** 2 for o, s in pairs) / len(pairs)),
        "MAD": sum(abs(o - s) for o, s in pairs) / len(pairs),
        "Schultz_D": schultz,
        "PBIAS_porcentaje": 100.0 * sum(s - o for o, s in pairs) / sum(obs) if sum(obs) else None,
        "PBIAS_altos_porcentaje": (
            100.0 * sum(s - o for o, s in high_pairs) / high_observed_sum
            if high_observed_sum else None
        ),
    }


def _validate_parameters(parameters: ModelParameters) -> None:
    _require(math.isfinite(parameters.area_km2) and parameters.area_km2 > 0, "El area debe ser positiva.")
    _require(math.isfinite(parameters.coef_escorrentia) and 0 <= parameters.coef_escorrentia <= 1, "C debe estar entre 0 y 1.")
    _require(math.isfinite(parameters.retencion_mm) and parameters.retencion_mm >= 0, "R debe ser no negativa.")
    _require(
        parameters.negative_balance_mode in ("strict", "controlled_clip"),
        "El tratamiento de balances negativos debe ser estricto o recorte controlado.",
    )


def run_model(
    records: Sequence[MonthlyRecord],
    parameters: ModelParameters,
    retention_config: RetentionConfig,
    calibration_years: Tuple[int, int],
    validation_years: Optional[Tuple[int, int]] = None,
) -> Dict[str, object]:
    """Ajusta el año promedio y simula toda la serie mensual."""

    validate_records(records)
    _validate_parameters(parameters)
    years, precipitation = _year_matrix(records, "precipitacion_mm")
    _, observed_matrix = _year_matrix(records, "caudal_observado_m3s")
    cal_start, cal_end = calibration_years
    _require(cal_start <= cal_end, "El periodo de calibracion es invalido.")
    calibration_indexes = [index for index, year in enumerate(years) if cal_start <= year <= cal_end]
    _require(calibration_indexes, "El periodo de calibracion no intersecta la serie.")

    pe_all, pe_details = effective_precipitation_series(precipitation, parameters.coef_escorrentia)
    pe_calibration = [pe_all[index] for index in calibration_indexes]
    pe_average = [sum(row[month] for row in pe_calibration) / len(pe_calibration) for month in range(12)]
    retention = retention_balance(parameters, retention_config)
    balance_diagnostics: Dict[str, object] = {}
    q_average = average_year(
        pe_average, retention, parameters.negative_balance_mode, balance_diagnostics
    )
    balance_diagnostics["retention_nonnegative_limit"] = retention_limit_for_nonnegative_balance(
        pe_average, retention
    )
    regression = fit_markov(q_average, pe_average)
    depth = generate_depth(pe_all, regression, q_average[-1])

    rows = []
    simulated_values, observed_values = [], []
    for year_index, year in enumerate(years):
        for month_index in range(12):
            simulated = depth_to_discharge(
                depth[year_index][month_index], parameters.area_km2, year, month_index + 1, parameters.compatible_matlab
            )
            observed = observed_matrix[year_index][month_index]
            rows.append(
                {
                    "fecha": date(year, month_index + 1, 1).isoformat(),
                    "anio": year,
                    "mes": month_index + 1,
                    "precipitacion_mm": precipitation[year_index][month_index],
                    "precipitacion_efectiva_mm": pe_all[year_index][month_index],
                    "lamina_simulada_mm": depth[year_index][month_index],
                    "caudal_simulado_m3s": simulated,
                    "caudal_observado_m3s": observed,
                    "temp_min_c": records[year_index * 12 + month_index].temp_min_c,
                    "temp_media_c": records[year_index * 12 + month_index].temp_media_c,
                    "temp_max_c": records[year_index * 12 + month_index].temp_max_c,
                    "etp_mm": records[year_index * 12 + month_index].etp_mm,
                }
            )
            simulated_values.append(simulated)
            observed_values.append(observed)

    def period_metrics(period: Optional[Tuple[int, int]]) -> Optional[Dict[str, Optional[float]]]:
        if period is None:
            return None
        start, end = period
        selected = [index for index, row in enumerate(rows) if start <= row["anio"] <= end]
        if not selected:
            return None
        return metrics([observed_values[index] for index in selected], [simulated_values[index] for index in selected])

    from .statistics import precipitation_statistics

    from .diagnostics import diagnostic_scales, flow_persistence

    diagnostics = {
        "complete": diagnostic_scales(rows, parameters.compatible_matlab),
        "calibration": diagnostic_scales(
            [row for row in rows if cal_start <= row["anio"] <= cal_end],
            parameters.compatible_matlab,
        ),
    }
    if validation_years:
        diagnostics["validation"] = diagnostic_scales(
            [row for row in rows if validation_years[0] <= row["anio"] <= validation_years[1]],
            parameters.compatible_matlab,
        )

    persistence = {
        "complete": flow_persistence(rows),
        "calibration": flow_persistence(
            [row for row in rows if cal_start <= row["anio"] <= cal_end]
        ),
    }
    if validation_years:
        persistence["validation"] = flow_persistence(
            [row for row in rows if validation_years[0] <= row["anio"] <= validation_years[1]]
        )

    return {
        "version": PLUGIN_VERSION,
        "years": years,
        "parameters": {
            "area_km2": parameters.area_km2,
            "coef_escorrentia": parameters.coef_escorrentia,
            "retencion_mm": parameters.retencion_mm,
            "K": parameters.k,
            "a_dia": retention["a_dia"],
            "compatible_matlab": parameters.compatible_matlab,
            "negative_balance_mode": parameters.negative_balance_mode,
        },
        "balance_diagnostics": balance_diagnostics,
        "retention": retention,
        "pe_average_mm": pe_average,
        "q_average_mm": q_average,
        "regression": regression,
        "pe_details": pe_details,
        "calibration_period": calibration_years,
        "validation_period": validation_years,
        "metrics_calibration": period_metrics(calibration_years),
        "metrics_validation": period_metrics(validation_years),
        "diagnostics": diagnostics,
        "flow_persistence": persistence,
        "rows": rows,
        "precipitation_statistics": precipitation_statistics(records),
    }
