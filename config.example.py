"""mapforge configuration — copy this file to `config.py` and edit the paths.

    cp config.example.py config.py     (or copy on Windows)

You need ROSE/SHO client data extracted to a loose `3DDATA` folder first
(unpack the client's GAME*.VFS archives). See the README.
"""

import os

# --------------------------------------------------------------------------
# ASSET_ROOT — where mapforge READS everything (the extracted client 3DDATA:
# maps, meshes, textures, STB tables). Point this at your extracted 3DDATA.
# --------------------------------------------------------------------------
ASSET_ROOT = r"C:\path\to\extracted\3DDATA"

# --------------------------------------------------------------------------
# WRITE_ROOTS — every 3DDATA root that should receive edited tile files (IFO /
# MOV) on save, so client and server stay byte-identical. Non-existent roots
# are skipped, so it is safe to list more than you have. For read-only viewing
# you can leave just the asset root.
# --------------------------------------------------------------------------
WRITE_ROOTS = [
    ("client", ASSET_ROOT),
    # ("server", r"C:\path\to\server\srvDATA\3DDATA"),
    # ("runtime", r"C:\path\to\game\client\3DDATA"),
]

STB_DIR        = os.path.join(ASSET_ROOT, "STB")
ZONE_LIST_STB  = os.path.join(STB_DIR, "LIST_ZONE.STB")
NPC_LIST_STB   = os.path.join(STB_DIR, "LIST_NPC.STB")
MORPH_LIST_STB = os.path.join(STB_DIR, "LIST_MORPH_OBJECT.STB")

# LIST_ZONE.STB columns
ZONE_COL_ZON       = 1
ZONE_COL_DECO_PACK = 11
ZONE_COL_CNST_PACK = 12

# World units (tile = 16000 units; zone is centred at 520000).
TILE_WORLD_SIZE = 16000
CENTER_WORLD    = 32 * TILE_WORLD_SIZE + TILE_WORLD_SIZE // 2


def asset_path(rel: str) -> str | None:
    """Resolve a backslash-relative '3DDATA\\...' path under ASSET_ROOT."""
    if not rel:
        return None
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if parts and parts[0].upper() == "3DDATA":
        parts = parts[1:]
    cand = os.path.join(ASSET_ROOT, *parts)
    return cand if os.path.exists(cand) else None


def relmap_from_zon(zon_rel: str) -> str | None:
    """LIST_ZONE .ZON path -> map-tile directory relative to a 3DDATA root."""
    if not zon_rel:
        return None
    parts = [p for p in zon_rel.replace("\\", "/").split("/") if p]
    if parts and parts[0].upper() == "3DDATA":
        parts = parts[1:]
    return "/".join(parts[:-1]) if len(parts) > 1 else "/".join(parts)


def write_targets(relmap: str, tile_filename: str) -> list[tuple[str, str]]:
    """For a tile file like '31_30.IFO', return [(root_label, abs_path), ...]
    for every WRITE_ROOT whose map directory exists on disk."""
    out = []
    rel_parts = relmap.split("/")
    for label, root in WRITE_ROOTS:
        d = os.path.join(root, *rel_parts)
        if os.path.isdir(d):
            out.append((label, os.path.join(d, tile_filename)))
    return out
