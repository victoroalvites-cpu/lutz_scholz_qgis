from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .dialog import LutzScholzDialog


class LutzScholzPlugin:
    """Integra el dialogo principal con la interfaz de QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.menu_name = self.tr("&Lutz Sholtz")

    def tr(self, text):
        return QCoreApplication.translate("LutzScholzPlugin", text)

    def initGui(self):
        self.action = QAction(
            QIcon(str(Path(__file__).with_name("icon.png"))),
            self.tr("Modelo Lutz Sholtz"),
            self.iface.mainWindow(),
        )
        self.action.setObjectName("lutzScholzModelAction")
        self.action.setToolTip(self.tr("Calcular caudales mensuales con Lutz Sholtz"))
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.menu_name, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(self.menu_name, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None

    def run(self):
        if self.dialog is None:
            self.dialog = LutzScholzDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
