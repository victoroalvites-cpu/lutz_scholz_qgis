from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

from qgis.PyQt.QtCore import QSettings, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtSvg import QSvgWidget
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QApplication, QCheckBox, QComboBox,
    QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLayout, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy,
)
from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox

from .core import (
    LutzError, ModelParameters, MonthlyRecord, RetentionConfig, apply_hargreaves,
    calibrate_parameters, calculate_retention_components, estimate_c_observed,
    estimate_c_southern_region, estimate_c_turc, regional_supply, run_model,
    select_k_by_criteria, summarize_etp, chronological_observed_split,
    flow_persistence, transfer_hydrological_flows,
)
from .io_utils import read_project, write_results
from .plotting import create_diagnostic_plots
from .reporting import (
    _display_mode, _display_modeling_id, _display_split,
    create_word_report, export_panel_pngs, finalize_manifest,
)
from .spatial_retention import calculate_from_layers, load_polygon_layer
from .gee_widget import GeeClimateWidget
from .project_utils import PROJECT_FOLDERS, ensure_project_structure
from .version import PLUGIN_SERIES, PLUGIN_VERSION


MONTHS = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
DEFAULT_POSITIONS = (0, 0, 0, 1, 2, 3, 4, 5, 6, 0, 0, 0)
DEFAULT_SUPPLY = (0.50, 0.30, 0.20, 0, 0, 0, 0, 0, 0, 0, 0, 0)


class LutzScholzDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.records = []
        self.project_input = None
        self.loaded_input_path = None
        self.last_outputs = {}
        self.result_plot_paths = {}
        self.last_run_folder = None
        self.period_split_info = None
        self.climate_provenance = {}
        self.c_estimate_result = None
        self.precipitation_source_title = "archivo o aun no definida"
        self.temperature_source_title = "archivo o aun no definida"
        self.supply_source_title = "manual"
        self.last_report_path = None
        self.project_folders = {}
        self.setWindowTitle(f"Modelo Lutz Scholz - v{PLUGIN_VERSION}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.setMinimumSize(680, 480)
        self.setSizeGripEnabled(True)
        self._build_ui()
        self._resize_to_screen()
        self._load_project_folder()

    def _resize_to_screen(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.resize(
            min(1120, max(760, int(available.width() * 0.92))),
            min(780, max(520, int(available.height() * 0.88))),
        )

    def _spin(self, minimum, maximum, value, decimals=3, step=0.1):
        widget = QDoubleSpinBox(); widget.setRange(minimum, maximum)
        widget.setDecimals(decimals); widget.setSingleStep(step); widget.setValue(value)
        return widget

    def _scroll(self, content):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll.setMinimumSize(0, 0)
        scroll.setWidget(content)
        return scroll

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetDefaultConstraint)
        root.addWidget(self._project_group())
        intro = QLabel(
            f"Modelo mensual Lutz Scholz v{PLUGIN_SERIES}: división temporal, clima trazable, "
            "calibración y validación independiente, diagnóstico multiescala e informe técnico."
        )
        intro.setWordWrap(True); root.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._data_tab(), "1. Datos")
        self.gee_widget = GeeClimateWidget(self.iface, self._apply_gee_rows, self)
        self.tabs.addTab(self._scroll(self.gee_widget), "2. Clima GEE")
        self.tabs.addTab(self._climate_tab(), "3. ETP")
        self.tabs.addTab(self._retention_tab(), "4. Retencion")
        self.tabs.addTab(self._k_tab(), "5. K")
        self.tabs.addTab(self._supply_tab(), "6. Abastecimiento")
        self.tabs.addTab(self._calibration_tab(), "7. Calibracion")
        self.tabs.addTab(self._flow_analysis_tab(), "8. Permanencia")
        self.tabs.addTab(self._scroll(self._results_tab()), "9. Resultados")
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setMinimumSize(0, 0)
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.tabs, 1)
        footer = QWidget()
        footer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(footer)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.run_button = QPushButton("Ejecutar modelo")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run)
        self.close_button = QPushButton("Cerrar")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.run_button)
        row.addWidget(self.close_button)
        root.addWidget(footer)

    def _project_group(self):
        group = QGroupBox("Proyecto")
        grid = QGridLayout(group)
        self.project_folder_edit = QLineEdit()
        self.project_folder_edit.setPlaceholderText("Seleccione la carpeta principal del proyecto")
        browse = QPushButton("Examinar")
        browse.clicked.connect(self._choose_project_folder)
        apply_button = QPushButton("Aplicar rutas")
        apply_button.clicked.connect(lambda: self._apply_project_folder(True))
        self.project_status_label = QLabel(
            "Configure un proyecto para organizar datos, clima, resultados y documentación."
        )
        self.project_status_label.setWordWrap(True)
        grid.addWidget(QLabel("Carpeta principal"), 0, 0)
        grid.addWidget(self.project_folder_edit, 0, 1)
        grid.addWidget(browse, 0, 2)
        grid.addWidget(apply_button, 0, 3)
        grid.addWidget(self.project_status_label, 1, 0, 1, 4)
        return group

    def _choose_project_folder(self):
        current = self.project_folder_edit.text().strip()
        path = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta del proyecto", current)
        if path:
            self.project_folder_edit.setText(path)
            self._apply_project_folder(True)

    def _load_project_folder(self):
        saved = str(QSettings().value("LutzScholz/project_root", "") or "").strip()
        if saved:
            self.project_folder_edit.setText(saved)
            self._apply_project_folder(False)

    def _apply_project_folder(self, show_message=False):
        selected = self.project_folder_edit.text().strip()
        if not selected:
            if show_message:
                QMessageBox.warning(self, "Proyecto", "Seleccione una carpeta principal.")
            return
        try:
            root, folders, config_path = ensure_project_structure(selected)
            self.project_folder_edit.setText(str(root))
            self.project_folders = folders
            QSettings().setValue("LutzScholz/project_root", str(root))
            self.output_edit.setText(str(folders["results"]))
            self.gee_widget.set_project_context(root, folders)
            names = ", ".join(PROJECT_FOLDERS.values())
            self.project_status_label.setText(f"Proyecto activo: {root} | Carpetas: {names}.")
            if show_message:
                QMessageBox.information(
                    self,
                    "Proyecto configurado",
                    f"La estructura de trabajo quedó preparada.\n\nConfiguración: {config_path}",
                )
        except Exception as error:
            self.project_status_label.setText(f"No se pudo configurar el proyecto: {error}")
            if show_message:
                QMessageBox.critical(self, "Proyecto", str(error))

    def _data_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        source = QGroupBox("Proyecto CSV o Excel")
        grid = QGridLayout(source); self.input_edit = QLineEdit()
        browse = QPushButton("Examinar"); browse.clicked.connect(self._browse_input)
        load = QPushButton("Cargar y validar"); load.clicked.connect(self._load_input)
        template = QPushButton("Abrir carpeta de plantilla"); template.clicked.connect(self._open_template_folder)
        self.file_status = QLabel("CSV: Fecha, Precipitacion_mm y Q observado opcional. Excel: use la plantilla incluida.")
        self.file_status.setWordWrap(True)
        grid.addWidget(QLabel("Archivo"), 0, 0); grid.addWidget(self.input_edit, 0, 1)
        grid.addWidget(browse, 0, 2); grid.addWidget(load, 0, 3); grid.addWidget(template, 1, 3)
        grid.addWidget(self.file_status, 1, 0, 1, 3); layout.addWidget(source)
        parameters = QGroupBox("Parametros principales"); form = QFormLayout(parameters)
        self.area_spin = self._spin(.001, 1_000_000, 950.54, 3, 1)
        self.matlab_check = QCheckBox("Calendario compatible con MATLAB"); self.matlab_check.setChecked(True)
        form.addRow("Area de cuenca (km2)", self.area_spin); form.addRow("Compatibilidad", self.matlab_check)
        layout.addWidget(parameters)
        periods = QGroupBox("Periodos"); grid = QGridLayout(periods)
        self.cal_start = QSpinBox(); self.cal_end = QSpinBox(); self.val_start = QSpinBox(); self.val_end = QSpinBox()
        for widget in (self.cal_start, self.cal_end, self.val_start, self.val_end): widget.setRange(1800, 2500)
        self.cal_start.setValue(1990); self.cal_end.setValue(2005); self.val_start.setValue(2006); self.val_end.setValue(2011)
        self.validation_check = QCheckBox("Evaluar validacion con parametros calibrados"); self.validation_check.setChecked(True)
        self.validation_check.toggled.connect(self._toggle_validation_fields)
        grid.addWidget(QLabel("Calibracion desde"), 0, 0); grid.addWidget(self.cal_start, 0, 1); grid.addWidget(QLabel("hasta"), 0, 2); grid.addWidget(self.cal_end, 0, 3)
        grid.addWidget(self.validation_check, 1, 0); grid.addWidget(self.val_start, 1, 1); grid.addWidget(QLabel("hasta"), 1, 2); grid.addWidget(self.val_end, 1, 3)
        self.split_combo = QComboBox(); self.split_combo.addItem("Automatico cronologico 60/40", "auto_60_40"); self.split_combo.addItem("Manual", "manual")
        apply_split = QPushButton("Recalcular division"); apply_split.clicked.connect(self._apply_auto_split)
        self.split_status = QLabel("Se usarán años con 12 caudales observados; la validación nunca recalibra."); self.split_status.setWordWrap(True)
        grid.addWidget(QLabel("Division temporal"), 2, 0); grid.addWidget(self.split_combo, 2, 1, 1, 2); grid.addWidget(apply_split, 2, 3)
        grid.addWidget(self.split_status, 3, 0, 1, 4)
        layout.addWidget(periods); layout.addStretch(1)
        return self._scroll(content)

    def _climate_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        box = QGroupBox("Evapotranspiracion potencial"); form = QFormLayout(box)
        self.etp_method = QComboBox(); self.etp_method.addItems(("Ninguna", "Importada del Excel/CSV", "Hargreaves-Samani"))
        self.latitude_spin = self._spin(-90, 90, -12, 5, .1)
        from_basin = QPushButton("Tomar latitud del centro de la cuenca"); from_basin.clicked.connect(self._latitude_from_basin)
        calculate = QPushButton("Calcular y actualizar cuadros de ETP")
        calculate.clicked.connect(lambda: self._refresh_etp_preview(False))
        self.etp_status = QLabel("Hargreaves requiere Tmin, Tmedia y Tmax para todos los meses."); self.etp_status.setWordWrap(True)
        form.addRow("Metodo", self.etp_method); form.addRow("Latitud (grados)", self.latitude_spin)
        form.addRow("", from_basin); form.addRow("", calculate); form.addRow("Estado", self.etp_status)
        layout.addWidget(box)

        self.etp_tables = QTabWidget()
        self.etp_monthly_table = QTableWidget(0, 5)
        self.etp_monthly_table.setHorizontalHeaderLabels(("Fecha", "Tmin C", "Tmedia C", "Tmax C", "ET0 mm/mes"))
        self.etp_annual_table = QTableWidget(0, 4)
        self.etp_annual_table.setHorizontalHeaderLabels(("Año", "Meses válidos", "ET0 total mm/año", "ET0 media mm/mes"))
        for table in (self.etp_monthly_table, self.etp_annual_table):
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(True)
        self.etp_tables.addTab(self.etp_monthly_table, "Cuadro mensual")
        self.etp_tables.addTab(self.etp_annual_table, "Cuadro anual")
        layout.addWidget(self.etp_tables, 1)
        self.etp_method.currentIndexChanged.connect(lambda _index: self._refresh_etp_preview(True))
        self.latitude_spin.editingFinished.connect(lambda: self._refresh_etp_preview(True))
        return content

    def _retention_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        mode_row = QHBoxLayout(); self.r_mode = QComboBox(); self.r_mode.addItems(("Manual", "Componentes de Excel", "Capas QGIS"))
        self.r_spin = self._spin(0, 10_000, 15, 3, 1)
        self.load_excel_r_button = QPushButton("Cargar R del Excel")
        self.load_excel_r_button.clicked.connect(self._load_retention_from_excel)
        mode_row.addWidget(QLabel("Método R")); mode_row.addWidget(self.r_mode)
        mode_row.addWidget(QLabel("R (mm/año)")); mode_row.addWidget(self.r_spin)
        mode_row.addWidget(self.load_excel_r_button); layout.addLayout(mode_row)
        self.r_tabs = QTabWidget()
        manual = QWidget(); m = QVBoxLayout(manual)
        explanation = QLabel(
            "Ingrese R directamente o recupere desde el Excel el valor Retencion_Manual_mm "
            "o el cálculo de la hoja Componentes_R, según el método configurado."
        )
        explanation.setWordWrap(True)
        self.r_excel_status = QLabel("No se ha cargado una retención desde el Excel.")
        self.r_excel_status.setWordWrap(True)
        m.addWidget(explanation); m.addWidget(self.r_excel_status); m.addStretch(1)
        self.r_tabs.addTab(manual, "Manual / Excel")
        spatial = QWidget(); grid = QGridLayout(spatial)
        self.basin_combo = self._layer_combo(QgsMapLayerProxyModel.PolygonLayer)
        self.snow_combo = self._layer_combo(QgsMapLayerProxyModel.PolygonLayer, True)
        self.lagoon_combo = self._layer_combo(QgsMapLayerProxyModel.PolygonLayer, True)
        self.aquifer_combo = self._layer_combo(QgsMapLayerProxyModel.PolygonLayer, True)
        self.dem_combo = self._layer_combo(QgsMapLayerProxyModel.RasterLayer, True)
        for row, (label, combo, kind) in enumerate((("Cuenca*", self.basin_combo, "vector"), ("Nevados/glaciares", self.snow_combo, "vector"), ("Lagunas/pantanos", self.lagoon_combo, "vector"), ("Acuiferos", self.aquifer_combo, "vector"), ("MDE para pendiente", self.dem_combo, "raster"))):
            button = QPushButton("Importar"); button.clicked.connect(lambda checked=False, c=combo, k=kind, n=label: self._import_layer(c, k, n))
            grid.addWidget(QLabel(label), row, 0); grid.addWidget(combo, row, 1); grid.addWidget(button, row, 2)
        self.manual_slope_check = QCheckBox("Usar pendiente manual de acuiferos"); self.manual_slope_check.setChecked(True)
        self.aquifer_slope = self._spin(0, .15, .05, 5, .005)
        calculate = QPushButton("Calcular retencion desde capas"); calculate.clicked.connect(self._calculate_spatial_retention)
        self.spatial_status = QPlainTextEdit(); self.spatial_status.setReadOnly(True); self.spatial_status.setMaximumHeight(150)
        grid.addWidget(self.manual_slope_check, 5, 0); grid.addWidget(self.aquifer_slope, 5, 1)
        grid.addWidget(calculate, 6, 0, 1, 3); grid.addWidget(self.spatial_status, 7, 0, 1, 3)
        self.r_tabs.addTab(spatial, "Capas opcionales SHP/GPKG"); layout.addWidget(self.r_tabs, 1)
        note = QLabel("Solo la cuenca es obligatoria. Las capas opcionales ausentes se calculan con area 0 y se informan como advertencia.")
        note.setWordWrap(True); layout.addWidget(note); return self._scroll(content)

    def _layer_combo(self, filters, allow_empty=False):
        combo = QgsMapLayerComboBox(); combo.setFilters(filters); combo.setAllowEmptyLayer(allow_empty)
        return combo

    def _k_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        box = QGroupBox("Coeficiente de agotamiento"); form = QFormLayout(box)
        self.k_mode = QComboBox(); self.k_mode.addItems(("K manual", "K recomendado por criterios", "a diario directo"))
        self.k_spin = self._spin(-1, 1, .034, 6, .001); self.a_spin = self._spin(.00001, 1, .0167, 6, .001)
        self.recession_combo = QComboBox(); self.recession_combo.addItems(("desconocido", "muy_rapido", "rapido", "mediano", "reducido", "muy_reducido"))
        self.cover_combo = QComboBox(); self.cover_combo.addItems(("desconocida", "puna_poco_desarrollada", "mixta", "acuiferos_bofedales"))
        self.storage_combo = QComboBox(); self.storage_combo.addItems(("desconocido", "bajo", "medio", "alto", "muy_alto"))
        recommend = QPushButton("Recomendar K y explicar"); recommend.clicked.connect(self._recommend_k)
        self.k_status = QLabel("El criterio observado de estiaje tiene mayor peso."); self.k_status.setWordWrap(True)
        form.addRow("Modo", self.k_mode); form.addRow("K", self.k_spin); form.addRow("a (1/dia)", self.a_spin)
        form.addRow("Comportamiento de estiaje", self.recession_combo); form.addRow("Cobertura", self.cover_combo); form.addRow("Almacenamiento", self.storage_combo)
        form.addRow("", recommend); form.addRow("Recomendacion", self.k_status)
        layout.addWidget(box); layout.addStretch(1); return self._scroll(content)

    def _supply_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        top = QHBoxLayout(); self.region_combo = QComboBox(); self.region_combo.addItems(("Manual", "Cusco", "Huancavelica", "Junin", "Cajamarca", "Ancash-Santa"))
        apply = QPushButton("Aplicar patron regional"); apply.clicked.connect(self._apply_region)
        from_excel = QPushButton("Cargar Gasto_Abastecimiento del Excel")
        from_excel.clicked.connect(self._load_supply_from_excel)
        top.addWidget(QLabel("Patron Sierra peruana")); top.addWidget(self.region_combo)
        top.addWidget(apply); top.addWidget(from_excel); top.addStretch(1); layout.addLayout(top)
        self.retention_table = QTableWidget(12, 3); self.retention_table.setHorizontalHeaderLabels(("Mes", "Posicion de gasto", "Fraccion de abastecimiento")); self.retention_table.verticalHeader().setVisible(False)
        for row, month in enumerate(MONTHS):
            item = QTableWidgetItem(month); item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.retention_table.setItem(row, 0, item); self.retention_table.setItem(row, 1, QTableWidgetItem(str(DEFAULT_POSITIONS[row]))); self.retention_table.setItem(row, 2, QTableWidgetItem(f"{DEFAULT_SUPPLY[row]:.6f}"))
        self.retention_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.retention_table)
        self.retention_table.itemChanged.connect(self._mark_supply_as_manual)
        self.supply_status = QLabel("Puede editar la tabla o importarla desde Gasto_Abastecimiento del Excel."); self.supply_status.setWordWrap(True); layout.addWidget(self.supply_status)
        return content

    def _mark_supply_as_manual(self, _item):
        self.supply_source_title = "tabla manual/editada"

    def _calibration_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)

        estimate = QGroupBox("Estimacion inicial del coeficiente de escorrentia C")
        estimate_form = QFormLayout(estimate)
        self.c_spin = self._spin(0, 1, .17, 5, .01)
        self.c_method_combo = QComboBox()
        self.c_method_combo.addItems(("Turc clasico", "Regional sierra sur (P y ETP)", "Observado Q/P"))
        calculate_c = QPushButton("Calcular C en el periodo de calibracion")
        calculate_c.clicked.connect(self._estimate_runoff_coefficient)
        use_c = QPushButton("Usar como C inicial y definir limites")
        use_c.clicked.connect(self._use_estimated_c)
        self.c_estimate_p = QLineEdit(); self.c_estimate_p.setReadOnly(True)
        self.c_estimate_climate = QLineEdit(); self.c_estimate_climate.setReadOnly(True)
        self.c_estimate_deficit = QLineEdit(); self.c_estimate_deficit.setReadOnly(True)
        self.c_estimate_value = QLineEdit(); self.c_estimate_value.setReadOnly(True)
        self.c_estimate_status = QLabel(
            "Turc es orientativo; para la sierra sur use la ecuacion regional y valide con Q observado."
        )
        self.c_estimate_status.setWordWrap(True)
        estimate_form.addRow("Metodo", self.c_method_combo)
        estimate_form.addRow("C inicial / manual", self.c_spin)
        estimate_form.addRow("P media anual (mm)", self.c_estimate_p)
        estimate_form.addRow("T media (C) / ETP anual (mm)", self.c_estimate_climate)
        estimate_form.addRow("Déficit estimado (mm/año)", self.c_estimate_deficit)
        estimate_form.addRow("C estimado", self.c_estimate_value)
        buttons_widget = QWidget(); buttons = QHBoxLayout(buttons_widget); buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(calculate_c); buttons.addWidget(use_c)
        estimate_form.addRow("", buttons_widget); estimate_form.addRow("Diagnostico", self.c_estimate_status)
        layout.addWidget(estimate)

        box = QGroupBox("Uso de caudales observados"); form = QFormLayout(box)
        self.auto_calibrate = QCheckBox("Calibrar automaticamente R y a con Q observado"); self.auto_calibrate.setChecked(True)
        self.scenario_edit = QLineEdit("escenario_base")
        manual_mode = QPushButton("Activar calibracion manual con C, R, a y fracciones actuales")
        manual_mode.clicked.connect(self._activate_manual_calibration)
        self.calibrate_c_check = QCheckBox("Incluir C en la calibracion (balance de volumen)")
        self.objective_combo = QComboBox(); self.objective_combo.addItems(("NSE", "Combinado", "Combinado con picos", "LogNSE", "KGE"))
        self.negative_balance_combo = QComboBox()
        self.negative_balance_combo.addItem("Estricto: exigir balance fisico", "strict")
        self.negative_balance_combo.addItem("Exploratorio: recorte controlado con advertencia", "controlled_clip")
        self.c_min = self._spin(0, .99999, .12, 5, .01); self.c_max = self._spin(.00001, 1, .22, 5, .01)
        self.r_min = self._spin(0, 1000, 0, 2, 5); self.r_max = self._spin(0.1, 2000, 200, 2, 5)
        self.a_min = self._spin(.00001, 1, .005, 5, .001); self.a_max = self._spin(.00002, 1, .06, 5, .001)
        self.grid_steps = QSpinBox(); self.grid_steps.setRange(4, 20); self.grid_steps.setValue(8)
        info = QLabel(
            "La calibracion automatica solo evalua combinaciones con Q mensual mayor o igual a cero. "
            "El recorte controlado queda reservado para ejecuciones manuales exploratorias."
        ); info.setWordWrap(True)
        form.addRow("Escenario", self.scenario_edit); form.addRow("", self.auto_calibrate); form.addRow("", manual_mode); form.addRow("", self.calibrate_c_check)
        form.addRow("Función objetivo", self.objective_combo)
        form.addRow("Balance negativo", self.negative_balance_combo)
        form.addRow("C mínimo", self.c_min); form.addRow("C máximo", self.c_max)
        form.addRow("R minimo", self.r_min); form.addRow("R maximo", self.r_max)
        form.addRow("a minimo", self.a_min); form.addRow("a maximo", self.a_max); form.addRow("Pasos por eje", self.grid_steps); form.addRow("", info)
        layout.addWidget(box); layout.addStretch(1); return self._scroll(content)

    def _activate_manual_calibration(self):
        self.auto_calibrate.setChecked(False)
        self.k_mode.setCurrentIndex(2)
        self.c_estimate_status.setText(
            "Modo manual activo: la corrida usara exactamente C, R, a y las 12 fracciones de abastecimiento visibles. "
            "Los indicadores se calcularan sin optimizar parametros."
        )

    def _flow_analysis_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        transfer = QGroupBox("Transposición hidrológica opcional")
        grid = QGridLayout(transfer)
        self.transfer_check = QCheckBox("Aplicar transferencia Qs = (As/Ac) × (Ps/Pc) × Qc")
        self.transfer_check.setChecked(False)
        self.donor_input_edit = QLineEdit()
        self.donor_input_edit.setPlaceholderText("CSV o Excel de la cuenca donante")
        browse = QPushButton("Examinar")
        browse.clicked.connect(self._browse_donor_input)
        self.donor_area_spin = self._spin(.001, 1_000_000, 1000.0, 3, 1)
        self.transfer_method_combo = QComboBox()
        self.transfer_method_combo.addItem("Factor anual de precipitación", "annual")
        self.transfer_method_combo.addItem("Factores por mes climatológico", "monthly_climatology")
        note = QLabel(
            "El archivo donante debe contener Fecha, Precipitación y Q observado. La cuenca objetivo "
            "es la serie cargada en Datos y su área es la indicada en Parámetros principales."
        )
        note.setWordWrap(True)
        grid.addWidget(self.transfer_check, 0, 0, 1, 3)
        grid.addWidget(QLabel("Serie donante"), 1, 0); grid.addWidget(self.donor_input_edit, 1, 1); grid.addWidget(browse, 1, 2)
        grid.addWidget(QLabel("Área donante (km²)"), 2, 0); grid.addWidget(self.donor_area_spin, 2, 1)
        grid.addWidget(QLabel("Ajuste de precipitación"), 3, 0); grid.addWidget(self.transfer_method_combo, 3, 1, 1, 2)
        grid.addWidget(note, 4, 0, 1, 3)
        layout.addWidget(transfer)

        persistence = QGroupBox("Persistencia y referencia ecológica")
        form = QFormLayout(persistence)
        self.persistence_source_combo = QComboBox()
        self.persistence_source_combo.addItem("Caudal simulado por Lutz Scholz", "simulado")
        self.persistence_source_combo.addItem("Caudal observado de la serie objetivo", "observado")
        self.persistence_source_combo.addItem("Caudal transferido", "transferido")
        self.persistence_status = QLabel(
            "La permanencia se calcula de forma independiente. No es necesario activar la transposición."
        )
        self.persistence_status.setWordWrap(True)
        form.addRow("Serie para Q75, Q95 y referencia del 15 %", self.persistence_source_combo)
        form.addRow("Criterio", self.persistence_status)
        layout.addWidget(persistence)
        warning = QLabel(
            "Q75 y Q95 son estadísticas hidrológicas. La referencia del 15 % y el Q95 no constituyen "
            "por sí solos un caudal ecológico aprobado por la ANA."
        )
        warning.setWordWrap(True); layout.addWidget(warning); layout.addStretch(1)
        return self._scroll(content)

    def _browse_donor_input(self):
        initial = self.donor_input_edit.text().strip()
        if not initial and self.project_folders:
            initial = str(self.project_folders["input"])
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar serie de la cuenca donante", initial,
            "Datos (*.xlsx *.csv *.txt);;Todos (*.*)",
        )
        if path:
            self.donor_input_edit.setText(path)

    def _apply_flow_analysis(self, result):
        selected_origin = self.persistence_source_combo.currentData() or "simulado"
        if self.transfer_check.isChecked():
            donor_path = self.donor_input_edit.text().strip()
            if not donor_path:
                raise LutzError("Seleccione el CSV o Excel de la cuenca donante.")
            donor = read_project(donor_path)
            rows, transfer = transfer_hydrological_flows(
                result["rows"], donor.records, self.area_spin.value(),
                self.donor_area_spin.value(), self.transfer_method_combo.currentData(),
            )
            transfer["donor_file"] = str(Path(donor_path).resolve())
            result["rows"] = rows
            result["flow_transfer"] = transfer
        else:
            result["flow_transfer"] = {"active": False}
            if selected_origin == "transferido":
                raise LutzError(
                    "La fuente elegida es 'Caudal transferido', pero la transposición está desactivada."
                )

        field = {
            "simulado": "caudal_simulado_m3s",
            "observado": "caudal_observado_m3s",
            "transferido": "caudal_transferido_m3s",
        }[selected_origin]
        if not any(row.get(field) is not None for row in result["rows"]):
            raise LutzError("La serie seleccionada para el análisis de permanencia no contiene caudales.")

        calibration = result["calibration_period"]
        validation = result.get("validation_period")
        persistence = {
            "complete": flow_persistence(result["rows"], selected_origin),
            "calibration": flow_persistence(
                [row for row in result["rows"] if calibration[0] <= int(row["anio"]) <= calibration[1]],
                selected_origin,
            ),
        }
        if validation:
            persistence["validation"] = flow_persistence(
                [row for row in result["rows"] if validation[0] <= int(row["anio"]) <= validation[1]],
                selected_origin,
            )
        result["flow_persistence"] = persistence
        result["persistence_analysis"] = {
            "selected_origin": selected_origin,
            "transfer_required": selected_origin == "transferido",
        }

    def _results_tab(self):
        content = QWidget(); layout = QVBoxLayout(content)
        row = QHBoxLayout(); self.output_edit = QLineEdit(); browse = QPushButton("Examinar"); browse.clicked.connect(self._browse_output)
        row.addWidget(QLabel("Carpeta de salida")); row.addWidget(self.output_edit, 1); row.addWidget(browse); layout.addLayout(row)
        self.add_table_check = QCheckBox("Agregar resultados_mensuales.csv a QGIS"); self.add_table_check.setChecked(True); layout.addWidget(self.add_table_check)
        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("Periodo mostrado"))
        self.result_period_combo = QComboBox()
        self.result_period_combo.currentIndexChanged.connect(self._refresh_result_period)
        period_row.addWidget(self.result_period_combo)
        self.result_period_note = QLabel("Ejecute el modelo para habilitar las vistas por periodo.")
        self.result_period_note.setWordWrap(True)
        period_row.addWidget(self.result_period_note, 1)
        layout.addLayout(period_row)
        self.result_tabs = QTabWidget(); self.summary = QPlainTextEdit(); self.summary.setReadOnly(True); self.result_tabs.addTab(self.summary, "Resumen")
        self.svg_widgets = {}
        for key, label in (("panel_diagnostico", "Diagnóstico integral"), ("serie_mensual", "Serie"), ("caudal_anual", "Anual"), ("regimen_multimensual", "Régimen"), ("dispersion", "Dispersión"), ("permanencia", "Permanencia"), ("resumen", "Ficha")):
            widget = QSvgWidget(); widget.setMinimumSize(700, 430); self.svg_widgets[key] = widget; self.result_tabs.addTab(widget, label)
        layout.addWidget(self.result_tabs, 1)
        open_folder = QPushButton("Abrir carpeta de resultados"); open_folder.clicked.connect(self._open_output); layout.addWidget(open_folder)
        open_report = QPushButton("Abrir informe técnico Word"); open_report.clicked.connect(self._open_report); layout.addWidget(open_report)
        self._set_result_period_options(False)
        return content

    def _toggle_validation_fields(self, enabled):
        for widget in (self.val_start, self.val_end):
            widget.setEnabled(bool(enabled))

    def _set_result_period_options(self, validation_available):
        if not hasattr(self, "result_period_combo"):
            return
        current = self.result_period_combo.currentData()
        self.result_period_combo.blockSignals(True)
        self.result_period_combo.clear()
        self.result_period_combo.addItem("Serie completa", "completo")
        self.result_period_combo.addItem("Calibracion", "calibracion")
        if validation_available:
            self.result_period_combo.addItem("Validacion", "validacion")
        index = self.result_period_combo.findData(current)
        self.result_period_combo.setCurrentIndex(index if index >= 0 else 0)
        self.result_period_combo.blockSignals(False)

    def _refresh_result_period(self, _index=None):
        if not self.result_plot_paths or not hasattr(self, "result_period_combo"):
            return
        period = self.result_period_combo.currentData() or "completo"
        suffix = "" if period == "completo" else f"_{period}"
        for logical_key in ("panel_diagnostico", "serie_mensual", "caudal_anual", "regimen_multimensual", "dispersion", "permanencia"):
            path = self.result_plot_paths.get(logical_key + suffix)
            if path:
                self.svg_widgets[logical_key].load(path)
        labels = {
            "completo": "Toda la serie; consulte por separado calibracion y validacion para evaluar transferencia.",
            "calibracion": "Periodo usado para ajustar parametros.",
            "validacion": "Periodo independiente; utiliza los parametros calibrados sin volver a ajustarlos.",
        }
        self.result_period_note.setText(labels.get(period, ""))

    def _browse_input(self):
        initial = self.input_edit.text().strip()
        if not initial and self.project_folders:
            initial = str(self.project_folders["input"])
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar proyecto", initial, "Datos (*.xlsx *.csv *.txt);;Todos (*.*)")
        if path:
            self.input_edit.setText(path)
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(Path(path).parent / "resultados_lutz"))

    def _browse_output(self):
        initial = self.output_edit.text().strip()
        if not initial and self.project_folders:
            initial = str(self.project_folders["results"])
        path = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", initial)
        if path: self.output_edit.setText(path)

    def _open_template_folder(self):
        folder = Path(__file__).parent / "templates"
        if folder.exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _load_input(self):
        try:
            source_path = Path(self.input_edit.text().strip()).resolve()
            self.project_input = read_project(str(source_path)); self.records = self.project_input.records
            years = sorted({record.fecha.year for record in self.records})
            if not years: raise LutzError("La serie esta vacia.")
            self.loaded_input_path = str(source_path)
            self._apply_workbook_retention(); self._apply_workbook_config(); self._apply_workbook_components()
            if self.split_combo.currentData() == "auto_60_40":
                self._apply_auto_split()
            precipitation = sum(record.precipitacion_mm is not None for record in self.records)
            observed = sum(record.caudal_observado_m3s is not None for record in self.records)
            temps = sum(None not in (record.temp_min_c, record.temp_media_c, record.temp_max_c) for record in self.records)
            self.precipitation_source_title = "Excel/CSV" if precipitation else "aun no definida"
            self.temperature_source_title = "Excel/CSV" if temps else "aun no definida"
            self.file_status.setText(
                f"Serie auxiliar valida: {len(self.records)} meses, {years[0]}-{years[-1]}, "
                f"{precipitation} meses con P, {observed} Q observados y {temps} meses con temperaturas. "
                "Los campos climaticos vacios pueden completarse desde Clima GEE."
            )
            if observed == 0: self.auto_calibrate.setChecked(False)
            self._refresh_etp_preview(True)
        except Exception as error:
            self.records = []; self.project_input = None; self.loaded_input_path = None
            QMessageBox.critical(self, "Entrada invalida", str(error))

    def _apply_auto_split(self):
        if not self.records:
            self.split_status.setText("Cargue primero una serie con caudales observados.")
            return
        try:
            split = chronological_observed_split(self.records, 0.60)
            self.period_split_info = split
            calibration = split["calibration_period"]
            validation = split["validation_period"]
            self.cal_start.setValue(calibration[0]); self.cal_end.setValue(calibration[1])
            self.val_start.setValue(validation[0]); self.val_end.setValue(validation[1])
            self.validation_check.setChecked(True)
            excluded = split["excluded_years"]
            excluded_text = "ninguno" if not excluded else ", ".join(map(str, excluded))
            self.split_status.setText(
                f"División automática por años completos: calibración {calibration[0]}-{calibration[1]} "
                f"({len(split['calibration_years'])} años, 60%); validación {validation[0]}-{validation[1]} "
                f"({len(split['validation_years'])} años, 40%). Años excluidos del corte: {excluded_text}."
            )
        except Exception as error:
            self.period_split_info = None
            self.split_status.setText(f"No fue posible dividir automaticamente: {error}")

    def _apply_gee_rows(self, rows, title):
        """Integra P y/o temperatura GEE conservando Q observado existente."""

        if not rows:
            raise LutzError("La serie de Earth Engine esta vacia.")
        climate = {}
        for row in rows:
            try:
                when = datetime.strptime(str(row.get("fecha", ""))[:10], "%Y-%m-%d").date().replace(day=1)
            except ValueError as error:
                raise LutzError(f"Fecha GEE invalida: {row.get('fecha')!r}.") from error
            if when in climate:
                raise LutzError(f"La serie GEE contiene el mes duplicado {when:%Y-%m}.")
            climate[when] = row
        existing = {record.fecha: record for record in self.records}
        has_precipitation = any(row.get("precipitacion_mm") is not None for row in rows)
        has_temperature = any(row.get("temp_min_c") is not None or row.get("temp_max_c") is not None for row in rows)
        first = rows[0]
        provenance = {
            "title": title,
            "source_key": first.get("source_key"),
            "method": first.get("metodo"),
            "native_crs": first.get("native_crs"),
            "native_transform": first.get("native_transform"),
            "spatial_reducer": first.get("spatial_reducer", "mean"),
            "months": len(rows),
            "first_month": str(rows[0].get("fecha", ""))[:7],
            "last_month": str(rows[-1].get("fecha", ""))[:7],
        }
        if has_precipitation:
            self.climate_provenance["precipitation"] = dict(provenance)
        if has_temperature:
            self.climate_provenance["temperature"] = dict(provenance)
        if has_precipitation:
            missing = [when for when, row in climate.items() if row.get("precipitacion_mm") is None]
            if missing:
                raise LutzError(
                    f"La fuente seleccionada no tiene precipitacion valida en {missing[0]:%Y-%m}. "
                    "Reduzca el periodo o revise la cobertura."
                )
            ordered = sorted(climate)
            if ordered[0].month != 1 or ordered[-1].month != 12 or len(ordered) % 12:
                raise LutzError("Para crear la entrada Lutz, seleccione años completos de enero a diciembre.")
            records = []
            for when in ordered:
                row = climate[when]
                old = existing.get(when)
                records.append(MonthlyRecord(
                    fecha=when,
                    precipitacion_mm=float(row["precipitacion_mm"]),
                    caudal_observado_m3s=old.caudal_observado_m3s if old else None,
                    temp_min_c=row.get("temp_min_c") if row.get("temp_min_c") is not None else (old.temp_min_c if old else None),
                    temp_media_c=row.get("temp_media_c") if row.get("temp_media_c") is not None else (old.temp_media_c if old else None),
                    temp_max_c=row.get("temp_max_c") if row.get("temp_max_c") is not None else (old.temp_max_c if old else None),
                    etp_mm=old.etp_mm if old else None,
                ))
            self.records = records
        else:
            if not self.records:
                raise LutzError("Cargue primero precipitacion; una serie solo de temperatura no puede iniciar el modelo Lutz.")
            overlap = set(existing) & set(climate)
            if not overlap:
                raise LutzError("La temperatura GEE no coincide con el periodo de la serie Lutz cargada.")
            records = []
            for old in self.records:
                row = climate.get(old.fecha)
                if row is None:
                    records.append(old); continue
                records.append(replace(
                    old,
                    temp_min_c=row.get("temp_min_c") if row.get("temp_min_c") is not None else old.temp_min_c,
                    temp_media_c=row.get("temp_media_c") if row.get("temp_media_c") is not None else old.temp_media_c,
                    temp_max_c=row.get("temp_max_c") if row.get("temp_max_c") is not None else old.temp_max_c,
                ))
            self.records = records
        years = sorted({record.fecha.year for record in self.records})
        observed = sum(record.caudal_observado_m3s is not None for record in self.records)
        temperatures = sum(None not in (record.temp_min_c, record.temp_media_c, record.temp_max_c) for record in self.records)
        if has_temperature:
            self.etp_method.setCurrentIndex(2)
        if has_precipitation:
            self.precipitation_source_title = title
        if has_temperature:
            self.temperature_source_title = title
        combined_title = (
            f"P: {self.precipitation_source_title} | T: {self.temperature_source_title}"
        )
        self.file_status.setText(
            f"Serie activa desde GEE: {len(self.records)} meses, {years[0]}-{years[-1]}, "
            f"{observed} Q observados, {temperatures} meses con temperaturas. Fuente: {title}."
        )
        self._refresh_etp_preview(True)
        if self.split_combo.currentData() == "auto_60_40" and any(
            record.caudal_observado_m3s is not None for record in self.records
        ):
            self._apply_auto_split()
        integrated = [
            {
                "fecha": record.fecha.isoformat(),
                "precipitacion_mm": record.precipitacion_mm,
                "temp_min_c": record.temp_min_c,
                "temp_media_c": record.temp_media_c,
                "temp_max_c": record.temp_max_c,
                "fuente": combined_title,
                "metodo": "serie_integrada_modelo",
            }
            for record in self.records
        ]
        return {
            "message": (
                f"Serie aplicada: {len(self.records)} meses; se conservaron "
                f"{observed} caudales observados."
            ),
            "rows": integrated,
            "title": f"Serie activa del modelo | {combined_title}",
        }

    def _config_number(self, key, widget):
        value = self.project_input.config.get(key) if self.project_input else None
        if value not in (None, ""):
            try: widget.setValue(float(value))
            except (TypeError, ValueError): pass

    def _apply_workbook_config(self):
        if not self.project_input: return
        self._config_number("area_km2", self.area_spin); self._config_number("coef_escorrentia_c", self.c_spin)
        self._config_number("retencion_manual_mm", self.r_spin); self._config_number("k_manual", self.k_spin)
        self._config_number("a_dia_manual", self.a_spin); self._config_number("latitud_grados", self.latitude_spin)
        self._config_number("calibracion_inicio", self.cal_start); self._config_number("calibracion_fin", self.cal_end)
        self._config_number("validacion_inicio", self.val_start); self._config_number("validacion_fin", self.val_end)
        method = str(self.project_input.config.get("metodo_etp", "")).lower()
        if "hargreaves" in method: self.etp_method.setCurrentIndex(2)
        elif "import" in method: self.etp_method.setCurrentIndex(1)
        method_r = str(self.project_input.config.get("metodo_r", "manual")).lower()
        self.r_mode.setCurrentIndex(1 if "component" in method_r else (2 if "capa" in method_r else 0))
        if self.project_input.config.get("retencion_manual_mm") not in (None, "") and self.r_mode.currentIndex() == 0:
            self.r_excel_status.setText(
                f"R cargada desde Configuracion: {self.r_spin.value():.3f} mm/año."
            )
        method_k = str(self.project_input.config.get("metodo_k", "manual")).lower()
        self.k_mode.setCurrentIndex(1 if "criter" in method_k else (2 if "a_dia" in method_k else 0))
        region = str(self.project_input.config.get("region_abastecimiento", "manual")).lower()
        region_index = {"cusco": 1, "huancavelica": 2, "junin": 3, "cajamarca": 4, "ancash_santa": 5}.get(region, 0)
        self.region_combo.setCurrentIndex(region_index)
        if region_index: self._apply_region()
        if self.k_mode.currentIndex() == 1: self._recommend_k()

    def _apply_workbook_retention(self):
        rows = self.project_input.retention_rows if self.project_input else []
        if not rows:
            self.supply_status.setText("El Excel cargado no contiene 12 filas en Gasto_Abastecimiento.")
            return 0
        imported = 0
        for index, row in enumerate(rows[:12]):
            position = row.get("posicion_gasto", row.get("orden_gasto", 0))
            fraction = row.get("fraccion_abastecimiento", row.get("abastecimiento_pct", 0))
            try:
                fraction = float(fraction); fraction = fraction / 100 if abs(fraction) > 1 else fraction
                self.retention_table.item(index, 1).setText(str(float(position)))
                self.retention_table.item(index, 2).setText(f"{fraction:.8f}")
                imported += 1
            except (TypeError, ValueError):
                continue
        total = sum(float(self.retention_table.item(index, 2).text()) for index in range(12))
        self.supply_source_title = "hoja Gasto_Abastecimiento del Excel"
        self.supply_status.setText(
            f"Gasto_Abastecimiento importado desde Excel: {imported}/12 meses; "
            f"suma de abastecimiento = {total:.6f}."
        )
        return imported

    def _load_supply_from_excel(self):
        if not self._ensure_selected_excel_loaded("Abastecimiento"):
            return
        imported = self._apply_workbook_retention()
        if imported != 12:
            QMessageBox.warning(self, "Abastecimiento", self.supply_status.text())

    def _ensure_selected_excel_loaded(self, title):
        path = Path(self.input_edit.text().strip())
        resolved = str(path.resolve()) if path.is_file() else None
        if (
            self.project_input is not None
            and path.suffix.lower() == ".xlsx"
            and self.loaded_input_path == resolved
        ):
            return True
        if not path.is_file() or path.suffix.lower() != ".xlsx":
            QMessageBox.warning(
                self, title,
                "Seleccione primero un archivo XLSX en la pestaña Datos. "
                "Si la ruta ya está visible, no necesita cambiar de pestaña: vuelva a pulsar este botón."
            )
            return False
        self._load_input()
        return (
            self.project_input is not None
            and bool(self.records)
            and self.loaded_input_path == resolved
        )

    def _load_retention_from_excel(self):
        if not self._ensure_selected_excel_loaded("Retención"):
            return
        config = self.project_input.config
        configured_method = str(config.get("metodo_r", "manual")).lower()
        if "component" in configured_method:
            self.r_mode.setCurrentIndex(1)
            if self._apply_workbook_components() is None:
                QMessageBox.warning(
                    self, "Retención",
                    "El Excel indica Componentes_R, pero no contiene componentes válidos."
                )
            return

        raw_value = config.get("retencion_manual_mm")
        if raw_value not in (None, ""):
            try:
                value = float(raw_value)
                if not 0 <= value <= 10_000:
                    raise ValueError
            except (TypeError, ValueError):
                QMessageBox.warning(
                    self, "Retención",
                    "Retencion_Manual_mm debe ser un número entre 0 y 10000."
                )
                return
            self.r_spin.setValue(value)
            self.r_mode.setCurrentIndex(0)
            self.r_tabs.setCurrentIndex(0)
            self.r_excel_status.setText(
                f"R cargada desde Configuracion: {value:.3f} mm/año."
            )
            return

        if self.project_input.components:
            self.r_mode.setCurrentIndex(1)
            self._apply_workbook_components()
            return
        QMessageBox.warning(
            self, "Retención",
            "El Excel no contiene Retencion_Manual_mm ni filas válidas en Componentes_R."
        )

    def _apply_workbook_components(self):
        rows = self.project_input.components if self.project_input else []
        if not rows or self.r_mode.currentIndex() != 1: return None
        components = []
        for row in rows:
            active = str(row.get("activo", "si")).lower() not in ("no", "0", "false")
            components.append({"type": row.get("tipo", ""), "area_km2": row.get("area_km2", 0), "slope_fraction": row.get("pendiente_fraccion", None), "specific_depth_mm": row.get("lamina_manual_mm", None), "active": active})
        result = calculate_retention_components(self.area_spin.value(), components)
        self.r_spin.setValue(result["retention_mm"]); self.r_mode.setCurrentIndex(1)
        self.r_tabs.setCurrentIndex(0)
        self.spatial_status.setPlainText(f"R desde Componentes_R: {result['retention_mm']:.4f} mm/año")
        self.r_excel_status.setText(
            f"R calculada desde Componentes_R: {result['retention_mm']:.4f} mm/año."
        )
        return result

    def _etp_records(self, strict=True):
        if self.etp_method.currentIndex() == 2:
            return apply_hargreaves(self.records, self.latitude_spin.value())
        if self.etp_method.currentIndex() == 1 and any(record.etp_mm is None for record in self.records):
            if strict:
                raise LutzError("La ETP importada tiene meses vacios.")
        return list(self.records)

    def _etp_text(self, value, decimals=3):
        if value is None:
            return ""
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return ""

    def _populate_etp_tables(self, records):
        summary = summarize_etp(records)
        monthly = summary["mensual"]
        self.etp_monthly_table.setRowCount(len(monthly))
        monthly_fields = ("fecha", "temp_min_c", "temp_media_c", "temp_max_c", "etp_mm")
        for row_index, row in enumerate(monthly):
            for column, field in enumerate(monthly_fields):
                value = row[field] if field == "fecha" else self._etp_text(row[field])
                self.etp_monthly_table.setItem(row_index, column, QTableWidgetItem(str(value)))

        annual = summary["anual"]
        self.etp_annual_table.setRowCount(len(annual))
        annual_fields = ("anio", "meses_validos", "etp_total_mm", "etp_media_mensual_mm")
        for row_index, row in enumerate(annual):
            for column, field in enumerate(annual_fields):
                value = row[field] if field in ("anio", "meses_validos") else self._etp_text(row[field], 2)
                item = QTableWidgetItem(str(value))
                if field == "meses_validos" and int(row[field]) != 12:
                    item.setToolTip("El total anual es parcial porque faltan meses con ETP.")
                self.etp_annual_table.setItem(row_index, column, item)

    def _refresh_etp_preview(self, silent=True):
        if not hasattr(self, "etp_monthly_table"):
            return
        if not self.records:
            self.etp_monthly_table.setRowCount(0); self.etp_annual_table.setRowCount(0)
            self.etp_status.setText("Cargue una serie mensual para calcular o mostrar ETP.")
            return
        try:
            records = self._etp_records(strict=not silent)
            if self.etp_method.currentIndex() == 2:
                self.records = records
                total = sum(record.etp_mm for record in records if record.etp_mm is not None)
                self.etp_status.setText(
                    f"Hargreaves calculado para {len(records)} meses; ET0 total = {total:.2f} mm."
                )
            elif self.etp_method.currentIndex() == 1:
                valid = sum(record.etp_mm is not None for record in records)
                self.etp_status.setText(f"ETP importada disponible en {valid} de {len(records)} meses.")
            else:
                self.etp_status.setText("ETP desactivada; los cuadros muestran los valores disponibles sin aplicarlos.")
            self._populate_etp_tables(records)
        except Exception as error:
            self._populate_etp_tables(self.records)
            self.etp_status.setText(str(error))
            if not silent:
                QMessageBox.warning(self, "Evapotranspiracion", str(error))

    def _prepared_records(self):
        records = self._etp_records(strict=True)
        if self.etp_method.currentIndex() == 2:
            self.records = records
            total = sum(record.etp_mm for record in records if record.etp_mm is not None)
            self.etp_status.setText(f"Hargreaves calculado: ET0 total de la serie = {total:.2f} mm.")
        self._populate_etp_tables(records)
        return records

    def _latitude_from_basin(self):
        layer = self.basin_combo.currentLayer() or self.iface.activeLayer()
        if layer is None: QMessageBox.warning(self, "Latitud", "Seleccione primero la capa de cuenca."); return
        try:
            from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry
            geometries = [f.geometry() for f in layer.getFeatures() if f.hasGeometry()]
            centroid = QgsGeometry.unaryUnion(geometries).centroid().asPoint()
            transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            self.latitude_spin.setValue(transform.transform(centroid).y())
        except Exception as error: QMessageBox.warning(self, "Latitud", str(error))

    def _import_layer(self, combo, kind, name):
        if kind == "raster":
            path, _ = QFileDialog.getOpenFileName(self, f"Importar {name}", "", "Raster (*.tif *.tiff *.img);;Todos (*.*)")
            if not path: return
            layer = QgsRasterLayer(path, Path(path).stem)
            if not layer.isValid(): QMessageBox.warning(self, "Capa invalida", "No se pudo abrir el raster."); return
            QgsProject.instance().addMapLayer(layer)
        else:
            path, _ = QFileDialog.getOpenFileName(self, f"Importar {name}", "", "Vector (*.shp *.gpkg *.geojson);;Todos (*.*)")
            if not path: return
            try: layer = load_polygon_layer(path, Path(path).stem)
            except Exception as error: QMessageBox.warning(self, "Capa invalida", str(error)); return
        combo.setLayer(layer)

    def _calculate_spatial_retention(self):
        try:
            basin = self.basin_combo.currentLayer()
            for label, layer in (
                ("nevados/glaciares", self.snow_combo.currentLayer()),
                ("lagunas/pantanos", self.lagoon_combo.currentLayer()),
                ("acuíferos", self.aquifer_combo.currentLayer()),
            ):
                if basin is not None and layer is not None and layer.id() == basin.id():
                    raise LutzError(
                        f"La capa de cuenca también está seleccionada como {label}. "
                        "Elija la capa temática correcta o deje ese componente vacío."
                    )
            result = calculate_from_layers(
                basin, self.snow_combo.currentLayer(), self.lagoon_combo.currentLayer(),
                self.aquifer_combo.currentLayer(),
                None if self.manual_slope_check.isChecked() else self.dem_combo.currentLayer(),
                self.aquifer_slope.value() if self.manual_slope_check.isChecked() else None,
            )
            self.area_spin.setValue(result["area_basin_km2"]); self.r_spin.setValue(result["retention_mm"]); self.r_mode.setCurrentIndex(2)
            self.r_excel_status.setText(
                "La R activa proviene de capas QGIS. Pulse «Cargar R del Excel» para restaurar el valor del libro."
            )
            lines = [f"Área de cuenca: {result['area_basin_km2']:.3f} km2", f"R calculada: {result['retention_mm']:.5f} mm/año", f"Volumen: {result['volume_total_mmc']:.5f} MMC"]
            lines += [f"{item['type']}: {item['area_km2']:.4f} km2; aporte {item['basin_contribution_mm']:.4f} mm" for item in result["components"]]
            lines.append(f"CRS de cuenca: {result.get('basin_crs', 'N/D')}")
            if result.get("aquifer_slope_mode"):
                lines.append(f"Pendiente de acuiferos: modo {result['aquifer_slope_mode']}")
            if result.get("slope_working_crs"):
                lines.append(
                    f"CRS de pendiente: {result['slope_working_crs']}; "
                    f"pixeles validos: {result.get('slope_pixel_count', 0)}"
                )
            lines += ["ADVERTENCIA: " + value for value in result["warnings"]]
            self.spatial_status.setPlainText("\n".join(lines))
        except Exception as error: QMessageBox.critical(self, "Retencion espacial", str(error))

    def _mean_temperature(self):
        values = [record.temp_media_c for record in self.records if record.temp_media_c is not None]
        return sum(values) / len(values) if values else None

    def _recommend_k(self):
        try:
            result = select_k_by_criteria(self.area_spin.value(), self.r_spin.value(), self._mean_temperature(), self.recession_combo.currentText(), self.cover_combo.currentText(), self.storage_combo.currentText())
            self.k_spin.setValue(result["K"]); self.a_spin.setValue(result["a_day"]); self.k_mode.setCurrentIndex(1)
            self.k_status.setText(f"{result['option']}: K={result['K']:.3f}; a={result['a_day']:.6f}; confianza {result['confidence']}. {result['justification']}")
        except Exception as error: QMessageBox.warning(self, "Seleccion de K", str(error))

    def _apply_region(self):
        if self.region_combo.currentIndex() == 0: return
        keys = ("cusco", "huancavelica", "junin", "cajamarca", "ancash_santa")
        result = regional_supply(keys[self.region_combo.currentIndex()-1])
        for row, fraction in enumerate(result["fractions"]): self.retention_table.item(row, 2).setText(f"{fraction:.8f}")
        self.supply_source_title = f"patrón regional {result['region']}"
        self.supply_status.setText(f"Patron {result['region']} aplicado. {result['warning']}")

    def _estimate_runoff_coefficient(self):
        if not self.records:
            QMessageBox.warning(self, "Coeficiente C", "Cargue primero la serie mensual.")
            return
        try:
            years = (self.cal_start.value(), self.cal_end.value())
            method = self.c_method_combo.currentIndex()
            if method == 0:
                result = estimate_c_turc(self.records, years)
            elif method == 1:
                climate_records = apply_hargreaves(self.records, self.latitude_spin.value())
                result = estimate_c_southern_region(climate_records, years)
            else:
                result = estimate_c_observed(self.records, years, self.area_spin.value())
            self.c_estimate_result = result
            self.c_estimate_p.setText(f"{result['precipitation_annual_mm']:.3f}")
            if result.get("temperature_annual_c") is not None:
                climate = f"T = {result['temperature_annual_c']:.3f} C"
            elif result.get("etp_annual_mm") is not None:
                climate = f"ETP = {result['etp_annual_mm']:.3f} mm/año"
            else:
                climate = f"Q = {result.get('runoff_observed_mm', 0):.3f} mm/año"
            self.c_estimate_climate.setText(climate)
            self.c_estimate_deficit.setText(f"{result['deficit_mm']:.3f}")
            self.c_estimate_value.setText(f"{result['coefficient']:.5f}")
            self.c_estimate_status.setText(
                f"{result['method']}: {result['complete_years']} años completos. {result['warning']}"
            )
        except Exception as error:
            self.c_estimate_result = None
            QMessageBox.warning(self, "Coeficiente C", str(error))

    def _use_estimated_c(self):
        if not self.c_estimate_result:
            QMessageBox.warning(self, "Coeficiente C", "Calcule primero un valor de C.")
            return
        value = float(self.c_estimate_result["coefficient"])
        lower = max(0.0, value - 0.05)
        upper = min(1.0, value + 0.05)
        if upper - lower < 0.01:
            lower = max(0.0, value - 0.01)
            upper = min(1.0, value + 0.01)
        self.c_spin.setValue(value)
        self.c_min.setValue(lower)
        self.c_max.setValue(upper)
        self.calibrate_c_check.setChecked(True)
        self.auto_calibrate.setChecked(True)
        self.c_estimate_status.setText(
            f"C inicial={value:.5f}; limites de calibracion [{lower:.5f}, {upper:.5f}]. "
            + self.c_estimate_result["warning"]
        )

    def _retention_config(self):
        positions, supply = [], []
        for row in range(12):
            try:
                positions.append(float(self.retention_table.item(row, 1).text().replace(",", ".")))
                supply.append(float(self.retention_table.item(row, 2).text().replace(",", ".")))
            except (AttributeError, ValueError) as error: raise LutzError(f"Valor invalido en abastecimiento, fila {row+1}.") from error
        if any(value < 0 for value in supply):
            raise LutzError("Las fracciones de abastecimiento no pueden ser negativas.")
        total = sum(supply)
        if total <= 0:
            raise LutzError("La suma de las fracciones de abastecimiento debe ser mayor que cero.")
        if abs(total - 1.0) > 1e-9:
            supply = [value / total for value in supply]
            for row, value in enumerate(supply):
                self.retention_table.setItem(row, 2, QTableWidgetItem(f"{value:.8f}"))
            self.supply_status.setText(
                f"Las fracciones ingresadas sumaban {total:.6f}; fueron normalizadas automaticamente a 1.000000."
            )
        else:
            self.supply_status.setText("Fracciones verificadas: suma = 1.000000.")
        return RetentionConfig(tuple(positions), tuple(supply))

    def _base_parameters(self):
        balance_mode = self.negative_balance_combo.currentData()
        if self.k_mode.currentIndex() == 2:
            return ModelParameters(
                self.area_spin.value(), self.c_spin.value(), self.r_spin.value(),
                a_dia=self.a_spin.value(), compatible_matlab=self.matlab_check.isChecked(),
                negative_balance_mode=balance_mode,
            )
        return ModelParameters(
            self.area_spin.value(), self.c_spin.value(), self.r_spin.value(),
            k=self.k_spin.value(), compatible_matlab=self.matlab_check.isChecked(),
            negative_balance_mode=balance_mode,
        )

    def _validated_periods(self, records):
        calibration = (self.cal_start.value(), self.cal_end.value())
        if calibration[0] > calibration[1]:
            raise LutzError("El inicio de calibracion debe ser anterior o igual al final.")
        validation = None
        if self.validation_check.isChecked():
            validation = (self.val_start.value(), self.val_end.value())
            if validation[0] > validation[1]:
                raise LutzError("El inicio de validacion debe ser anterior o igual al final.")
            if not (validation[0] > calibration[1] or validation[1] < calibration[0]):
                raise LutzError("Los periodos de calibracion y validacion no deben superponerse.")
            observed = [
                record for record in records
                if validation[0] <= record.fecha.year <= validation[1]
                and record.caudal_observado_m3s is not None
            ]
            if len(observed) < 3:
                raise LutzError(
                    "La validacion requiere al menos 3 caudales observados dentro de su periodo. "
                    "Desactive 'Calcular validacion' si solo desea simular."
                )
        return calibration, validation

    def _create_run_folder(self, base_output):
        base = Path(base_output)
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = base / f"corrida_{stamp}"
        counter = 2
        while folder.exists():
            folder = base / f"corrida_{stamp}_{counter:02d}"
            counter += 1
        folder.mkdir(parents=True)
        return folder

    def _run_metadata(self, records, calibration_period, validation_period, run_folder):
        temperatures = [record.temp_media_c for record in records if record.temp_media_c is not None]
        precipitation = [record.precipitacion_mm for record in records if record.precipitacion_mm is not None]
        automatic_split = self.split_combo.currentData() == "auto_60_40" and self.period_split_info
        spatial_retention = self.r_mode.currentIndex() == 2
        return {
            "run_id": run_folder.name,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "plugin_version": PLUGIN_VERSION,
            "scenario": self.scenario_edit.text().strip() or "sin_nombre",
            "calibration_mode": "automatica" if self.auto_calibrate.isChecked() else "manual",
            "split_method": self.period_split_info["method"] if automatic_split else "manual",
            "calibration_period": list(calibration_period),
            "validation_period": list(validation_period) if validation_period else None,
            "validation_recalibrates": False,
            "input_file": self.input_edit.text().strip(),
            "precipitation_source": self.precipitation_source_title,
            "temperature_source": self.temperature_source_title,
            "etp_method": self.etp_method.currentText(),
            "supply_source": self.supply_source_title,
            "retention_source": self.r_mode.currentText(),
            "persistence_source": self.persistence_source_combo.currentData(),
            "flow_transfer_active": self.transfer_check.isChecked(),
            "spatial_layers": {
                "basin": self.basin_combo.currentLayer().name() if spatial_retention and self.basin_combo.currentLayer() else None,
                "snow_glacier": self.snow_combo.currentLayer().name() if spatial_retention and self.snow_combo.currentLayer() else None,
                "lagoons_wetlands": self.lagoon_combo.currentLayer().name() if spatial_retention and self.lagoon_combo.currentLayer() else None,
                "aquifers": self.aquifer_combo.currentLayer().name() if spatial_retention and self.aquifer_combo.currentLayer() else None,
                "dem": self.dem_combo.currentLayer().name() if spatial_retention and self.dem_combo.currentLayer() else None,
            },
            "climate_provenance": self.climate_provenance,
            "series_fingerprint": {
                "months": len(records),
                "first_month": records[0].fecha.isoformat() if records else None,
                "last_month": records[-1].fecha.isoformat() if records else None,
                "mean_precipitation_mm": sum(precipitation) / len(precipitation) if precipitation else None,
                "mean_temperature_c": sum(temperatures) / len(temperatures) if temperatures else None,
            },
        }

    def _run(self):
        if not self.records: self._load_input()
        if not self.records: return
        output = self.output_edit.text().strip()
        if not output: QMessageBox.warning(self, "Salida", "Seleccione una carpeta de resultados."); return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            records = self._prepared_records(); parameters = self._base_parameters(); retention = self._retention_config()
            calibration_period, validation = self._validated_periods(records)
            if self.auto_calibrate.isChecked():
                best = calibrate_parameters(
                    records, parameters, retention, calibration_period, validation,
                    (self.r_min.value(), self.r_max.value()),
                    (self.a_min.value(), self.a_max.value()),
                    (self.c_min.value(), self.c_max.value()),
                    self.grid_steps.value(), self.objective_combo.currentText(),
                    self.calibrate_c_check.isChecked(),
                )
                result = best["result"]
                self.c_spin.setValue(best["coefficient"])
                self.r_spin.setValue(best["retention_mm"]); self.a_spin.setValue(best["a_day"]); self.k_mode.setCurrentIndex(2)
                strict_index = self.negative_balance_combo.findData("strict")
                if strict_index >= 0:
                    self.negative_balance_combo.setCurrentIndex(strict_index)
                result["automatic_calibration"] = {
                    key: best[key] for key in (
                        "score", "coefficient", "initial_coefficient", "retention_mm",
                        "initial_retention_mm", "a_day", "initial_a_day", "trials",
                        "objective", "calibrated_c", "search_bounds", "steps_per_axis",
                        "refinements",
                        "physical_balance_enforced", "rejected_trials", "retention_limit_mm",
                    )
                }
                result["automatic_calibration"]["initial_metrics_calibration"] = (
                    best["initial_result"].get("metrics_calibration") if best["initial_result"] else None
                )
                result["automatic_calibration"]["initial_error"] = best.get("initial_error")
                result["automatic_calibration"]["final_metrics_calibration"] = result.get("metrics_calibration")
            else:
                result = run_model(records, parameters, retention, calibration_period, validation)
            if self.c_estimate_result:
                result["c_estimation"] = dict(self.c_estimate_result)
            self._apply_flow_analysis(result)
            run_folder = self._create_run_folder(output)
            result["run_metadata"] = self._run_metadata(records, calibration_period, validation, run_folder)
            self.last_outputs = write_results(result, str(run_folder))
            plots = create_diagnostic_plots(result, str(run_folder))
            pngs = export_panel_pngs(plots, str(run_folder))
            report_path = create_word_report(
                result, self.last_outputs, pngs, str(run_folder / "Informe_Tecnico_Lutz_Scholz.docx")
            )
            finalize_manifest(self.last_outputs["manifest"], result, plots, pngs, report_path)
            self.result_plot_paths = plots
            self.last_outputs.update({"grafico_"+key: value for key, value in plots.items()})
            self.last_outputs.update({"png_"+key: value for key, value in pngs.items()})
            self.last_outputs["informe_word"] = report_path
            self.last_report_path = report_path
            self.last_run_folder = str(run_folder)
            pointer = {
                "run_id": run_folder.name,
                "folder": str(run_folder),
                "created_at": result["run_metadata"]["created_at"],
            }
            (Path(output) / "ultima_corrida.json").write_text(
                json.dumps(pointer, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if plots.get("resumen"):
                self.svg_widgets["resumen"].load(plots["resumen"])
            self._set_result_period_options(bool(result.get("metrics_validation")))
            self._refresh_result_period()
            if self.add_table_check.isChecked(): self._add_results_table(self.last_outputs["series"])
            self.summary.setPlainText(self._format_summary(result, self.last_outputs)); self.tabs.setCurrentIndex(8)
            QMessageBox.information(
                self, "Modelo finalizado",
                "Cálculo, indicadores, gráficos e informe Word generados.\n\n"
                f"Identificador de modelación: {_display_modeling_id(run_folder.name)}\n{run_folder}"
            )
        except Exception as error: QMessageBox.critical(self, "No se pudo ejecutar", str(error))
        finally: QApplication.restoreOverrideCursor()

    def _add_results_table(self, path):
        uri = QUrl.fromLocalFile(path).toString() + "?type=csv&delimiter=,&detectTypes=yes&geomType=none"
        layer = QgsVectorLayer(uri, "Lutz Scholz - resultados mensuales", "delimitedtext")
        if layer.isValid(): QgsProject.instance().addMapLayer(layer)

    def _format_summary(self, result, outputs):
        metadata = result.get("run_metadata", {})
        lines = [f"MODELO LUTZ SCHOLZ v{PLUGIN_SERIES}", "="*42,
                 f"Identificador de modelación: {_display_modeling_id(metadata.get('run_id'))}",
                 f"Modalidad: {_display_mode(metadata.get('calibration_mode'))}",
                 f"División temporal: {_display_split(metadata.get('split_method'))}",
                 "Validación: utiliza los parámetros calibrados sin reajustarlos",
                 f"Área: {result['parameters']['area_km2']:.3f} km2", f"C: {result['parameters']['coef_escorrentia']:.5f}", f"R: {result['parameters']['retencion_mm']:.3f} mm/año", f"a: {result['parameters']['a_dia']:.6f} 1/día"]
        balance = result.get("balance_diagnostics") or {}
        if balance.get("annual_balance_modified"):
            lines += [
                "", "ADVERTENCIA DE BALANCE",
                f"  Meses recortados: {balance.get('clipped_months', 0)}",
                f"  Lamina ajustada: {balance.get('clipped_total_mm', 0.0):.4f} mm",
            ]
            limit = balance.get("retention_nonnegative_limit") or {}
            if limit:
                lines.append(
                    f"  R maximo estimado sin negativos: {limit.get('retencion_maxima_mm', 0.0):.3f} mm "
                    f"(mes limitante: {limit.get('mes_limitante', 'N/D')})"
                )
        stats = result.get("precipitation_statistics") or {}
        if stats:
            lines += ["", "PRECIPITACION DESCRIPTIVA", f"  Media anual: {stats['media_anual_mm']:.2f} mm", f"  Desviacion anual: {stats['desviacion_anual_mm']:.2f} mm", f"  CV anual: {stats['cv_anual_porcentaje']:.2f}%", f"  Min/Max anual: {stats['min_anual_mm']:.2f} / {stats['max_anual_mm']:.2f} mm"]
        auto = result.get("automatic_calibration")
        if auto:
            lines += [
                f"Calibracion automatica: {auto['trials']} evaluaciones",
                f"Objetivo {auto['objective']}: {auto['score']:.5f}",
                f"C inicial/calibrado: {auto['initial_coefficient']:.5f} / {auto['coefficient']:.5f}",
            ]
            initial_metrics = auto.get("initial_metrics_calibration") or {}
            final_metrics = auto.get("final_metrics_calibration") or {}
            if initial_metrics.get("PBIAS_porcentaje") is not None and final_metrics.get("PBIAS_porcentaje") is not None:
                lines.append(
                    f"PBIAS inicial/final: {initial_metrics['PBIAS_porcentaje']:.3f}% / "
                    f"{final_metrics['PBIAS_porcentaje']:.3f}%"
                )
        for label, key in (("CALIBRACION", "calibration"), ("VALIDACION INDEPENDIENTE", "validation")):
            scales = result.get("diagnostics", {}).get(key, {})
            if scales:
                lines += ["", label]
                for scale in ("monthly", "annual", "regime"):
                    values = scales.get(scale)
                    if values:
                        lines.append(f"  Escala {scale} (Q observado vs Q simulado):")
                        lines += [f"    {name}: {'N/D' if value is None else f'{value:.6f}'}" for name, value in values.items()]
        permanence = (result.get("flow_persistence") or {}).get("complete") or {}
        if permanence:
            selected = permanence.get("selected_origin", "simulado")
            lines += ["", "PERMANENCIA DE CAUDALES", f"  Fuente seleccionada: {selected}"]
            for origin, label in (("simulado", "Simulado"), ("observado", "Observado"), ("transferido", "Transferido")):
                values = permanence.get(origin) or {}
                if values.get("n"):
                    lines.append(
                        f"  {label}: Q75={values.get('Q75_m3s', 0):.3f} m3/s; "
                        f"Q95={values.get('Q95_m3s', 0):.3f} m3/s"
                    )
            lines.append("  Q75/Q95 son estadísticas hidrológicas; no equivalen por sí solas a un caudal ecológico aprobado.")
        lines += ["", "ARCHIVOS"] + [f"  {name}: {path}" for name, path in outputs.items()]
        return "\n".join(lines)

    def _open_output(self):
        path = self.last_run_folder or self.output_edit.text().strip()
        if path and Path(path).exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_report(self):
        path = self.last_report_path
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, "Informe técnico", "Ejecute primero el modelo para generar el informe Word.")
