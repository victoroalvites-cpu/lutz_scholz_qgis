"""Ejecutor aislado para consultas Earth Engine iniciadas por QGIS."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_DIR.parent))

from lutz_scholz_qgis.gee_service import (
    collection_summary,
    diagnose_default_sources,
    extract_monthly_series,
    initialize_ee,
)


def execute(request):
    operation = request.get("operation")
    project = request.get("project_id", "")
    connection = initialize_ee(project)
    if operation == "initialize":
        return connection
    if operation == "summary":
        return collection_summary(
            request.get("source_key", ""),
            request.get("pisco_precip_asset", ""),
            request.get("pisco_temp_asset", ""),
        )
    if operation == "diagnose":
        return diagnose_default_sources(
            request.get("pisco_precip_asset", ""),
            request.get("pisco_temp_asset", ""),
        )
    if operation == "extract":
        return extract_monthly_series(
            request.get("source_key", ""),
            request.get("geometry_geojson") or {},
            request.get("start_date", ""),
            request.get("end_date", ""),
            request.get("pisco_precip_asset", ""),
            request.get("pisco_temp_asset", ""),
        )
    raise ValueError(f"Operacion externa desconocida: {operation!r}.")


def main():
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        response = {"ok": True, "result": execute(request)}
        code = 0
    except Exception as error:
        response = {
            "ok": False,
            "error_type": type(error).__name__,
            "error": str(error) or repr(error),
        }
        code = 1
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
