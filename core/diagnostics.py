"""Diagnosticos hidrologicos por escala para resultados Lutz Scholz."""

from __future__ import annotations

import math
from collections import defaultdict

from .model import days_for_month, metrics


def _valid_pairs(rows):
    return [
        (float(row["caudal_observado_m3s"]), float(row["caudal_simulado_m3s"]))
        for row in rows
        if row.get("caudal_observado_m3s") is not None
        and math.isfinite(float(row["caudal_observado_m3s"]))
        and math.isfinite(float(row["caudal_simulado_m3s"]))
    ]


def annual_series(rows, matlab_compatible=True):
    grouped = defaultdict(list)
    for row in rows:
        grouped[int(row["anio"])].append(row)
    output = []
    for year in sorted(grouped):
        selected = grouped[year]
        valid = [row for row in selected if row.get("caudal_observado_m3s") is not None]
        if not valid:
            continue
        weights = [days_for_month(year, int(row["mes"]), matlab_compatible) for row in valid]
        total = float(sum(weights))
        output.append({
            "anio": year,
            "meses_validos": len(valid),
            "cobertura_porcentaje": 100.0 * total / sum(
                days_for_month(year, month, matlab_compatible) for month in range(1, 13)
            ),
            "caudal_observado_m3s": sum(
                float(row["caudal_observado_m3s"]) * weight for row, weight in zip(valid, weights)
            ) / total,
            "caudal_simulado_m3s": sum(
                float(row["caudal_simulado_m3s"]) * weight for row, weight in zip(valid, weights)
            ) / total,
        })
    return output


def regime_series(rows):
    output = []
    for month in range(1, 13):
        selected = [row for row in rows if int(row["mes"]) == month]
        valid = [row for row in selected if row.get("caudal_observado_m3s") is not None]
        if not valid:
            output.append({"mes": month, "n": 0, "caudal_observado_m3s": None, "caudal_simulado_m3s": None})
            continue
        output.append({
            "mes": month,
            "n": len(valid),
            "caudal_observado_m3s": sum(float(row["caudal_observado_m3s"]) for row in valid) / len(valid),
            "caudal_simulado_m3s": sum(float(row["caudal_simulado_m3s"]) for row in valid) / len(valid),
        })
    return output


def regression_summary(rows):
    pairs = _valid_pairs(rows)
    if len(pairs) < 2:
        return None
    obs = [pair[0] for pair in pairs]
    sim = [pair[1] for pair in pairs]
    mean_obs = sum(obs) / len(obs)
    mean_sim = sum(sim) / len(sim)
    denominator = sum((value - mean_obs) ** 2 for value in obs)
    if denominator <= 0:
        return None
    slope = sum((o - mean_obs) * (s - mean_sim) for o, s in pairs) / denominator
    intercept = mean_sim - slope * mean_obs
    fitted = [intercept + slope * value for value in obs]
    total = sum((value - mean_sim) ** 2 for value in sim)
    residual = sum((value - predicted) ** 2 for value, predicted in zip(sim, fitted))
    r2 = 1.0 - residual / total if total > 0 else None
    return {
        "n": len(pairs),
        "intercept": intercept,
        "slope": slope,
        "R2": r2,
        "equation": f"Qsim = {intercept:.3f} + {slope:.3f} Qobs",
    }


def diagnostic_scales(rows, matlab_compatible=True):
    rows = list(rows)
    pairs = _valid_pairs(rows)
    annual = annual_series(rows, matlab_compatible)
    regime = regime_series(rows)
    annual_pairs = _valid_pairs(annual)
    regime_pairs = _valid_pairs(regime)
    by_month = {}
    for month in range(1, 13):
        month_pairs = _valid_pairs([row for row in rows if int(row["mes"]) == month])
        by_month[str(month)] = metrics(
            [pair[0] for pair in month_pairs], [pair[1] for pair in month_pairs]
        ) if len(month_pairs) >= 3 else None
    return {
        "monthly": metrics([pair[0] for pair in pairs], [pair[1] for pair in pairs]) if len(pairs) >= 3 else None,
        "annual": metrics([pair[0] for pair in annual_pairs], [pair[1] for pair in annual_pairs]) if len(annual_pairs) >= 3 else None,
        "regime": metrics([pair[0] for pair in regime_pairs], [pair[1] for pair in regime_pairs]) if len(regime_pairs) >= 3 else None,
        "scatter": regression_summary(rows),
        "annual_series": annual,
        "regime_series": regime,
        "monthly_by_calendar_month": by_month,
    }
