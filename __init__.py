"""Punto de entrada del complemento Lutz Scholz para QGIS."""


def classFactory(iface):
    from .plugin import LutzScholzPlugin

    return LutzScholzPlugin(iface)
