"""Exportacion raster y reporte DOCX sin dependencias Python externas.

El DOCX se construye como OOXML para que el complemento no dependa de
``python-docx`` ni de extensiones compiladas en la instalacion de QGIS.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def svg_to_png(svg_path: str, png_path: str, width: int = 2400, height: int = 1600) -> str:
    """Rasteriza un SVG con el motor Qt incluido en QGIS."""

    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtGui import QColor, QImage, QPainter
    from qgis.PyQt.QtSvg import QSvgRenderer

    target = Path(png_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError(f"No se pudo interpretar el SVG: {svg_path}")
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    if not image.save(str(target), "PNG"):
        raise ValueError(f"No se pudo guardar el PNG: {target}")
    return str(target)


def export_panel_pngs(plot_paths, output_folder):
    folder = Path(output_folder) / "graficos" / "png"
    folder.mkdir(parents=True, exist_ok=True)
    mapping = {
        "panel_diagnostico": "diagnostico_serie_completa.png",
        "panel_diagnostico_calibracion": "diagnostico_calibracion.png",
        "panel_diagnostico_validacion": "diagnostico_validacion.png",
    }
    output = {}
    for key, filename in mapping.items():
        source = plot_paths.get(key)
        if source:
            output[key] = svg_to_png(source, str(folder / filename))
    return output


def _safe(value, decimals=3):
    if value is None:
        return "N/D"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return "N/D"
        return f"{float(value):.{decimals}f}"
    return str(value)


def _display_modeling_id(value):
    """Presenta el identificador interno con terminología técnica legible."""

    text = str(value or "N/D")
    if text.startswith("corrida_"):
        return f"modelacion_{text[len('corrida_'):]}"
    return text


def _display_datetime(value):
    text = str(value or "N/D")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    offset = parsed.strftime("%z")
    suffix = f" (UTC{offset[:3]}:{offset[3:]})" if offset else ""
    return parsed.strftime("%d/%m/%Y %H:%M:%S") + suffix


def _display_label(value):
    text = str(value or "N/D").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "N/D"


def _display_mode(value):
    text = str(value or "N/D").strip()
    return {
        "automatica": "Calibración automática",
        "automática": "Calibración automática",
        "manual": "Modelación manual",
    }.get(text.lower(), _display_label(text))


def _display_split(value):
    text = str(value or "N/D").strip()
    return {
        "cronologico_60_40": "Cronológica 60/40",
        "auto_60_40": "Cronológica 60/40",
        "manual": "Definida manualmente",
    }.get(text.lower(), _display_label(text))


def _display_climate_source(value):
    text = str(value or "N/D").strip()
    exact = {
        "PISCO precipitacion mensual (solo precipitacion)": "PISCO: precipitación mensual",
        "PISCO temperatura mensual (solo temperatura)": "PISCO: temperatura mensual",
        "PISCO T extendida con ERA5-Land corregido (solo temperatura)":
            "PISCO T extendida y corregida con ERA5-Land",
        "ERA5-Land temperatura mensual (promedio de extremos diarios)":
            "ERA5-Land: temperatura mensual (promedio de extremos diarios)",
        "CHIRPS Daily precipitacion": "CHIRPS Daily: precipitación",
    }
    if text in exact:
        return exact[text]
    return (
        text.replace("precipitacion", "precipitación")
        .replace("(solo precipitación)", "")
        .replace("(solo temperatura)", "")
        .strip()
    )


def _display_supply_source(value):
    text = str(value or "N/D").strip()
    return {
        "tabla manual/editada": "Tabla manual o editada",
        "hoja Gasto_Abastecimiento del Excel":
            "Hoja «Gasto_Abastecimiento» del archivo Excel",
    }.get(text, _display_label(text))


def _run(text, bold=False, color=None, size=None, italic=False):
    properties = []
    if bold:
        properties.append("<w:b/>")
    if italic:
        properties.append("<w:i/>")
    if color:
        properties.append(f'<w:color w:val="{color}"/>')
    if size:
        properties.append(f'<w:sz w:val="{int(size * 2)}"/><w:szCs w:val="{int(size * 2)}"/>')
    rpr = f"<w:rPr>{''.join(properties)}</w:rPr>" if properties else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r>'


def _paragraph(text="", style=None, bold=False, color=None, size=None, italic=False,
               align=None, before=None, after=None, keep_next=False, page_break_before=False):
    if style == "Heading1":
        bold, color, size, keep_next = True, color or "2E74B5", size or 16, True
    elif style == "Heading2":
        bold, color, size, keep_next = True, color or "2E74B5", size or 13, True
    elif style == "Heading3":
        bold, color, size, keep_next = True, color or "1F4D78", size or 12, True
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    if align:
        props.append(f'<w:jc w:val="{align}"/>')
    spacing = []
    if before is not None:
        spacing.append(f'w:before="{int(before * 20)}"')
    if after is not None:
        spacing.append(f'w:after="{int(after * 20)}"')
    if spacing:
        props.append(f"<w:spacing {' '.join(spacing)}/>")
    if keep_next:
        props.append("<w:keepNext/>")
    if page_break_before:
        props.append("<w:pageBreakBefore/>")
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{ppr}{_run(text, bold, color, size, italic)}</w:p>"


def _cell(text, width, header=False, align="left", font_size=None):
    fill = '<w:shd w:fill="F2F4F7"/>' if header else ""
    tcpr = (
        f'<w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}'
        '<w:tcMar><w:top w:w="80" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>'
    )
    return f"<w:tc>{tcpr}{_paragraph(text, bold=header, align=align, after=0, size=font_size)}</w:tc>"


def _table(rows, widths, header=True, font_size=None):
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    tr_rows = []
    for row_index, row in enumerate(rows):
        cells = "".join(
            _cell(value, widths[index], header and row_index == 0,
                  "center" if index > 0 else "left", font_size)
            for index, value in enumerate(row)
        )
        row_properties = "<w:trPr><w:tblHeader/></w:trPr>" if header and row_index == 0 else ""
        tr_rows.append(f"<w:tr>{row_properties}{cells}</w:tr>")
    borders = "".join(
        f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="C7CDD4"/>'
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        '<w:tblInd w:w="120" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>"
        f"{''.join(tr_rows)}</w:tbl>"
    )


def _image_paragraph(rel_id, name, doc_pr_id, width_emu=5943600, height_emu=3962400):
    return f'''<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="80" w:after="120"/></w:pPr>
<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="{width_emu}" cy="{height_emu}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>
<wp:docPr id="{doc_pr_id}" name="{escape(name)}" descr="{escape(name)}"/><wp:cNvGraphicFramePr/>
<a:graphic xmlns:a="{NS_A}"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:pic xmlns:pic="{NS_PIC}"><pic:nvPicPr><pic:cNvPr id="0" name="{escape(name)}"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>
</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'''


def _page_break():
    """Salto de página explícito, más estable entre Word y otros visores DOCX."""

    return '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr><w:r><w:br w:type="page"/></w:r></w:p>'


def _figure_heading_block(heading, caption):
    """Encabezado de figura estable, sin bordes ni anclajes flotantes."""

    cell = (
        '<w:tc><w:tcPr><w:tcW w:w="9360" w:type="dxa"/>'
        '<w:tcMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
        '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tcMar></w:tcPr>'
        + _paragraph(heading, bold=True, color="2E74B5", size=16, before=16, after=8)
        + _paragraph(caption, italic=True, color="4B5563", after=8)
        + '</w:tc>'
    )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        '<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid><w:gridCol w:w="9360"/></w:tblGrid>'
        f'<w:tr>{cell}</w:tr></w:tbl>'
    )


def _styles_xml():
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{NS_W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="0" w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="160"/></w:pPr><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="80"/></w:pPr><w:rPr><w:b/><w:color w:val="1F4D78"/><w:sz w:val="24"/></w:rPr></w:style>
</w:styles>'''


def _metric_rows(result):
    rows = [["Periodo", "Escala", "n", "NSE", "LogNSE", "KGE", "PBIAS (%)"]]
    labels = {"complete": "Completa", "calibration": "Calibración", "validation": "Validación"}
    scales = {"monthly": "Mensual", "annual": "Anual", "regime": "Régimen"}
    for period in ("complete", "calibration", "validation"):
        values = (result.get("diagnostics") or {}).get(period, {})
        for scale in ("monthly", "annual", "regime"):
            metric = values.get(scale)
            if metric:
                rows.append([
                    labels[period], scales[scale], _safe(metric.get("n"), 0),
                    _safe(metric.get("NSE")), _safe(metric.get("LogNSE")),
                    _safe(metric.get("KGE")), _safe(metric.get("PBIAS_porcentaje")),
                ])
    return rows


def _complementary_metric_rows(result):
    rows = [["Periodo", "Correlación r", "RMSE (m³/s)", "MAD (m³/s)", "Schultz D"]]
    labels = {"complete": "Completa", "calibration": "Calibración", "validation": "Validación"}
    for period in ("complete", "calibration", "validation"):
        metric = ((result.get("diagnostics") or {}).get(period) or {}).get("monthly")
        if metric:
            rows.append([
                labels[period], _safe(metric.get("Correlacion")), _safe(metric.get("RMSE")),
                _safe(metric.get("MAD")), _safe(metric.get("Schultz_D")),
            ])
    return rows


def _persistence_summary_rows(result):
    values = (result.get("flow_persistence") or {}).get("complete") or {}
    rows = [["Origen", "n", "Q medio", "Q75 (persist. 75 %)", "Q95 (persist. 95 %)", "Ceros (%)"]]
    for key, label in (("simulado", "Simulado"), ("observado", "Observado"), ("transferido", "Transferido")):
        current = values.get(key) or {}
        if current.get("n"):
            rows.append([
                label, _safe(current.get("n"), 0), _safe(current.get("mean_m3s")),
                _safe(current.get("Q75_m3s")), _safe(current.get("Q95_m3s")),
                _safe(current.get("zero_percentage")),
            ])
    return rows


def _monthly_regime_rows(result):
    values = (result.get("flow_persistence") or {}).get("complete") or {}
    selected_origin = values.get("selected_origin", "simulado")
    rows = [["Mes", "n", "Q medio", "Desv. estándar", "CV", "Q máximo", "Q mínimo"]]
    for item in values.get("mensual", []):
        selected = item.get(selected_origin) or {}
        month = int(item.get("mes", 0))
        label = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")[month - 1]
        rows.append([
            label, _safe(selected.get("n"), 0), _safe(selected.get("mean_m3s")),
            _safe(selected.get("standard_deviation_m3s")),
            _safe(selected.get("coefficient_variation")),
            _safe(selected.get("maximum_m3s")), _safe(selected.get("minimum_m3s")),
        ])
    return rows


def _persistence_monthly_rows(result):
    values = (result.get("flow_persistence") or {}).get("complete") or {}
    selected_origin = values.get("selected_origin", "simulado")
    rows = [[
        "Mes", "Q10", "Q25", "Q50", "Q75\nPersist. 75 %", "Q90",
        "Q95\nPersist. 95 %", "15 % Q medio",
    ]]
    for item in values.get("mensual", []):
        selected = item.get(selected_origin) or {}
        month = int(item.get("mes", 0))
        label = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")[month - 1]
        rows.append([
            label, _safe(selected.get("Q10_m3s")), _safe(selected.get("Q25_m3s")),
            _safe(selected.get("Q50_m3s")), _safe(selected.get("Q75_m3s")),
            _safe(selected.get("Q90_m3s")), _safe(selected.get("Q95_m3s")),
            _safe(selected.get("reference_15pct_mean_m3s")),
        ])
    return rows


def _conclusion(result):
    diagnostics = result.get("diagnostics") or {}
    cal = (diagnostics.get("calibration") or {}).get("monthly") or {}
    val = (diagnostics.get("validation") or {}).get("monthly") or {}
    paragraphs = []
    if cal.get("NSE") is not None:
        paragraphs.append(
            f"Durante la calibración, los indicadores mensuales fueron: NSE = {cal['NSE']:.3f}, "
            f"KGE = {_safe(cal.get('KGE'))} y PBIAS = {_safe(cal.get('PBIAS_porcentaje'))} %."
        )
    if val.get("NSE") is not None:
        delta_nse = val["NSE"] - cal.get("NSE", val["NSE"])
        direction_nse = "aumentó" if delta_nse >= 0 else "disminuyó"
        paragraphs.append(
            f"En la validación independiente se obtuvo NSE = {val['NSE']:.3f}. Respecto de la calibración, "
            f"el NSE {direction_nse} en {abs(delta_nse):.3f}. Esta evaluación utilizó los parámetros "
            "calibrados sin reajustarlos."
        )
        if val.get("KGE") is not None and cal.get("KGE") is not None:
            delta_kge = val["KGE"] - cal["KGE"]
            direction_kge = "aumentó" if delta_kge >= 0 else "disminuyó"
            validation_pbias = val.get("PBIAS_porcentaje")
            transfer = (
                "conserva un desempeño general estable fuera del periodo calibrado"
                if val["NSE"] >= 0.5 and val["KGE"] >= 0.5
                and (validation_pbias is None or abs(validation_pbias) <= 15.0)
                else "requiere cautela al transferirse fuera del periodo calibrado"
            )
            paragraphs.append(
                f"El KGE mensual de validación fue {_safe(val.get('KGE'))} y "
                f"{direction_kge} en {abs(delta_kge):.3f} respecto del valor de calibración; "
                f"el modelo {transfer}."
            )
        if val.get("LogNSE") is not None and cal.get("LogNSE") is not None:
            delta_log = val["LogNSE"] - cal["LogNSE"]
            if delta_log < -0.05:
                paragraphs.append(
                    f"El LogNSE bajó de {_safe(cal.get('LogNSE'))} a {_safe(val.get('LogNSE'))}; "
                    "el ajuste de caudales bajos es más débil en validación y conviene revisarlo por separado."
                )
        pbias = val.get("PBIAS_porcentaje")
        if pbias is not None:
            bias_text = "subestimación" if pbias < 0 else "sobreestimación"
            paragraphs.append(
                f"El PBIAS mensual de validación fue {_safe(pbias)} %, equivalente a una {bias_text} "
                "global según la convención Qsim - Qobs usada en este informe."
            )
        high_bias = val.get("PBIAS_altos_porcentaje")
        if high_bias is not None and abs(high_bias) >= 10:
            tendency = "sobreestimar" if high_bias > 0 else "subestimar"
            paragraphs.append(
                f"En caudales altos, el sesgo fue {_safe(high_bias)} %; existe tendencia a {tendency} "
                "los eventos mayores y no debe asumirse la misma precisión para todos los picos."
            )
    persistence = (result.get("flow_persistence") or {}).get("complete") or {}
    simulated = persistence.get("simulado") or {}
    observed = persistence.get("observado") or {}
    if observed.get("Q95_m3s") not in (None, 0) and simulated.get("Q95_m3s") is not None:
        ratio = simulated["Q95_m3s"] / observed["Q95_m3s"]
        if ratio < 0.8:
            paragraphs.append(
                f"En estiaje, el Q95 simulado ({_safe(simulated.get('Q95_m3s'))} m³/s) es inferior "
                f"al observado ({_safe(observed.get('Q95_m3s'))} m³/s). La estimación de permanencias "
                "bajas debe revisarse antes de emplearse en decisiones de disponibilidad o caudal ecológico."
            )
    if not paragraphs:
        paragraphs.append("La modelación finalizó correctamente y conserva sus resultados para revisión técnica.")
    return paragraphs


def create_word_report(result, outputs, png_paths, output_path):
    """Crea un informe técnico Word con datos, métricas, advertencias y figuras."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = result.get("run_metadata") or {}
    params = result.get("parameters") or {}
    balance = result.get("balance_diagnostics") or {}
    calibration = result.get("automatic_calibration") or {}

    body = []
    body.append(_paragraph("INFORME TÉCNICO", bold=True, size=23, color="0B2545", after=4))
    body.append(_paragraph("Modelo hidrológico mensual Lutz Scholz", size=14, color="4B5563", after=14))
    body.append(_table([
        ["Campo", "Valor"],
        ["Identificador de modelación", _display_modeling_id(metadata.get("run_id"))],
        ["Escenario", _display_label(metadata.get("scenario"))],
        ["Fecha y hora", _display_datetime(metadata.get("created_at"))],
        ["Versión del complemento", result.get("version", "N/D")],
        ["Modalidad", _display_mode(metadata.get("calibration_mode"))],
    ], [3300, 6060]))

    body.append(_paragraph("1. Configuración y trazabilidad", style="Heading1"))
    body.append(_table([
        ["Elemento", "Configuración"],
        ["Archivo de entrada", metadata.get("input_file", "N/D")],
        ["Precipitación", _display_climate_source(metadata.get("precipitation_source"))],
        ["Temperatura", _display_climate_source(metadata.get("temperature_source"))],
        ["ETP", metadata.get("etp_method", "N/D")],
        ["División temporal", _display_split(metadata.get("split_method"))],
        ["Abastecimiento", _display_supply_source(metadata.get("supply_source"))],
        ["Fuente de retención", _display_label(metadata.get("retention_source"))],
    ], [2700, 6660]))

    body.append(_page_break())
    body.append(_paragraph("2. Parámetros del modelo", style="Heading1"))
    parameter_rows = [
        ["Parámetro", "Valor"],
        ["Área de cuenca", f"{_safe(params.get('area_km2'))} km²"],
        ["Coeficiente de escorrentía C", _safe(params.get("coef_escorrentia"), 5)],
        ["Retención R", f"{_safe(params.get('retencion_mm'))} mm/año"],
        ["Coeficiente de agotamiento a", f"{_safe(params.get('a_dia'), 6)} 1/día"],
        ["Tratamiento de balance negativo", {
            "controlled_clip": "Exploratorio: recorte controlado",
            "strict": "Balance físico estricto (Q >= 0)",
        }.get(params.get("negative_balance_mode"), "N/D")],
        ["Calibración", f"{result.get('calibration_period', ('N/D', 'N/D'))[0]} - {result.get('calibration_period', ('N/D', 'N/D'))[1]}"],
        ["Validación independiente", "No definida" if not result.get("validation_period") else f"{result['validation_period'][0]} - {result['validation_period'][1]}"],
    ]
    body.append(_table(parameter_rows, [3600, 5760]))
    if calibration:
        body.append(_paragraph("Calibración automática y trazabilidad de la búsqueda", style="Heading2"))
        bounds = calibration.get("search_bounds") or {}
        c_bounds = bounds.get("coefficient")
        r_bounds = bounds.get("retention_mm")
        a_bounds = bounds.get("a_day")
        search_parts = []
        if calibration.get("refinements") is not None:
            search_parts.append(f"{calibration['refinements']} mallas sucesivas")
        if calibration.get("steps_per_axis") is not None:
            search_parts.append(f"{calibration['steps_per_axis']} pasos por eje")
        if calibration.get("trials") is not None:
            search_parts.append(f"{calibration['trials']} evaluaciones")
        search_design = "; ".join(search_parts) or "N/D"
        calibration_rows = [
            ["Elemento", "Detalle"],
            ["Función objetivo", calibration.get("objective", "N/D")],
            ["Diseño de búsqueda", search_design],
        ]
        c_detail = f"Inicial/final: {_safe(calibration.get('initial_coefficient'), 5)} / {_safe(calibration.get('coefficient'), 5)}"
        if c_bounds and calibration.get("calibrated_c"):
            c_detail += f"; límites: {_safe(c_bounds[0], 5)} a {_safe(c_bounds[1], 5)}"
        elif calibration.get("calibrated_c") is False:
            c_detail += "; C fijo, no incluido en la búsqueda"
        calibration_rows.append(["Coeficiente C", c_detail])
        if calibration.get("initial_retention_mm") is None:
            r_detail = f"Final: {_safe(calibration.get('retention_mm'))} mm/año"
        else:
            r_detail = (
                f"Inicial/final: {_safe(calibration.get('initial_retention_mm'))} / "
                f"{_safe(calibration.get('retention_mm'))} mm/año"
            )
        if r_bounds:
            r_detail += f"; límites: {_safe(r_bounds[0])} a {_safe(r_bounds[1])} mm/año"
        calibration_rows.append(["Retención R", r_detail])
        if calibration.get("initial_a_day") is None:
            a_detail = f"Final: {_safe(calibration.get('a_day'), 6)} 1/día"
        else:
            a_detail = (
                f"Inicial/final: {_safe(calibration.get('initial_a_day'), 6)} / "
                f"{_safe(calibration.get('a_day'), 6)} 1/día"
            )
        if a_bounds:
            a_detail += f"; límites: {_safe(a_bounds[0], 6)} a {_safe(a_bounds[1], 6)} 1/día"
        calibration_rows.append(["Agotamiento a", a_detail])
        balance_detail = f"Combinaciones rechazadas: {calibration.get('rejected_trials', 'N/D')}"
        if calibration.get("retention_limit_mm") is not None:
            balance_detail += f"; límite físico de R: {_safe(calibration.get('retention_limit_mm'))} mm/año"
        calibration_rows.append(["Control de balance físico", balance_detail])
        body.append(_table(calibration_rows, [3900, 5460], font_size=9))
        body.append(_paragraph(
            "La validación utilizó los parámetros finales sin recalibrarlos.",
            italic=True, color="4B5563", before=4,
        ))
        initial_metrics = calibration.get("initial_metrics_calibration") or {}
        final_metrics = calibration.get("final_metrics_calibration") or {}
        if initial_metrics or final_metrics:
            body.append(_paragraph("Cambio del ajuste mensual en calibración", style="Heading3"))
            body.append(_table([
                ["Estado", "NSE", "LogNSE", "KGE", "PBIAS (%)"],
                ["Inicial", _safe(initial_metrics.get("NSE")), _safe(initial_metrics.get("LogNSE")),
                 _safe(initial_metrics.get("KGE")), _safe(initial_metrics.get("PBIAS_porcentaje"))],
                ["Final", _safe(final_metrics.get("NSE")), _safe(final_metrics.get("LogNSE")),
                 _safe(final_metrics.get("KGE")), _safe(final_metrics.get("PBIAS_porcentaje"))],
            ], [2100, 1500, 1800, 1500, 2460], font_size=9))
        elif calibration.get("initial_error"):
            body.append(_paragraph(
                "El punto inicial no produjo un balance físico válido; la búsqueda continuó dentro de los límites "
                "definidos hasta encontrar una combinación admisible.",
                italic=True, color="4B5563",
            ))
        if calibration.get("physical_balance_enforced"):
            body.append(_paragraph(
                "La búsqueda descartó automáticamente las combinaciones que no mantuvieron "
                "Q mensual mayor o igual a cero."
            ))
    if balance.get("annual_balance_modified"):
        body.append(_paragraph(
            f"Ejecución exploratoria: ajuste de balance en {balance.get('clipped_months', 0)} "
            "mes(es); detalle completo en el Anexo A.",
            italic=True, color="4B5563",
        ))

    body.append(_page_break())
    body.append(_paragraph("3. Indicadores de desempeño", style="Heading1"))
    body.append(_table(_metric_rows(result), [1650, 1300, 650, 1150, 1450, 1150, 2010], font_size=9))
    body.append(_paragraph(
        "Los indicadores comparan caudal observado y simulado. La validación es independiente y no modifica los parámetros.",
        italic=True, color="4B5563", before=4, after=4,
    ))
    body.append(_paragraph(
        "Convención de PBIAS: 100 × Σ(Qsim - Qobs) / ΣQobs. Un valor negativo indica subestimación "
        "global del caudal y un valor positivo indica sobreestimación.",
        italic=True, color="4B5563", after=4,
    ))
    body.append(_paragraph("Indicadores mensuales complementarios", style="Heading2"))
    body.append(_table(
        _complementary_metric_rows(result),
        [1900, 1865, 1865, 1865, 1865],
        font_size=9,
    ))
    body.append(_paragraph(
        "El criterio de Schultz D se presenta como diagnóstico complementario; valores menores indican "
        "menor desviación ponderada y su interpretación es especialmente útil en análisis de eventos.",
        italic=True, color="4B5563", after=4,
    ))

    image_order = [
        ("panel_diagnostico", "4. Diagnóstico de la serie completa", "Serie completa"),
        ("panel_diagnostico_calibracion", "5. Diagnóstico de calibración", "Calibración"),
        ("panel_diagnostico_validacion", "6. Diagnóstico de validación", "Validación"),
    ]
    image_entries = []
    for key, heading, caption in image_order:
        image_path = png_paths.get(key)
        if image_path and Path(image_path).exists():
            image_entries.append((key, heading, caption, Path(image_path)))

    for index, (_key, heading, caption, _image_path) in enumerate(image_entries, start=1):
        body.append(_page_break())
        body.append(_figure_heading_block(
            heading,
            f"Figura {index}. {caption}: serie mensual, caudal anual, régimen multimensual y dispersión.",
        ))
        body.append(_image_paragraph(
            f"rIdImage{index}", caption, index,
            width_emu=5532120, height_emu=3688080,
        ))

    persistence = (result.get("flow_persistence") or {}).get("complete") or {}
    transfer = result.get("flow_transfer") or {}
    if image_entries or persistence:
        body.append(_page_break())
    if transfer.get("active"):
        body.append(_paragraph("7. Transposición hidrológica", style="Heading1"))
        method_label = {
            "annual": "factor anual de precipitación",
            "annual_mean_precipitation": "relación de precipitaciones medias anuales",
            "monthly_climatology": "factores por mes climatológico",
        }.get(transfer.get("method"), _display_label(transfer.get("method")))
        source_area = transfer.get("source_area_km2", transfer.get("donor_area_km2"))
        body.append(_paragraph(
            "La serie Qc corresponde al caudal simulado final de Lutz Scholz. La serie objetivo "
            "se estimó con Qs = (As/Ac) × (Ps/Pc) × Qc, utilizando "
            f"{method_label}. La cuenca modelada tiene {_safe(source_area)} km² "
            f"y Pc = {_safe(transfer.get('source_annual_precipitation_mm'))} mm/año; "
            f"la cuenca objetivo tiene {_safe(transfer.get('target_area_km2'))} km² "
            f"y Ps = {_safe(transfer.get('target_annual_precipitation_mm'))} mm/año. "
            f"Se transfirieron {_safe(transfer.get('transferred_months'), 0)} caudales mensuales."
        ))
        body.append(_paragraph(str(transfer.get("assumption", "")), italic=True, color="4B5563"))
        body.append(_page_break())
    if persistence:
        persistence_number = 8 if transfer.get("active") else 7
        selected_origin = persistence.get("selected_origin", "simulado")
        selected_label = {
            "simulado": "caudal simulado por Lutz Scholz",
            "observado": "caudal observado",
            "transferido": "caudal transferido",
        }.get(selected_origin, selected_origin)
        body.append(_paragraph(f"{persistence_number}. Régimen multimensual, persistencia y referencia ecológica", style="Heading1"))
        body.append(_paragraph(
            "Los caudales se agruparon por mes calendario. Para cada mes se calcularon el régimen "
            "multimensual y los caudales Q10, Q25, Q50, Q75, Q90 y Q95 con posiciones de trazado "
            "de Weibull P = m/(n+1) e interpolación lineal. Q75 es el caudal igualado o excedido "
            f"el 75 % del tiempo. La fuente seleccionada para el análisis fue: {selected_label}."
        ))
        body.append(_table(_persistence_summary_rows(result), [1800, 700, 1700, 1700, 1700, 1760], font_size=9))
        body.append(_paragraph("Régimen multimensual de la serie completa (m³/s)", style="Heading2"))
        body.append(_table(
            _monthly_regime_rows(result),
            [700, 650, 1450, 1600, 1000, 1500, 1500],
            font_size=8,
        ))
        body.append(_paragraph("Caudales de persistencia por mes calendario (m³/s)", style="Heading2"))
        body.append(_table(
            _persistence_monthly_rows(result),
            [650, 1050, 1050, 1050, 1500, 1050, 1500, 1900],
            font_size=8,
        ))
        body.append(_paragraph(
            "La columna «15 % Q medio» es una referencia hidrológica mensual basada en el Anexo I "
            "de la Resolución Jefatural N.° 267-2019-ANA. Q75, Q95 y ese porcentaje son resultados "
            "técnicos de apoyo; ninguno equivale por sí solo a un caudal ecológico aprobado. La "
            "metodología aplicable y su aprobación dependen de la clasificación del proyecto, del tramo "
            "de río y de la Autoridad Administrativa del Agua competente.",
            italic=True, color="4B5563",
        ))
        body.append(_page_break())
    conclusion_number = 7 + (1 if persistence else 0) + (1 if transfer.get("active") else 0)
    body.append(_paragraph(f"{conclusion_number}. Conclusiones del desempeño", style="Heading1"))
    for paragraph in _conclusion(result):
        body.append(_paragraph(paragraph))
    body.append(_paragraph("Archivos de respaldo", style="Heading2"))
    body.append(_paragraph(
        "La carpeta de la modelación contiene las series mensuales, el análisis de permanencia, los "
        "indicadores, los parámetros, los gráficos en formatos SVG y PNG, y el manifiesto de trazabilidad."
    ))
    if balance.get("negative_months"):
        body.append(_page_break())
        body.append(_paragraph(
            "Anexo A. Trazabilidad del balance exploratorio",
            style="Heading1",
        ))
        body.append(_paragraph(
            "Este anexo documenta los valores originales de una ejecución manual con recorte "
            "controlado. Las calibraciones automáticas no aceptan estas combinaciones."
        ))
        rows = [["Mes", "Q original (mm)", "PE (mm)", "Gasto (mm)", "Abastecimiento (mm)"]]
        for item in balance["negative_months"]:
            rows.append([
                item["mes"], _safe(item["valor_original_mm"], 4),
                _safe(item["precipitacion_efectiva_mm"], 4),
                _safe(item["gasto_mm"], 4), _safe(item["abastecimiento_mm"], 4),
            ])
        body.append(_table(rows, [900, 1800, 1800, 1800, 3060]))
        limit = balance.get("retention_nonnegative_limit") or {}
        if limit:
            body.append(_paragraph(
                f"R máximo estimado para evitar valores negativos con esta distribución: "
                f"{_safe(limit.get('retencion_maxima_mm'))} mm, limitado por "
                f"{limit.get('mes_limitante', 'N/D')}."
            ))

    sect = (
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
        '<w:footerReference w:type="default" r:id="rIdFooter"/>'
        '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" '
        'w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
        '<w:cols w:space="708"/><w:docGrid w:linePitch="360"/></w:sectPr>'
    )
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}" xmlns:wp="{NS_WP}" xmlns:a="{NS_A}" xmlns:pic="{NS_PIC}"><w:body>{''.join(body)}{sect}</w:body></w:document>'''

    image_rels = []
    content_defaults = ['<Default Extension="png" ContentType="image/png"/>'] if image_entries else []
    for index, (_key, _heading, _caption, image_path) in enumerate(image_entries, start=1):
        image_rels.append(
            f'<Relationship Id="rIdImage{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{index}.png"/>'
        )

    content_types = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>{''.join(content_defaults)}
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    doc_rels = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
{''.join(image_rels)}</Relationships>'''
    header_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="{NS_W}"><w:p><w:pPr><w:jc w:val="right"/><w:spacing w:after="0"/></w:pPr>{_run('MODELO LUTZ SCHOLZ | INFORME TÉCNICO', color='6B7280', size=9)}</w:p></w:hdr>'''
    footer_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="{NS_W}"><w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="0"/></w:pPr>{_run(_display_modeling_id(metadata.get('run_id', 'Modelación Lutz Scholz')), color='6B7280', size=9)}</w:p></w:ftr>'''
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    core_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Informe técnico Lutz Scholz</dc:title><dc:creator>Modelo Lutz Scholz para QGIS</dc:creator><cp:lastModifiedBy>Modelo Lutz Scholz para QGIS</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'''
    app_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Modelo Lutz Scholz para QGIS</Application></Properties>'''

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("docProps/core.xml", core_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", _styles_xml())
        archive.writestr("word/header1.xml", header_xml)
        archive.writestr("word/footer1.xml", footer_xml)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        for index, (_key, _heading, _caption, image_path) in enumerate(image_entries, start=1):
            archive.write(image_path, f"word/media/image{index}.png")
    return str(path)


def finalize_manifest(manifest_path, outputs, plot_paths, png_paths, report_path):
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Todas las rutas del manifiesto son relativas a la carpeta de modelacion,
    # aunque el propio manifiesto viva dentro de ``trazabilidad``.
    base = path.parent.parent

    def relative(value):
        try:
            return str(Path(value).resolve().relative_to(base.resolve())).replace("\\", "/")
        except (ValueError, OSError):
            return str(value)

    payload["files"].update({
        "technical_report": relative(report_path),
        "svg_plots": {key: relative(value) for key, value in plot_paths.items()},
        "png_panels": {key: relative(value) for key, value in png_paths.items()},
    })
    payload["balance_diagnostics"] = outputs.get("balance_diagnostics")
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)
