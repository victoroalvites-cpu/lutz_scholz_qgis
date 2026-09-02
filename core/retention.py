"""Retencion natural calculada por componentes de la cuenca."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from .model import LutzError


def calculate_retention_components(area_basin_km2: float, components: Iterable[Mapping[str, object]]):
    if not math.isfinite(area_basin_km2) or area_basin_km2 <= 0:
        raise LutzError("El area de cuenca debe ser positiva.")
    details = []
    warnings = []
    for component in components:
        if not component.get("active", True):
            continue
        kind = str(component.get("type", "")).strip().lower()
        area = float(component.get("area_km2", 0) or 0)
        if not math.isfinite(area) or area < 0:
            raise LutzError("Las areas de los componentes deben ser no negativas.")
        manual = component.get("specific_depth_mm")
        if manual not in (None, ""):
            specific = float(manual)
        elif kind in ("acuifero", "acuiferos"):
            slope = component.get("slope_fraction")
            if slope in (None, ""):
                raise LutzError("Para acuiferos se requiere pendiente media (fraccion) o un MDE.")
            slope = float(slope)
            if not 0 <= slope <= 0.15:
                raise LutzError("La pendiente media de acuiferos debe estar entre 0 y 0.15.")
            specific = -750 * slope + 315
        elif kind in ("laguna", "lagunas", "pantano", "pantanos", "lagunas_pantanos", "bofedales"):
            specific = 500.0
        elif kind in ("nevado", "nevados", "glaciar", "glaciares"):
            specific = 500.0
        else:
            raise LutzError(f"Tipo de almacenamiento desconocido: {kind!r}.")
        if specific < 0:
            raise LutzError("Una lamina especifica resulto negativa.")
        contribution = specific * area / area_basin_km2
        details.append(
            {
                "type": kind,
                "area_km2": area,
                "specific_depth_mm": specific,
                "volume_mmc": specific * area / 1000.0,
                "basin_contribution_mm": contribution,
            }
        )
    if sum(item["area_km2"] for item in details) > area_basin_km2:
        warnings.append("La suma de areas de almacenamiento supera el area de cuenca; revise solapamientos.")
    return {
        "retention_mm": sum(item["basin_contribution_mm"] for item in details),
        "volume_total_mmc": sum(item["volume_mmc"] for item in details),
        "components": details,
        "warnings": warnings,
    }
