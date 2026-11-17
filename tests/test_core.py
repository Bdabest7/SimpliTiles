r"""Unit tests for the SimpliTiles core (no QGIS needed, only GDAL, NumPy, Pillow).

Run with QGIS's Python from the plugin's parent folder, e.g. in the OSGeo4W shell:

    cd <profile>\python\plugins
    python -m unittest SimpliTiles.tests.test_core -v
"""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from osgeo import gdal, osr

from .. import core  # noqa: F401  (package import check)
from ..core import pipeline as pl
from ..core import raster_reader as rr


def make_raster(path, size=512, bands=3, tiled=True):
    """A small UTM 16N RGB raster near Jackson, TN (about 100 m x 100 m)."""
    drv = gdal.GetDriverByName('GTiff')
    opts = ['TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256'] if tiled else []
    ds = drv.Create(path, size, size, bands, gdal.GDT_Byte, options=opts)
    ds.SetGeoTransform((332946.0, 0.2, 0, 3949823.0, 0, -0.2))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32616)
    ds.SetProjection(srs.ExportToWkt())
    rng = np.random.default_rng(1)
    for b in range(1, bands + 1):
        ds.GetRasterBand(b).WriteArray(rng.integers(1, 255, (size, size), dtype=np.uint8))
    ds = None


def _failing_warp(*args, **kwargs):
    raise RuntimeError("boom")


class Feedback:
    def __init__(self, cancel_after=None):
        self.msgs = []
        self.cancel_after = cancel_after
        self.progress = []

    def pushInfo(self, m): self.msgs.append(m)
    def reportError(self, m): self.msgs.append('ERR ' + m)
    def setProgress(self, p):
        self.progress.append(p)

    def isCanceled(self):
        return self.cancel_after is not None and len(self.progress) >= self.cancel_after


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, 'src.tif')
        make_raster(self.src)
        self.out = os.path.join(self.tmp.name, 'Tiles')

    def tearDown(self):
        self.tmp.cleanup()

    def run_pipeline(self, workers=2, zmin=17, zmax=19, feedback=None):
        p = pl.TileGenerationPipeline(self.src, self.out, zmin, zmax,
                                      num_workers=workers, feedback=feedback or Feedback())
        return p.run()

    # Finding 17: more workers than tiles must not crash the reader cleanup.
    def test_finding17_many_workers_few_tiles(self):
        result = self.run_pipeline(workers=32, zmin=17, zmax=18)
        self.assertGreater(result['tiles_generated'], 0)
        self.assertLess(result['tiles_generated'], 32)

    # Finding 1: a read error must stop the run, not become a blank tile.
    def test_finding1_read_error_raises(self):
        msg = None
        with mock.patch.object(rr.gdal, 'Warp', new=_failing_warp):  # a plain function: a Mock would keep the dataset in call_args
            try:
                self.run_pipeline(workers=2)
            except RuntimeError as e:
                msg = str(e)  # keep only the text: the traceback would pin open frames
        self.assertIsNotNone(msg, 'the run must raise')
        self.assertIn('boom', msg)

    def test_finding1_outside_tile_is_none(self):
        reader = rr.RasterReader(self.src)
        self.assertIsNone(reader.read_tile_region(0, 0, 19))
        self.assertIsNotNone(reader.read_tile_region(*self._inside_tile(reader)))


    # Finding 6: a cancelled run raises; it must not report success.
    def test_finding6_cancel_raises_multi_reader(self):
        fb = Feedback(cancel_after=2)
        with self.assertRaises(pl.TileGenerationCancelled):
            self.run_pipeline(workers=2, zmin=18, zmax=21, feedback=fb)

    def test_finding6_cancel_raises_single_reader(self):
        make_raster(self.src, tiled=False)
        fb = Feedback(cancel_after=2)
        with self.assertRaises(pl.TileGenerationCancelled):
            self.run_pipeline(workers=2, zmin=18, zmax=21, feedback=fb)


    # Finding 4: output appears only when complete, with a manifest; old output is kept aside.
    def test_finding4_success_publishes_with_manifest(self):
        self.run_pipeline(workers=2, zmin=18, zmax=19)
        self.assertTrue(os.path.isdir(self.out))
        from ..core import output_dir as od
        self.assertTrue(od.is_complete(self.out))
        import json
        with open(os.path.join(self.out, od.MANIFEST_NAME)) as f:
            m = json.load(f)
        self.assertEqual(m['tile_count'], len(m['files']))
        for ent in m['files']:
            self.assertTrue(os.path.isfile(os.path.join(self.out, ent['path'])))
        leftovers = [n for n in os.listdir(self.tmp.name) if '.partial-' in n]
        self.assertEqual(leftovers, [])

    def test_finding4_failure_leaves_no_output_folder(self):
        failed = False
        with mock.patch.object(rr.gdal, 'Warp', new=_failing_warp):
            try:
                self.run_pipeline(workers=2)
            except RuntimeError:
                failed = True
        self.assertTrue(failed)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(len([n for n in os.listdir(self.tmp.name) if '.partial-' in n]), 1)

    def test_finding4_cancel_leaves_no_output_folder(self):
        with self.assertRaises(pl.TileGenerationCancelled):
            self.run_pipeline(workers=2, zmin=18, zmax=21, feedback=Feedback(cancel_after=2))
        self.assertFalse(os.path.exists(self.out))

    def test_finding4_old_output_is_moved_aside_not_mixed(self):
        os.makedirs(os.path.join(self.out, '5', '1'))
        stale = os.path.join(self.out, '5', '1', '1.png')
        open(stale, 'wb').write(b'old')
        self.run_pipeline(workers=2, zmin=18, zmax=19)
        self.assertFalse(os.path.exists(os.path.join(self.out, '5')))
        prev = [n for n in os.listdir(self.tmp.name) if '.previous-' in n]
        self.assertEqual(len(prev), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, prev[0], '5', '1', '1.png')))

    def test_finding4_empty_existing_folder_is_filled(self):
        os.makedirs(self.out)
        self.run_pipeline(workers=2, zmin=18, zmax=19)
        from ..core import output_dir as od
        self.assertTrue(od.is_complete(self.out))
        self.assertEqual([n for n in os.listdir(self.tmp.name) if '.previous-' in n or '.partial-' in n], [])

    @staticmethod
    def _inside_tile(reader):
        from ..core import tile_math
        w, s, e, n = reader.get_extent_wgs84()
        for _, x, y in tile_math.tiles_in_extent(w, s, e, n, 19):
            return x, y, 19


if __name__ == '__main__':
    unittest.main()
