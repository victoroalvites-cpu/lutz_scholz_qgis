"""Punto de entrada del complemento Lutz Sholtz para QGIS."""


def classFactory(iface):
    from .plugin import LutzScholzPlugin

    return LutzScholzPlugin(iface)
