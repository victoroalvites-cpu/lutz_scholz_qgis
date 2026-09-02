"""Proceso auxiliar de OAuth para no bloquear el proceso principal de QGIS."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_DIR.parent))

from lutz_scholz_qgis.gee_service import authenticate_ee


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else ""
    force = len(sys.argv) > 2 and sys.argv[2] == "1"
    try:
        result = authenticate_ee(project, force)
        print("LUTZ_GEE_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
