from qgis.core import QgsApplication

from .processing_provider.provider import SimpliTilesProvider


class SimpliTilesPlugin:
    def __init__(self):
        self.provider = None

    def initProcessing(self):
        self.provider = SimpliTilesProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
