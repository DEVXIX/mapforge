"""Export a zone's NPC (MOB lump) + monster-spawn (REGEN lump) placements for the
UE5 importer.

Writes NPCs/npcs.json with every placement (kind, object_id, world pos, rotation)
plus the map's ROSE world bounds so the UE5 script can self-calibrate ROSE coords
onto the imported map (UE5 is Z-up/cm like ROSE, so it's a per-axis linear remap).

MOB    = fixed NPCs   (object_id -> LIST_NPC.CHR character)
REGEN  = monster spawn points (extra.basic/tactics = the mobs that spawn there)

Animated skeletal meshes are a later step; this locks the placements + coordinate
mapping first (placeholders go down at these spots so the mapping can be verified).
"""

from __future__ import annotations

import os
import sys
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import zone as Z
import rose_ifo as RI
import rose_map

TILE = config.TILE_WORLD_SIZE   # 16000 ROSE units / tile


def _mob_list(o):
    out = []
    for m in (o.extra.get("basic", []) or []):
        out.append({"name": getattr(m, "name", ""), "id": getattr(m, "mob_id", -1),
                    "count": getattr(m, "count", 0)})
    return out


def compute(key):
    z = Z.find_zone(key)
    if not z:
        raise KeyError(key)
    zon = rose_map.read_zon(Z._find_zon(z))
    cy = zon.info.center_y or 32

    npcs = []
    txs, tys = [], []
    for x, y, stem in Z._tiles_in(z["dir"]):
        txs.append(x); tys.append(y)
        try:
            ifo = RI.read_ifo(stem + ".IFO")
        except Exception:
            continue
        for lump, kind in ((RI.LUMP_MOB, "NPC"), (RI.LUMP_REGEN, "MONSTER")):
            lp = ifo.lumps.get(lump)
            if not lp:
                continue
            for o in lp.objects:
                rec = {"kind": kind, "object_id": o.object_id,
                       "pos": [float(v) for v in o.pos], "rot": [float(v) for v in o.rot],
                       "scale": [float(v) for v in o.scale]}
                if kind == "MONSTER":
                    rec["regen_name"] = o.extra.get("regen_name", "")
                    rec["mobs"] = _mob_list(o)
                npcs.append(rec)

    # ROSE map world bounds from the tile grid — same frame as the placements and
    # the terrain mesh (terrain ox=x*TILE, oy=(2*cy-y)*TILE; object pos already
    # carries +CENTER_WORLD so it lands in this same frame).
    if not txs:
        raise RuntimeError("no tiles found for %s" % key)
    minx, maxx = min(txs) * TILE, (max(txs) + 1) * TILE
    miny, maxy = (2 * cy - max(tys)) * TILE, (2 * cy - min(tys) + 1) * TILE
    zs = [n["pos"][2] for n in npcs] or [0.0]
    bounds = {"min": [minx, miny, min(zs) - 5000.0], "max": [maxx, maxy, max(zs) + 5000.0]}

    return {"zone": key, "center_y": cy, "tile_world_size": TILE,
            "rose_bounds": bounds, "npcs": npcs}


def build(key, bundle):
    out_dir = os.path.join(bundle, "NPCs")
    os.makedirs(out_dir, exist_ok=True)
    data = compute(key)
    with open(os.path.join(out_dir, "npcs.json"), "w") as f:
        json.dump(data, f, indent=1)
    return {"npcs": len([n for n in data["npcs"] if n["kind"] == "NPC"]),
            "monsters": len([n for n in data["npcs"] if n["kind"] == "MONSTER"])}


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = os.path.join(_HERE, "exports", "%s_fbx" % k)
    os.makedirs(out, exist_ok=True)
    print(json.dumps(build(k, out), indent=2))
