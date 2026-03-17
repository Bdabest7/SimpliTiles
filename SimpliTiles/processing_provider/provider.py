from qgis.core import QgsProcessingProvider

from .simplitiles_algorithm import SimpliTilesAlgorithm


class SimpliTilesProvider(QgsProcessingProvider):
    def id(self):
        return 'simplitiles'

    def name(self):
        return 'SimpliTiles'

    def longName(self):
        return 'SimpliTiles - Parallel XYZ Tile Generator'

    def icon(self):
        return QgsProcessingProvider.icon(self)

    def loadAlgorithms(self):
        self.addAlgorithm(SimpliTilesAlgorithm())
