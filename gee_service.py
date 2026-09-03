"""Acceso opcional a Google Earth Engine para el complemento.

El modulo evita importar QGIS y carga ``earthengine-api`` de manera diferida.
De ese modo el modelo Lutz clasico sigue funcionando aunque la dependencia o
la conexion a Internet no esten disponibles.
"""

from __future__ import annotations

import csv
import importlib
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence


class GeeError(RuntimeError):
    """Error de Earth Engine listo para mostrar en la interfaz."""


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    label: str
    collection_id: str
    frequency: str
    raw_bands: tuple
    output_bands: tuple
    nominal_scale_m: Optional[float] = None
    spatial_reducer: str = "area_weighted"


DEFAULT_PISCO_PRECIP = "projects/ee-hidrog/assets/PISCO/PISCOp_v3_monthly"  # pragma: allowlist secret
DEFAULT_PISCO_TEMP = "projects/ee-hidrog/assets/PISCO/PISCOt_v1_monthly"  # pragma: allowlist secret

SOURCE_CATALOG = {
    "pisco_p": SourceDefinition(
        "pisco_p", "PISCO precipitacion mensual", DEFAULT_PISCO_PRECIP,
        "monthly", ("precipitation",), ("precipitacion_mm",), None,
        "pixel_mean",
    ),
    "pisco_t": SourceDefinition(
        "pisco_t", "PISCO temperatura mensual", DEFAULT_PISCO_TEMP,
        "monthly", ("tmin", "tmean", "tmax"),
        ("temp_min_c", "temp_media_c", "temp_max_c"), None,
    ),
    "era5": SourceDefinition(
        "era5", "ERA5-Land temperatura (promedio de extremos diarios)",
        "ECMWF/ERA5_LAND/DAILY_AGGR",
        "daily_temperature_mean",
        ("temperature_2m_min", "temperature_2m_max"),
        ("temp_min_c", "temp_media_c", "temp_max_c"),
        11132.0,
    ),
    "chirps": SourceDefinition(
        "chirps", "CHIRPS Daily", "UCSB-CHG/CHIRPS/DAILY",
        "daily", ("precipitation",), ("precipitacion_mm",), 5566.0,
    ),
}


def _load_ee():
    """Carga la API instalada o una copia ``vendor`` de desarrollo, si existe."""

    required = ("Authenticate", "Initialize", "ImageCollection", "Geometry")
    try:
        module = importlib.import_module("ee")
        if all(hasattr(module, name) for name in required):
            return module
        raise ImportError("El modulo ee encontrado no es earthengine-api.")
    except ImportError as first_error:
        vendor = Path(__file__).with_name("vendor")
        if vendor.is_dir() and str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        # QGIS puede exponer un namespace vacio llamado ``ee``. Se elimina la
        # carga parcial para que Python resuelva la API incluida en vendor.
        for name in tuple(sys.modules):
            if name == "ee" or name.startswith("ee."):
                sys.modules.pop(name, None)
        importlib.invalidate_caches()
        try:
            module = importlib.import_module("ee")
            if not all(hasattr(module, name) for name in required):
                raise ImportError("La dependencia incluida no contiene la API de Earth Engine.")
            return module
        except ImportError as error:
            raise GeeError(
                "No se encontro earthengine-api. El modelo local sigue disponible. "
                "Para usar Clima GEE, instale earthengine-api en el Python de QGIS, "
                "reinicie QGIS y consulte README.md."
            ) from (error or first_error)


def dependency_status() -> Dict[str, object]:
    try:
        ee = _load_ee()
        return {"available": True, "version": getattr(ee, "__version__", "desconocida")}
    except Exception as error:
        return {"available": False, "error": str(error)}


def initialize_ee(project_id: str) -> Dict[str, object]:
    project = str(project_id or "").strip()
    if not project:
        raise GeeError("Ingrese el ID del proyecto de Google Cloud.")
    ee = _load_ee()
    try:
        ee.Initialize(project=project)
        # En earthengine-api 1.7.x, ``setDeadline`` instala recursos HTTP y
        # requiere que ``Initialize`` ya haya creado la sesion de solicitudes.
        if hasattr(ee.data, "setDeadline"):
            ee.data.setDeadline(30000)
        value = ee.String("conexion_correcta").getInfo()
    except Exception as error:
        detail = str(error).strip() or repr(error)
        raise GeeError(
            "No fue posible inicializar Earth Engine. Use 'Conectar / autorizar' y verifique el proyecto. "
            f"Detalle: {detail}"
        ) from error
    return {"project": project, "test": value, "api_version": getattr(ee, "__version__", "")}


def authenticate_ee(project_id: str, force: bool = False) -> Dict[str, object]:
    """Inicia OAuth local cuando no existen credenciales reutilizables."""

    project = str(project_id or "").strip()
    if not project:
        raise GeeError("Ingrese el ID del proyecto de Google Cloud.")
    ee = _load_ee()
    if not force:
        try:
            ee.Initialize(project=project)
            ee.Number(1).getInfo()
            return {"project": project, "authenticated": True, "reused": True}
        except Exception:
            logging.getLogger(__name__).debug(
                "Las credenciales GEE almacenadas no se pudieron reutilizar.",
                exc_info=True,
            )
    try:
        # Puerto automatico: evita bloqueos cuando 8085 ya esta ocupado.
        ee.Authenticate(auth_mode="localhost:0", force=force)
        ee.Initialize(project=project)
        ee.Number(1).getInfo()
    except Exception as error:
        raise GeeError(
            "La autorizacion de Earth Engine no termino correctamente. "
            "Compruebe el navegador, la cuenta seleccionada y el acceso al proyecto. "
            f"Detalle: {error}"
        ) from error
    return {"project": project, "authenticated": True, "reused": False}


def authenticate_ee_external(
    project_id: str,
    force: bool = False,
    timeout_seconds: float = 180.0,
    cancel_check=None,
) -> Dict[str, object]:
    """Ejecuta OAuth fuera del proceso de QGIS para evitar bloqueos de la GUI."""

    project = str(project_id or "").strip()
    if not project:
        raise GeeError("Ingrese el ID del proyecto de Google Cloud.")
    helper = Path(__file__).with_name("gee_auth_helper.py")
    python_executable = Path(sys.prefix) / ("python.exe" if os.name == "nt" else "bin/python")
    if not python_executable.is_file():
        raise GeeError(f"No se encontro el Python de QGIS en {python_executable}.")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(  # nosec B603 -- lista cerrada, sin shell
        [str(python_executable), str(helper), project, "1" if force else "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    started = time.monotonic()
    while process.poll() is None:
        if cancel_check is not None and cancel_check():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise GeeError("Autenticacion cancelada por el usuario.")
        if time.monotonic() - started > timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise GeeError(
                "La autenticacion supero 3 minutos. Revise si el navegador abrio la pagina de Google, "
                "complete la autorizacion y vuelva a intentarlo."
            )
        time.sleep(0.2)
    stdout, stderr = process.communicate()
    marker = "LUTZ_GEE_RESULT="
    result_line = next((line for line in reversed(stdout.splitlines()) if line.startswith(marker)), "")
    if process.returncode != 0 or not result_line:
        detail = (stderr or stdout or "El proceso no devolvio un resultado.").strip()
        raise GeeError(f"No se completo la autenticacion externa. Detalle: {detail[-2000:]}")
    try:
        return json.loads(result_line[len(marker):])
    except json.JSONDecodeError as error:
        raise GeeError("La respuesta del proceso de autenticacion no fue valida.") from error


def run_gee_external(
    operation: str,
    payload: Mapping[str, object],
    timeout_seconds: float = 300.0,
    cancel_check=None,
):
    """Ejecuta consultas GEE en Python externo y devuelve un resultado JSON.

    QGIS carga bibliotecas geoespaciales y SSL dentro de su proceso principal.
    En algunas instalaciones estas colisionan con las dependencias de Google.
    El proceso aislado evita esa interferencia y mantiene la interfaz activa.
    """

    helper = Path(__file__).with_name("gee_worker_helper.py")
    python_executable = Path(sys.prefix) / ("python.exe" if os.name == "nt" else "bin/python")
    if not python_executable.is_file():
        raise GeeError(f"No se encontro el Python de QGIS en {python_executable}.")
    request = dict(payload)
    request["operation"] = str(operation)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="lutz_gee_") as folder:
        request_path = Path(folder) / "request.json"
        response_path = Path(folder) / "response.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        process = subprocess.Popen(  # nosec B603 -- ejecutables y archivos locales validados
            [str(python_executable), str(helper), str(request_path), str(response_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        started = time.monotonic()
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise GeeError("Operacion Earth Engine cancelada por el usuario.")
            if time.monotonic() - started > timeout_seconds:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise GeeError(
                    f"La operacion Earth Engine supero {int(timeout_seconds)} segundos y fue detenida."
                )
            time.sleep(0.2)
        stdout, stderr = process.communicate()
        if not response_path.is_file():
            detail = (stderr or stdout or "El proceso externo no genero respuesta.").strip()
            raise GeeError(f"Fallo el proceso externo de Earth Engine: {detail[-2000:]}")
        response = json.loads(response_path.read_text(encoding="utf-8"))
        if process.returncode != 0 or not response.get("ok"):
            error_type = response.get("error_type", "Error")
            detail = response.get("error") or stderr or stdout or "Sin detalle"
            raise GeeError(f"{error_type}: {detail}")
        return response.get("result")


def _definition(source_key: str, pisco_precip_asset: str = "", pisco_temp_asset: str = "") -> SourceDefinition:
    if source_key not in SOURCE_CATALOG:
        raise GeeError(f"Fuente climatica no reconocida: {source_key}.")
    base = SOURCE_CATALOG[source_key]
    asset = base.collection_id
    if source_key == "pisco_p" and str(pisco_precip_asset).strip():
        asset = str(pisco_precip_asset).strip()
    if source_key == "pisco_t" and str(pisco_temp_asset).strip():
        asset = str(pisco_temp_asset).strip()
    return SourceDefinition(
        base.key, base.label, asset, base.frequency, base.raw_bands,
        base.output_bands, base.nominal_scale_m, base.spatial_reducer,
    )


def _date_from_millis(value) -> Optional[date]:
    if value is None:
        return None
    # ``datetime.utcfromtimestamp`` falla en Windows para algunas fechas
    # negativas (ERA5-Land comienza antes de 1970). La suma desde el epoch es
    # portable entre Windows, Linux y macOS.
    return (datetime(1970, 1, 1) + timedelta(milliseconds=float(value))).date().replace(day=1)


def _month_range(first: date, last: date) -> List[date]:
    values = []
    current = first.replace(day=1)
    end = last.replace(day=1)
    while current <= end:
        values.append(current)
        current = date(current.year + (1 if current.month == 12 else 0), 1 if current.month == 12 else current.month + 1, 1)
    return values


def collection_summary(
    source_key: str,
    pisco_precip_asset: str = "",
    pisco_temp_asset: str = "",
) -> Dict[str, object]:
    """Devuelve fechas, bandas, escala y continuidad basica de una coleccion."""

    ee = _load_ee()
    if hasattr(ee.data, "setDeadline"):
        ee.data.setDeadline(60000)
    definition = _definition(source_key, pisco_precip_asset, pisco_temp_asset)
    try:
        collection = ee.ImageCollection(definition.collection_id).sort("system:time_start")
        first = ee.Image(collection.first())
        raw = ee.Dictionary(
            {
                "size": collection.size(),
                "first_time": collection.aggregate_min("system:time_start"),
                "last_time": collection.aggregate_max("system:time_start"),
                "bands": first.bandNames(),
                "scale_m": first.select(0).projection().nominalScale(),
            }
        ).getInfo()
        size = int(raw.get("size") or 0)
        if not size:
            raise GeeError("La coleccion existe pero no contiene imagenes.")
        missing_bands = [band for band in definition.raw_bands if band not in (raw.get("bands") or [])]
        first_date = _date_from_millis(raw.get("first_time"))
        last_date = _date_from_millis(raw.get("last_time"))
        result = {
            "key": definition.key,
            "label": definition.label,
            "collection_id": definition.collection_id,
            "frequency": definition.frequency,
            "size": size,
            "first_date": first_date.isoformat() if first_date else None,
            "last_date": last_date.isoformat() if last_date else None,
            "bands": list(raw.get("bands") or []),
            "required_bands": list(definition.raw_bands),
            "missing_bands": missing_bands,
            "scale_m": float(raw.get("scale_m")) if raw.get("scale_m") is not None else None,
            "duplicates": [],
            "missing_months": [],
        }
        if definition.frequency == "monthly" and size <= 5000:
            times = collection.aggregate_array("system:time_start").getInfo() or []
            months = [_date_from_millis(value) for value in times]
            labels = [value.strftime("%Y-%m") for value in months if value]
            result["duplicates"] = sorted({value for value in labels if labels.count(value) > 1})
            if first_date and last_date:
                available = set(labels)
                result["missing_months"] = [
                    value.strftime("%Y-%m") for value in _month_range(first_date, last_date)
                    if value.strftime("%Y-%m") not in available
                ]
        return result
    except GeeError:
        raise
    except Exception as error:
        raise GeeError(f"No se pudo inspeccionar {definition.collection_id}: {error}") from error


def diagnose_default_sources(pisco_precip_asset: str = "", pisco_temp_asset: str = "") -> Dict[str, object]:
    output = {}
    for key in SOURCE_CATALOG:
        try:
            output[key] = {"ok": True, "summary": collection_summary(key, pisco_precip_asset, pisco_temp_asset)}
        except Exception as error:
            output[key] = {"ok": False, "error": str(error)}
    return output


def _empty_raw_image(ee, bands: Sequence[str]):
    return ee.Image.constant([0] * len(bands)).rename(list(bands)).updateMask(ee.Image(0))


def _transform_image(ee, definition: SourceDefinition, image):
    image = ee.Image(image)
    if definition.key == "era5":
        tmin = image.select("temperature_2m_min").subtract(273.15).rename("temp_min_c")
        tmax = image.select("temperature_2m_max").subtract(273.15).rename("temp_max_c")
        # Hargreaves-Samani requiere temperaturas mensuales representativas,
        # no los extremos absolutos del producto MONTHLY_AGGR. ``image`` es el
        # promedio mensual de los extremos diarios de DAILY_AGGR.
        tmean = tmin.add(tmax).divide(2).rename("temp_media_c")
        return ee.Image.cat([tmin, tmean, tmax])
    return image.select(list(definition.raw_bands), list(definition.output_bands))


def extract_monthly_series(
    source_key: str,
    geometry_geojson: Mapping[str, object],
    start_date: str,
    end_date: str,
    pisco_precip_asset: str = "",
    pisco_temp_asset: str = "",
    coverage_warning_pct: float = 90.0,
) -> List[Dict[str, object]]:
    """Calcula promedios areales mensuales ponderados por area de pixel.

    ``end_date`` representa el ultimo mes incluido. Los valores enmascarados no
    se sustituyen por -99; se informa la cobertura espacial valida.
    """

    ee = _load_ee()
    if hasattr(ee.data, "setDeadline"):
        ee.data.setDeadline(600000)
    definition = _definition(source_key, pisco_precip_asset, pisco_temp_asset)
    try:
        first = datetime.strptime(str(start_date)[:10], "%Y-%m-%d").date().replace(day=1)
        last = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date().replace(day=1)
    except ValueError as error:
        raise GeeError("Las fechas deben tener el formato AAAA-MM-DD.") from error
    if last < first:
        raise GeeError("La fecha final debe ser posterior o igual a la fecha inicial.")
    months = _month_range(first, last)
    if len(months) > 1200:
        raise GeeError("El periodo solicitado supera 100 años mensuales.")
    try:
        geometry = ee.Geometry(dict(geometry_geojson), proj="EPSG:4326", geodesic=False)
        collection = ee.ImageCollection(definition.collection_id)
        projection_info = (
            ee.Image(collection.first())
            .select(definition.raw_bands[0])
            .projection()
            .getInfo()
            or {}
        )
        native_crs = projection_info.get("crs")
        native_transform = projection_info.get("transform")
        if not native_crs or not isinstance(native_transform, (list, tuple)) or len(native_transform) != 6:
            raise GeeError(
                f"No se pudo leer la malla nativa de {definition.label}; "
                "la extraccion se cancelo para evitar un remuestreo silencioso."
            )
        month_strings = ee.List([value.isoformat() for value in months])
        empty = _empty_raw_image(ee, definition.raw_bands)

        def build_feature(value):
            month_start = ee.Date(value)
            month_end = month_start.advance(1, "month")
            subset = collection.filterDate(month_start, month_end)
            count = subset.size()
            if definition.frequency == "daily":
                raw_image = ee.Image(ee.Algorithms.If(count.gt(0), subset.select(list(definition.raw_bands)).sum(), empty))
            elif definition.frequency == "daily_temperature_mean":
                raw_image = ee.Image(ee.Algorithms.If(
                    count.gt(0), subset.select(list(definition.raw_bands)).mean(), empty
                ))
            else:
                raw_image = ee.Image(ee.Algorithms.If(count.gt(0), subset.first(), empty))
            image = _transform_image(ee, definition, raw_image)
            combined_mask = image.mask().reduce(ee.Reducer.min())
            area_image = ee.Image.pixelArea().updateMask(combined_mask)
            reduce_args = {
                "reducer": ee.Reducer.sum(),
                "geometry": geometry,
                # Usar exactamente la malla de origen evita que ``scale`` en
                # metros desplace o remuestree un asset geografico de 0.1°.
                "crs": native_crs,
                "crsTransform": list(native_transform),
                "maxPixels": 1e13,
                "tileScale": 4,
            }
            if definition.key == "era5":
                # Replica el script GEE entregado por el usuario: promedio
                # areal ponderado con scale=11132 m, sin forzar CRS. Mantener
                # este camino explicito evita comparar dos mallas distintas.
                reduce_args.pop("crs", None)
                reduce_args.pop("crsTransform", None)
                reduce_args["scale"] = definition.nominal_scale_m
            valid_raw = area_image.reduceRegion(**reduce_args).get("area")
            valid_area = ee.Number(ee.Algorithms.If(valid_raw, valid_raw, 0))
            basin_area = geometry.area(1)
            if definition.key == "era5":
                method_label = "replica_script_gee_extremos_diarios_scale_11132_area_ponderada"
            elif definition.spatial_reducer == "pixel_mean":
                method_label = "media_zonal_pixeles_nativos"
            else:
                method_label = "media_areal_ponderada"
            properties = ee.Dictionary(
                {
                    "fecha": month_start.format("YYYY-MM-dd"),
                    "system_time_start": month_start.millis(),
                    "fuente": definition.label,
                    "source_key": definition.key,
                    "image_count": count,
                    "expected_image_count": month_end.difference(month_start, "day"),
                    "metodo": method_label,
                    "native_crs": native_crs,
                    "native_transform": json.dumps(list(native_transform)),
                    "analysis_scale_m": definition.nominal_scale_m,
                    "spatial_reducer": definition.spatial_reducer,
                    "coverage_pct": ee.Algorithms.If(
                        basin_area.gt(0), valid_area.divide(basin_area).multiply(100).min(100), None
                    ),
                }
            )
            for band in definition.output_bands:
                if definition.spatial_reducer == "pixel_mean":
                    mean_args = dict(reduce_args)
                    # QGIS ZonalStatistics calcula la media de las celdas de
                    # la malla que pertenecen a la zona. El reductor no
                    # ponderado evita que Earth Engine introduzca pesos por la
                    # fraccion de pixel intersectada en el borde de la cuenca.
                    mean_args["reducer"] = ee.Reducer.mean().unweighted()
                    mean_raw = image.select(band).reduceRegion(**mean_args).get(band)
                    value_out = ee.Algorithms.If(valid_area.gt(0), mean_raw, -999999.0)
                else:
                    numerator_raw = image.select(band).multiply(area_image).reduceRegion(**reduce_args).get(band)
                    # Dictionary.set no admite ``null``. Se usa un centinela
                    # solo durante la transferencia y se restaura en Python.
                    value_out = ee.Algorithms.If(
                        valid_area.gt(0), ee.Number(numerator_raw).divide(valid_area), -999999.0
                    )
                properties = properties.set(band, value_out)
            return ee.Feature(None, properties)

        features = ee.FeatureCollection(month_strings.map(build_feature)).sort("system_time_start")
        info = features.getInfo()
        rows = []
        for feature in info.get("features", []):
            row = dict(feature.get("properties") or {})
            for band in definition.output_bands:
                value = row.get(band)
                if value is not None and float(value) <= -999000.0:
                    row[band] = None
            coverage = row.get("coverage_pct")
            row["advertencia_cobertura"] = bool(
                coverage is not None and math.isfinite(float(coverage)) and float(coverage) < coverage_warning_pct
            )
            expected = row.get("expected_image_count")
            count = row.get("image_count")
            row["advertencia_temporal"] = bool(
                definition.frequency == "daily_temperature_mean"
                and expected is not None and count is not None
                and int(count) != int(round(float(expected)))
            )
            rows.append(row)
        return sorted(rows, key=lambda row: row.get("fecha", ""))
    except GeeError:
        raise
    except Exception as error:
        raise GeeError(f"No se pudo obtener la serie areal de {definition.label}: {error}") from error


def write_climate_csv(rows: Sequence[Mapping[str, object]], path: str) -> str:
    if not rows:
        raise GeeError("No existen resultados climaticos para exportar.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    preferred = (
        "fecha", "precipitacion_mm", "temp_min_c", "temp_media_c", "temp_max_c",
        "coverage_pct", "image_count", "expected_image_count", "fuente", "metodo", "source_key",
        "native_crs", "native_transform", "analysis_scale_m", "spatial_reducer",
        "advertencia_cobertura", "advertencia_temporal",
    )
    extra = sorted({key for row in rows for key in row if key not in preferred and key != "system_time_start"})
    fields = [key for key in preferred if any(key in row for row in rows)] + extra
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return str(target)
