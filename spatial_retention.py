"""Puente QGIS para medir componentes espaciales de retencion."""

from __future__ import annotations

from qgis.core import (
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .core import LutzError, calculate_retention_components


def load_polygon_layer(path, name):
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid() or QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
        raise LutzError(f"La capa {name} no es una capa poligonal valida.")
    QgsProject.instance().addMapLayer(layer)
    return layer


def _union(layer):
    geometries = [feature.geometry().makeValid() for feature in layer.getFeatures() if feature.hasGeometry()]
    if not geometries:
        raise LutzError(f"La capa {layer.name()} no contiene geometria.")
    return QgsGeometry.unaryUnion(geometries)


def _transform_geometry(geometry, source_crs, destination_crs):
    output = QgsGeometry(geometry)
    if source_crs != destination_crs:
        transform = QgsCoordinateTransform(
            source_crs, destination_crs, QgsProject.instance().transformContext()
        )
        output.transform(transform)
    return output


def _measure_km2(geometry, crs):
    distance = QgsDistanceArea()
    distance.setSourceCrs(crs, QgsProject.instance().transformContext())
    distance.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
    square_metres = distance.measureArea(geometry)
    return distance.convertAreaMeasurement(square_metres, QgsUnitTypes.AreaSquareKilometers)


def basin_area_km2(basin_layer):
    return _measure_km2(_union(basin_layer), basin_layer.crs())


def intersection_area_km2(component_layer, basin_layer):
    basin_geometry = _union(basin_layer)
    parts = []
    for feature in component_layer.getFeatures():
        if not feature.hasGeometry():
            continue
        geometry = _transform_geometry(
            feature.geometry().makeValid(), component_layer.crs(), basin_layer.crs()
        )
        clipped = geometry.intersection(basin_geometry)
        if not clipped.isEmpty():
            parts.append(clipped)
    if not parts:
        return 0.0
    return _measure_km2(QgsGeometry.unaryUnion(parts), basin_layer.crs())


def _aquifer_slope_diagnostics(aquifer_layer, basin_layer, dem_layer):
    """Calcula la pendiente en el CRS del MDE y devuelve trazabilidad."""
    try:
        import processing
    except ImportError as error:
        raise LutzError("No se pudo cargar el proveedor Processing de QGIS.") from error
    if dem_layer is None or not dem_layer.isValid():
        raise LutzError("Seleccione un MDE valido para calcular la pendiente de acuiferos.")
    if not dem_layer.crs().isValid():
        raise LutzError("El MDE no tiene un CRS valido definido.")
    basin_geometry = _union(basin_layer)
    intersection_parts = []
    for feature in aquifer_layer.getFeatures():
        if not feature.hasGeometry():
            continue
        geometry = _transform_geometry(
            feature.geometry().makeValid(), aquifer_layer.crs(), basin_layer.crs()
        )
        clipped = geometry.intersection(basin_geometry)
        if not clipped.isEmpty():
            intersection_parts.append(clipped)
    if not intersection_parts:
        return {"slope_fraction": 0.0, "pixel_count": 0, "working_crs": dem_layer.crs().authid()}

    clipped_basin_crs = QgsGeometry.unaryUnion(intersection_parts)
    clipped_dem_crs = _transform_geometry(
        clipped_basin_crs, basin_layer.crs(), dem_layer.crs()
    )
    if not clipped_dem_crs.boundingBox().intersects(dem_layer.extent()):
        raise LutzError(
            "Los acuiferos no intersectan el MDE despues de transformar ambos al mismo CRS. "
            "Revise el CRS asignado a las capas, no solo su apariencia en el lienzo de QGIS."
        )

    crs_token = dem_layer.crs().authid() or dem_layer.crs().toWkt()
    memory = QgsVectorLayer(f"Polygon?crs={crs_token}", "acuiferos_recortados_mde", "memory")
    if not memory.isValid():
        raise LutzError("No se pudo crear la mascara temporal en el CRS del MDE.")
    feature = QgsFeature(); feature.setGeometry(clipped_dem_crs)
    memory.dataProvider().addFeature(feature); memory.updateExtents()
    slope = processing.run("gdal:slope", {
        "INPUT": dem_layer, "BAND": 1, "SCALE": 1.0, "AS_PERCENT": True,
        "COMPUTE_EDGES": True, "ZEVENBERGEN": False, "OPTIONS": "", "EXTRA": "",
        "OUTPUT": "TEMPORARY_OUTPUT",
    })["OUTPUT"]
    zonal = processing.run("native:zonalstatisticsfb", {
        "INPUT": memory, "INPUT_RASTER": slope, "RASTER_BAND": 1,
        "COLUMN_PREFIX": "pend_", "STATISTICS": [0, 2], "OUTPUT": "TEMPORARY_OUTPUT",
    })["OUTPUT"]
    features = list(zonal.getFeatures())
    values = [item["pend_mean"] for item in features if item["pend_mean"] is not None]
    if not values:
        raise LutzError(
            "No fue posible obtener píxeles válidos de pendiente sobre los acuíferos. "
            "Revise NoData, CRS y resolución del MDE. Como alternativa, active "
            "'Usar pendiente manual de acuíferos' e ingrese una pendiente media justificable."
        )
    pixel_count = sum(int(item["pend_count"] or 0) for item in features)
    return {
        "slope_fraction": float(sum(values) / len(values)) / 100.0,
        "pixel_count": pixel_count,
        "working_crs": dem_layer.crs().authid() or "CRS personalizado",
    }


def mean_aquifer_slope_fraction(aquifer_layer, basin_layer, dem_layer):
    """Compatibilidad: devuelve solo la pendiente media como fraccion."""

    return _aquifer_slope_diagnostics(aquifer_layer, basin_layer, dem_layer)["slope_fraction"]


def calculate_from_layers(basin_layer, snow_layer=None, lagoon_layer=None, aquifer_layer=None,
                          dem_layer=None, manual_aquifer_slope_fraction=None):
    if basin_layer is None:
        raise LutzError("Seleccione la capa de cuenca.")
    area_basin = basin_area_km2(basin_layer)
    components = []
    missing = []
    if snow_layer is None:
        missing.append("nevados/glaciares")
    else:
        components.append({"type": "nevados", "area_km2": intersection_area_km2(snow_layer, basin_layer)})
    if lagoon_layer is None:
        missing.append("lagunas/pantanos")
    else:
        components.append({"type": "lagunas_pantanos", "area_km2": intersection_area_km2(lagoon_layer, basin_layer)})
    if aquifer_layer is None:
        missing.append("acuiferos")
    else:
        slope_diagnostics = None
        # La eleccion manual tiene prioridad absoluta aunque exista un MDE
        # seleccionado en el proyecto.
        if manual_aquifer_slope_fraction is not None:
            slope = float(manual_aquifer_slope_fraction)
            slope_mode = "manual"
        elif dem_layer is not None:
            slope_diagnostics = _aquifer_slope_diagnostics(aquifer_layer, basin_layer, dem_layer)
            slope = slope_diagnostics["slope_fraction"]
            slope_mode = "MDE"
        else:
            slope = None
            slope_mode = "sin definir"
        if slope is None:
            raise LutzError("Para acuiferos seleccione un MDE o indique la pendiente media manual.")
        components.append({"type": "acuiferos", "area_km2": intersection_area_km2(aquifer_layer, basin_layer), "slope_fraction": slope})
    result = calculate_retention_components(area_basin, components)
    result["area_basin_km2"] = area_basin
    result["basin_crs"] = basin_layer.crs().authid() or "CRS personalizado"
    if aquifer_layer is not None:
        result["aquifer_slope_mode"] = slope_mode
        if slope_diagnostics:
            result["slope_working_crs"] = slope_diagnostics["working_crs"]
            result["slope_pixel_count"] = slope_diagnostics["pixel_count"]
    if missing:
        result["warnings"].append("Capas no proporcionadas tratadas con area 0: " + ", ".join(missing) + ".")
    return result
