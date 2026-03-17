# SimpliTiles

A QGIS plugin that replaces the built-in **Generate XYZ Tiles (Directory)** algorithm with a fast, parallel GDAL-based pipeline designed for large orthomosaic files.

## Why

The built-in QGIS tile generator routes every tile through the QGIS rendering pipeline — it is slow (~80 tiles/sec), prone to random failures, and scales poorly on multi-core hardware. SimpliTiles reads directly from GDAL, parallelizes via threads, and generates the same output 10–20× faster.

| | SimpliTiles | QGIS built-in |
|---|---|---|
| 18,421 tiles (zoom 12–21) | ~60–90 s | 4+ hours |
| Throughput | ~200–400 tiles/sec | ~80 tiles/sec |
| Failure rate | None observed | Random crashes |

Tested on a 49,058 × 65,126 px JPEG-compressed GeoTIFF (Pix4D Matic 2.0.2, EPSG:32614, 7 overview levels).

## Features

- Parallel tile generation using `ThreadPoolExecutor` — no subprocess spawning, no QGIS re-initialization
- **Path A** — Tiled GeoTIFF: per-thread GDAL handles, true parallel I/O
- **Path B** — Striped GeoTIFF / JPEG: single reader + parallel PNG writes
- `gdal.Warp` per tile for correct UTM → Web Mercator reprojection (no alignment seams)
- Leverages pre-built GDAL overviews for fast low-zoom generation
- Writes blank transparent PNGs for tiles outside the raster extent (matching QGIS output spec)
- Leaflet HTML preview generated automatically for QA
- Detailed performance summary in the QGIS log

## Requirements

- QGIS 3.4 or later (tested on 4.0)
- GDAL 3.x (bundled with QGIS)
- Pillow ≥ 9.0

Install Pillow into the QGIS Python environment if not already present:

```
# Windows — find QGIS Python, then:
"C:\Program Files\QGIS 4.0.0\apps\Python312\python.exe" -m pip install Pillow
```

## Installation

### From the QGIS Plugin Manager (recommended)
1. Search for **SimpliTiles**, click **Install**

### Manual install
1. Download the latest release ZIP from [GitHub Releases](https://github.com/Bdabest7/SimpliTiles)
2. **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded file and click **Install Plugin**

### From source
```
cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
git clone https://github.com/Bdabest7/SimpliTiles.git
```
Restart QGIS and enable the plugin.

## Usage

1. Open **Processing Toolbox → SimpliTiles → Generate XYZ Tiles (SimpliTiles)**
2. Set parameters:

| Parameter | Description |
|---|---|
| Input raster layer | Your orthomosaic (.tif or .jpeg) |
| Extent | Optional — defaults to full raster extent |
| Minimum zoom | Typically 12–14 for regional context |
| Maximum zoom | Typically 20–21 for full-resolution detail |
| Tile size | 256 px (standard) |
| Parallel workers | Defaults to CPU core count, capped at 32 |
| Output directory | Root folder for `z/x/y.png` tile tree |
| Output HTML | Leaflet preview file (optional) |

3. Click **Run**. Progress updates in real time.
4. Open the generated HTML file to preview tiles over an OpenStreetMap basemap.

## How It Works

### Source format detection
On startup the plugin inspects the GDAL block size:
- **Tiled GeoTIFF** (block width > 1 and block height > 1, e.g., 512×512): uses Path A
- **Striped GeoTIFF or JPEG**: uses Path B

### Path A — Multi-reader threads (tiled GeoTIFF)
Each thread holds its own `gdal.Open()` handle via `threading.local()`. GDAL releases the Python GIL during C-level block reads, so threads genuinely run I/O in parallel across independent tile blocks.

### Path B — Single reader + parallel writers (striped/JPEG)
JPEG has no random-access structure; GDAL decompresses the entire file on first read. A single reader performs sequential GDAL reads; a `ThreadPoolExecutor` handles PIL PNG encoding and disk writes in parallel.

### Tile reprojection
Each tile calls `gdal.Warp` with the exact Web Mercator tile bounds and `GRA_Lanczos` resampling. This produces pixel-accurate alignment with no seams between adjacent tiles — unlike a bounding-box `ReadAsArray` approach, which introduces sub-pixel offsets that become visible multi-pixel shifts at high zoom levels.

## Performance Tips

- **Build overviews before tiling.** Without overviews, low-zoom tiles require GDAL to read and downsample the full-resolution raster for every tile.
  ```
  gdaladdo -ro --config COMPRESS_OVERVIEW DEFLATE your_file.tif 2 4 8 16 32 64 128
  ```
- **Use a tiled GeoTIFF** when possible — Path A with 16–32 workers can saturate NVMe I/O.
- The default worker count (`min(cpu_count, 32)`) is a good starting point. On I/O-bound systems, increasing beyond CPU count yields diminishing returns.

## Output Structure

```
output_dir/
├── 12/
│   └── 930/
│       └── 1677.png
├── ...
└── 21/
    └── 119360/
        └── 215040.png
```

Standard `{z}/{x}/{y}.png` XYZ tile structure, compatible with Leaflet, MapLibre, QGIS, and any TMS client.

## License

MIT — see [LICENSE](LICENSE)
