"""Safe publication of a finished tile folder (finding 4).

Tiles are written into a partial sibling folder of the requested output
folder. Only when the run has completed is a manifest written and the partial
folder renamed into place, so a cancelled, failed or killed run never leaves a
folder at the output path that looks complete, and tiles left over from an
earlier run are never mixed into a new tile set (and so never zipped with it).

Nothing is deleted. A folder that already exists at the output path is renamed
aside to '<name>.previous-<stamp>'.
"""

import json
import os
import time

MANIFEST_NAME = 'simplitiles-manifest.json'
MANIFEST_VERSION = 1


def _stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def partial_dir_for(output_dir):
    """Unique sibling path for the in-progress tiles of output_dir."""
    base = os.path.normpath(output_dir)
    return f"{base}.partial-{_stamp()}-{os.getpid()}"


def write_manifest(work_dir, info):
    """List every tile in work_dir and write the manifest as the last file.

    The manifest is the completion marker: a tile folder without it is
    incomplete. Returns the manifest dict.
    """
    files = []
    for dirpath, _dirs, names in os.walk(work_dir):
        for n in names:
            if n == MANIFEST_NAME:
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, work_dir).replace(os.sep, '/')
            files.append({'path': rel, 'bytes': os.path.getsize(full)})
    files.sort(key=lambda f: f['path'])
    manifest = dict(info)
    manifest.update({
        'manifest_version': MANIFEST_VERSION,
        'complete': True,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'tile_count': len(files),
        'files': files,
    })
    tmp = os.path.join(work_dir, MANIFEST_NAME + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp, os.path.join(work_dir, MANIFEST_NAME))
    return manifest


def publish(work_dir, output_dir, log=lambda m: None):
    """Move the completed work_dir to output_dir. Returns the path of a
    previous folder that was renamed aside, or None."""
    output_dir = os.path.normpath(output_dir)
    moved_aside = None
    if os.path.isdir(output_dir):
        if not os.listdir(output_dir):
            # An empty folder (QGIS may create the destination up front):
            # move the results into it, manifest last.
            names = sorted(os.listdir(work_dir), key=lambda n: n == MANIFEST_NAME)
            for n in names:
                os.rename(os.path.join(work_dir, n), os.path.join(output_dir, n))
            os.rmdir(work_dir)  # our own, now empty, partial folder
            return None
        moved_aside = f"{output_dir}.previous-{_stamp()}"
        n = 1
        while os.path.exists(moved_aside):
            n += 1
            moved_aside = f"{output_dir}.previous-{_stamp()}-{n}"
        os.rename(output_dir, moved_aside)
        log(f"Existing output folder renamed to {moved_aside} "
            f"(kept, not deleted) so old tiles are not mixed with the new set")
    elif os.path.exists(output_dir):
        raise RuntimeError(f"Output path exists and is not a folder: {output_dir}")
    parent = os.path.dirname(output_dir)
    if parent:
        os.makedirs(parent, exist_ok=True)
    os.rename(work_dir, output_dir)
    return moved_aside


def is_complete(folder):
    """True if folder holds a completed tile set (its manifest is present)."""
    return os.path.isfile(os.path.join(folder, MANIFEST_NAME))
