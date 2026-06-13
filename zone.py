"""Zone assembler — discovery, per-tile terrain + IFO + MOV, and dual-root save.

Reuses the proven binary readers (rose_map for ZON/HIM/TIL) and the new
full-lump IFO codec + MOV reader. Everything is reported in absolute world
units so terrain, objects, spawns and the collision grid all register in one
coordinate space.
"""

from __future__ import annotations

import os
import sys
import shutil

# Make the sibling parser modules importable (rose_map / rose_zsc / rose_zms).
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import rose_map                      # noqa: E402  (ZON/HIM/TIL readers)
from parse_stb import StbFile        # noqa: E402

import config                        # noqa: E402
import rose_ifo as RI                # noqa: E402
import rose_mov as RM                # noqa: E402

GRID_PER_TILE_AXIS = 64              # HIM is 65x65 verts -> 64 quads -> 250u each
VERT_STEP = config.TILE_WORLD_SIZE / GRID_PER_TILE_AXIS   # 250


# --------------------------------------------------------------------------
# Zone discovery
# --------------------------------------------------------------------------
_zone_cache: list | None = None


def list_zones() -> list:
    """Rows of LIST_ZONE.STB that have a real map folder on disk."""
    global _zone_cache
    if _zone_cache is not None:
        return _zone_cache
    stb = StbFile(config.ZONE_LIST_STB)
    zones = []
    for r in range(stb.row_count):
        zon_rel = stb.get(r, config.ZONE_COL_ZON)
        if not zon_rel:
            continue
        relmap = config.relmap_from_zon(zon_rel)
        if not relmap:
            continue
        tile_dir = config.asset_path(relmap)
        if not tile_dir or not os.path.isdir(tile_dir):
            continue
        # Must actually contain map tiles.
        if not any(f.lower().endswith(".ifo") for f in os.listdir(tile_dir)):
            continue
        zones.append({
            "key": os.path.basename(relmap),
            "row": r,
            "name": stb.get(r, 0) or os.path.basename(relmap),
            "zon_rel": zon_rel,
            "relmap": relmap,
            "dir": tile_dir,
            "deco_pack": stb.get(r, config.ZONE_COL_DECO_PACK) or "",
            "cnst_pack": stb.get(r, config.ZONE_COL_CNST_PACK) or "",
        })
    _zone_cache = zones
    return zones


def find_zone(key: str) -> dict | None:
    for z in list_zones():
        if z["key"].lower() == key.lower():
            return z
    return None


# --------------------------------------------------------------------------
# Per-tile discovery
# --------------------------------------------------------------------------
def _tiles_in(zone_dir: str):
    """Yield (x, y, stem_path) for every X_Y tile in the folder."""
    seen = {}
    for fn in os.listdir(zone_dir):
        stem, ext = os.path.splitext(fn)
        if ext.lower() != ".ifo" or "_" not in stem:
            continue
        try:
            x, y = (int(p) for p in stem.split("_", 1))
        except ValueError:
            continue
        seen[(x, y)] = os.path.join(zone_dir, stem)
    for (x, y), stem in sorted(seen.items()):
        yield x, y, stem


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------
def _regenmob_json(m: RI.RegenMob) -> dict:
    return {"name": m.name, "mob_id": m.mob_id, "count": m.count}


def _obj_json(o: RI.IfoObject, idx: int) -> dict:
    extra = {}
    for k, v in o.extra.items():
        if k in ("basic", "tactics"):
            extra[k] = [_regenmob_json(m) for m in v]
        else:
            extra[k] = v
    return {
        "idx": idx,
        "name": o.name,
        "object_id": o.object_id,
        "object_type": o.object_type,
        "warp_id": o.warp_id,
        "event_id": o.event_id,
        "pos": [o.pos[0], o.pos[1], o.pos[2]],
        "rot": [o.rot[0], o.rot[1], o.rot[2], o.rot[3]],
        "scale": [o.scale[0], o.scale[1], o.scale[2]],
        "extra": extra,
    }


def tile_ifo_json(stem: str) -> dict:
    """All decoded lumps for one tile as JSON, grouped by lump name."""
    ifo = RI.read_ifo(stem + ".IFO")
    out = {}
    meta = {}
    for lt, lump in ifo.lumps.items():
        meta[lump.name] = {"decoded": lump.decoded, "consumer": lump.consumer,
                           "count": len(lump.objects)}
        if not lump.decoded:
            continue
        if lt == RI.LUMP_OCEAN:
            out["OCEAN"] = {
                "patch_size": lump.ocean.patch_size,
                "blocks": [[list(s), list(e)] for s, e in lump.ocean.blocks],
            }
            continue
        out[lump.name] = [_obj_json(o, i) for i, o in enumerate(lump.objects)]
    return {"lumps": out, "meta": meta}


def tile_terrain_json(x: int, y: int, stem: str, center_y: int = 32) -> dict:
    """Heightfield + tile material indices in world space."""
    him = rose_map.read_him(stem + ".HIM")
    til = rose_map.read_til(stem + ".TIL")
    ox = x * config.TILE_WORLD_SIZE
    # The map-file Y index is flipped relative to world space (verified: object
    # world positions for file (X,Y) land on world tile 2*center_y - Y). Objects
    # use the engine's +center world convention with no flip, so terrain must be
    # placed at the flipped Y to register with them. X is not flipped.
    oy = (2 * center_y - y) * config.TILE_WORLD_SIZE
    # Flatten heights row-major (row 0 = south / lowest world Y).
    flat = []
    for row in him.heights:
        flat.extend(row)
    materials = []
    for trow in til.tiles:
        materials.append([e.tile_no for e in trow])
    return {
        "x": x, "y": y, "origin": [ox, oy],
        "verts": him.width, "step": VERT_STEP,
        "heights": flat,
        "materials": materials,
    }


def tile_mov_json(x: int, y: int, stem: str, center_y: int = 32) -> dict | None:
    path = stem + ".MOV"
    if not os.path.exists(path):
        return None
    mov = RM.read_mov(path, x, y)
    # Same Y flip as terrain so the collision grid registers with the ground.
    oy = (2 * center_y - y) * config.TILE_WORLD_SIZE
    return {
        "x": x, "y": y,                 # FILE tile (used for save ops)
        "w": mov.width, "h": mov.height,
        "cell": RM.CELL_SIZE,
        "origin": [x * config.TILE_WORLD_SIZE, oy],
        "cells": mov.cells,          # cells[row][col], row 0 = lowest world Y
    }


# --------------------------------------------------------------------------
# Full zone load
# --------------------------------------------------------------------------
def load_zone(key: str) -> dict:
    z = find_zone(key)
    if not z:
        raise KeyError(key)
    zon = rose_map.read_zon(_find_zon(z))
    cy = zon.info.center_y or 32
    tiles = []
    for x, y, stem in _tiles_in(z["dir"]):
        t = tile_terrain_json(x, y, stem, center_y=cy)
        t["ifo"] = tile_ifo_json(stem)
        tiles.append(t)
    return {
        "key": z["key"],
        "name": z["name"],
        "tile_world_size": config.TILE_WORLD_SIZE,
        "center_world": config.CENTER_WORLD,
        "center_y": cy,
        "deco_pack": z["deco_pack"],
        "cnst_pack": z["cnst_pack"],
        "zone_name": getattr(zon.economy, "zone_name", "") if zon.economy else "",
        # Terrain tile palette: tile_no -> tile_types[no] -> layer texture idx
        # -> tile_textures[idx] = DDS path. Lets the client texture the ground.
        "tile_textures": zon.tile_textures,
        "tile_types": zon.tile_types,
        "tiles": tiles,
    }


def load_mov(key: str) -> dict:
    z = find_zone(key)
    if not z:
        raise KeyError(key)
    cy = rose_map.read_zon(_find_zon(z)).info.center_y or 32
    grids = []
    for x, y, stem in _tiles_in(z["dir"]):
        g = tile_mov_json(x, y, stem, center_y=cy)
        if g:
            grids.append(g)
    return {"key": z["key"], "cell": RM.CELL_SIZE, "tiles": grids}


def _find_zon(z: dict) -> str:
    for f in os.listdir(z["dir"]):
        if f.lower().endswith(".zon"):
            return os.path.join(z["dir"], f)
    raise FileNotFoundError(f"no .ZON in {z['dir']}")


# --------------------------------------------------------------------------
# Save — apply ops per tile, write IFO/MOV to every existing data root
# --------------------------------------------------------------------------
_LUMP_BY_NAME = {v: k for k, v in RI.LUMP_NAME.items()}


def _apply_obj_fields(o: RI.IfoObject, op: dict) -> None:
    if "pos" in op:   o.pos = tuple(float(v) for v in op["pos"])
    if "rot" in op:   o.rot = tuple(float(v) for v in op["rot"])
    if "scale" in op: o.scale = tuple(float(v) for v in op["scale"])
    if "object_id" in op:   o.object_id = int(op["object_id"])
    if "object_type" in op: o.object_type = int(op["object_type"])
    if "warp_id" in op:     o.warp_id = int(op["warp_id"])
    if "event_id" in op:    o.event_id = int(op["event_id"])
    if "name" in op:        o.name = str(op["name"])


def _apply_regen(o: RI.IfoObject, op: dict) -> None:
    e = o.extra
    if "regen_name" in op: e["regen_name"] = str(op["regen_name"])
    for key in ("interval", "limit", "range", "tactics_point"):
        if key in op:
            e[key] = int(op[key])
    for grp in ("basic", "tactics"):
        if grp in op:
            e[grp] = [RI.RegenMob(m.get("name", ""), int(m["mob_id"]), int(m["count"]))
                      for m in op[grp]]


def _resolve_asset(rel: str):
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if parts and parts[0].lower() == "3ddata":
        parts = parts[1:]
    cur = config.ASSET_ROOT
    for p in parts:
        if not os.path.isdir(cur):
            return None
        m = [e for e in os.listdir(cur) if e.lower() == p.lower()]
        if not m:
            return None
        cur = os.path.join(cur, m[0])
    return cur if os.path.exists(cur) else None


def _resolve_for_zsc(rel: str):
    """ZSC-edit resolver: asset path for reading, but writes go to data roots
    inside append_model itself."""
    return _resolve_asset(rel)


def save_ops(key: str, ops: list) -> dict:
    import rose_zsc_edit as ZE
    z = find_zone(key)
    if not z:
        raise KeyError(key)
    relmap = z["relmap"]

    # --- Resolve "place" ops first: a foreign model gets appended to this
    # zone's DECO pack (new object_id), then placed as an OBJECT record. Models
    # already in this zone's packs are placed directly. MORPH is global.
    append_cache: dict = {}     # (source_pack, source_idx) -> object_id
    extra_adds: list = []       # converted into normal add ops below
    for op in ops:
        if op.get("op") != "place":
            continue
        kind = op.get("source_kind", "OBJECT")
        spack = op.get("source_pack", "")
        sidx = int(op.get("source_model", 0))
        common = {"tile": op["tile"], "pos": op.get("pos"), "rot": op.get("rot", [0, 0, 0, 1]),
                  "scale": op.get("scale", [1, 1, 1]), "op": "add"}
        if kind == "MORPH":
            extra_adds.append({**common, "lump": "MORPH", "object_id": sidx})
        elif spack == z["deco_pack"]:
            extra_adds.append({**common, "lump": "OBJECT", "object_id": sidx})
        elif spack == z["cnst_pack"]:
            extra_adds.append({**common, "lump": "CNST", "object_id": sidx})
        else:
            ck = (spack, sidx)
            if ck not in append_cache:
                append_cache[ck] = ZE.append_model(z["deco_pack"], spack, sidx, _resolve_for_zsc)
            extra_adds.append({**common, "lump": "OBJECT", "object_id": append_cache[ck]})

    ops = [o for o in ops if o.get("op") != "place"] + extra_adds

    # Group ops by tile.
    by_tile: dict = {}
    mov_ops: dict = {}
    for op in ops:
        t = tuple(op["tile"])
        if op.get("op") == "mov":
            mov_ops.setdefault(t, []).append(op)
        else:
            by_tile.setdefault(t, []).append(op)

    written = []

    # --- IFO edits ---------------------------------------------------------
    for (tx, ty), tile_ops in by_tile.items():
        stem = os.path.join(z["dir"], f"{tx}_{ty}")
        ifo = RI.read_ifo(stem + ".IFO")

        # deletes last + descending index so earlier indices stay valid
        def _key(op):
            return (1, -int(op.get("idx", 0))) if op.get("op") == "delete" else (0, 0)

        for op in sorted(tile_ops, key=_key):
            kind = op.get("op", "update")
            lump_name = op["lump"]
            lt = _LUMP_BY_NAME.get(lump_name)
            lump = ifo.lumps.get(lt)
            if lump is None or not lump.decoded:
                continue

            if kind == "update":
                i = int(op["idx"])
                if 0 <= i < len(lump.objects):
                    o = lump.objects[i]
                    _apply_obj_fields(o, op)
                    if lt == RI.LUMP_REGEN:
                        _apply_regen(o, op)
                    lump.dirty = True

            elif kind == "regen":
                i = int(op["idx"])
                if 0 <= i < len(lump.objects):
                    _apply_regen(lump.objects[i], op)
                    lump.dirty = True

            elif kind == "delete":
                i = int(op["idx"])
                if 0 <= i < len(lump.objects):
                    lump.objects.pop(i)
                    lump.dirty = True

            elif kind == "add":
                o = RI.IfoObject(
                    name=op.get("name", ""),
                    warp_id=int(op.get("warp_id", 0)),
                    event_id=int(op.get("event_id", 0)),
                    object_type=int(op.get("object_type", 0)),
                    object_id=int(op.get("object_id", 0)),
                    map_x=int(op.get("map_x", 0)),
                    map_y=int(op.get("map_y", 0)),
                    rot=tuple(op.get("rot", (0, 0, 0, 1))),
                    pos=tuple(op.get("pos", (0, 0, 0))),
                    scale=tuple(op.get("scale", (1, 1, 1))),
                    extra=_default_extra(lt),
                )
                if lt == RI.LUMP_REGEN:
                    _apply_regen(o, op)
                lump.objects.append(o)
                lump.dirty = True

        # Write this tile's IFO to every existing data root (1:1 sync).
        for label, path in config.write_targets(relmap, f"{tx}_{ty}.IFO"):
            _backup_once(path)
            RI.write_ifo(path, ifo)
        written.append({"tile": [tx, ty], "kind": "ifo"})

    # --- MOV (collision) edits --------------------------------------------
    for (tx, ty), tile_ops in mov_ops.items():
        stem = os.path.join(z["dir"], f"{tx}_{ty}")
        mov = RM.read_mov(stem + ".MOV", tx, ty)
        for op in tile_ops:
            for c in op.get("cells", []):
                row, col, val = int(c[0]), int(c[1]), int(c[2])
                if 0 <= row < mov.height and 0 <= col < mov.width:
                    mov.cells[row][col] = val
        for label, path in config.write_targets(relmap, f"{tx}_{ty}.MOV"):
            _backup_once(path)
            RM.write_mov(path, mov)
        written.append({"tile": [tx, ty], "kind": "mov"})

    return {"written": written, "roots": [lbl for lbl, _ in config.WRITE_ROOTS]}


def _default_extra(lt: int) -> dict:
    if lt == RI.LUMP_MOB:
        return {"ai_index": 0, "quest_name": ""}
    if lt == RI.LUMP_SOUND:
        return {"sound_file": "", "range": 0, "interval": 0}
    if lt == RI.LUMP_EFFECT:
        return {"effect_file": ""}
    if lt in (RI.LUMP_EVENT, RI.LUMP_AREA):
        return {"str1": "", "str2": ""}
    if lt == RI.LUMP_REGEN:
        return {"regen_name": "", "basic": [], "tactics": [],
                "interval": 0, "limit": 0, "range": 0, "tactics_point": 0}
    return {}


def _backup_once(path: str) -> None:
    """First time we touch a file in this process, snapshot a .mapforge.bak."""
    bak = path + ".mapforge.bak"
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
