"""Export a zone's animated (MORPH) objects as vertex-animated FBX clips + a
placement manifest, for Unity.

Each MORPH object references a ZMS mesh + a ZMO motion. ZMO POSITION channels
(one per vertex) become per-frame vertex positions -> baked to FBX blend shapes
+ an AnimationClip via Blender. ocean_ring-style UV-only clips are reported but
not baked (they're texture flow -> use a scroll shader).

Usage:  python export_anim.py JPT01-1
Output: exports/<zone>_anim/  (per-object .fbx + animations.json)
"""

from __future__ import annotations

import os
import sys
import json
import subprocess

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config
import zone as Z
import rose_ifo as RI
import rose_zmo
from parse_stb import StbFile
from rose_zms import read_zms

BLENDER = os.environ.get("BLENDER_EXE",
                         r"C:/Program Files/Blender Foundation/Blender 5.0/blender.exe")


def resolve(rel):
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


def build(key, out_root=None, frame_stride=1):
    out_root = out_root or os.path.join(_HERE, "exports")
    out = os.path.join(out_root, "%s_anim" % key)
    os.makedirs(out, exist_ok=True)

    stb = StbFile(os.path.join(config.STB_DIR, "LIST_MORPH_OBJECT.STB"))
    z = Z.find_zone(key)
    if not z:
        raise KeyError(key)

    # MORPH placements (ROSE world coords)
    placements = {}
    for x, y, stem in Z._tiles_in(z["dir"]):
        ifo = RI.read_ifo(stem + ".IFO")
        ml = ifo.lumps.get(RI.LUMP_MORPH)
        if not ml:
            continue
        for o in ml.objects:
            placements.setdefault(o.object_id, []).append(
                {"pos": list(o.pos), "rot": list(o.rot), "scale": list(o.scale)})

    anims, skipped = {}, []
    for oid in sorted(placements):
        mesh_rel = stb.get(oid, 1)
        mot_rel = stb.get(oid, 2)
        mp = resolve(mesh_rel) if mesh_rel else None
        zp = resolve(mot_rel) if mot_rel else None
        if not mp or not zp:
            skipped.append((oid, "missing mesh/motion"))
            continue
        zms = read_zms(mp)
        zmo = rose_zmo.read_zmo(zp)
        pos_ch = {c.refer_id: c for c in zmo.channels if c.ctype == rose_zmo.CT_POSITION}
        if not pos_ch:
            skipped.append((oid, "%s = UV/texture flow (use scroll shader)" % os.path.basename(mot_rel)))
            continue

        nv = len(zms.positions)
        # ZMO POSITION channels are already in external (mesh*100) scale; only the
        # non-animated rest verts need the v7 mesh scale to sit at the same size.
        mesh_scale = 100.0 if zms.version >= 7 else 1.0
        frames = list(range(0, zmo.num_frames, max(1, frame_stride)))
        verts = np.zeros((len(frames), nv, 3), dtype=np.float32)
        rest = np.array(zms.positions, dtype=np.float32) * mesh_scale
        for fi, f in enumerate(frames):
            for v in range(nv):
                ch = pos_ch.get(v)
                verts[fi, v] = np.array(ch.frames[f], dtype=np.float32) if ch else rest[v]

        faces = np.array(zms.faces, dtype=np.int32)
        uvs = np.array(zms.uvs[0], dtype=np.float32) if zms.uvs else np.zeros((nv, 2), np.float32)
        name = os.path.splitext(os.path.basename(mesh_rel))[0]
        npz = os.path.join(out, name + ".npz")
        np.savez(npz, verts=verts, faces=faces, uvs=uvs, fps=max(1, zmo.fps // max(1, frame_stride)))
        fbx = os.path.join(out, name + ".fbx")
        subprocess.run([BLENDER, "--background", "--python",
                        os.path.join(_HERE, "blender_vertex_anim.py"), "--", npz, fbx], check=True)
        os.remove(npz)
        anims[oid] = {"fbx": name + ".fbx", "frames": len(frames),
                      "fps": zmo.fps, "verts": nv}

    manifest = {
        "zone": key,
        "rose_to_unity": {"rotate_x_deg": -90, "scale": 0.01},   # same as the map glb root
        "anims": anims,
        "placements": placements,
    }
    with open(os.path.join(out, "animations.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    return {"out": out, "animated_objects": len(anims),
            "placements": sum(len(v) for v in placements.values()),
            "skipped": skipped}


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    print("building animations for %s" % k)
    print(json.dumps(build(k), indent=2))
