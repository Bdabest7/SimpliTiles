"""
Tile generation pipeline.

Two execution paths, both using ThreadPoolExecutor (no subprocess/QGIS launch):

  Path A — Multi-reader threads (tiled GeoTIFF)
    Each thread opens its own gdal.Open() handle. GDAL releases the GIL
    during C-level file I/O, so threads genuinely run in parallel for
    tiled rasters where blocks are independently addressable.

  Path B — Single-reader + parallel writers (striped GeoTIFF / plain JPEG)
    One thread does GDAL reads; the thread pool handles PNG encoding + disk
    writes (PIL also releases the GIL during compression and file I/O).
"""

import os
import time
import threading
import concurrent.futures

import numpy as np
from osgeo import gdal

from .tile_math import tiles_in_extent, count_tiles
from .source_prep import detect_source_format


class TileGenerationPipeline:
    def __init__(self, input_path, output_dir, zoom_min, zoom_max,
                 tile_size=256, num_workers=16,
                 extent_wgs84=None, feedback=None, crs_wkt=None):
        self.input_path = input_path
        self.output_dir = output_dir
        self.zoom_min = zoom_min
        self.zoom_max = zoom_max
        self.tile_size = tile_size
        self.num_workers = num_workers
        self.extent_wgs84 = extent_wgs84
        self.feedback = feedback
        self.crs_wkt = crs_wkt

    def _log(self, msg):
        if self.feedback:
            self.feedback.pushInfo(msg)

    def _log_error(self, msg):
        if self.feedback:
            self.feedback.reportError(msg)

    def _is_canceled(self):
        return self.feedback and self.feedback.isCanceled()

    def _set_progress(self, pct):
        if self.feedback:
            self.feedback.setProgress(int(pct))

    def run(self):
        t_start = time.time()

        # 16 GB GDAL cache shared across threads in this process
        gdal.SetCacheMax(16 * 1024 * 1024 * 1024)

        # --- Detect source format ---
        fmt = detect_source_format(self.input_path)
        block_desc = f"tiled {fmt['block_size']}" if fmt['tiled'] else 'striped'
        self._log(f"Source: {fmt['driver']} ({block_desc}), "
                  f"{fmt['width']}x{fmt['height']}px, {fmt['band_count']} bands")
        if not fmt['has_overviews']:
            self._log("WARNING: No overviews. Low-zoom tiles will be slow.")

        # --- Open one reader to get extent ---
        t_open = time.time()
        from .raster_reader import RasterReader
        reader = RasterReader(self.input_path, crs_wkt=self.crs_wkt)
        self._log(f"Raster opened ({time.time() - t_open:.2f}s)")

        west, south, east, north = (
            self.extent_wgs84 if self.extent_wgs84
            else reader.get_extent_wgs84()
        )
        self._log(f"Extent (WGS84): W={west:.6f} S={south:.6f} E={east:.6f} N={north:.6f}")
        reader.close()

        # --- Count & enumerate tiles ---
        total_tiles = 0
        for z in range(self.zoom_min, self.zoom_max + 1):
            cnt = count_tiles(west, south, east, north, z, z)
            total_tiles += cnt
            self._log(f"  Zoom {z}: {cnt} tiles")
        self._log(f"Total: {total_tiles} tiles")

        if total_tiles == 0:
            self._log("No tiles to generate.")
            return self._make_result(0, 0, 0, time.time() - t_start)

        all_tiles = [
            (z, x, y)
            for z in range(self.zoom_min, self.zoom_max + 1)
            for _, x, y in tiles_in_extent(west, south, east, north, z)
        ]

        # --- Pre-create output directories ---
        t_dirs = time.time()
        seen = set()
        for z, x, _ in all_tiles:
            k = (z, x)
            if k not in seen:
                os.makedirs(os.path.join(self.output_dir, str(z), str(x)), exist_ok=True)
                seen.add(k)
        self._log(f"Directories pre-created ({time.time() - t_dirs:.2f}s)")

        # --- Choose pipeline ---
        t_gen = time.time()
        if fmt['tiled']:
            pipeline_name = f"Multi-reader threads ({self.num_workers} threads, per-thread GDAL handle)"
            tiles_written, tiles_empty = self._run_multi_reader(all_tiles, total_tiles)
        else:
            pipeline_name = f"Single-reader + {self.num_workers} writer threads"
            tiles_written, tiles_empty = self._run_single_reader(all_tiles, total_tiles)

        elapsed_gen = time.time() - t_gen
        elapsed_total = time.time() - t_start

        self._log_summary(fmt, pipeline_name, elapsed_total, elapsed_gen,
                          tiles_written, tiles_empty)

        return self._make_result(tiles_written, tiles_empty,
                                 tiles_written + tiles_empty, elapsed_total)

    # ------------------------------------------------------------------
    # Path A: per-thread GDAL handle (tiled GeoTIFF)
    # ------------------------------------------------------------------
    def _run_multi_reader(self, all_tiles, total):
        """Each thread opens its own GDAL handle. GDAL releases the GIL
        during C reads, so threads genuinely run I/O in parallel."""
        from .raster_reader import RasterReader
        from .tile_writer import get_blank_png_bytes

        blank_bytes = get_blank_png_bytes(self.tile_size)
        thread_local = threading.local()
        cancelled = threading.Event()

        tiles_written = 0
        tiles_empty = 0
        lock = threading.Lock()

        def get_reader():
            if not hasattr(thread_local, 'reader'):
                thread_local.reader = RasterReader(
                    self.input_path, crs_wkt=self.crs_wkt
                )
            return thread_local.reader

        def process_tile(args):
            z, x, y = args
            if cancelled.is_set():
                return None

            reader = get_reader()
            tile_data = reader.read_tile_region(x, y, z, self.tile_size)
            tile_path = os.path.join(self.output_dir, str(z), str(x), f"{y}.png")

            if tile_data is not None:
                rgba = np.transpose(tile_data, (1, 2, 0))
                from PIL import Image
                img = Image.fromarray(rgba, 'RGBA')
                img.save(tile_path, 'PNG', compress_level=1)
                return True
            else:
                with open(tile_path, 'wb') as f:
                    f.write(blank_bytes)
                return False

        tiles_done = 0
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.num_workers) as executor:
            for result in executor.map(process_tile, all_tiles, chunksize=64):
                if self._is_canceled():
                    cancelled.set()
                    break
                if result is True:
                    tiles_written += 1
                elif result is False:
                    tiles_empty += 1
                tiles_done += 1
                self._set_progress(100.0 * tiles_done / total)

        return tiles_written, tiles_empty

    # ------------------------------------------------------------------
    # Path B: single reader + parallel writers (striped / JPEG)
    # ------------------------------------------------------------------
    def _run_single_reader(self, all_tiles, total):
        """Single GDAL reader (sequential reads) with a thread pool for
        PNG encoding and disk writes."""
        from .raster_reader import RasterReader
        from .tile_writer import get_blank_png_bytes

        reader = RasterReader(self.input_path, crs_wkt=self.crs_wkt)
        blank_bytes = get_blank_png_bytes(self.tile_size)

        tiles_written = 0
        tiles_empty = 0
        tiles_done = 0
        max_inflight = self.num_workers * 4
        pending = {}

        def write_task(z, x, y, tile_data):
            tile_path = os.path.join(self.output_dir, str(z), str(x), f"{y}.png")
            if tile_data is not None:
                rgba = np.transpose(tile_data, (1, 2, 0))
                from PIL import Image
                img = Image.fromarray(rgba, 'RGBA')
                img.save(tile_path, 'PNG', compress_level=1)
                return True
            else:
                with open(tile_path, 'wb') as f:
                    f.write(blank_bytes)
                return False

        def drain(futures_dict, wait_for=concurrent.futures.FIRST_COMPLETED):
            nonlocal tiles_written, tiles_empty, tiles_done
            done, _ = concurrent.futures.wait(
                futures_dict, return_when=wait_for
            )
            for fut in done:
                had_data = fut.result()
                if had_data:
                    tiles_written += 1
                else:
                    tiles_empty += 1
                tiles_done += 1
                self._set_progress(100.0 * tiles_done / total)
                del futures_dict[fut]

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.num_workers) as executor:
            for z, x, y in all_tiles:
                if self._is_canceled():
                    break
                if len(pending) >= max_inflight:
                    drain(pending)

                tile_data = reader.read_tile_region(x, y, z, self.tile_size)
                fut = executor.submit(write_task, z, x, y, tile_data)
                pending[fut] = True

            # Drain remaining
            if pending:
                drain(pending, wait_for=concurrent.futures.ALL_COMPLETED)

        reader.close()
        return tiles_written, tiles_empty

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _log_summary(self, fmt, pipeline_name, elapsed_total, elapsed_gen,
                     tiles_written, tiles_empty):
        total = tiles_written + tiles_empty
        tps = total / elapsed_gen if elapsed_gen > 0 else 0
        block_desc = f"tiled {fmt['block_size']}" if fmt['tiled'] else 'striped'
        self._log("")
        self._log("=" * 50)
        self._log("PERFORMANCE SUMMARY")
        self._log("=" * 50)
        self._log(f"  Source:     {fmt['driver']} ({block_desc})")
        self._log(f"  Overviews:  {'yes' if fmt['has_overviews'] else 'NO'}")
        self._log(f"  Pipeline:   {pipeline_name}")
        self._log(f"  Generation: {elapsed_gen:.1f}s")
        self._log(f"  Total time: {elapsed_total:.1f}s")
        self._log(f"  Written:    {tiles_written} ({tiles_empty} blank)")
        self._log(f"  Throughput: {tps:.1f} tiles/sec")
        self._log("=" * 50)

    def _make_result(self, tiles_written, tiles_empty, total, elapsed):
        return {
            'tiles_generated': total,
            'tiles_written': tiles_written,
            'tiles_empty': tiles_empty,
            'elapsed_seconds': elapsed,
            'tiles_per_second': total / elapsed if elapsed > 0 else 0,
        }


def generate_leaflet_html(output_dir, west, south, east, north, zoom_min, zoom_max):
    """Return Leaflet HTML string for a local tile directory."""
    center_lat = (south + north) / 2
    center_lon = (west + east) / 2
    tile_base = output_dir.replace(os.sep, '/')

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>SimpliTiles Preview</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>body{{margin:0;padding:0;}} #map{{position:absolute;top:0;bottom:0;width:100%;}}</style>
</head>
<body>
<div id="map"></div>
<script>
    var map = L.map('map', {{
        center: [{center_lat}, {center_lon}],
        zoom: {min(zoom_min + 2, zoom_max)},
        minZoom: {zoom_min}, maxZoom: {zoom_max}
    }});
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        attribution: '&copy; OpenStreetMap contributors', maxZoom: 19, opacity: 0.3
    }}).addTo(map);
    L.tileLayer('file:///{tile_base}/{{z}}/{{x}}/{{y}}.png', {{
        minZoom: {zoom_min}, maxZoom: {zoom_max}, tms: false, opacity: 1.0
    }}).addTo(map);
    map.fitBounds([[{south},{west}],[{north},{east}]]);
</script>
</body>
</html>"""
