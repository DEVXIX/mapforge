"""Bake the zone's NPCs/monsters as a VERTEX ANIMATION TEXTURE (VAT) crowd for UE5.

Why VAT: UE5's *skeletal* glTF import silently rescales/rotates ROSE characters
(the whole manual-fix saga). VAT sidesteps it entirely — each character is a STATIC
mesh (which UE imports 1:1, proven by the map) whose vertices are pushed each frame
by a World-Position-Offset material that reads a baked "position texture". Reliable
placement + real idle motion, no skeleton at import time.

What this writes (into <bundle>):
  npcs_vat.glb              one static mesh per unique character (rest = idle frame 0),
                            built through the SAME ROSE_zone root as the map so it
                            imports upright & correctly sized. Per-vertex row index is
                            stored in COLOR_0 (UV1 is dropped by UE's glTF importer;
                            vertex colour survives AND stops vertex welding).
  VAT/<id>.png             position texture: width = #frames, height = #verts,
                            RGB = normalised per-vertex offset (idle frame f - frame 0),
                            already in UE space (Y-flipped: C*R = diag(1,-1,1)).
  VAT/manifest.json        per-character params (frames, fps, verts, decode range) +
                            every placement (UE world pos + yaw). The UE importer
                            (import_npcs_vat_ue.py) builds the WPO material from this.

Key transform fact (from the posed-static success): a ROSE vector maps to UE by the
PURE Y-flip diag(1,-1,1) (the ROSE_zone root's 0.01 and UE's x100 cancel). So the
mesh needs NO actor scale/roll — only world position + yaw — and the per-frame
offsets are baked with the same Y-flip.
"""

from __future__ import annotations

import os
import sys
import json

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export_map                       # Glb builder + ROSE_zone root
import export_npcs                      # placement data
import export_npc_models as NM          # rig/parts loaders + math
from export_npc_posed import _anim_globals
from rose_zms import read_zms

ARRAY_BUFFER = 34962
MAX_FRAMES = 60                          # texture width cap (idle loops are short)
DEFAULT_FPS = 30.0
YFLIP = np.array([1.0, -1.0, 1.0])       # ROSE -> UE (C*R), positions & offsets alike


def _frame_count(anim):
    nf = 1
    if anim:
        for ch in anim["rot"]:
            if ch:
                nf = max(nf, len(ch))
        for ch in anim["pos"]:
            if ch:
                nf = max(nf, len(ch))
    return min(nf, MAX_FRAMES)


def _skin_per_frame(bones, anim, G, nf):
    """List (len nf) of (nb,4,4) skin matrices A[f]·inv(G)."""
    nb = len(bones)
    Ginv = np.array([np.linalg.inv(G[i]) for i in range(nb)])
    out = []
    for f in range(nf):
        A = _anim_globals(bones, anim, f)
        out.append(np.array([A[i] @ Ginv[i] for i in range(nb)]))
    return out


def _part_skin_indices(zms, nb):
    nv = len(zms.positions)
    B = np.zeros((nv, 4), dtype=np.int64)
    W = np.zeros((nv, 4), dtype=np.float64)
    if zms.bones and zms.weights and zms.bone_indices:
        pal = zms.bone_indices
        for v in range(nv):
            bi, w = zms.bones[v], zms.weights[v]
            for k in range(4):
                pi = bi[k] if k < len(bi) else 0
                gj = pal[pi] if 0 <= pi < len(pal) else 0
                B[v, k] = gj if 0 <= gj < nb else 0
                W[v, k] = w[k] if k < len(w) else 0.0
        s = W.sum(1, keepdims=True)
        W = np.where(s > 0, W / s, np.array([1.0, 0, 0, 0]))
    else:
        W[:, 0] = 1.0
    return B, W


def _posed_positions(pos100, B, W, skins_f):
    """Posed positions of one part at a single frame (nv,3)."""
    nv = pos100.shape[0]
    posh = np.concatenate([pos100, np.ones((nv, 1))], axis=1)
    out = np.zeros((nv, 3))
    for k in range(4):
        out += W[:, k:k + 1] * np.einsum("nij,nj->ni", skins_f[B[:, k]], posh)[:, :3]
    return out


def _char_vat(char_id, chrf, zsc):
    """Return (parts, vat_offsets(total,nf,3), nf, total_verts) or None."""
    if not (0 <= char_id < len(chrf.characters)):
        return None
    ch = chrf.characters[char_id]
    if not (ch and ch.objects):
        return None
    bones, anim = NM._char_bones_anim(chrf, ch)
    if not bones:
        return None
    G = NM._bone_globals(bones)
    nf = _frame_count(anim)
    skins = _skin_per_frame(bones, anim, G, nf)
    nb = len(bones)

    parts, frames_acc, row0 = [], None, 0
    for (mesh_rel, mat_rel, two_sided) in NM._char_parts(zsc, ch):
        ab = NM._resolve(mesh_rel)
        if not ab:
            continue
        try:
            zms = read_zms(ab)
        except Exception:
            continue
        if not zms.positions or not zms.faces:
            continue
        scale = 100.0 if zms.version >= 7 else 1.0
        pos100 = np.array(zms.positions, dtype=np.float64) * scale
        B, W = _part_skin_indices(zms, nb)
        # posed positions for every frame -> (nf, nv, 3)
        seq = np.stack([_posed_positions(pos100, B, W, skins[f]) for f in range(nf)], axis=0)
        rest = seq[0]                                   # idle frame 0 = the static mesh
        off = (seq - rest[None]) * YFLIP                # (nf,nv,3) offsets in UE space
        nv = rest.shape[0]
        nrm = (np.array(zms.normals, dtype=np.float64) if zms.normals
               else np.zeros((nv, 3)))
        uv = (np.array(zms.uvs[0], dtype=np.float32) if zms.uvs
              else np.zeros((nv, 2), np.float32))
        idx = np.array(zms.faces, dtype=np.uint32).reshape(-1)
        parts.append({"rest": rest.astype(np.float32), "nrm": nrm.astype(np.float32),
                      "uv": uv, "idx": idx, "mat": mat_rel, "two": bool(two_sided),
                      "row0": row0, "n": nv})
        # offsets transposed to (nv, nf, 3) and accumulated by global row
        frames_acc = off.transpose(1, 0, 2) if frames_acc is None \
            else np.concatenate([frames_acc, off.transpose(1, 0, 2)], axis=0)
        row0 += nv

    if not parts or frames_acc is None:
        return None
    return parts, frames_acc, nf, row0


def _row_color(row, total):
    """Per-vertex COLOR_0 (uint8 VEC4) encoding the VAT row as hi/lo bytes."""
    hi = (row >> 8) & 0xFF
    lo = row & 0xFF
    c = np.zeros((len(row), 4), dtype=np.uint8)
    c[:, 0] = hi
    c[:, 1] = lo
    c[:, 3] = 255
    return c


def _add_color(glb, arr_u8):
    bv = glb._view(arr_u8.tobytes(), ARRAY_BUFFER)
    glb.accessors.append({"bufferView": bv, "componentType": 5121, "count": len(arr_u8),
                          "type": "VEC4", "normalized": True})
    return len(glb.accessors) - 1


def _yaw_ue(rot):
    # ROSE Z rotation -> UE yaw, negated for the Y-flip
    return -np.degrees(2.0 * np.arctan2(rot[2], rot[3]))


def build(key, bundle):
    import rose_chr
    from rose_zsc import read_zsc
    chrp = NM._resolve("NPC/LIST_NPC.CHR")
    zscp = NM._resolve("NPC/PART_NPC.ZSC")
    if not chrp or not zscp:
        raise RuntimeError("LIST_NPC.CHR / PART_NPC.ZSC not found")
    chrf = rose_chr.read_chr(chrp)
    zsc = read_zsc(zscp)

    vat_dir = os.path.join(bundle, "VAT")
    tex_dir = os.path.join(vat_dir, "Tex")
    os.makedirs(tex_dir, exist_ok=True)
    glb = export_map.Glb()
    data = export_npcs.compute(key)

    chars = {}            # char_id -> {"node": mesh_idx, "params": {...}}
    root_children = []
    tex_cache = {}        # src abs path -> exported png filename

    def export_tex(mat_rel):
        if not mat_rel:
            return None
        src = NM._resolve(mat_rel)
        if not src:
            return None
        key_s = src.lower()
        if key_s in tex_cache:
            return tex_cache[key_s]
        base = "".join(c if (c.isalnum() or c == "_") else "_"
                       for c in os.path.splitext(os.path.basename(src))[0])[:40]
        fn = "%s.png" % base
        n = 1
        while fn in tex_cache.values() and os.path.exists(os.path.join(tex_dir, fn)):
            fn = "%s_%d.png" % (base, n); n += 1
        try:
            im = Image.open(src); im.load()
            im.convert("RGB").save(os.path.join(tex_dir, fn), "PNG")
        except Exception:
            fn = None
        tex_cache[key_s] = fn
        return fn

    def ensure_char(cid):
        if cid in chars:
            return chars[cid]
        res = _char_vat(cid, chrf, zsc)
        if res is None:
            chars[cid] = None
            return None
        parts, frames_acc, nf, total = res
        if total > 8192:
            print("  [vat] char %d has %d verts (>8192) — skipped" % (cid, total))
            chars[cid] = None
            return None
        # --- position texture: H=total verts, W=frames, RGB=normalised offset ---
        gmin = float(frames_acc.min())
        gmax = float(frames_acc.max())
        rng = (gmax - gmin) or 1.0
        norm = np.clip((frames_acc - gmin) / rng, 0.0, 1.0)        # (total, nf, 3)
        img = (norm * 255.0 + 0.5).astype(np.uint8)                # (H, W, 3)
        Image.fromarray(img, "RGB").save(os.path.join(vat_dir, "%d.png" % cid))
        # --- glb multi-primitive static mesh (rest pose + COLOR_0 row index) ---
        prims, parts_info = [], []
        for p in parts:
            rows = np.arange(p["row0"], p["row0"] + p["n"], dtype=np.int64)
            col = _row_color(rows, total)
            attr = {"POSITION": glb.add_vec3(p["rest"]),
                    "NORMAL": glb.add_vec3(p["nrm"]),
                    "TEXCOORD_0": glb.add_vec2(p["uv"]),
                    "COLOR_0": _add_color(glb, col)}
            mat = glb.material_for_texture(NM._resolve(p["mat"]) if p["mat"] else None,
                                           alpha=False, mode="OPAQUE", double=p["two"],
                                           kind="npcvat")
            prims.append({"attributes": attr, "indices": glb.add_indices(p["idx"]),
                          "mode": 4, "material": mat})
            parts_info.append({"tex": export_tex(p["mat"]), "two": p["two"]})
        glb.meshes.append({"primitives": prims, "name": "NPCVAT_%d" % cid})
        mesh_idx = len(glb.meshes) - 1
        params = {"id": cid, "frames": nf, "fps": DEFAULT_FPS, "verts": total,
                  "decode_min": gmin, "decode_range": rng,
                  "anim_speed": DEFAULT_FPS / max(nf, 1),
                  "vat": "VAT/%d.png" % cid, "mesh": "NPCVAT_%d" % cid,
                  "parts": parts_info}
        chars[cid] = {"mesh_idx": mesh_idx, "params": params}
        return chars[cid]

    placements = []
    for npc in data["npcs"]:
        if npc["kind"] == "NPC":
            c = ensure_char(npc["object_id"])
            if not c:
                continue
            x, y, z = npc["pos"]
            placements.append({"id": npc["object_id"], "x": x, "y": -y, "z": z,
                               "yaw": _yaw_ue(npc["rot"])})
        else:
            seen, mobs = set(), []
            for mb in npc.get("mobs", []):
                if mb["id"] not in seen:
                    seen.add(mb["id"]); mobs.append(mb["id"])
            mobs = [m for m in mobs if ensure_char(m)]
            nm = min(len(mobs), 8)
            ring = max(800.0, nm * 280.0)
            for k in range(nm):
                ang = (k / float(nm)) * 2.0 * np.pi if nm > 1 else 0.0
                ox = np.cos(ang) * ring if nm > 1 else 0.0
                oy = np.sin(ang) * ring if nm > 1 else 0.0
                x, y, z = npc["pos"][0] + ox, npc["pos"][1] + oy, npc["pos"][2]
                placements.append({"id": mobs[k], "x": x, "y": -y, "z": z,
                                   "yaw": -np.degrees(ang + np.pi)})

    # nodes: one per unique character mesh, at origin under ROSE_zone (-> upright, sized)
    node_of = {}
    for cid, c in chars.items():
        if c:
            node_of[cid] = glb.node(mesh=c["mesh_idx"], name="NPCVAT_%d" % cid)
            root_children.append(node_of[cid])
    glb.write(os.path.join(bundle, "npcs_vat.glb"), root_children)

    manifest = {"zone": key,
                "characters": [c["params"] for c in chars.values() if c],
                "placements": placements}
    with open(os.path.join(vat_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return {"characters": len(manifest["characters"]),
            "placements": len(placements),
            "glb_bytes": os.path.getsize(os.path.join(bundle, "npcs_vat.glb"))}


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = os.path.join(_HERE, "exports", "%s_fbx" % k)
    os.makedirs(out, exist_ok=True)
    print(json.dumps(build(k, out), indent=2))
