"""Graficos SVG livianos para las series climaticas descargadas."""

from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape


COLORS = {
    "precipitacion_mm": "#2878B5",
    "temp_min_c": "#3B82F6",
    "temp_media_c": "#F59E0B",
    "temp_max_c": "#DC2626",
}
LABELS = {
    "precipitacion_mm": "Precipitacion (mm/mes)",
    "temp_min_c": "Tmin (C)",
    "temp_media_c": "Tmedia (C)",
    "temp_max_c": "Tmax (C)",
}


def _finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _panel(rows, fields, x, y, width, height, title):
    values = [float(row[field]) for row in rows for field in fields if _finite(row.get(field))]
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        low -= 1
        high += 1
    pad = (high - low) * 0.06
    low -= pad
    high += pad
    left, right, top, bottom = x + 62, x + width - 18, y + 34, y + height - 48
    commands = [
        f'<text x="{x + width / 2:.1f}" y="{y + 18}" text-anchor="middle" class="title">{escape(title)}</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>',
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="axis"/>',
    ]
    for tick in range(5):
        value = low + (high - low) * tick / 4
        py = bottom - (bottom - top) * tick / 4
        commands.append(f'<line x1="{left}" y1="{py:.1f}" x2="{right}" y2="{py:.1f}" class="grid"/>')
        commands.append(f'<text x="{left - 8}" y="{py + 4:.1f}" text-anchor="end" class="tick">{value:.1f}</text>')
    count = max(len(rows) - 1, 1)
    for field in fields:
        segments, current = [], []
        for index, row in enumerate(rows):
            value = row.get(field)
            if not _finite(value):
                if current:
                    segments.append(current); current = []
                continue
            px = left + (right - left) * index / count
            py = bottom - (float(value) - low) / (high - low) * (bottom - top)
            current.append((px, py))
        if current:
            segments.append(current)
        for segment in segments:
            points = " ".join(f"{px:.1f},{py:.1f}" for px, py in segment)
            commands.append(f'<polyline points="{points}" fill="none" stroke="{COLORS[field]}" stroke-width="1.6"/>')
    label_indexes = sorted({0, len(rows) // 2, max(len(rows) - 1, 0)})
    for index in label_indexes:
        if not rows:
            continue
        px = left + (right - left) * index / count
        label = str(rows[index].get("fecha", ""))[:7]
        commands.append(f'<text x="{px:.1f}" y="{bottom + 18}" text-anchor="middle" class="tick">{escape(label)}</text>')
    legend_x = left
    for field in fields:
        commands.append(f'<line x1="{legend_x}" y1="{y + height - 12}" x2="{legend_x + 22}" y2="{y + height - 12}" stroke="{COLORS[field]}" stroke-width="2"/>')
        commands.append(f'<text x="{legend_x + 27}" y="{y + height - 8}" class="tick">{escape(LABELS[field])}</text>')
        legend_x += 175
    return commands


def create_climate_svg(rows, path, title="Serie climatica areal"):
    fields_p = ["precipitacion_mm"] if any(_finite(row.get("precipitacion_mm")) for row in rows) else []
    fields_t = [field for field in ("temp_min_c", "temp_media_c", "temp_max_c") if any(_finite(row.get(field)) for row in rows)]
    panels = int(bool(fields_p)) + int(bool(fields_t))
    height = 690 if panels == 2 else 390
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}">',
        '<style>.title{font:600 15px Arial;fill:#17365D}.tick{font:11px Arial;fill:#475467}.axis{stroke:#344054;stroke-width:1}.grid{stroke:#D0D5DD;stroke-width:.7}</style>',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="550" y="24" text-anchor="middle" style="font:700 19px Arial;fill:#17365D">{escape(title)}</text>',
    ]
    y = 38
    if fields_p:
        svg.extend(_panel(rows, fields_p, 12, y, 1076, 300, "Precipitacion media areal"))
        y += 300
    if fields_t:
        svg.extend(_panel(rows, fields_t, 12, y, 1076, 300, "Temperatura media areal"))
    svg.append("</svg>")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(svg), encoding="utf-8")
    return str(target)
