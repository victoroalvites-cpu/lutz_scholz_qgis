"""Catalogos regionales y recomendacion explicable de K."""

from __future__ import annotations

import math
from collections import defaultdict

from .model import LutzError, depletion_coefficient


K_CATALOG = (
    ("muy_rapido", 0.034, "T > 10 C; retencion reducida a mediana"),
    ("rapido", 0.030, "Retención de 50-80 mm/año y puna poco desarrollada"),
    ("mediano", 0.026, "Retencion mediana y vegetacion mezclada"),
    ("reducido", 0.023, "Retencion alta y vegetacion mezclada"),
    ("muy_reducido", 0.018, "Descarga base altamente persistente"),
)

REGIONAL_SUPPLY_PERCENT = {
    "cusco": (40, 20, 0, 0, 0, 0, 0, 0, 0, 0, 5, 35),
    "huancavelica": (30, 20, 5, 0, 0, 0, 0, 0, 0, 10, 0, 35),
    "junin": (30, 30, 5, 0, 0, 0, 0, 0, 0, 10, 0, 25),
    "cajamarca": (20, 25, 35, 0, 0, 0, 0, 0, 0, 25, -5, 0),
    "ancash_santa": (22, 37, 45, 0, 0, 0, 0, 0, 0, 3, -7, 1),
}


def chronological_observed_split(records, calibration_fraction=0.60):
    """Propone periodos cronológicos usando solo años completos con Q observado.

    Los meses no se mezclan ni se seleccionan al azar. Los años incompletos se
    informan para que puedan mantenerse como simulacion, pero no determinan el
    punto de corte de calibracion y validacion.
    """

    fraction = float(calibration_fraction)
    if not 0 < fraction < 1:
        raise LutzError("La fraccion de calibracion debe estar entre 0 y 1.")
    grouped = defaultdict(list)
    for record in records:
        grouped[record.fecha.year].append(record)
    complete = []
    excluded = []
    for year in sorted(grouped):
        rows = grouped[year]
        months = {row.fecha.month for row in rows}
        observed = [row for row in rows if row.caudal_observado_m3s is not None]
        if months == set(range(1, 13)) and len(observed) == 12:
            complete.append(year)
        else:
            excluded.append(year)
    if len(complete) < 2:
        raise LutzError(
            "La división automática requiere al menos dos años completos con 12 caudales observados."
        )
    split = int(math.floor(len(complete) * fraction + 0.5))
    split = min(max(split, 1), len(complete) - 1)
    calibration_years = complete[:split]
    validation_years = complete[split:]
    return {
        "method": f"cronologico_{int(round(fraction * 100))}_{int(round((1-fraction) * 100))}",
        "fraction_calibration": fraction,
        "complete_observed_years": complete,
        "excluded_years": excluded,
        "calibration_years": calibration_years,
        "validation_years": validation_years,
        "calibration_period": (calibration_years[0], calibration_years[-1]),
        "validation_period": (validation_years[0], validation_years[-1]),
    }


def regional_supply(region: str):
    key = str(region).strip().lower().replace(" ", "_")
    key = {"cuzco": "cusco", "ancash": "ancash_santa", "santa": "ancash_santa"}.get(key, key)
    if key not in REGIONAL_SUPPLY_PERCENT:
        raise LutzError(f"Region de abastecimiento desconocida: {region!r}.")
    values = REGIONAL_SUPPLY_PERCENT[key]
    total = sum(values)
    return {
        "region": key,
        "original_percent": values,
        "original_sum_percent": total,
        "fractions": tuple(value / total for value in values),
        "warning": "" if abs(total - 100) < 1e-9 else f"El patron suma {total:.1f}%; fue normalizado a 100%.",
    }


def select_k_by_criteria(area_km2, retention_mm, mean_temperature_c=None,
                         recession="desconocido", cover="desconocida", storage="desconocido"):
    if area_km2 <= 0 or retention_mm < 0:
        raise LutzError("Area y retencion deben ser validas para seleccionar K.")
    options = [row[0] for row in K_CATALOG]
    scores = {name: 0 for name in options}
    reasons = []
    preferred_r = "mediano"
    recession = str(recession).lower()
    if recession != "desconocido":
        if recession not in scores:
            raise LutzError("Comportamiento de estiaje no reconocido.")
        scores[recession] += 6
        reasons.append(f"El estiaje fue clasificado como {recession}.")
    if retention_mm < 50:
        scores["muy_rapido"] += 3; scores["rapido"] += 1; preferred_r = "muy_rapido"
        reasons.append("R menor de 50 mm favorece descarga rapida.")
    elif retention_mm <= 80:
        scores["muy_rapido"] += 2; scores["rapido"] += 2; scores["mediano"] += 1; preferred_r = "rapido"
        reasons.append("R esta en el intervalo 50-80 mm.")
    elif retention_mm <= 100:
        scores["mediano"] += 3; scores["rapido"] += 1
        reasons.append("R representa retencion mediana.")
    else:
        scores["reducido"] += 3; scores["muy_reducido"] += 1; preferred_r = "reducido"
        reasons.append("R alta favorece descarga persistente.")
    if mean_temperature_c is not None and math.isfinite(float(mean_temperature_c)):
        if float(mean_temperature_c) > 10:
            scores["muy_rapido"] += 2; reasons.append("T media anual es mayor que 10 C.")
        else:
            scores["mediano"] += 1; scores["reducido"] += 1
    cover = str(cover).lower()
    if cover == "puna_poco_desarrollada": scores["rapido"] += 2
    elif cover == "mixta": scores["reducido" if retention_mm > 100 else "mediano"] += 2
    elif cover == "acuiferos_bofedales": scores["reducido"] += 2; scores["muy_reducido"] += 2
    elif cover != "desconocida": raise LutzError("Cobertura no reconocida.")
    storage = str(storage).lower()
    if storage == "bajo": scores["muy_rapido"] += 2
    elif storage == "medio": scores["rapido"] += 1; scores["mediano"] += 2
    elif storage == "alto": scores["reducido"] += 2
    elif storage == "muy_alto": scores["reducido"] += 1; scores["muy_reducido"] += 3
    elif storage != "desconocido": raise LutzError("Almacenamiento no reconocido.")
    candidates = [name for name, score in scores.items() if score == max(scores.values())]
    selected = preferred_r if preferred_r in candidates else (recession if recession in candidates else candidates[0])
    row = next(item for item in K_CATALOG if item[0] == selected)
    ordered = sorted(scores.values(), reverse=True)
    margin = ordered[0] - ordered[1]
    confidence = "baja" if len(candidates) > 1 or margin < 2 else "media"
    if recession != "desconocido" and margin >= 4 and cover != "desconocida" and storage != "desconocido":
        confidence = "alta"
    return {
        "option": selected, "K": row[1], "a_day": depletion_coefficient(area_km2, row[1]),
        "description": row[2], "confidence": confidence,
        "justification": " ".join(reasons), "scores": scores,
    }
