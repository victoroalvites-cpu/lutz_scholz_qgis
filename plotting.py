"""Graficos SVG portables para exportar y mostrar dentro de QGIS."""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .core.diagnostics import diagnostic_scales
from .reporting import _display_mode, _display_modeling_id, _display_split


MONTHS = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")


def _svg_start(title, width=1000, height=560):
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#1f2937}.title{font-size:21px;font-weight:bold}.subtitle{font-size:12px}.label{font-size:13px}.tick{font-size:10px}.metric{font-size:11px}.axis{stroke:#64748b}.grid{stroke:#e5e7eb}.sim{fill:none;stroke:#d97706;stroke-width:2}.obs{fill:none;stroke:#1d4ed8;stroke-width:1.7}.reg{fill:none;stroke:#dc2626;stroke-width:1.5}</style>',
        f'<text x="{width/2:.0f}" y="31" text-anchor="middle" class="title">{html.escape(title)}</text>',
    ]


def _frame(parts, y_label="Caudal (m3/s)", x_label="Tiempo", width=1000, height=560,
           box=None):
    if box is None:
        px, py, pw, ph = 78, 62, width - 112, height - 122
    else:
        px, py, pw, ph = box
    for fraction in (0, .25, .5, .75, 1):
        y = py + ph * fraction
        parts.append(f'<line x1="{px}" y1="{y:.1f}" x2="{px+pw}" y2="{y:.1f}" class="grid"/>')
    parts.extend((
        f'<line x1="{px}" y1="{py}" x2="{px}" y2="{py+ph}" class="axis"/>',
        f'<line x1="{px}" y1="{py+ph}" x2="{px+pw}" y2="{py+ph}" class="axis"/>',
        f'<text x="{px+pw/2}" y="{py+ph+38}" text-anchor="middle" class="label">{html.escape(x_label)}</text>',
        f'<text x="{px-50}" y="{py+ph/2}" transform="rotate(-90 {px-50} {py+ph/2})" text-anchor="middle" class="label">{html.escape(y_label)}</text>',
    ))
    return px, py, pw, ph


def _write(path, parts):
    parts.append("</svg>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")
    return str(path)


def _maximum(*series):
    values = [float(value) for values in series for value in values
              if value is not None and math.isfinite(float(value))]
    return (max(values, default=1.0) * 1.07) or 1.0


def _polyline(parts, values: Iterable, plot, css, maximum):
    values = list(values)
    px, py, pw, ph = plot
    segments, current = [], []
    for index, value in enumerate(values):
        if value is None or not math.isfinite(float(value)):
            if current:
                segments.append(current); current = []
            continue
        x = px + pw * index / max(len(values) - 1, 1)
        y = py + ph - ph * float(value) / maximum
        current.append(f"{x:.2f},{y:.2f}")
    if current:
        segments.append(current)
    for points in segments:
        parts.append(f'<polyline points="{" ".join(points)}" class="{css}"/>')


def _y_labels(parts, plot, maximum):
    px, py, _, ph = plot
    for fraction in (0, .25, .5, .75, 1):
        value = maximum * (1 - fraction)
        parts.append(f'<text x="{px-8}" y="{py+ph*fraction+4:.1f}" text-anchor="end" class="tick">{value:.2f}</text>')


def _legend(parts, x=760, y=52):
    parts.append(f'<line x1="{x}" y1="{y}" x2="{x+28}" y2="{y}" class="obs"/><text x="{x+35}" y="{y+5}" class="label">Observado</text>')
    parts.append(f'<line x1="{x+110}" y1="{y}" x2="{x+138}" y2="{y}" class="sim"/><text x="{x+145}" y="{y+5}" class="label">Simulado</text>')


def _metric_text(metrics):
    if not metrics:
        return "Sin pares observados"
    values = []
    for key in ("NSE", "KGE"):
        value = metrics.get(key)
        values.append(f"{key}={'N/D' if value is None else f'{value:.3f}'}")
    values.extend((f"PBIAS={metrics.get('PBIAS_porcentaje', float('nan')):.1f}%", f"n={metrics.get('n', 0)}"))
    return " · ".join(values)


def _date_labels(parts, plot, rows, maximum_labels=7):
    if not rows:
        return
    px, py, pw, ph = plot
    indexes = sorted({round(i * (len(rows)-1) / max(min(maximum_labels, len(rows))-1, 1))
                      for i in range(min(maximum_labels, len(rows)))})
    for index in indexes:
        x = px + pw * index / max(len(rows)-1, 1)
        label = str(rows[index].get("fecha", ""))[:7]
        parts.append(f'<text x="{x:.1f}" y="{py+ph+18}" text-anchor="middle" class="tick">{html.escape(label)}</text>')


def _series_svg(rows, path, period_label="Serie completa", periods=None, diagnostics=None):
    sim = [row["caudal_simulado_m3s"] for row in rows]
    obs = [row["caudal_observado_m3s"] for row in rows]
    maximum = _maximum(sim, obs)
    parts = _svg_start(f"Serie mensual: observado vs. simulado - {period_label}")
    plot = _frame(parts)
    if periods and rows:
        px, py, pw, ph = plot
        for label, period, color in (("Calibracion", periods.get("calibration"), "#dbeafe"),
                                     ("Validacion", periods.get("validation"), "#dcfce7")):
            if not period:
                continue
            selected = [i for i, row in enumerate(rows) if period[0] <= row["anio"] <= period[1]]
            if selected:
                x1 = px + pw * min(selected) / max(len(rows)-1, 1)
                x2 = px + pw * max(selected) / max(len(rows)-1, 1)
                parts.append(f'<rect x="{x1:.2f}" y="{py}" width="{max(x2-x1, 1):.2f}" height="{ph}" fill="{color}" fill-opacity="0.35"/>')
                parts.append(f'<text x="{(x1+x2)/2:.2f}" y="{py+16}" text-anchor="middle" class="tick">{label}</text>')
    _y_labels(parts, plot, maximum); _polyline(parts, sim, plot, "sim", maximum); _polyline(parts, obs, plot, "obs", maximum)
    _date_labels(parts, plot, rows); _legend(parts)
    parts.append(f'<text x="78" y="52" class="metric">Escala mensual · {_metric_text((diagnostics or {}).get("monthly"))}</text>')
    return _write(path, parts)


def _annual_svg(rows, path, period_label="Serie completa", diagnostics=None):
    diagnostics = diagnostics or diagnostic_scales(rows)
    values = diagnostics["annual_series"]
    years = [row["anio"] for row in values]
    sim = [row["caudal_simulado_m3s"] for row in values]; obs = [row["caudal_observado_m3s"] for row in values]
    maximum = _maximum(sim, obs)
    parts = _svg_start(f"Caudal medio anual ponderado por dias - {period_label}")
    plot = _frame(parts, x_label="Anio"); _y_labels(parts, plot, maximum); _legend(parts)
    _polyline(parts, sim, plot, "sim", maximum); _polyline(parts, obs, plot, "obs", maximum)
    px, py, pw, ph = plot
    for index, year in enumerate(years):
        if index % max(1, len(years)//10) == 0:
            x = px + pw * index / max(len(years)-1, 1)
            parts.append(f'<text x="{x:.1f}" y="{py+ph+18}" text-anchor="middle" class="tick">{year}</text>')
    parts.append(f'<text x="78" y="52" class="metric">Escala anual ponderada · {_metric_text(diagnostics.get("annual"))}</text>')
    return _write(path, parts)


def _monthly_svg(rows, path, period_label="Serie completa", diagnostics=None):
    diagnostics = diagnostics or diagnostic_scales(rows)
    values = diagnostics["regime_series"]
    sim = [row["caudal_simulado_m3s"] for row in values]; obs = [row["caudal_observado_m3s"] for row in values]
    maximum = _maximum(sim, obs)
    parts = _svg_start(f"Regimen multimensual - {period_label}")
    plot = _frame(parts, x_label="Mes"); _y_labels(parts, plot, maximum); _legend(parts)
    _polyline(parts, sim, plot, "sim", maximum); _polyline(parts, obs, plot, "obs", maximum)
    px, py, pw, ph = plot
    for month in range(12):
        x = px + pw * month/11
        parts.append(f'<text x="{x:.1f}" y="{py+ph+18}" text-anchor="middle" class="tick">{MONTHS[month]}</text>')
    parts.append(f'<text x="78" y="52" class="metric">Escala regimen · {_metric_text(diagnostics.get("regime"))}</text>')
    return _write(path, parts)


def _scatter_svg(rows, path, period_label="Serie completa", diagnostics=None):
    diagnostics = diagnostics or diagnostic_scales(rows)
    pairs = [(float(row["caudal_observado_m3s"]), float(row["caudal_simulado_m3s"]))
             for row in rows if row["caudal_observado_m3s"] is not None]
    maximum = _maximum([value for pair in pairs for value in pair])
    regression = diagnostics.get("scatter") or {}
    parts = _svg_start(f"Dispersion mensual: observado vs. simulado - {period_label}")
    plot = _frame(parts, y_label="Simulado (m3/s)", x_label="Observado (m3/s)"); _y_labels(parts, plot, maximum)
    px, py, pw, ph = plot
    parts.append(f'<line x1="{px}" y1="{py+ph}" x2="{px+pw}" y2="{py}" stroke="#64748b" stroke-dasharray="6 5"/>')
    slope, intercept = regression.get("slope"), regression.get("intercept")
    if slope is not None and intercept is not None:
        points = []
        for observed in (0.0, maximum):
            simulated = intercept + slope * observed
            if 0 <= simulated <= maximum:
                x = px + pw * observed/maximum; y = py + ph - ph * simulated/maximum
                points.append(f"{x:.2f},{y:.2f}")
        if len(points) == 2:
            parts.append(f'<polyline points="{" ".join(points)}" class="reg"/>')
    for observed, simulated in pairs:
        x = px + pw * observed/maximum; y = py + ph - ph * simulated/maximum
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#0f766e" fill-opacity="0.65"/>')
    equation = regression.get("equation", "Regresion no disponible")
    r2 = regression.get("R2")
    r2_text = "N/D" if r2 is None else f"{r2:.3f}"
    parts.append(f'<text x="78" y="52" class="metric">{html.escape(equation)} · R2={r2_text} · n={regression.get("n", 0)}</text>')
    return _write(path, parts)


def _panel_diagnostic_svg(rows, path, period_label, diagnostics=None):
    """Panel compacto 2x2 inspirado en el diagnostico exportado por R."""
    diagnostics = diagnostics or diagnostic_scales(rows)
    width, height = 1200, 820
    parts = _svg_start(f"Diagnostico hidrologico - {period_label}", width, height)
    boxes = [(68, 92, 500, 270), (668, 92, 460, 270), (68, 486, 500, 250), (668, 486, 460, 250)]

    # Serie mensual.
    sim = [row["caudal_simulado_m3s"] for row in rows]; obs = [row["caudal_observado_m3s"] for row in rows]
    maximum = _maximum(sim, obs); plot = _frame(parts, x_label="Fecha", box=boxes[0]); _y_labels(parts, plot, maximum)
    _polyline(parts, sim, plot, "sim", maximum); _polyline(parts, obs, plot, "obs", maximum)
    parts.append(f'<text x="{boxes[0][0]}" y="{boxes[0][1]-15}" class="label">Serie mensual · {_metric_text(diagnostics.get("monthly"))}</text>')

    # Serie anual ponderada.
    annual = diagnostics["annual_series"]; annual_sim = [row["caudal_simulado_m3s"] for row in annual]; annual_obs = [row["caudal_observado_m3s"] for row in annual]
    maximum = _maximum(annual_sim, annual_obs); plot = _frame(parts, x_label="Anio", box=boxes[1]); _y_labels(parts, plot, maximum)
    _polyline(parts, annual_sim, plot, "sim", maximum); _polyline(parts, annual_obs, plot, "obs", maximum)
    parts.append(f'<text x="{boxes[1][0]}" y="{boxes[1][1]-15}" class="label">Caudal anual ponderado · {_metric_text(diagnostics.get("annual"))}</text>')

    # Regimen.
    regime = diagnostics["regime_series"]; reg_sim = [row["caudal_simulado_m3s"] for row in regime]; reg_obs = [row["caudal_observado_m3s"] for row in regime]
    maximum = _maximum(reg_sim, reg_obs); plot = _frame(parts, x_label="Mes", box=boxes[2]); _y_labels(parts, plot, maximum)
    _polyline(parts, reg_sim, plot, "sim", maximum); _polyline(parts, reg_obs, plot, "obs", maximum)
    parts.append(f'<text x="{boxes[2][0]}" y="{boxes[2][1]-15}" class="label">Regimen multimensual · {_metric_text(diagnostics.get("regime"))}</text>')

    # Dispersion.
    pairs = [(float(row["caudal_observado_m3s"]), float(row["caudal_simulado_m3s"])) for row in rows if row["caudal_observado_m3s"] is not None]
    maximum = _maximum([value for pair in pairs for value in pair]); plot = _frame(parts, y_label="Qsim", x_label="Qobs", box=boxes[3])
    px, py, pw, ph = plot; parts.append(f'<line x1="{px}" y1="{py+ph}" x2="{px+pw}" y2="{py}" stroke="#64748b" stroke-dasharray="6 5"/>')
    for observed, simulated in pairs:
        parts.append(f'<circle cx="{px+pw*observed/maximum:.2f}" cy="{py+ph-ph*simulated/maximum:.2f}" r="2.2" fill="#0f766e" fill-opacity="0.55"/>')
    scatter = diagnostics.get("scatter") or {}
    r2 = scatter.get("R2")
    r2_text = "N/D" if r2 is None else f"{r2:.3f}"
    parts.append(f'<text x="{boxes[3][0]}" y="{boxes[3][1]-15}" class="label">Dispersion mensual · {html.escape(scatter.get("equation", ""))} · R2={r2_text} · n={scatter.get("n", 0)}</text>')
    parts.append('<line x1="860" y1="54" x2="888" y2="54" class="obs"/><text x="895" y="59" class="label">Observado</text>')
    parts.append('<line x1="990" y1="54" x2="1018" y2="54" class="sim"/><text x="1025" y="59" class="label">Simulado</text>')
    return _write(path, parts)


def _summary_svg(result, path):
    parts = _svg_start("Ficha trazable del modelo Lutz Scholz", height=850)
    metadata = result.get("run_metadata", {})
    lines = [
        "Origen de indicadores: comparación mensual entre Q observado y Q simulado",
        f"Identificador de modelación: {_display_modeling_id(metadata.get('run_id'))}",
        f"Modalidad: {_display_mode(metadata.get('calibration_mode'))}",
        f"División temporal: {_display_split(metadata.get('split_method'))}",
        f"Área: {result['parameters']['area_km2']:.3f} km²",
        f"C: {result['parameters']['coef_escorrentia']:.5f}",
        f"R: {result['parameters']['retencion_mm']:.3f} mm/año",
        f"a: {result['parameters']['a_dia']:.6f} 1/día",
    ]
    for label, key in (("Calibración", "calibration"), ("Validación independiente", "validation")):
        values = result.get("diagnostics", {}).get(key, {}).get("monthly")
        if values:
            lines.extend((label, "Escala: mensual", f"Meses válidos: {values.get('n', 0)}",
                          f"NSE: {values.get('NSE'):.4f}",
                          f"LogNSE: {values.get('LogNSE'):.4f}" if values.get('LogNSE') is not None else "LogNSE: N/D",
                          f"KGE: {values.get('KGE'):.4f}" if values.get('KGE') is not None else "KGE: N/D",
                          f"RMSE: {values.get('RMSE'):.4f} m3/s", f"PBIAS: {values.get('PBIAS_porcentaje'):.2f}%"))
    calibration = result.get("automatic_calibration")
    if calibration:
        lines.extend(("Calibración automática", f"Objetivo: {calibration['objective']}", f"Evaluaciones: {calibration['trials']}"))
    for index, line in enumerate(lines):
        parts.append(f'<text x="85" y="{75 + index*29}" style="font-family:monospace;font-size:16px">{html.escape(str(line))}</text>')
    return _write(path, parts)


def create_diagnostic_plots(result: Dict[str, object], output_folder: str) -> Dict[str, str]:
    folder = Path(output_folder) / "graficos" / "svg"; folder.mkdir(parents=True, exist_ok=True)
    rows = list(result["rows"])
    diagnostics = result.get("diagnostics") or {
        "complete": diagnostic_scales(rows),
        "calibration": diagnostic_scales([]),
        "validation": diagnostic_scales([]),
    }
    periods = {"calibration": result.get("calibration_period"), "validation": result.get("validation_period")}
    complete = diagnostics["complete"]
    output = {
        "serie_mensual": _series_svg(rows, folder / "grafico_serie_mensual.svg", "Serie completa", periods, complete),
        "caudal_anual": _annual_svg(rows, folder / "grafico_caudal_anual.svg", "Serie completa", complete),
        "regimen_multimensual": _monthly_svg(rows, folder / "grafico_regimen_multimensual.svg", "Serie completa", complete),
        "dispersion": _scatter_svg(rows, folder / "grafico_dispersion.svg", "Serie completa", complete),
        "panel_diagnostico": _panel_diagnostic_svg(rows, folder / "panel_diagnostico.svg", "Serie completa", complete),
        "resumen": _summary_svg(result, folder / "grafico_resumen.svg"),
    }
    for period_key, result_key, label, diagnostic_key in (
        ("calibracion", "calibration_period", "Calibracion", "calibration"),
        ("validacion", "validation_period", "Validacion independiente", "validation"),
    ):
        period = result.get(result_key)
        if not period:
            continue
        selected = [row for row in rows if period[0] <= row["anio"] <= period[1]]
        if not selected:
            continue
        current = diagnostics[diagnostic_key]
        output.update({
            f"serie_mensual_{period_key}": _series_svg(selected, folder / f"grafico_serie_mensual_{period_key}.svg", label, diagnostics=current),
            f"caudal_anual_{period_key}": _annual_svg(selected, folder / f"grafico_caudal_anual_{period_key}.svg", label, current),
            f"regimen_multimensual_{period_key}": _monthly_svg(selected, folder / f"grafico_regimen_multimensual_{period_key}.svg", label, current),
            f"dispersion_{period_key}": _scatter_svg(selected, folder / f"grafico_dispersion_{period_key}.svg", label, current),
            f"panel_diagnostico_{period_key}": _panel_diagnostic_svg(selected, folder / f"panel_diagnostico_{period_key}.svg", label, current),
        })
    return output


def create_diagnostic_plot(result: Dict[str, object], output_folder: str) -> str:
    return create_diagnostic_plots(result, output_folder)["serie_mensual"]
