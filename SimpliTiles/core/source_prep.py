from osgeo import gdal


def detect_source_format(filepath):
    """Inspect a raster file and return format characteristics.

    Returns a dict with:
        driver     - GDAL short driver name ('GTiff', 'JPEG', ...)
        tiled      - True if the raster has internal tiling (square blocks)
        block_size - [block_width, block_height]
        has_overviews - True if overview levels are present
        width      - raster width in pixels
        height     - raster height in pixels
        band_count - number of raster bands
    """
    ds = gdal.OpenEx(filepath, gdal.OF_RASTER | gdal.OF_READONLY)
    if ds is None:
        raise RuntimeError(f"Cannot open raster for format detection: {filepath}")

    band = ds.GetRasterBand(1)
    block_w, block_h = band.GetBlockSize()
    width = ds.RasterXSize
    height = ds.RasterYSize

    # A raster is considered "tiled" when blocks are square (or nearly so) and
    # smaller than the full raster dimensions — i.e. not a single strip.
    tiled = (
        block_w > 1
        and block_h > 1
        and block_h < height
        and block_w < width
    )

    return {
        'driver': ds.GetDriver().ShortName,
        'tiled': tiled,
        'block_size': [block_w, block_h],
        'has_overviews': band.GetOverviewCount() > 0,
        'width': width,
        'height': height,
        'band_count': ds.RasterCount,
    }
