"""Calibracion asistida que usa caudales observados."""

from __future__ import annotations

import math
from dataclasses import replace

from .model import (
    LutzError,
    depletion_coefficient,
    effective_precipitation_series,
    retention_balance,
    retention_limit_for_nonnegative_balance,
    run_model,
)


def _physical_retention_limit(
    records, base_parameters, retention_config, calibration_years, coefficient, a_day
):
    """Obtiene el R máximo que mantiene Q >= 0 para C y a dados."""

    years = sorted({record.fecha.year for record in records})
    precipitation = [
        [
            float(record.precipitacion_mm)
            for record in records
            if record.fecha.year == year
        ]
        for year in years
    ]
    pe_all, _details = effective_precipitation_series(precipitation, coefficient)
    selected = [
        row for year, row in zip(years, pe_all)
        if calibration_years[0] <= year <= calibration_years[1]
    ]
    if not selected:
        raise LutzError("El periodo de calibracion no intersecta la serie.")
    pe_average = [
        sum(row[month] for row in selected) / len(selected)
        for month in range(12)
    ]
    unit_parameters = replace(
        base_parameters,
        coef_escorrentia=float(coefficient),
        retencion_mm=1.0,
        k=None,
        a_dia=float(a_day),
        negative_balance_mode="strict",
    )
    unit_retention = retention_balance(unit_parameters, retention_config)
    limit = retention_limit_for_nonnegative_balance(pe_average, unit_retention)
    return math.inf if limit is None else float(limit["retencion_maxima_mm"])


def _strict_parameters(base_parameters, coefficient, retention, a_day):
    return replace(
        base_parameters,
        coef_escorrentia=float(coefficient),
        retencion_mm=float(retention),
        k=None,
        a_dia=float(a_day),
        negative_balance_mode="strict",
    )


def _feasible_r_values(r_lo, r_hi, limit, steps):
    feasible_hi = min(float(r_hi), float(limit))
    if feasible_hi + 1e-10 < float(r_lo):
        return []
    if abs(feasible_hi - float(r_lo)) <= 1e-10:
        return [float(r_lo)]
    return [
        float(r_lo) + (feasible_hi - float(r_lo)) * index / (steps - 1)
        for index in range(steps)
    ]


def _objective(metrics, name):
    if not metrics or metrics.get("NSE") is None:
        return -math.inf
    if name == "NSE":
        return metrics["NSE"]
    if name == "LogNSE":
        return metrics.get("LogNSE") if metrics.get("LogNSE") is not None else -math.inf
    if name == "KGE":
        return metrics.get("KGE") if metrics.get("KGE") is not None else -math.inf
    log_nse = metrics.get("LogNSE") if metrics.get("LogNSE") is not None else -2.0
    pbias = abs(metrics.get("PBIAS_porcentaje") or 0.0)
    score = metrics["NSE"] + 0.25 * log_nse - 0.001 * pbias
    if name == "Combinado con picos":
        peak_bias = abs(metrics.get("PBIAS_altos_porcentaje") or 0.0)
        score -= 0.002 * peak_bias
    return score


def calibrate_retention_and_a(records, base_parameters, retention_config, calibration_years,
                              validation_years=None, r_bounds=(0.0, 200.0),
                              a_bounds=(0.005, 0.06), steps=9, objective="NSE"):
    observed = [r for r in records if calibration_years[0] <= r.fecha.year <= calibration_years[1]
                and r.caudal_observado_m3s is not None]
    if len(observed) < 12:
        raise LutzError("La calibracion automatica requiere al menos 12 caudales observados en el periodo.")
    if steps < 4:
        raise LutzError("La malla de calibracion debe tener al menos 4 pasos.")
    r_lo, r_hi = map(float, r_bounds); a_lo, a_hi = map(float, a_bounds)
    if not (0 <= r_lo < r_hi and 0 < a_lo < a_hi):
        raise LutzError("Los limites de calibracion no son validos.")
    best = None
    trials = 0
    rejected_trials = 0
    for refinement in range(2):
        a_values = [a_lo + (a_hi - a_lo) * i / (steps - 1) for i in range(steps)]
        for a_day in a_values:
            limit = _physical_retention_limit(
                records, base_parameters, retention_config, calibration_years,
                base_parameters.coef_escorrentia, a_day,
            )
            r_values = _feasible_r_values(r_lo, r_hi, limit, steps)
            for retention in r_values:
                trials += 1
                parameters = _strict_parameters(
                    base_parameters, base_parameters.coef_escorrentia, retention, a_day
                )
                try:
                    result = run_model(records, parameters, retention_config, calibration_years, validation_years)
                except LutzError:
                    rejected_trials += 1
                    continue
                score = _objective(result["metrics_calibration"], objective)
                if best is None or score > best["score"]:
                    best = {
                        "score": score, "retention_mm": retention, "a_day": a_day,
                        "retention_limit_mm": limit if math.isfinite(limit) else None,
                        "result": result,
                    }
        if best is None:
            raise LutzError(
                "Ninguna combinacion de R y a mantuvo un balance mensual fisicamente valido. "
                "Revise los limites, C y el patron de abastecimiento."
            )
        r_step = (r_hi - r_lo) / (steps - 1)
        a_step = (a_hi - a_lo) / (steps - 1)
        r_lo = max(0.0, best["retention_mm"] - r_step)
        r_hi = best["retention_mm"] + r_step
        a_lo = max(1e-5, best["a_day"] - a_step)
        a_hi = best["a_day"] + a_step
    best["trials"] = trials
    best["objective"] = objective
    best["physical_balance_enforced"] = True
    best["rejected_trials"] = rejected_trials
    return best


def calibrate_parameters(records, base_parameters, retention_config, calibration_years,
                         validation_years=None, r_bounds=(0.0, 200.0),
                         a_bounds=(0.005, 0.06), c_bounds=(0.05, 0.60),
                         steps=9, objective="NSE", calibrate_c=False):
    """Calibra R y a e incluye opcionalmente C mediante ajuste escalonado.

    C se selecciona primero por balance de volumen (PBIAS absoluto) y luego
    R/a por la funcion objetivo elegida. Dos refinamientos reducen la
    interdependencia sin convertir la busqueda en una malla tridimensional
    costosa.
    """

    observed = [r for r in records if calibration_years[0] <= r.fecha.year <= calibration_years[1]
                and r.caudal_observado_m3s is not None]
    if len(observed) < 12:
        raise LutzError("La calibracion automatica requiere al menos 12 caudales observados en el periodo.")
    if steps < 4:
        raise LutzError("La malla de calibracion debe tener al menos 4 pasos.")
    r_lo, r_hi = map(float, r_bounds)
    a_lo, a_hi = map(float, a_bounds)
    c_lo, c_hi = map(float, c_bounds)
    if not (0 <= r_lo < r_hi and 0 < a_lo < a_hi):
        raise LutzError("Los limites de R o a no son validos.")
    if calibrate_c and not (0 <= c_lo < c_hi <= 1):
        raise LutzError("Los limites de C deben cumplir 0 <= Cmin < Cmax <= 1.")

    initial_a = base_parameters.a_dia
    if initial_a is None:
        if base_parameters.k is None:
            raise LutzError("Se requiere K o a para iniciar la calibracion.")
        initial_a = depletion_coefficient(base_parameters.area_km2, float(base_parameters.k))
    current_c = float(base_parameters.coef_escorrentia)
    current_r = float(base_parameters.retencion_mm)
    current_a = float(initial_a)
    initial_result = None
    initial_error = None
    strict_initial = _strict_parameters(
        base_parameters, current_c, current_r, current_a
    )
    try:
        initial_result = run_model(
            records, strict_initial, retention_config, calibration_years, validation_years
        )
    except LutzError as error:
        # Un punto inicial fisicamente incompatible no debe impedir que la
        # busqueda encuentre una combinacion valida dentro de los limites.
        initial_error = str(error)
    best = None
    trials = 0
    rejected_trials = 0

    for _refinement in range(2):
        if calibrate_c:
            c_values = [c_lo + (c_hi - c_lo) * i / (steps - 1) for i in range(steps)]
            c_candidates = []
            for coefficient in c_values:
                trials += 1
                limit = _physical_retention_limit(
                    records, base_parameters, retention_config, calibration_years,
                    coefficient, current_a,
                )
                candidate_r_values = _feasible_r_values(r_lo, r_hi, limit, steps)
                if not candidate_r_values:
                    rejected_trials += 1
                    continue
                candidate_r = min(candidate_r_values, key=lambda value: abs(value - current_r))
                parameters = _strict_parameters(
                    base_parameters, coefficient, candidate_r, current_a
                )
                try:
                    result = run_model(records, parameters, retention_config, calibration_years, validation_years)
                except LutzError:
                    rejected_trials += 1
                    continue
                metrics = result.get("metrics_calibration") or {}
                pbias = metrics.get("PBIAS_porcentaje")
                volume_score = -abs(float(pbias)) if pbias is not None else -math.inf
                c_candidates.append((volume_score, _objective(metrics, objective), coefficient, result))
            if not c_candidates:
                raise LutzError("Ningun valor de C produjo una simulacion valida.")
            _, _, current_c, _ = max(c_candidates, key=lambda item: (item[0], item[1]))

        a_values = [a_lo + (a_hi - a_lo) * i / (steps - 1) for i in range(steps)]
        round_best = None
        for a_day in a_values:
            limit = _physical_retention_limit(
                records, base_parameters, retention_config, calibration_years,
                current_c, a_day,
            )
            r_values = _feasible_r_values(r_lo, r_hi, limit, steps)
            for retention in r_values:
                trials += 1
                parameters = _strict_parameters(base_parameters, current_c, retention, a_day)
                try:
                    result = run_model(records, parameters, retention_config, calibration_years, validation_years)
                except LutzError:
                    rejected_trials += 1
                    continue
                score = _objective(result["metrics_calibration"], objective)
                candidate = {
                    "score": score, "coefficient": current_c,
                    "retention_mm": retention, "a_day": a_day,
                    "retention_limit_mm": limit if math.isfinite(limit) else None,
                    "result": result,
                }
                if round_best is None or score > round_best["score"]:
                    round_best = candidate
                if best is None or score > best["score"]:
                    best = candidate
        if round_best is None:
            raise LutzError(
                "Ninguna combinacion de parametros mantuvo Q mensual mayor o igual a cero. "
                "Amplie los limites de C o a y revise R y el patron de abastecimiento."
            )
        current_r = round_best["retention_mm"]
        current_a = round_best["a_day"]

        r_step = (r_hi - r_lo) / (steps - 1)
        a_step = (a_hi - a_lo) / (steps - 1)
        r_lo = max(0.0, current_r - r_step)
        r_hi = current_r + r_step
        a_lo = max(1e-5, current_a - a_step)
        a_hi = current_a + a_step
        if calibrate_c:
            c_step = (c_hi - c_lo) / (steps - 1)
            c_lo = max(0.0, current_c - c_step)
            c_hi = min(1.0, current_c + c_step)

    best["trials"] = trials
    best["objective"] = objective
    best["calibrated_c"] = bool(calibrate_c)
    best["initial_coefficient"] = float(base_parameters.coef_escorrentia)
    best["initial_result"] = initial_result
    best["initial_error"] = initial_error
    best["physical_balance_enforced"] = True
    best["rejected_trials"] = rejected_trials
    return best
