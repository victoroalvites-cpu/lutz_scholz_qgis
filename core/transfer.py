"""Transferencia hidrologica de caudales entre cuencas.

La formulacion base sigue ``Qs = (As/Ac) (Ps/Pc) Qc``.  Se ofrecen factores
de precipitacion anual y por mes calendario; ambos se calculan unicamente con
fechas comunes para mantener la trazabilidad de la comparacion.
"""

from __future__ import annotations

import math

from .model import LutzError


def _finite(value):
    return value is not None and math.isfinite(float(value))


def transfer_simulated_flows(source_rows, source_area_km2, target_area_km2,
                             target_annual_precipitation_mm):
    """Transfiere la simulacion final de Lutz Sholtz a la cuenca objetivo.

    ``source_rows`` contiene ``Qc`` y ``Pc`` de la cuenca modelada. El usuario
    solo proporciona ``As`` y la precipitación media anual ``Ps`` de la cuenca
    objetivo. La relacion aplicada a cada caudal mensual es
    ``Qs=(As/Ac)*(Ps/Pc)*Qc``.
    """

    if not _finite(source_area_km2) or float(source_area_km2) <= 0:
        raise LutzError("El area de la cuenca modelada debe ser positiva.")
    if not _finite(target_area_km2) or float(target_area_km2) <= 0:
        raise LutzError("El area de la cuenca objetivo debe ser positiva.")
    if (not _finite(target_annual_precipitation_mm)
            or float(target_annual_precipitation_mm) <= 0):
        raise LutzError(
            "La precipitacion media anual de la cuenca objetivo debe ser positiva."
        )

    precipitation_by_year = {}
    for row in source_rows:
        if _finite(row.get("precipitacion_mm")):
            precipitation_by_year.setdefault(int(row["anio"]), {})[
                int(row["mes"])
            ] = float(row["precipitacion_mm"])
    annual_totals = [
        sum(months.values())
        for months in precipitation_by_year.values()
        if set(months) == set(range(1, 13))
    ]
    if not annual_totals:
        raise LutzError(
            "No existe un año completo de precipitacion en la cuenca modelada para calcular Pc."
        )

    area_factor = float(target_area_km2) / float(source_area_km2)
    source_annual_precipitation = sum(annual_totals) / len(annual_totals)
    if source_annual_precipitation <= 0:
        raise LutzError("La precipitacion media anual de la cuenca modelada debe ser mayor que cero.")
    target_annual_precipitation = float(target_annual_precipitation_mm)
    precipitation_factor = target_annual_precipitation / source_annual_precipitation
    factor = area_factor * precipitation_factor

    output = []
    transferred_count = 0
    for original in source_rows:
        row = dict(original)
        if _finite(row.get("caudal_simulado_m3s")):
            row["caudal_donante_m3s"] = float(row["caudal_simulado_m3s"])
            row["precipitacion_donante_mm"] = source_annual_precipitation
            row["precipitacion_objetivo_mm"] = target_annual_precipitation
            row["factor_transferencia"] = factor
            row["caudal_transferido_m3s"] = float(row["caudal_simulado_m3s"]) * factor
            transferred_count += 1
        else:
            row["caudal_donante_m3s"] = None
            row["precipitacion_donante_mm"] = None
            row["precipitacion_objetivo_mm"] = None
            row["factor_transferencia"] = None
            row["caudal_transferido_m3s"] = None
        output.append(row)

    metadata = {
        "active": True,
        "source": "caudal_simulado_final_lutz_scholz",
        "method": "annual_mean_precipitation",
        "equation": "Qs=(As/Ac)*(Ps/Pc)*Qc",
        "source_area_km2": float(source_area_km2),
        "target_area_km2": float(target_area_km2),
        "area_factor": area_factor,
        "source_annual_precipitation_mm": source_annual_precipitation,
        "target_annual_precipitation_mm": target_annual_precipitation,
        "precipitation_factors": {"annual": precipitation_factor},
        "complete_precipitation_years": len(annual_totals),
        "transferred_months": transferred_count,
        "assumption": (
            "La cuenca modelada y la cuenca objetivo se consideran hidrologicamente semejantes; "
            "el metodo no corrige diferencias de regulacion, glaciarizacion, geologia ni aporte subterraneo."
        ),
    }
    return output, metadata


def transfer_hydrological_flows(target_rows, donor_records, target_area_km2,
                                donor_area_km2, method="annual"):
    """Agrega a ``target_rows`` una serie transferida desde ``donor_records``.

    ``method`` puede ser ``annual`` (un factor fijo de precipitacion) o
    ``monthly_climatology`` (doce factores climatologicos). La funcion devuelve
    nuevas filas y un diccionario de trazabilidad.
    """

    if not _finite(target_area_km2) or float(target_area_km2) <= 0:
        raise LutzError("El area de la cuenca objetivo debe ser positiva.")
    if not _finite(donor_area_km2) or float(donor_area_km2) <= 0:
        raise LutzError("El area de la cuenca donante debe ser positiva.")
    if method not in ("annual", "monthly_climatology"):
        raise LutzError("El metodo de transferencia no es valido.")

    donor_by_month = {
        (record.fecha.year, record.fecha.month): record for record in donor_records
    }
    matches = []
    for row in target_rows:
        donor = donor_by_month.get((int(row["anio"]), int(row["mes"])))
        if donor is None or not _finite(donor.caudal_observado_m3s):
            continue
        if not _finite(row.get("precipitacion_mm")) or not _finite(donor.precipitacion_mm):
            continue
        matches.append((row, donor))
    if not matches:
        raise LutzError(
            "No existen meses comunes con Q y precipitacion validos entre la serie donante y la objetivo."
        )

    area_factor = float(target_area_km2) / float(donor_area_km2)
    precipitation_factors = {}
    if method == "annual":
        target_mean = sum(float(row["precipitacion_mm"]) for row, _ in matches) / len(matches)
        donor_mean = sum(float(donor.precipitacion_mm) for _, donor in matches) / len(matches)
        if donor_mean <= 0:
            raise LutzError("La precipitacion media de la cuenca donante debe ser mayor que cero.")
        precipitation_factors["annual"] = target_mean / donor_mean
    else:
        for month in range(1, 13):
            selected = [(row, donor) for row, donor in matches if int(row["mes"]) == month]
            if not selected:
                continue
            target_mean = sum(float(row["precipitacion_mm"]) for row, _ in selected) / len(selected)
            donor_mean = sum(float(donor.precipitacion_mm) for _, donor in selected) / len(selected)
            if donor_mean > 0:
                precipitation_factors[str(month)] = target_mean / donor_mean
        if not precipitation_factors:
            raise LutzError("No fue posible calcular factores mensuales de precipitacion.")

    output = []
    transferred_count = 0
    for original in target_rows:
        row = dict(original)
        donor = donor_by_month.get((int(row["anio"]), int(row["mes"])))
        precipitation_factor = precipitation_factors.get(
            "annual" if method == "annual" else str(int(row["mes"]))
        )
        if donor is not None and _finite(donor.caudal_observado_m3s) and precipitation_factor is not None:
            factor = area_factor * precipitation_factor
            row["caudal_donante_m3s"] = float(donor.caudal_observado_m3s)
            row["precipitacion_donante_mm"] = (
                float(donor.precipitacion_mm) if _finite(donor.precipitacion_mm) else None
            )
            row["factor_transferencia"] = factor
            row["caudal_transferido_m3s"] = float(donor.caudal_observado_m3s) * factor
            transferred_count += 1
        else:
            row["caudal_donante_m3s"] = None
            row["precipitacion_donante_mm"] = None
            row["factor_transferencia"] = None
            row["caudal_transferido_m3s"] = None
        output.append(row)

    metadata = {
        "active": True,
        "method": method,
        "equation": "Qs=(As/Ac)*(Ps/Pc)*Qc",
        "target_area_km2": float(target_area_km2),
        "donor_area_km2": float(donor_area_km2),
        "area_factor": area_factor,
        "precipitation_factors": precipitation_factors,
        "common_months": len(matches),
        "transferred_months": transferred_count,
        "assumption": (
            "Las cuencas se consideran hidrologicamente semejantes; el metodo no corrige "
            "diferencias de regulacion, glaciarizacion, geologia ni aporte subterraneo."
        ),
    }
    return output, metadata
