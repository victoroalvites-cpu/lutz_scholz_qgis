"""Lector XLSX ligero para la plantilla del complemento, sin dependencias externas."""

from __future__ import annotations

import posixpath
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET  # nosec B405 -- se valida antes de analizar

from .core import LutzError


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_XML_BYTES = 32 * 1024 * 1024


def _safe_xml_from_archive(archive, member):
    """Lee OOXML sin DTD, entidades ni miembros XML desproporcionados."""

    try:
        info = archive.getinfo(member)
    except KeyError as error:
        raise LutzError(f"El XLSX no contiene {member}.") from error
    if info.file_size > MAX_XML_BYTES:
        raise LutzError(f"El miembro XML {member} supera el limite de seguridad.")
    payload = archive.read(info)
    lowered = payload[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise LutzError(f"El miembro XML {member} contiene declaraciones no permitidas.")
    return ET.fromstring(payload)  # nosec B314 -- DTD y entidades rechazadas arriba


def _column_index(reference):
    letters = "".join(char for char in reference if char.isalpha()).upper()
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value - 1


def _excel_value(raw, cell_type, shared):
    if raw is None:
        return ""
    if cell_type == "s":
        return shared[int(raw)]
    if cell_type == "b":
        return raw == "1"
    if cell_type in ("str", "inlineStr"):
        return raw
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return raw


def read_xlsx_sheets(path):
    source = Path(path)
    if not source.is_file():
        raise LutzError("No se encontro el archivo Excel de entrada.")
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as error:
        raise LutzError("El archivo no es un XLSX valido.") from error
    with archive:
        try:
            workbook = _safe_xml_from_archive(archive, "xl/workbook.xml")
            relations = _safe_xml_from_archive(archive, "xl/_rels/workbook.xml.rels")
        except (KeyError, ET.ParseError) as error:
            raise LutzError("El XLSX no contiene una estructura de libro valida.") from error
        relation_map = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relations.findall(f"{{{PKG_REL}}}Relationship")
        }
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = _safe_xml_from_archive(archive, "xl/sharedStrings.xml")
            for item in root.findall(f"{{{MAIN}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{MAIN}}}t")))
        result = {}
        for sheet in workbook.findall(f".//{{{MAIN}}}sheet"):
            name = sheet.attrib["name"]
            target = relation_map[sheet.attrib[f"{{{REL}}}id"]].lstrip("/")
            xml_path = posixpath.normpath(target if target.startswith("xl/") else posixpath.join("xl", target))
            root = _safe_xml_from_archive(archive, xml_path)
            rows = []
            for row in root.findall(f".//{{{MAIN}}}row"):
                values = []
                for cell in row.findall(f"{{{MAIN}}}c"):
                    index = _column_index(cell.attrib.get("r", "A1"))
                    while len(values) <= index:
                        values.append("")
                    cell_type = cell.attrib.get("t", "n")
                    if cell_type == "inlineStr":
                        raw = "".join(node.text or "" for node in cell.iter(f"{{{MAIN}}}t"))
                    else:
                        node = cell.find(f"{{{MAIN}}}v")
                        raw = node.text if node is not None else ""
                    values[index] = _excel_value(raw, cell_type, shared)
                rows.append(values)
            result[name] = rows
        return result


def excel_serial_to_date(value):
    if isinstance(value, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(value))
    return None
