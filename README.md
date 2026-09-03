# Lutz Sholtz para QGIS

Complemento para la simulación hidrológica mensual mediante el método de
Lutz Sholtz. La edición pública estable `0.2.2` integra preparación de datos,
calibración, validación independiente, diagnóstico gráfico y reporte técnico
en una sola interfaz de QGIS.

## Funciones principales

- importa series mensuales desde CSV o desde la plantilla Excel incluida;
- reconoce caudales observados y propone una división cronológica 60/40 por
  años completos;
- calibra en el primer periodo y valida en el segundo con los mismos
  parámetros, sin volver a ajustarlos;
- descarta durante la calibración cualquier combinación que produzca láminas
  mensuales negativas y optimiza las métricas únicamente entre soluciones
  físicamente válidas;
- permite ejecución manual con C, R, a y abastecimiento visibles;
- calcula ETP por Hargreaves-Samani, retención espacial y patrones regionales;
- obtiene, de forma opcional, precipitación o temperatura desde Earth Engine;
- muestra diagnósticos mensual, anual, multimensual y de dispersión;
- informa NSE, LogNSE, KGE, correlación, RMSE, MAD, PBIAS y el criterio de
  Schultz como diagnóstico complementario;
- permite transponer opcionalmente el caudal simulado final de Lutz Sholtz
  mediante `Qs = (As/Ac) (Ps/Pc) Qc`; el usuario solo proporciona el área y la
  precipitación mensual de la cuenca objetivo;
- calcula el régimen multimensual y las persistencias mensuales Q10, Q25, Q50,
  Q75, Q90 y Q95, además de la referencia mensual del 15 % a
  partir de la serie simulada, observada o transferida que el usuario seleccione,
  sin obligar a realizar una transposición;
- exporta CSV, JSON, gráficos PNG/SVG y un informe técnico Word por modelación.

## Configuración del proyecto

En la parte superior del diálogo seleccione una **Carpeta principal** y pulse
**Aplicar rutas**. El complemento conserva esa elección en QGIS y crea una
estructura de trabajo reutilizable:

```text
Proyecto_Lutz/
├── 01_Datos_Entrada/
├── 02_Clima/
├── 03_Resultados/
├── 04_Documentacion/
└── proyecto_lutz_scholz.json
```

La carpeta de clima se propone al exportar la serie areal y la carpeta de
resultados se usa como destino predeterminado de las modelaciones. No se eliminan
ni sustituyen archivos existentes.

## Instalación

1. Descargue `Lutz_Sholtz_QGIS_v0.2.2.zip`.
2. En QGIS 3.40 o posterior abra **Complementos > Administrar e instalar
   complementos > Instalar desde ZIP**.
3. Seleccione el ZIP y confirme la instalación cuando QGIS lo solicite.
4. Abra **Complementos > Lutz Sholtz > Modelo Lutz Sholtz**.

El núcleo local del modelo funciona con las bibliotecas incluidas en QGIS.

## Integración opcional con Google Earth Engine

El paquete público no distribuye bibliotecas binarias ni credenciales. Para
usar la pestaña **Clima GEE**, instale `earthengine-api` en el entorno Python
de la misma instalación de QGIS, reinicie QGIS y autorice su cuenta. En una
consola OSGeo4W asociada a QGIS puede utilizar:

```text
python -m pip install earthengine-api
```

El campo **Proyecto Cloud** se deja vacío en instalaciones nuevas. Cada
usuario debe escribir un proyecto de Google Cloud propio o autorizado que
tenga Earth Engine habilitado. Los assets PISCO vienen propuestos mediante sus
rutas, pero solo podrán consultarse si su propietario ha concedido permiso de
lectura a la cuenta conectada o los ha publicado. ERA5-Land y CHIRPS son
fuentes públicas de Earth Engine. Si la API no está instalada, solamente se
deshabilitan las operaciones de Clima GEE; el modelo local continúa disponible.

## Datos de entrada

La carpeta `templates` contiene
`Plantilla_Entradas_Lutz_Scholz_QGIS_v0.1.xlsx`. Para CSV se admite, como
mínimo:

```text
fecha,precipitacion_mm,caudal_observado_m3s
1990-01-01,160.0,3.80
1990-02-01,190.0,6.70
```

La serie debe estar ordenada y mantener continuidad mensual. Para la división
automática solo se consideran años con 12 caudales observados válidos. Los
primeros años completos forman aproximadamente el 60 % de calibración y los
restantes el 40 % de validación.

En la pestaña **Retención**, el botón **Cargar R del Excel** permite restaurar
la retención después de un cálculo con capas. Si `Metodo_R` indica `manual`,
se recupera `Retencion_Manual_mm` de la hoja `Configuracion`; si indica
componentes, R se recalcula con la hoja `Componentes_R`. El archivo puede estar
ya cargado o simplemente seleccionado en la pestaña Datos.

## Resultados y trazabilidad

Cada modelación se guarda en una carpeta
`modelacion_AAAAMMDD_HHMMSS`. Incluye las
series, métricas, parámetros, procedencia de datos, gráficos individuales y
paneles en PNG/SVG, además de `Informe_Tecnico_Lutz_Sholtz.docx`. La validación
se informa por separado y nunca modifica los parámetros calibrados. El informe
Word conserva los valores iniciales y finales, los límites y la resolución de
la búsqueda automática, las combinaciones rechazadas por balance físico y la
comparación del ajuste antes y después de calibrar.

La búsqueda automática admite valores de agotamiento desde `a = 0.001 1/día`
para representar respuestas lentas. El punto inicial se evalúa como candidato y
se conserva cuando ninguna combinación de la malla mejora la función objetivo;
por ello, la calibración automática nunca degrada deliberadamente el ajuste de
partida.

En el informe y en los archivos de salida se utiliza terminología técnica
legible: «modelación», «calibración automática», «Escenario base» y
«Cronológica 60/40». Las modelaciones antiguas que ya tengan nombres
`corrida_*` continúan siendo válidas y no se renombran automáticamente.

La convención de sesgo utilizada es
`PBIAS = 100 * suma(Qsim - Qobs) / suma(Qobs)`: un valor negativo representa
subestimación global y uno positivo, sobreestimación. Las conclusiones separan
el desempeño general, los caudales bajos y el comportamiento de caudales altos.

La pestaña **Permanencia** permite activar o dejar desactivada la transposición
hidrológica. La cuenca modelada en Lutz Sholtz actúa automáticamente como fuente:
`Qc` es su caudal simulado final, `Pc` su precipitación y `Ac` su área. El usuario
solo ingresa la precipitación media anual `Ps` en mm/año y el área `As` de la
cuenca objetivo. No se requiere otro Excel. El factor fijo
`(As/Ac) × (Ps/Pc)` se aplica a cada caudal mensual `Qc`, y el complemento
registra la serie resultante en `datos/transposicion_caudales.csv`.

La pestaña **Resultados** muestra la serie seleccionada como matriz año por mes.
También se exportan `matriz_caudales_simulados.csv`,
`matriz_caudales_observados.csv` y, cuando corresponde,
`matriz_caudales_transferidos.csv`.

El archivo `datos/permanencia_caudales.csv`, la vista gráfica y el informe Word
presentan Q10, Q25, Q50, Q75, Q90 y Q95 mediante posiciones de trazado de
Weibull `P = m/(n+1)`, además de una referencia equivalente al 15 % del caudal
medio mensual de la fuente seleccionada. Q75 es el caudal igualado o excedido
el 75 % del tiempo; estas persistencias son estadísticas hidrológicas. La
referencia del 15 % se incluye como apoyo para el método hidrológico-hidráulico
del Anexo I de la Resolución Jefatural N.° 267-2019-ANA; ninguno de estos valores
constituye por sí solo un caudal ecológico aprobado. El método aplicable debe
definirse según la categoría del proyecto y coordinarse o aprobarse con la
Autoridad Administrativa del Agua competente.

Las columnas Q95 y 15 % del caudal medio se exportan además en
`datos/referencias_caudal_ecologico.csv`, manteniendo explícitamente su carácter
referencial y no aprobatorio.

El recorte controlado se conserva únicamente como alternativa manual
exploratoria. Cuando se utiliza, el informe principal presenta una nota breve
y traslada el detalle mensual al anexo de trazabilidad.

## Soporte y licencia

- Código fuente: <https://github.com/victoroalvites-cpu/lutz_scholz_qgis>
- Incidencias: <https://github.com/victoroalvites-cpu/lutz_scholz_qgis/issues>
- Licencia: GNU General Public License v3 o posterior; consulte `LICENSE`.

Autor: Victor Olivos.
