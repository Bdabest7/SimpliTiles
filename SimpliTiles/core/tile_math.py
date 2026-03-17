import math


def deg2num(lat_deg, lon_deg, zoom):
    """Convert WGS84 lat/lon to tile indices at a given zoom level."""
    lat_rad = math.radians(lat_deg)
    n = 2 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(x, n - 1))
    y = max(0, min(y, n - 1))
    return x, y


def num2deg(x, y, zoom):
    """Convert tile indices to the NW corner in WGS84 lat/lon."""
    n = 2 ** zoom
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def tile_bounds_wgs84(x, y, zoom):
    """Return (west, south, east, north) in WGS84 for a tile."""
    north, west = num2deg(x, y, zoom)
    south, east = num2deg(x + 1, y + 1, zoom)
    return west, south, east, north


def _lat_to_mercator_y(lat_deg):
    """Convert WGS84 latitude to Web Mercator Y coordinate."""
    lat_rad = math.radians(lat_deg)
    return 6378137.0 * math.log(math.tan(math.pi / 4.0 + lat_rad / 2.0))


def _lon_to_mercator_x(lon_deg):
    """Convert WGS84 longitude to Web Mercator X coordinate."""
    return 6378137.0 * math.radians(lon_deg)


def tile_bounds_mercator(x, y, zoom):
    """Return (xmin, ymin, xmax, ymax) in EPSG:3857 for a tile."""
    west, south, east, north = tile_bounds_wgs84(x, y, zoom)
    xmin = _lon_to_mercator_x(west)
    ymin = _lat_to_mercator_y(south)
    xmax = _lon_to_mercator_x(east)
    ymax = _lat_to_mercator_y(north)
    return xmin, ymin, xmax, ymax


def tiles_in_extent(west, south, east, north, zoom):
    """Yield (z, x, y) for all tiles overlapping the WGS84 extent at a zoom level."""
    # Clamp latitude to valid Mercator range
    south = max(south, -85.05112878)
    north = min(north, 85.05112878)

    x_min, y_min = deg2num(north, west, zoom)  # NW corner -> min x, min y
    x_max, y_max = deg2num(south, east, zoom)   # SE corner -> max x, max y

    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            yield zoom, x, y


def count_tiles(west, south, east, north, zoom_min, zoom_max):
    """Count total tiles across all zoom levels for the given extent."""
    south = max(south, -85.05112878)
    north = min(north, 85.05112878)
    total = 0
    for z in range(zoom_min, zoom_max + 1):
        x_min, y_min = deg2num(north, west, z)
        x_max, y_max = deg2num(south, east, z)
        total += (x_max - x_min + 1) * (y_max - y_min + 1)
    return total
