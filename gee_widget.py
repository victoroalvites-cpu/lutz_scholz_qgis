"""Pestana QGIS para autenticacion y series climaticas de Earth Engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from qgis.PyQt.QtCore import QDate, QSettings, QStandardPaths, Qt
from qgis.PyQt.QtSvg import QSvgWidget
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateEdit, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsMapLayerProxyModel, QgsProject, QgsTask,
)
from qgis.gui import QgsMapLayerComboBox

from .climate_plotting import create_climate_svg
from .core import (
    extend_pisco_temperature, select_climate_variables,
)
from .gee_service import (
    DEFAULT_PISCO_PRECIP, DEFAULT_PISCO_TEMP, SOURCE_CATALOG, GeeError,
    authenticate_ee_external, dependency_status, run_gee_external,
    write_climate_csv,
)


class GeeClimateWidget(QWidget):
    """Panel autocontenido que no modifica el modelo hasta pulsar Aplicar."""

    SETTINGS_PREFIX = "LutzScholz/GEE"

    def __init__(self, iface, apply_callback=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.apply_callback = apply_callback
        self.downloads = {}
        self.precipitation_key = None
        self.temperature_key = None
        self.active_source_key = None
        self.active_rows = []
        self.active_title = "Serie climatica areal"
        self.project_root = None
        self.project_folders = {}
        self._busy = 0
        self._current_task = None
        self._dependency_available = False
        self._build_ui()
        self._load_settings()
        self._show_dependency_status()

    def set_project_context(self, root_folder, folders):
        """Recibe las rutas del proyecto sin acoplar el panel al diálogo principal."""

        self.project_root = Path(root_folder)
        self.project_folders = {key: Path(value) for key, value in folders.items()}

    def _build_ui(self):
        layout = QVBoxLayout(self)
        connection = QGroupBox("Conexion con Google Earth Engine")
        grid = QGridLayout(connection)
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText(
            "Ingrese su proyecto Cloud habilitado en Earth Engine"
        )
        self.project_edit.setToolTip(
            "Cada usuario debe indicar un proyecto Cloud propio o autorizado "
            "con Earth Engine habilitado."
        )
        self.connect_button = QPushButton("Conectar / autorizar")
        self.connect_button.clicked.connect(lambda: self._connect(False))
        self.change_button = QPushButton("Cambiar cuenta")
        self.change_button.clicked.connect(lambda: self._connect(True))
        self.check_button = QPushButton("Comprobar conexion")
        self.check_button.clicked.connect(self._check_connection)
        self.cancel_button = QPushButton("Cancelar operacion")
        self.cancel_button.clicked.connect(self._cancel_current_task)
        self.cancel_button.setEnabled(False)
        self.install_help_button = QPushButton("Ver instrucciones de instalación")
        self.install_help_button.clicked.connect(self._show_installation_help)
        self.connection_status = QLabel("Sin comprobar")
        self.connection_status.setWordWrap(True)
        grid.addWidget(QLabel("Proyecto Cloud"), 0, 0)
        grid.addWidget(self.project_edit, 0, 1)
        grid.addWidget(self.connect_button, 0, 2)
        grid.addWidget(self.change_button, 0, 3)
        grid.addWidget(self.check_button, 1, 2)
        grid.addWidget(self.cancel_button, 1, 3)
        grid.addWidget(self.connection_status, 1, 0, 1, 2)
        grid.addWidget(self.install_help_button, 2, 0, 1, 4)
        layout.addWidget(connection)

        sources = QGroupBox("Fuentes climaticas separadas, cuenca y periodo")
        grid = QGridLayout(sources)
        self.precip_source_combo = QComboBox()
        for key in ("pisco_p", "chirps"):
            self.precip_source_combo.addItem(SOURCE_CATALOG[key].label, key)
        self.temperature_source_combo = QComboBox()
        for key in ("pisco_t", "era5"):
            self.temperature_source_combo.addItem(SOURCE_CATALOG[key].label, key)
        self.pisco_p_edit = QLineEdit(DEFAULT_PISCO_PRECIP)
        self.pisco_t_edit = QLineEdit(DEFAULT_PISCO_TEMP)
        self.basin_combo = QgsMapLayerComboBox()
        self.basin_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        active_button = QPushButton("Usar capa activa")
        active_button.clicked.connect(self._use_active_layer)
        self.start_edit = QDateEdit(QDate(1981, 1, 1))
        self.end_edit = QDateEdit(QDate(2025, 12, 1))
        for edit in (self.start_edit, self.end_edit):
            edit.setDisplayFormat("yyyy-MM")
            edit.setCalendarPopup(True)
        self.inspect_precip_button = QPushButton("Disponibilidad P")
        self.inspect_precip_button.clicked.connect(lambda: self._inspect_source("precipitation"))
        self.download_precip_button = QPushButton("Obtener precipitacion areal")
        self.download_precip_button.clicked.connect(lambda: self._download("precipitation"))
        self.apply_precip_button = QPushButton("Aplicar precipitacion")
        self.apply_precip_button.clicked.connect(self._apply_precipitation)
        self.inspect_temp_button = QPushButton("Disponibilidad T")
        self.inspect_temp_button.clicked.connect(lambda: self._inspect_source("temperature"))
        self.download_temp_button = QPushButton("Obtener temperatura areal")
        self.download_temp_button.clicked.connect(lambda: self._download("temperature"))
        self.apply_temp_button = QPushButton("Aplicar temperatura")
        self.apply_temp_button.clicked.connect(self._apply_temperature)
        self.diagnose_button = QPushButton("Diagnosticar todas las fuentes")
        self.diagnose_button.clicked.connect(self._diagnose)
        grid.addWidget(QLabel("Precipitacion"), 0, 0); grid.addWidget(self.precip_source_combo, 0, 1)
        grid.addWidget(self.inspect_precip_button, 0, 2); grid.addWidget(self.download_precip_button, 0, 3); grid.addWidget(self.apply_precip_button, 0, 4)
        grid.addWidget(QLabel("Temperatura"), 1, 0); grid.addWidget(self.temperature_source_combo, 1, 1)
        grid.addWidget(self.inspect_temp_button, 1, 2); grid.addWidget(self.download_temp_button, 1, 3); grid.addWidget(self.apply_temp_button, 1, 4)
        grid.addWidget(QLabel("Asset PISCO P"), 2, 0); grid.addWidget(self.pisco_p_edit, 2, 1, 1, 4)
        grid.addWidget(QLabel("Asset PISCO T"), 3, 0); grid.addWidget(self.pisco_t_edit, 3, 1, 1, 4)
        grid.addWidget(QLabel("Cuenca"), 4, 0); grid.addWidget(self.basin_combo, 4, 1, 1, 3); grid.addWidget(active_button, 4, 4)
        grid.addWidget(QLabel("Desde"), 5, 0); grid.addWidget(self.start_edit, 5, 1)
        grid.addWidget(QLabel("Hasta"), 5, 2); grid.addWidget(self.end_edit, 5, 3, 1, 2)
        grid.addWidget(self.diagnose_button, 6, 0, 1, 5)
        layout.addWidget(sources)

        action_row = QHBoxLayout()
        self.extend_button = QPushButton("Extender PISCO T con ERA5 corregido")
        self.extend_button.clicked.connect(self._extend_temperature)
        self.export_button = QPushButton("Exportar CSV")
        self.export_button.clicked.connect(self._export)
        action_row.addWidget(self.extend_button)
        action_row.addWidget(self.export_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.precipitation_status = QLabel("P activa: archivo cargado o aun no definida.")
        self.temperature_status = QLabel("T activa: archivo cargado o aun no definida.")
        for status in (self.precipitation_status, self.temperature_status):
            status.setWordWrap(True)
            layout.addWidget(status)

        splitter = QSplitter(Qt.Orientation.Vertical)
        result_box = QWidget(); result_layout = QVBoxLayout(result_box); result_layout.setContentsMargins(0, 0, 0, 0)
        self.table_note = QLabel(
            "Vista previa: todavia no se ha descargado una fuente. Cambiar un selector "
            "no modifica el modelo; use el boton Aplicar correspondiente."
        )
        self.table_note.setWordWrap(True)
        result_layout.addWidget(self.table_note)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(("Fecha", "P mm", "Tmin C", "Tmedia C", "Tmax C", "Cobertura %", "Imagenes", "Fuente", "Metodo"))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        result_layout.addWidget(self.table)
        self.plot = QSvgWidget(); self.plot.setMinimumHeight(300)
        splitter.addWidget(result_box); splitter.addWidget(self.plot)
        splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(125)
        layout.addWidget(self.log)

    def _settings(self):
        return QSettings()

    def _load_settings(self):
        settings = self._settings()
        saved_project = settings.value(f"{self.SETTINGS_PREFIX}/project", "")
        self.project_edit.setText(str(saved_project or "").strip())
        self.pisco_p_edit.setText(settings.value(f"{self.SETTINGS_PREFIX}/pisco_p", DEFAULT_PISCO_PRECIP))
        self.pisco_t_edit.setText(settings.value(f"{self.SETTINGS_PREFIX}/pisco_t", DEFAULT_PISCO_TEMP))

    def _save_settings(self):
        settings = self._settings()
        settings.setValue(f"{self.SETTINGS_PREFIX}/project", self.project_edit.text().strip())
        settings.setValue(f"{self.SETTINGS_PREFIX}/pisco_p", self.pisco_p_edit.text().strip())
        settings.setValue(f"{self.SETTINGS_PREFIX}/pisco_t", self.pisco_t_edit.text().strip())

    def _selected_project(self):
        project = self.project_edit.text().strip()
        if project:
            return project
        QMessageBox.warning(
            self,
            "Proyecto Cloud requerido",
            "Ingrese el identificador de un proyecto Google Cloud propio o "
            "autorizado que tenga Earth Engine habilitado.",
        )
        self.project_edit.setFocus()
        return None

    def _show_dependency_status(self):
        status = dependency_status()
        self._dependency_available = bool(status.get("available"))
        if self._dependency_available:
            self.connection_status.setText(
                f"API Earth Engine disponible (version {status.get('version')}). "
                "Falta comprobar la cuenta."
            )
        else:
            self.connection_status.setText(
                "Clima GEE es opcional y está deshabilitado porque Earth Engine API "
                "no está instalada. El modelo local funciona normalmente. Pulse "
                "«Ver instrucciones de instalación» para habilitar PISCO, ERA5-Land "
                "y CHIRPS."
            )
        self._set_gee_controls_enabled(self._dependency_available)

    def _set_gee_controls_enabled(self, enabled):
        for button in (
            self.connect_button, self.change_button, self.check_button,
            self.inspect_precip_button, self.download_precip_button,
            self.inspect_temp_button, self.download_temp_button,
            self.diagnose_button,
        ):
            button.setEnabled(bool(enabled))

    def _show_installation_help(self):
        python_executable = Path(sys.prefix) / (
            "python.exe" if os.name == "nt" else "bin/python"
        )
        quoted_python = f'"{python_executable}"'
        command = (
            f"& {quoted_python} -m pip install --user earthengine-api"
            if os.name == "nt"
            else f"{quoted_python} -m pip install --user earthengine-api"
        )
        message = QMessageBox(self)
        message.setWindowTitle("Habilitar Clima GEE")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(
            "Clima GEE es una función opcional. El modelo Lutz Sholtz local funciona "
            "sin instalar componentes adicionales."
        )
        message.setInformativeText(
            "Para usar PISCO, ERA5-Land o CHIRPS, ejecute el comando mostrado en una "
            "terminal, cierre completamente QGIS, vuelva a abrirlo y pulse "
            "«Conectar / autorizar». También necesitará una cuenta habilitada en "
            "Google Earth Engine."
        )
        message.setDetailedText(
            "Comando para PowerShell en Windows o para la terminal del sistema:\n\n"
            f"{command}\n\n"
            "Después de instalar:\n"
            "1. Cierre todas las ventanas de QGIS.\n"
            "2. Abra QGIS nuevamente.\n"
            "3. Regrese a Clima GEE y autorice su cuenta.\n\n"
            "Nota: los assets PISCO requieren permisos de lectura; ERA5-Land y "
            "CHIRPS usan colecciones públicas de Earth Engine."
        )
        copy_button = message.addButton("Copiar comando", QMessageBox.ButtonRole.ActionRole)
        message.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is copy_button:
            QApplication.clipboard().setText(command)

    def _append_log(self, text):
        self.log.appendPlainText(str(text))

    def _set_busy(self, busy):
        self._busy += 1 if busy else -1
        self._busy = max(0, self._busy)
        enabled = self._busy == 0 and self._dependency_available
        for button in (
            self.connect_button, self.change_button, self.check_button,
            self.inspect_precip_button, self.download_precip_button,
            self.inspect_temp_button, self.download_temp_button, self.diagnose_button,
        ):
            button.setEnabled(enabled)
        self.cancel_button.setEnabled(not enabled)

    def _cancel_current_task(self):
        if self._current_task is not None:
            self._append_log("Cancelando operacion...")
            self._current_task.cancel()

    def _task(self, description, function, finished, task_aware=False):
        self._set_busy(True)
        self._append_log(description + "...")

        def worker(task):
            if task.isCanceled():
                return None
            return function(task) if task_aware else function()

        def completed(exception, result=None):
            self._set_busy(False)
            self._current_task = None
            if exception is not None:
                self._append_log("ERROR: " + str(exception))
                QMessageBox.critical(self, "Google Earth Engine", str(exception))
                return
            try:
                finished(result)
            except Exception as error:
                self._append_log("ERROR: " + str(error))
                QMessageBox.critical(self, "Google Earth Engine", str(error))

        task = QgsTask.fromFunction(description, worker, on_finished=completed)
        self._current_task = task
        QgsApplication.taskManager().addTask(task)

    def _connect(self, force):
        project = self._selected_project()
        if not project:
            return
        self._save_settings()

        def done(result):
            reused = result.get("reused")
            suffix = "credenciales existentes" if reused else "autorizacion nueva"
            self.connection_status.setText(f"Conectado a {project} ({suffix}).")
            self._append_log("Conexion correcta con " + project + ".")

        self.connection_status.setText(
            "Esperando autorizacion. Si es necesario se abrira el navegador; puede cancelar la operacion."
        )
        self._task(
            "Autenticando Earth Engine",
            lambda task: authenticate_ee_external(
                project, force, timeout_seconds=180, cancel_check=task.isCanceled
            ),
            done,
            task_aware=True,
        )

    def _check_connection(self):
        project = self._selected_project()
        if not project:
            return
        self._save_settings()

        def done(result):
            self.connection_status.setText(f"Conexion verificada: {result['project']} | API {result.get('api_version') or 'disponible'}")
            self._append_log("Earth Engine respondio correctamente.")

        self._task(
            "Comprobando conexion",
            lambda task: run_gee_external(
                "initialize", {"project_id": project},
                timeout_seconds=45, cancel_check=task.isCanceled,
            ),
            done,
            task_aware=True,
        )

    def _source_key(self, variable):
        if variable == "precipitation":
            return str(self.precip_source_combo.currentData())
        if variable == "temperature":
            return str(self.temperature_source_combo.currentData())
        raise GeeError(f"Variable climatica desconocida: {variable!r}.")

    def _source_args(self):
        return (self.pisco_p_edit.text().strip(), self.pisco_t_edit.text().strip())

    def _inspect_source(self, variable):
        key = self._source_key(variable); p_asset, t_asset = self._source_args()
        project = self._selected_project()
        if not project:
            return

        def done(summary):
            first = summary.get("first_date")
            last = summary.get("last_date")
            if first:
                parsed = QDate.fromString(first[:10], "yyyy-MM-dd")
                if parsed.isValid(): self.start_edit.setDate(parsed)
            if last:
                parsed = QDate.fromString(last[:10], "yyyy-MM-dd")
                if parsed.isValid(): self.end_edit.setDate(parsed)
            lines = [
                f"{summary['label']}: {summary['size']} imagenes",
                f"Periodo: {first} a {last}",
                f"Bandas: {', '.join(summary['bands'])}",
                f"Escala aproximada: {summary.get('scale_m', 0):.1f} m",
            ]
            if summary["missing_bands"]: lines.append("FALTAN BANDAS: " + ", ".join(summary["missing_bands"]))
            if summary["missing_months"]: lines.append(f"Meses faltantes ({len(summary['missing_months'])}): " + ", ".join(summary["missing_months"][:24]))
            if summary["duplicates"]: lines.append("Meses duplicados: " + ", ".join(summary["duplicates"][:24]))
            self._append_log("\n".join(lines))

        payload = {
            "project_id": project,
            "source_key": key,
            "pisco_precip_asset": p_asset,
            "pisco_temp_asset": t_asset,
        }
        self._task(
            "Inspeccionando coleccion",
            lambda task: run_gee_external(
                "summary", payload, timeout_seconds=120, cancel_check=task.isCanceled,
            ),
            done,
            task_aware=True,
        )

    def _diagnose(self):
        p_asset, t_asset = self._source_args()
        project = self._selected_project()
        if not project:
            return

        def done(result):
            lines = ["DIAGNOSTICO DE FUENTES"]
            for key, item in result.items():
                if item.get("ok"):
                    summary = item["summary"]
                    warning = "" if not summary["missing_bands"] else " | faltan bandas"
                    lines.append(f"OK {summary['label']}: {summary['size']} imagenes, {summary['first_date']} a {summary['last_date']}{warning}")
                else:
                    lines.append(f"ERROR {key}: {item.get('error')}")
            self._append_log("\n".join(lines))

        payload = {
            "project_id": project,
            "pisco_precip_asset": p_asset,
            "pisco_temp_asset": t_asset,
        }
        self._task(
            "Diagnosticando catalogo climatico",
            lambda task: run_gee_external(
                "diagnose", payload, timeout_seconds=300, cancel_check=task.isCanceled,
            ),
            done,
            task_aware=True,
        )

    def _use_active_layer(self):
        layer = self.iface.activeLayer() if self.iface else None
        if layer is None:
            QMessageBox.warning(self, "Cuenca", "No existe una capa activa.")
            return
        self.basin_combo.setLayer(layer)

    def _basin_geojson(self):
        layer = self.basin_combo.currentLayer()
        if layer is None:
            raise GeeError("Seleccione una capa poligonal de cuenca.")
        geometries = [feature.geometry() for feature in layer.getFeatures() if feature.hasGeometry() and not feature.geometry().isEmpty()]
        if not geometries:
            raise GeeError("La capa de cuenca no contiene geometrias validas.")
        geometry = QgsGeometry.unaryUnion(geometries)
        if geometry.isEmpty():
            raise GeeError("No se pudo unir la geometria de la cuenca.")
        if not geometry.isGeosValid():
            geometry = geometry.makeValid()
        transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        geometry.transform(transform)
        return json.loads(geometry.asJson(8))

    def _download(self, variable):
        project = self._selected_project()
        if not project:
            return
        try:
            geometry = self._basin_geojson()
        except Exception as error:
            QMessageBox.warning(self, "Cuenca", str(error)); return
        key = self._source_key(variable); p_asset, t_asset = self._source_args()
        start = self.start_edit.date().toString("yyyy-MM-01")
        end = self.end_edit.date().toString("yyyy-MM-01")

        def done(rows):
            if key == "era5":
                # Defensa adicional: incluso si una respuesta antigua o cacheada
                # contiene lluvia ERA5, nunca se expone ni se aplica al modelo.
                rows = select_climate_variables(rows, "temperature")
            self.downloads[key] = rows
            if variable == "precipitation":
                self.precipitation_key = key
                self.precipitation_status.setText(
                    f"P preparada: {SOURCE_CATALOG[key].label}. Pulse Aplicar precipitacion."
                )
            else:
                self.temperature_key = key
                self.temperature_status.setText(
                    f"T preparada: {SOURCE_CATALOG[key].label}. Pulse Aplicar temperatura."
                )
            self.active_rows = rows
            self.active_source_key = key
            self.active_title = SOURCE_CATALOG[key].label
            if key == "era5":
                self.table_note.setText(
                    "ERA5-Land DAILY_AGGR: promedio mensual de Tmin y Tmax diarias; "
                    "Tmedia=(Tmin+Tmax)/2. La precipitacion activa no se modifica."
                )
            elif key == "pisco_p":
                self.table_note.setText(
                    "PISCO: media zonal unica de pixeles nativos, compatible con el flujo "
                    "PyQGIS. Pulse Aplicar precipitacion para incorporarla al modelo."
                )
            else:
                self.table_note.setText(
                    f"Vista previa descargada: {SOURCE_CATALOG[key].label}. Cambiar el selector "
                    "no cambia esta tabla ni el modelo. Pulse el boton Aplicar correspondiente."
                )
            self._populate(rows)
            valid = sum(1 for row in rows if int(row.get("image_count") or 0) > 0)
            low_coverage = sum(bool(row.get("advertencia_cobertura")) for row in rows)
            incomplete_time = sum(bool(row.get("advertencia_temporal")) for row in rows)
            self._append_log(
                f"Serie obtenida: {len(rows)} meses, {valid} con imagenes, "
                f"{low_coverage} con cobertura menor a 90% y "
                f"{incomplete_time} con dias incompletos."
            )

        payload = {
            "project_id": project,
            "source_key": key,
            "geometry_geojson": geometry,
            "start_date": start,
            "end_date": end,
            "pisco_precip_asset": p_asset,
            "pisco_temp_asset": t_asset,
        }
        self._task(
            "Calculando promedio areal",
            lambda task: run_gee_external(
                "extract", payload, timeout_seconds=900, cancel_check=task.isCanceled,
            ),
            done,
            task_aware=True,
        )

    def _format_value(self, value, decimals=3):
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return ""

    def _populate(self, rows):
        self.table.setRowCount(len(rows))
        fields = ("fecha", "precipitacion_mm", "temp_min_c", "temp_media_c", "temp_max_c", "coverage_pct", "image_count", "fuente", "metodo")
        for row_index, row in enumerate(rows):
            for column, field in enumerate(fields):
                value = row.get(field, "")
                if field in ("precipitacion_mm", "temp_min_c", "temp_media_c", "temp_max_c", "coverage_pct"):
                    value = self._format_value(value)
                item = QTableWidgetItem(str(value or ""))
                if row.get("advertencia_cobertura") and field == "coverage_pct":
                    item.setToolTip("Cobertura espacial valida menor a 90%.")
                if row.get("advertencia_temporal") and field == "image_count":
                    item.setToolTip(
                        f"Mes incompleto: {row.get('image_count')} de "
                        f"{row.get('expected_image_count')} dias."
                    )
                self.table.setItem(row_index, column, item)
        temp_folder = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
        ) / "lutz_scholz_qgis"
        plot_path = create_climate_svg(rows, temp_folder / "serie_climatica_gee.svg", self.active_title)
        self.plot.load(plot_path)

    def _extend_temperature(self):
        pisco = self.downloads.get("pisco_t")
        era5 = self.downloads.get("era5")
        if not pisco or not era5:
            QMessageBox.warning(self, "Extension climatica", "Descargue primero PISCO temperatura y ERA5-Land con un periodo comun suficiente.")
            return
        try:
            result = extend_pisco_temperature(pisco, era5)
            self.downloads["pisco_t_extended"] = result["rows"]
            self.temperature_key = "pisco_t_extended"
            self.active_rows = result["rows"]
            self.active_source_key = "pisco_t_extended"
            self.active_title = "PISCO T extendida con ERA5-Land corregido"
            self.table_note.setText(
                "Vista previa de temperatura PISCO extendida con ERA5. La precipitacion activa "
                "no se modifica; pulse Aplicar temperatura para incorporar esta serie."
            )
            self.temperature_status.setText(
                "T preparada: PISCO T extendida con ERA5-Land corregido. Pulse Aplicar temperatura."
            )
            self._populate(self.active_rows)
            correlations = [model["correlacion"] for field in result["models"].values() for model in field.values()]
            mean_r = sum(correlations) / len(correlations)
            complete = sum(
                all(row.get(field) is not None for field in ("temp_min_c", "temp_media_c", "temp_max_c"))
                for row in result["rows"]
            )
            self._append_log(
                f"Extension creada. Ultimo PISCO: {result['last_reference']}; "
                f"traslape: {result['overlap_months']} meses; correlacion mensual media: {mean_r:.3f}.\n"
                f"Serie termica extendida: {len(result['rows'])} meses; "
                f"{complete} meses con temperatura completa."
            )
        except Exception as error:
            QMessageBox.critical(self, "Extension climatica", str(error))

    def _apply_precipitation(self):
        key = self.precipitation_key
        rows = self.downloads.get(key) if key else None
        if not rows:
            QMessageBox.warning(self, "Precipitacion", "Obtenga primero PISCO P o CHIRPS.")
            return
        self._apply_rows_to_model(rows, "precipitation", SOURCE_CATALOG[key].label, key)

    def _apply_temperature(self):
        key = self.temperature_key
        rows = self.downloads.get(key) if key else None
        if not rows:
            QMessageBox.warning(self, "Temperatura", "Obtenga PISCO T o ERA5, o cree la extension termica.")
            return
        title = (
            "PISCO T extendida con ERA5-Land corregido"
            if key == "pisco_t_extended" else SOURCE_CATALOG[key].label
        )
        self._apply_rows_to_model(rows, "temperature", title, key)

    def _apply_rows_to_model(self, rows, mode, title, source_key):
        if self.apply_callback is None:
            return
        try:
            selected_rows = select_climate_variables(rows, mode)
            mode_label = "precipitacion" if mode == "precipitation" else "temperatura"
            result = self.apply_callback(selected_rows, f"{title} (solo {mode_label})")
            if mode == "precipitation":
                self.precipitation_status.setText(f"P activa en el modelo: {title}.")
            else:
                self.temperature_status.setText(f"T activa en el modelo: {title}.")
            if isinstance(result, dict):
                message = result.get("message")
                integrated_rows = result.get("rows") or []
                if integrated_rows:
                    self.active_rows = integrated_rows
                    self.active_title = result.get("title") or "Serie activa del modelo"
                    self.active_source_key = "model_integrated"
                    self.table_note.setText(
                        "Serie integrada activa en el modelo. La P y T mostradas son las que se "
                        "usaran al ejecutar; sus fuentes se indican en los estados superiores."
                    )
                    self._populate(self.active_rows)
            else:
                message = result
            self._append_log(message or "Serie aplicada al modelo Lutz.")
        except Exception as error:
            QMessageBox.critical(self, "Datos climaticos", str(error))

    def _export(self):
        if not self.active_rows:
            QMessageBox.warning(self, "Exportar", "No existen resultados climaticos para exportar.")
            return
        climate_folder = self.project_folders.get("climate")
        default_path = (
            climate_folder / "serie_climatica_areal.csv"
            if climate_folder else Path("serie_climatica_areal.csv")
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar serie climatica", str(default_path), "CSV (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            saved = write_climate_csv(self.active_rows, path)
            self._append_log("CSV exportado: " + saved)
        except Exception as error:
            QMessageBox.critical(self, "Exportar", str(error))
