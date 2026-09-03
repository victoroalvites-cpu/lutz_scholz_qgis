"""Creación y persistencia de la estructura de trabajo del complemento."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


PROJECT_FILE = "proyecto_lutz_scholz.json"
PROJECT_FOLDERS = {
    "input": "01_Datos_Entrada",
    "climate": "02_Clima",
    "results": "03_Resultados",
    "documentation": "04_Documentacion",
}


def ensure_project_structure(root_folder):
    """Crea la estructura estándar sin eliminar ni sobrescribir datos del usuario."""

    root = Path(root_folder).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    folders = {}
    for key, name in PROJECT_FOLDERS.items():
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        folders[key] = folder

    config_path = root / PROJECT_FILE
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if config_path.is_file():
        try:
            previous = json.loads(config_path.read_text(encoding="utf-8"))
            created_at = previous.get("created_at") or created_at
        except (OSError, ValueError, TypeError):
            pass

    payload = {
        "schema_version": 1,
        "application": "Lutz Sholtz para QGIS",
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folders": {key: path.name for key, path in folders.items()},
    }
    config_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return root, folders, config_path
