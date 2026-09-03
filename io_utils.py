"""Lectura de entradas y escritura de resultados del complemento."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .core import LutzError, MonthlyRecord
from .xlsx_reader import excel_serial_to_date, read_xlsx_sheets


@dataclass
class ProjectInput:
    records: List[MonthlyRecord]
    config: Dict[str, object] = field(default_factory=dict)
    retention_rows: List[Dict[str, object]] = field(default_factory=list)
    components: List[Dict[str, object]] = field(default_factory=list)


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _find_header(headers: Iterable[str], alternatives: Iterable[str]) -> Optional[str]:
    normalized = {_normalize(header): header for header in headers}
    for alternative in alternatives:
        if alternative in normalized:
            return normalized[alternative]
    return None


def _parse_date(value: str) -> date:
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%Y-%m", "%d/%m/%Y", "%m/%Y", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text, pattern).date()
            return parsed.replace(day=1)
        except ValueError:
            continue
    raise LutzError(f"Fecha no reconocida: {value!r}. Use AAAA-MM-01.")


def _parse_number(value: str, name: str, required: bool = True) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        if required:
            raise LutzError(f"Falta un valor en la columna {name}.")
        return None
    try:
        return float(text.replace(" ", "").replace(",", "."))
    except ValueError as error:
        raise LutzError(f"Valor no numerico en {name}: {value!r}.") from error


def read_monthly_csv(path: str) -> List[MonthlyRecord]:
    source = Path(path)
    if not source.is_file():
        raise LutzError("No se encontro el archivo CSV de entrada.")
    sample = source.read_text(encoding="utf-8-sig")[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, dialect=dialect)
        if not reader.fieldnames:
            raise LutzError("El CSV no contiene encabezados.")
        date_header = _find_header(reader.fieldnames, ("fecha", "date", "mes"))
        p_header = _find_header(reader.fieldnames, ("precipitacion_mm", "precipitacion", "p_mm", "p"))
        q_header = _find_header(
            reader.fieldnames,
            (
                "caudal_observado_m3s",
                "caudal_obs_m3s",
                "caudal_observado",
                "caudal_obs",
                "q_observado_m3s",
                "q_obs_m3s",
                "q_observado",
                "q_obs",
                "qobs",
                "caudal",
                "q",
            ),
        )
        tmin_header = _find_header(reader.fieldnames, ("temp_min_c", "temperatura_min_c", "tmin_c", "tmin"))
        tmean_header = _find_header(reader.fieldnames, ("temp_media_c", "temperatura_media_c", "tmedia_c", "tmean_c", "tmedia"))
        tmax_header = _find_header(reader.fieldnames, ("temp_max_c", "temperatura_max_c", "tmax_c", "tmax"))
        etp_header = _find_header(reader.fieldnames, ("etp_mm", "et0_mm", "etp_importada_mm", "evapotranspiracion_mm"))
        if date_header is None or p_header is None:
            raise LutzError("El CSV debe incluir las columnas fecha y precipitacion_mm.")
        records = []
        for row_number, row in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in row.values()):
                continue
            try:
                records.append(
                    MonthlyRecord(
                        fecha=_parse_date(row[date_header]),
                        precipitacion_mm=_parse_number(row[p_header], p_header, required=False),
                        caudal_observado_m3s=_parse_number(row.get(q_header, ""), q_header or "caudal", required=False),
                        temp_min_c=_parse_number(row.get(tmin_header, ""), tmin_header or "Tmin", required=False),
                        temp_media_c=_parse_number(row.get(tmean_header, ""), tmean_header or "Tmedia", required=False),
                        temp_max_c=_parse_number(row.get(tmax_header, ""), tmax_header or "Tmax", required=False),
                        etp_mm=_parse_number(row.get(etp_header, ""), etp_header or "ETP", required=False),
                    )
                )
            except LutzError as error:
                raise LutzError(f"Fila {row_number}: {error}") from error
    return records


def _row_dict(headers, row):
    return {_normalize(headers[index]): row[index] if index < len(row) else "" for index in range(len(headers))}


def _header_row_index(rows, required):
    for index, row in enumerate(rows):
        names = {_normalize(value) for value in row if str(value).strip()}
        if all(any(option in names for option in alternatives) for alternatives in required):
            return index
    return None


def _optional_number(value):
    if value in (None, ""):
        return None
    return float(value)


def read_lutz_xlsx(path: str) -> ProjectInput:
    """Lee la plantilla v0.1 y tambien libros con una hoja de serie equivalente."""

    sheets = read_xlsx_sheets(path)
    series_name = next((name for name in sheets if _normalize(name) in ("series_mensuales", "serie_mensual", "datos")), None)
    if series_name is None or not sheets[series_name]:
        raise LutzError("El Excel debe incluir la hoja Series_Mensuales.")
    source = sheets[series_name]
    header_index = _header_row_index(source, (("fecha", "date", "mes"), ("precipitacion_mm", "precipitacion", "p_mm", "p")))
    if header_index is None:
        raise LutzError("Series_Mensuales requiere Fecha y Precipitacion_mm.")
    headers = [str(value) for value in source[header_index]]
    normalized = [_normalize(value) for value in headers]
    date_key = next((key for key in ("fecha", "date", "mes") if key in normalized), None)
    p_key = next((key for key in ("precipitacion_mm", "precipitacion", "p_mm", "p") if key in normalized), None)
    if date_key is None or p_key is None:
        raise LutzError("Series_Mensuales requiere Fecha y Precipitacion_mm.")
    records = []
    for number, row in enumerate(source[header_index + 1:], start=header_index + 2):
        if not any(value not in (None, "") for value in row):
            continue
        item = _row_dict(headers, row)
        raw_date = item[date_key]
        parsed = excel_serial_to_date(raw_date) or _parse_date(str(raw_date))
        q = next((item[key] for key in ("caudal_observado_m3s", "caudal_obs_m3s", "q_obs_m3s", "qobs") if key in item), "")
        records.append(MonthlyRecord(
            fecha=parsed.replace(day=1),
            precipitacion_mm=_optional_number(item[p_key]),
            caudal_observado_m3s=_optional_number(q),
            temp_min_c=_optional_number(next((item[key] for key in ("temp_min_c", "temperatura_min_c", "tmin_c") if key in item), "")),
            temp_media_c=_optional_number(next((item[key] for key in ("temp_media_c", "temperatura_media_c", "tmedia_c") if key in item), "")),
            temp_max_c=_optional_number(next((item[key] for key in ("temp_max_c", "temperatura_max_c", "tmax_c") if key in item), "")),
            etp_mm=_optional_number(next((item[key] for key in ("etp_importada_mm", "etp_mm", "et0_mm") if key in item), "")),
        ))
    config = {}
    for name, rows in sheets.items():
        if _normalize(name) == "configuracion":
            idx = _header_row_index(rows, (("parametro",), ("valor",)))
            for row in rows[(idx + 1 if idx is not None else 0):]:
                if len(row) >= 2 and str(row[0]).strip():
                    config[_normalize(row[0])] = row[1]
    retention_rows = []
    for name, rows in sheets.items():
        if _normalize(name) == "gasto_abastecimiento" and rows:
            idx = _header_row_index(rows, (("posicion_gasto", "orden_gasto"), ("fraccion_abastecimiento", "abastecimiento_pct")))
            if idx is not None:
                hdr = [str(value) for value in rows[idx]]
                retention_rows = [_row_dict(hdr, row) for row in rows[idx + 1:idx + 13] if any(value not in (None, "") for value in row)]
    components = []
    for name, rows in sheets.items():
        if _normalize(name) in ("componentes_r", "componentes_retencion") and rows:
            idx = _header_row_index(rows, (("tipo",), ("area_km2",)))
            if idx is not None:
                hdr = [str(value) for value in rows[idx]]
                components = [_row_dict(hdr, row) for row in rows[idx + 1:] if any(value not in (None, "") for value in row)]
    return ProjectInput(records, config, retention_rows, components)


def read_project(path: str) -> ProjectInput:
    suffix = Path(path).suffix.lower()
    if suffix == ".xlsx":
        return read_lutz_xlsx(path)
    return ProjectInput(read_monthly_csv(path))


def write_results(result: Dict[str, object], output_folder: str) -> Dict[str, str]:
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    data_folder = folder / "datos"
    trace_folder = folder / "trazabilidad"
    data_folder.mkdir(parents=True, exist_ok=True)
    trace_folder.mkdir(parents=True, exist_ok=True)
    series_path = data_folder / "resultados_mensuales.csv"
    metrics_path = data_folder / "metricas.csv"
    parameters_path = trace_folder / "parametros_modelo.json"
    precipitation_path = data_folder / "estadisticas_precipitacion.csv"
    persistence_path = data_folder / "permanencia_caudales.csv"
    transfer_path = data_folder / "transposicion_caudales.csv"
    manifest_path = trace_folder / "manifiesto_corrida.json"

    rows = list(result["rows"])
    with series_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with metrics_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("periodo", "escala", "indicador", "valor"))
        diagnostics = result.get("diagnostics", {})
        for period in ("complete", "calibration", "validation"):
            period_values = diagnostics.get(period, {})
            for scale in ("monthly", "annual", "regime"):
                values = period_values.get(scale)
                if values:
                    for name, value in values.items():
                        writer.writerow((period, scale, name, "" if value is None else value))
            scatter = period_values.get("scatter")
            if scatter:
                for name, value in scatter.items():
                    writer.writerow((period, "scatter_monthly", name, "" if value is None else value))
            for month, values in period_values.get("monthly_by_calendar_month", {}).items():
                if values:
                    for name, value in values.items():
                        writer.writerow((period, f"calendar_month_{int(month):02d}", name, "" if value is None else value))

    persistence = result.get("flow_persistence")
    if not persistence:
        from .core.diagnostics import flow_persistence

        persistence = {"complete": flow_persistence(rows)}
        for key, period in (("calibration", result.get("calibration_period")),
                            ("validation", result.get("validation_period"))):
            if period:
                persistence[key] = flow_persistence(
                    [row for row in rows if period[0] <= int(row["anio"]) <= period[1]]
                )
        result["flow_persistence"] = persistence

    with persistence_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow((
            "periodo", "mes", "origen", "n", "caudal_medio_m3s", "Q75_m3s", "Q95_m3s",
            "referencia_15pct_media_m3s", "ceros_porcentaje", "metodo",
        ))
        for period in ("complete", "calibration", "validation"):
            values = persistence.get(period)
            if not values:
                continue
            for origin in ("simulado", "observado", "transferido"):
                summary = values.get(origin) or {}
                writer.writerow((
                    period, "serie", origin, summary.get("n", 0), summary.get("mean_m3s"),
                    summary.get("Q75_m3s"), summary.get("Q95_m3s"),
                    summary.get("reference_15pct_mean_m3s"), summary.get("zero_percentage"),
                    values.get("method", ""),
                ))
            for monthly in values.get("mensual", []):
                for origin in ("simulado", "observado", "transferido"):
                    summary = monthly.get(origin) or {}
                    writer.writerow((
                        period, monthly.get("mes"), origin, summary.get("n", 0),
                        summary.get("mean_m3s"), summary.get("Q75_m3s"), summary.get("Q95_m3s"),
                        summary.get("reference_15pct_mean_m3s"), summary.get("zero_percentage"),
                        values.get("method", ""),
                    ))

    transfer = result.get("flow_transfer") or {}
    if transfer.get("active"):
        with transfer_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow((
                "fecha", "precipitacion_objetivo_mm", "precipitacion_donante_mm",
                "caudal_donante_m3s", "factor_transferencia", "caudal_transferido_m3s",
            ))
            for row in rows:
                writer.writerow((
                    row.get("fecha"), row.get("precipitacion_mm"),
                    row.get("precipitacion_donante_mm"), row.get("caudal_donante_m3s"),
                    row.get("factor_transferencia"), row.get("caudal_transferido_m3s"),
                ))

    statistics = result.get("precipitation_statistics") or {}
    with precipitation_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("grupo", "periodo", "media_mm", "desviacion_mm", "cv_porcentaje", "min_mm", "max_mm"))
        writer.writerow(("anual_resumen", "serie", statistics.get("media_anual_mm"), statistics.get("desviacion_anual_mm"), statistics.get("cv_anual_porcentaje"), statistics.get("min_anual_mm"), statistics.get("max_anual_mm")))
        for row in statistics.get("mensual", []):
            writer.writerow(("climatologia_mensual", row["mes"], row["media_mm"], row["desviacion_mm"], row["cv_porcentaje"], row["min_mm"], row["max_mm"]))

    payload = {key: result[key] for key in ("version", "parameters", "retention", "pe_average_mm", "q_average_mm", "regression", "calibration_period", "validation_period")}
    payload["balance_diagnostics"] = result.get("balance_diagnostics", {})
    payload["run_metadata"] = result.get("run_metadata", {})
    payload["diagnostic_scales"] = result.get("diagnostics", {})
    payload["flow_persistence"] = persistence
    payload["persistence_analysis"] = result.get("persistence_analysis", {})
    payload["flow_transfer"] = transfer
    if result.get("automatic_calibration"):
        payload["automatic_calibration"] = result["automatic_calibration"]
    if result.get("c_estimation"):
        payload["c_estimation"] = result["c_estimation"]
    parameters_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema": "lutz-scholz-run-manifest-v2",
        "plugin_version": result.get("version"),
        "run_metadata": result.get("run_metadata", {}),
        "parameters": result.get("parameters"),
        "retention": result.get("retention"),
        "calibration_period": result.get("calibration_period"),
        "validation_period": result.get("validation_period"),
        "validation_recalibrates": False,
        "files": {
            "monthly_results": "datos/" + series_path.name,
            "metrics": "datos/" + metrics_path.name,
            "precipitation_statistics": "datos/" + precipitation_path.name,
            "flow_persistence": "datos/" + persistence_path.name,
            "parameters": "trazabilidad/" + parameters_path.name,
        },
    }
    if transfer.get("active"):
        manifest["files"]["flow_transfer"] = "datos/" + transfer_path.name
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs = {
        "series": str(series_path), "metrics": str(metrics_path),
        "precipitation_statistics": str(precipitation_path),
        "flow_persistence": str(persistence_path),
        "parameters": str(parameters_path), "manifest": str(manifest_path),
    }
    if transfer.get("active"):
        outputs["flow_transfer"] = str(transfer_path)
    return outputs
