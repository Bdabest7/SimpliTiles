import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterExtent,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterFileDestination,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
)


class SimpliTilesAlgorithm(QgsProcessingAlgorithm):
    INPUT_RASTER = 'INPUT_RASTER'
    EXTENT = 'EXTENT'
    ZOOM_MIN = 'ZOOM_MIN'
    ZOOM_MAX = 'ZOOM_MAX'
    TILE_SIZE = 'TILE_SIZE'
    NUM_WORKERS = 'NUM_WORKERS'
    OUTPUT_DIR = 'OUTPUT_DIR'
    OUTPUT_HTML = 'OUTPUT_HTML'

    def name(self):
        return 'generatexyztiles'

    def displayName(self):
        return 'Generate XYZ Tiles (SimpliTiles)'

    def group(self):
        return 'Raster Tools'

    def groupId(self):
        return 'rastertools'

    def shortHelpString(self):
        return (
            'Generates XYZ map tiles from a raster layer using direct GDAL reads, '
            'bypassing the QGIS rendering pipeline.\n\n'
            'Automatically selects the optimal pipeline:\n'
            '  - Tiled GeoTIFF: multiprocessing (parallel GDAL reads)\n'
            '  - Striped / JPEG: single reader + parallel PNG writes\n\n'
            'Outputs a Leaflet HTML preview for quick QA.'
        )

    def createInstance(self):
        return SimpliTilesAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RASTER, 'Input raster layer',
        ))

        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, 'Extent', optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.ZOOM_MIN, 'Minimum zoom level',
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=12, minValue=0, maxValue=25,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.ZOOM_MAX, 'Maximum zoom level',
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=21, minValue=0, maxValue=25,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.TILE_SIZE, 'Tile size (pixels)',
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=256, minValue=64, maxValue=1024,
        ))

        default_workers = min(os.cpu_count() or 4, 32)
        self.addParameter(QgsProcessingParameterNumber(
            self.NUM_WORKERS, 'Parallel workers',
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=default_workers, minValue=1, maxValue=128,
        ))

        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_DIR, 'Output directory',
        ))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, 'Output HTML (Leaflet) [optional]',
            fileFilter='HTML files (*.html)',
            optional=True, createByDefault=True,
        ))

    def checkParameterValues(self, parameters, context):
        zoom_min = self.parameterAsInt(parameters, self.ZOOM_MIN, context)
        zoom_max = self.parameterAsInt(parameters, self.ZOOM_MAX, context)
        if zoom_max < zoom_min:
            return False, 'Maximum zoom must be >= minimum zoom'
        return True, ''

    def processAlgorithm(self, parameters, context, feedback):
        raster_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RASTER, context)
        if raster_layer is None:
            raise QgsProcessingException('Invalid input raster layer')

        input_path = raster_layer.source()
        zoom_min = self.parameterAsInt(parameters, self.ZOOM_MIN, context)
        zoom_max = self.parameterAsInt(parameters, self.ZOOM_MAX, context)
        tile_size = self.parameterAsInt(parameters, self.TILE_SIZE, context)
        num_workers = self.parameterAsInt(parameters, self.NUM_WORKERS, context)
        output_dir = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        output_html = self.parameterAsString(parameters, self.OUTPUT_HTML, context)

        crs_wkt = raster_layer.crs().toWkt()

        extent_wgs84 = None
        extent_param = parameters.get(self.EXTENT)
        if extent_param is not None and str(extent_param).strip():
            extent = self.parameterAsExtent(
                parameters, self.EXTENT, context,
                QgsCoordinateReferenceSystem('EPSG:4326')
            )
            extent_wgs84 = (
                extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum(),
            )

        feedback.pushInfo(f"Input:   {input_path}")
        feedback.pushInfo(f"Output:  {output_dir}")
        feedback.pushInfo(f"Zoom:    {zoom_min}-{zoom_max}, tile {tile_size}px")
        feedback.pushInfo(f"Workers: {num_workers}")
        feedback.pushInfo(f"CRS:     {raster_layer.crs().authid()}")

        from ..core.pipeline import TileGenerationPipeline, generate_leaflet_html

        pipeline = TileGenerationPipeline(
            input_path=input_path,
            output_dir=output_dir,
            zoom_min=zoom_min,
            zoom_max=zoom_max,
            tile_size=tile_size,
            num_workers=num_workers,
            extent_wgs84=extent_wgs84,
            feedback=feedback,
            crs_wkt=crs_wkt,
        )

        try:
            result = pipeline.run()
        except Exception as e:
            import traceback
            feedback.reportError(traceback.format_exc())
            raise QgsProcessingException(f"Tile generation failed: {e}")

        # Leaflet HTML viewer
        if output_html:
            if extent_wgs84:
                west, south, east, north = extent_wgs84
            else:
                from ..core.raster_reader import RasterReader
                r = RasterReader(input_path, crs_wkt=crs_wkt)
                west, south, east, north = r.get_extent_wgs84()
                r.close()

            html = generate_leaflet_html(output_dir, west, south, east, north, zoom_min, zoom_max)
            with open(output_html, 'w', encoding='utf-8') as f:
                f.write(html)
            feedback.pushInfo(f"Leaflet viewer: {output_html}")

        return {
            self.OUTPUT_DIR: output_dir,
            self.OUTPUT_HTML: output_html or '',
        }
