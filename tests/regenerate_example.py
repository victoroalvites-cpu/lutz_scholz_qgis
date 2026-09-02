"""Regenera el caudal observado sintetico del CSV demostrativo."""

import csv
from pathlib import Path

from lutz_scholz_qgis.core import ModelParameters, RetentionConfig, run_model
from lutz_scholz_qgis.io_utils import read_monthly_csv


source = Path(__file__).parents[1] / "examples" / "serie_mensual_ejemplo.csv"
records = read_monthly_csv(str(source))
config = RetentionConfig(
    (0, 0, 0, 1, 2, 3, 4, 5, 6, 0, 0, 0),
    (0.50, 0.30, 0.20, 0, 0, 0, 0, 0, 0, 0, 0, 0),
)
result = run_model(
    records,
    ModelParameters(950.54, 0.17, 15.0, k=0.034),
    config,
    (1990, 1994),
    (1995, 1995),
)
monthly_factors = (0.95, 1.03, 1.05, 0.97, 1.02, 0.96, 1.01, 1.04, 0.98, 1.03, 0.96, 1.02)
rows = []
for row in result["rows"]:
    year_factor = 1.0 + 0.005 * ((row["anio"] - 1990) % 3 - 1)
    observed = row["caudal_simulado_m3s"] * monthly_factors[row["mes"] - 1] * year_factor
    rows.append(
        {
            "fecha": row["fecha"],
            "precipitacion_mm": f"{row['precipitacion_mm']:.3f}",
            "caudal_observado_m3s": f"{observed:.4f}",
        }
    )

with source.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=("fecha", "precipitacion_mm", "caudal_observado_m3s"))
    writer.writeheader()
    writer.writerows(rows)
