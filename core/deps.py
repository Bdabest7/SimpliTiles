r"""Dependency check for SimpliTiles (finding 14).

SimpliTiles needs Pillow, which QGIS does not ship with. The tested version is
pinned in requirements.txt; install it ahead of time into QGIS's Python:

    "C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat" -m pip install -r requirements.txt

The automation's preflight can run this file with QGIS's Python; it prints one
line per dependency and exits 1 when something is missing:

    python-qgis.bat <plugin folder>\core\deps.py
"""
import importlib
import os
import sys

# module to import -> name in requirements.txt
REQUIRED = {'PIL': 'Pillow', 'numpy': 'numpy', 'osgeo.gdal': 'GDAL'}


def _pins():
    pins = {}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'requirements.txt')
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.split('#')[0].strip()
                if '==' in line:
                    name, ver = line.split('==', 1)
                    pins[name.strip().lower()] = ver.strip()
    except OSError:
        pass
    return pins


def check_dependencies():
    """Return (problems, notes): problems are missing imports (fatal), notes
    are version differences from the pin (informational)."""
    pins = _pins()
    problems, notes = [], []
    for module, pkg in REQUIRED.items():
        try:
            mod = importlib.import_module(module)
        except Exception as e:  # ImportError, or a broken DLL
            hint = f" (install with: pip install {pkg}=={pins[pkg.lower()]})" if pkg.lower() in pins else ''
            problems.append(f"{pkg}: cannot import {module}: {e}{hint}")
            continue
        version = getattr(mod, '__version__', None) or getattr(mod, 'VERSION', '?')
        want = pins.get(pkg.lower())
        if want and str(version) != want:
            notes.append(f"{pkg} is {version}, tested with {want}")
        else:
            notes.append(f"{pkg} {version} ok")
    return problems, notes


def require_dependencies():
    """Raise ImportError with every missing dependency named, or return the notes."""
    problems, notes = check_dependencies()
    if problems:
        raise ImportError('SimpliTiles dependencies missing: ' + '; '.join(problems))
    return notes


if __name__ == '__main__':
    problems, notes = check_dependencies()
    for n in notes:
        print('dep', n)
    for p in problems:
        print('dep MISSING', p)
    print(f"python {sys.version.split()[0]} at {sys.executable}")
    sys.exit(1 if problems else 0)
