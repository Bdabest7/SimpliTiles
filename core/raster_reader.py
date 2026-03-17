import numpy as np
from osgeo import gdal, osr

from . import tile_math

gdal.UseExceptions()


class RasterReader:
    def __init__(self, filepath, crs_wkt=None):
        self.ds = gdal.OpenEx(filepath, gdal.OF_RASTER | gdal.OF_READONLY)
        if self.ds is None:
            raise RuntimeError(f"Cannot open raster: {filepath}")

        self.filepath = filepath
        self.band_count = self.ds.RasterCount
        self.width = self.ds.RasterXSize
        self.height = self.ds.RasterYSize
        self.gt = self.ds.GetGeoTransform()
        self.inv_gt = gdal.InvGeoTransform(self.gt)

        # Source CRS — prefer the raster's own projection, fall back to caller-supplied CRS
        proj_wkt = self.ds.GetProjection()
        if not proj_wkt and crs_wkt:
            proj_wkt = crs_wkt
        if not proj_wkt:
            raise RuntimeError(
                "Raster has no CRS and none was provided. "
                "Set the layer CRS in QGIS before running."
            )
        self.src_srs = osr.SpatialReference()
        self.src_srs.ImportFromWkt(proj_wkt)

        # Web Mercator (EPSG:3857) → source CRS transform
        self.merc_srs = osr.SpatialReference()
        self.merc_srs.ImportFromEPSG(3857)
        self.merc_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self.src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self.merc_to_src = osr.CoordinateTransformation(self.merc_srs, self.src_srs)

        # Source CRS → WGS84 transform (for extent computation)
        self.wgs84_srs = osr.SpatialReference()
        self.wgs84_srs.ImportFromEPSG(4326)
        self.wgs84_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self.src_to_wgs84 = osr.CoordinateTransformation(self.src_srs, self.wgs84_srs)

        # Nodata values per band
        self.nodata = []
        for i in range(1, self.band_count + 1):
            nd = self.ds.GetRasterBand(i).GetNoDataValue()
            self.nodata.append(nd)

        self.has_overviews = self.ds.GetRasterBand(1).GetOverviewCount() > 0

        # Cache raster extent in WGS84 for fast tile intersection checks
        self._wgs84_west, self._wgs84_south, self._wgs84_east, self._wgs84_north = \
            self.get_extent_wgs84()

    def is_tiled(self):
        """Return True if the raster uses internal square tiling."""
        bw, bh = self.ds.GetRasterBand(1).GetBlockSize()
        return bw > 1 and bh > 1 and bh < self.height and bw < self.width

    def get_extent_wgs84(self):
        """Return (west, south, east, north) in WGS84."""
        x0, y0 = self.gt[0], self.gt[3]
        x1 = self.gt[0] + self.width * self.gt[1] + self.height * self.gt[2]
        y1 = self.gt[3] + self.width * self.gt[4] + self.height * self.gt[5]

        corners = [
            self.src_to_wgs84.TransformPoint(x0, y0)[:2],
            self.src_to_wgs84.TransformPoint(x1, y0)[:2],
            self.src_to_wgs84.TransformPoint(x0, y1)[:2],
            self.src_to_wgs84.TransformPoint(x1, y1)[:2],
        ]
        lons = [c[0] for c in corners]
        lats = [c[1] for c in corners]
        return min(lons), min(lats), max(lons), max(lats)

    def read_tile_region(self, x, y, z, tile_size=256):
        """Read and resample the source raster for tile (z, x, y).

        Returns an RGBA numpy array (4, tile_size, tile_size) uint8,
        or None if the tile doesn't intersect the raster.
        Uses gdal.Warp for correct UTM→Web Mercator reprojection with exact
        tile boundaries and sub-pixel accuracy (no seams between tiles).
        """
        # Fast geographic intersection check — skip warp entirely if no overlap
        tile_west, tile_south, tile_east, tile_north = tile_math.tile_bounds_wgs84(x, y, z)
        if (tile_east <= self._wgs84_west or tile_west >= self._wgs84_east or
                tile_north <= self._wgs84_south or tile_south >= self._wgs84_north):
            return None

        merc_xmin, merc_ymin, merc_xmax, merc_ymax = tile_math.tile_bounds_mercator(x, y, z)

        try:
            warp_ds = gdal.Warp(
                '',
                self.ds,
                format='MEM',
                outputBounds=[merc_xmin, merc_ymin, merc_xmax, merc_ymax],
                outputBoundsSRS='EPSG:3857',
                width=tile_size,
                height=tile_size,
                dstSRS='EPSG:3857',
                resampleAlg=gdal.GRA_Lanczos,
                multithread=False,
            )
        except Exception:
            return None

        if warp_ds is None:
            return None

        data = warp_ds.ReadAsArray()
        warp_ds = None  # release in-memory dataset

        if data is None:
            return None
        if data.ndim == 2:
            data = data[np.newaxis]

        if data.shape[0] >= 4:
            return data[:4].astype(np.uint8)

        result = np.zeros((4, tile_size, tile_size), dtype=np.uint8)
        if data.shape[0] == 3:
            result[:3] = data[:3].astype(np.uint8)
            result[3] = 255
            if self.nodata[0] is not None:
                nd = self.nodata[0]
                mask = (data[0] == nd) & (data[1] == nd) & (data[2] == nd)
                result[3][mask] = 0
        elif data.shape[0] == 1:
            result[0] = result[1] = result[2] = data[0].astype(np.uint8)
            result[3] = 255
            if self.nodata[0] is not None:
                result[3][data[0] == self.nodata[0]] = 0
        else:
            return None

        return result

    def close(self):
        self.ds = None
