"""Diagnosticos hidrologicos por escala para resultados Lutz Sholtz."""

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


def exceedance_flow(values, probability):
    """Caudal igualado o excedido con probabilidad ``probability``.

    Usa posiciones de trazado de Weibull ``P = m / (n + 1)`` sobre la serie
    ordenada de mayor a menor e interpolacion lineal entre rangos. Los valores
    ausentes o no finitos se excluyen y el resultado queda acotado por los
    extremos observados de la muestra.
    """

    probability = float(probability)
    if not 0.0 < probability < 1.0:
        raise ValueError("La probabilidad de excedencia debe estar entre 0 y 1.")
    valid = sorted(
        (float(value) for value in values
         if value is not None and math.isfinite(float(value))),
        reverse=True,
    )
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    rank = probability * (len(valid) + 1)
    if rank <= 1.0:
        return valid[0]
    if rank >= len(valid):
        return valid[-1]
    lower_rank = int(math.floor(rank))
    fraction = rank - lower_rank
    upper_value = valid[lower_rank - 1]
    lower_value = valid[lower_rank]
    return upper_value + fraction * (lower_value - upper_value)


def _persistence_origin(rows, field):
    values = [
        float(row[field]) for row in rows
        if row.get(field) is not None and math.isfinite(float(row[field]))
    ]
    if not values:
        return {
            "n": 0, "mean_m3s": None, "standard_deviation_m3s": None,
            "coefficient_variation": None, "maximum_m3s": None, "minimum_m3s": None,
            "Q10_m3s": None, "Q25_m3s": None, "Q50_m3s": None,
            "Q75_m3s": None, "Q90_m3s": None, "Q95_m3s": None,
            "reference_15pct_mean_m3s": None, "zero_percentage": None,
        }
    mean = sum(values) / len(values)
    standard_deviation = math.sqrt(
        sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    ) if len(values) > 1 else 0.0
    return {
        "n": len(values),
        "mean_m3s": mean,
        "standard_deviation_m3s": standard_deviation,
        "coefficient_variation": standard_deviation / mean if abs(mean) > 1e-12 else None,
        "maximum_m3s": max(values),
        "minimum_m3s": min(values),
        "Q10_m3s": exceedance_flow(values, 0.10),
        "Q25_m3s": exceedance_flow(values, 0.25),
        "Q50_m3s": exceedance_flow(values, 0.50),
        "Q75_m3s": exceedance_flow(values, 0.75),
        "Q90_m3s": exceedance_flow(values, 0.90),
        "Q95_m3s": exceedance_flow(values, 0.95),
        "reference_15pct_mean_m3s": 0.15 * mean,
        "zero_percentage": 100.0 * sum(value <= 1e-12 for value in values) / len(values),
    }


def flow_persistence(rows, selected_origin="simulado"):
    """Calcula el regimen y las persistencias mensuales de una serie.

    Cada mes calendario se procesa de manera independiente mediante Weibull.
    Q75 es el caudal igualado o excedido el 75 % del tiempo; no es el percentil
    estadistico ascendente 75. El 15 % se conserva como una referencia
    hidrologica del Anexo I de la RJ 267-2019-ANA, no como un caudal ecologico
    aprobado.
    """

    rows = list(rows)
    origin_fields = (
        ("simulado", "caudal_simulado_m3s"),
        ("observado", "caudal_observado_m3s"),
        ("transferido", "caudal_transferido_m3s"),
    )
    if selected_origin not in {item[0] for item in origin_fields}:
        raise ValueError("El origen seleccionado para permanencia no es valido.")
    monthly = []
    for month in range(1, 13):
        selected = [row for row in rows if int(row["mes"]) == month]
        item = {"mes": month}
        for origin, field in origin_fields:
            item[origin] = _persistence_origin(selected, field)
        monthly.append(item)
    result = {
        "method": "Weibull P=m/(n+1), interpolacion lineal",
        "probabilities_percent": [10, 25, 50, 75, 90, 95],
        "selected_origin": selected_origin,
        "regulation_reference": "Resolucion Jefatural N. 267-2019-ANA, Anexo I",
        "regulatory_note": (
            "Q10, Q25, Q50, Q75, Q90 y Q95 son caudales de persistencia; Q75 es el "
            "caudal igualado o excedido el 75 % del tiempo. La columna del 15 % "
            "es una referencia hidrologica mensual del Anexo I y no equivale por si sola "
            "a un caudal ecologico aprobado por la ANA."
        ),
        "mensual": monthly,
    }
    for origin, field in origin_fields:
        result[origin] = _persistence_origin(rows, field)
    result["seleccionado"] = result[selected_origin]
    return result


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
