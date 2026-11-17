"""Scoped GDAL settings (finding 15).

SimpliTiles needs GDAL exceptions and a large block cache while it runs, but
QGIS is one long-lived process: leaving those switched on afterwards changes
how every other plugin and tool in the session behaves. gdal_settings() sets
them for the duration of a run and puts back what was there before.
"""
from contextlib import contextmanager

from osgeo import gdal, ogr, osr

_MODULES = (gdal, ogr, osr)


def _exceptions_on(mod):
    getter = getattr(mod, 'GetUseExceptions', None)
    return bool(getter()) if getter else False


@contextmanager
def gdal_settings(cache_bytes=None, exceptions=True):
    prev_cache = gdal.GetCacheMax()
    prev_exc = {m: _exceptions_on(m) for m in _MODULES}
    try:
        if cache_bytes:
            gdal.SetCacheMax(int(cache_bytes))
        if exceptions:
            gdal.UseExceptions()
        yield
    finally:
        gdal.SetCacheMax(prev_cache)
        for mod, was_on in prev_exc.items():
            if was_on:
                mod.UseExceptions()
            else:
                mod.DontUseExceptions()
