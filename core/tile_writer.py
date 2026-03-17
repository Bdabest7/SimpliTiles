import io
import os
import numpy as np


def get_blank_png_bytes(tile_size=256):
    """Return PNG bytes for a fully transparent tile (cached per call site)."""
    try:
        from PIL import Image
        img = Image.new('RGBA', (tile_size, tile_size), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, 'PNG', compress_level=1)
        return buf.getvalue()
    except ImportError:
        # Minimal 1x1 transparent PNG (scale-invariant in Leaflet)
        return (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
            b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
            b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        )


class TileWriter:
    """Writes tile arrays to {z}/{x}/{y}.png on disk."""

    def __init__(self, output_dir):
        self.output_dir = output_dir

    def write_tile(self, z, x, y, tile_array):
        """Write a single tile to disk as PNG.

        Args:
            tile_array: numpy array (4, tile_size, tile_size) RGBA uint8
        """
        tile_path = os.path.join(self.output_dir, str(z), str(x), f"{y}.png")
        rgba = np.transpose(tile_array, (1, 2, 0))

        try:
            from PIL import Image
            img = Image.fromarray(rgba, 'RGBA')
            img.save(tile_path, 'PNG', compress_level=1)
        except ImportError:
            from osgeo import gdal
            driver = gdal.GetDriverByName('PNG')
            h, w = tile_array.shape[1], tile_array.shape[2]
            out_ds = driver.Create(tile_path, w, h, 4, gdal.GDT_Byte)
            for i in range(4):
                out_ds.GetRasterBand(i + 1).WriteArray(tile_array[i])
            out_ds.FlushCache()
            out_ds = None

    def write_blank_tile(self, z, x, y, tile_size=256):
        """Write a fully transparent PNG tile."""
        tile_path = os.path.join(self.output_dir, str(z), str(x), f"{y}.png")
        with open(tile_path, 'wb') as f:
            f.write(get_blank_png_bytes(tile_size))
