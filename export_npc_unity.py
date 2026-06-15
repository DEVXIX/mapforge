"""Build the Unity NPC/monster crowd: a posed-static FBX of baked 1:1 placements
(named NPCPOSE__<charid>__<n>) + a per-character blend-shape idle FBX, mirroring
the proven animated-MORPH pipeline. The Unity importer (RoseNPCs.cs) drops the
static crowd in, then overlays the looping idle clip on each placement.

Why blend shapes (not VAT): Unity's established animation path is blend shapes +
looping AnimatorClips (the MORPH__/ANIM__ overlay). Reusing it means the NPCs get
the SAME 1:1 placement as the map (via the glTF->Blender bake) with zero coordinate
guesswork, plus real looping idle motion.

Writes into <bundle>:
  NPCs/Unity/npcs_posed.fbx        static crowd, named placements, textures embedded
  NPCs/Unity/Anim/<charid>.fbx     per-character blend-shape idle (textures embedded)
  NPCs/Unity/Tex/<tex>.png         base textures (for the blend-shape FBX materials)
  NPCs/npcs_unity.json             manifest: characters (fbx/match/frames/fps) + count
"""

from __future__ import annotations

import os
import sys
import json
import subprocess

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import export_map
import export_npcs
import export_npc_models as NM
import export_npc_vat as VAT          # per-frame skinning helpers
from export_npc_posed import _anim_globals
from rose_zms import read_zms

BLENDER = os.environ.get("BLENDER_EXE",
                         r"C:/Program Files/Blender Foundation/Blender 5.0/blender.exe")


def _yaw(rot):
    import math
    return 2.0 * math.atan2(rot[2], rot[3])


def _char_parts_geo(char_id, chrf, zsc):
    """Per-frame merged geometry for one character.
    Returns dict or None: verts(F,V,3 file-scale, ROSE Z-up), faces(T,3), uvs(V,2),
    face_mat(T,), mats[{name,tex,two}], nf, fps, rest0(V,3)."""
    if not (0 <= char_id < len(chrf.characters)):
        return None
    ch = chrf.characters[char_id]
    if not (ch and ch.objects):
        return None
    bones, anim = NM._char_bones_anim(chrf, ch)
    if not bones:
        return None
    G = NM._bone_globals(bones)
    nf = VAT._frame_count(anim)
    skins = VAT._skin_per_frame(bones, anim, G, nf)
    nb = len(bones)

    seqs, faces, uvs, face_mat, mats, voff = [], [], [], [], [], 0
    for (mesh_rel, mat_rel, two) in NM._char_parts(zsc, ch):
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
        B, W = VAT._part_skin_indices(zms, nb)
        seq = np.stack([VAT._posed_positions(pos100, B, W, skins[f]) for f in range(nf)], axis=0)
        nv = pos100.shape[0]
        mi = len(mats)
        base = "".join(c if (c.isalnum() or c == "_") else "_"
                       for c in os.path.splitext(os.path.basename(mesh_rel))[0])[:24]
        mats.append({"name": "NPCM%d_%s" % (mi, base), "rel": mat_rel, "two": bool(two)})
        pf = np.array(zms.faces, dtype=np.int64).reshape(-1, 3) + voff
        faces.append(pf)
        face_mat.extend([mi] * len(pf))
        uvs.append(np.array(zms.uvs[0], dtype=np.float32) if zms.uvs
                   else np.zeros((nv, 2), np.float32))
        seqs.append(seq)
        voff += nv
    if not seqs:
        return None
    verts = np.concatenate(seqs, axis=1)                 # (F, V, 3)
    return {"verts": verts, "faces": np.concatenate(faces, 0),
            "uvs": np.concatenate(uvs, 0), "face_mat": np.array(face_mat, dtype=np.int64),
            "mats": mats, "nf": nf, "fps": float(VAT.DEFAULT_FPS), "rest0": verts[0].copy()}


def build(key, bundle):
    import rose_chr
    from rose_zsc import read_zsc
    chrp = NM._resolve("NPC/LIST_NPC.CHR")
    zscp = NM._resolve("NPC/PART_NPC.ZSC")
    if not chrp or not zscp:
        raise RuntimeError("LIST_NPC.CHR / PART_NPC.ZSC not found")
    chrf = rose_chr.read_chr(chrp)
    zsc = read_zsc(zscp)

    udir = os.path.join(bundle, "NPCs", "Unity")
    adir = os.path.join(udir, "Anim")
    tdir = os.path.join(udir, "Tex")
    for d in (adir, tdir):
        os.makedirs(d, exist_ok=True)

    data = export_npcs.compute(key)
    geo_cache, tex_cache = {}, {}

    def export_tex(mat_rel):
        if not mat_rel:
            return None
        src = NM._resolve(mat_rel)
        if not src:
            return None
        if src.lower() in tex_cache:
            return tex_cache[src.lower()]
        base = "".join(c if (c.isalnum() or c == "_") else "_"
                       for c in os.path.splitext(os.path.basename(src))[0])[:40]
        fn = "%s.png" % base
        try:
            im = Image.open(src); im.load()
            im.convert("RGB").save(os.path.join(tdir, fn), "PNG")
        except Exception:
            fn = None
        tex_cache[src.lower()] = fn
        return fn

    def get_geo(cid):
        if cid in geo_cache:
            return geo_cache[cid]
        g = _char_parts_geo(cid, chrf, zsc)
        geo_cache[cid] = g
        return g

    # ---- per-character animated blend-shape FBX (built once) ----
    chars_meta = []
    built = set()

    def build_anim(cid):
        if cid in built:
            return get_geo(cid) is not None
        built.add(cid)
        g = get_geo(cid)
        if g is None:
            return False
        # pre-rotate (x, y, z) -> (x, -z, y): +90 X to match the glTF->Blender path
        verts = np.stack([g["verts"][..., 0], -g["verts"][..., 2], g["verts"][..., 1]],
                         axis=-1).astype(np.float32)
        npz = os.path.join(adir, "%d.npz" % cid)
        np.savez(npz, verts=verts, faces=g["faces"].astype(np.int32),
                 uvs=g["uvs"].astype(np.float32), face_mat=g["face_mat"].astype(np.int32),
                 fps=int(g["fps"]))
        mats = [{"name": m["name"], "tex": export_tex(m["rel"]), "two": m["two"]}
                for m in g["mats"]]
        mj = os.path.join(adir, "%d.mats.json" % cid)
        json.dump({"tex_dir": tdir, "mats": mats}, open(mj, "w"))
        fbx = os.path.join(adir, "%d.fbx" % cid)
        subprocess.run([BLENDER, "--background", "--python",
                        os.path.join(_HERE, "blender_npc_anim.py"), "--", npz, mj, fbx],
                       check=True)
        for p in (npz, mj):
            if os.path.exists(p):
                os.remove(p)
        chars_meta.append({"id": cid, "fbx": "Anim/%d.fbx" % cid,
                           "match": "NPCPOSE__%d" % cid, "frames": g["nf"],
                           "fps": g["fps"], "verts": int(g["rest0"].shape[0])})
        return True

    # ---- posed-static glb (named placements, frame-0 pose, merged per placement) ----
    glb = export_map.Glb()
    mesh_cache = {}                      # cid -> merged frame-0 mesh idx
    root_children = []
    counter = {}

    def char_mesh(cid):
        if cid in mesh_cache:
            return mesh_cache[cid]
        g = get_geo(cid)
        if g is None:
            mesh_cache[cid] = None
            return None
        prims = []
        rest = g["rest0"]                # frame 0 (file scale, ROSE Z-up)
        fm = g["faces"]
        for mi, m in enumerate(g["mats"]):
            sel = np.where(g["face_mat"] == mi)[0]
            if len(sel) == 0:
                continue
            tri = fm[sel].reshape(-1)
            used = np.unique(tri)
            remap = {int(o): i for i, o in enumerate(used)}
            idx = np.array([remap[int(v)] for v in tri], dtype=np.uint32)
            pos = rest[used].astype(np.float32)
            uv = g["uvs"][used].astype(np.float32)
            mat = glb.material_for_texture(NM._resolve(m["rel"]) if m["rel"] else None,
                                           alpha=False, mode="OPAQUE", double=m["two"],
                                           kind="npc")
            prims.append({"attributes": {"POSITION": glb.add_vec3(pos),
                                         "TEXCOORD_0": glb.add_vec2(uv)},
                          "indices": glb.add_indices(idx), "mode": 4, "material": mat})
        if not prims:
            mesh_cache[cid] = None
            return None
        glb.meshes.append({"primitives": prims, "name": "NPCMESH_%d" % cid})
        mesh_cache[cid] = len(glb.meshes) - 1
        return mesh_cache[cid]

    def place(cid, pos, rot, scale):
        if not build_anim(cid):
            return
        mi = char_mesh(cid)
        if mi is None:
            return
        n = counter.get(cid, 0); counter[cid] = n + 1
        M = export_map.compose(pos, rot, scale)
        root_children.append(glb.node(mesh=mi, matrix=M.flatten(order="F"),
                                      name="NPCPOSE__%d__%d" % (cid, n)))

    n_npc = n_mob = 0
    for npc in data["npcs"]:
        if npc["kind"] == "NPC":
            place(npc["object_id"], npc["pos"], npc["rot"], npc["scale"])
            n_npc += 1
        else:
            seen, mobs = set(), []
            for mb in npc.get("mobs", []):
                if mb["id"] not in seen:
                    seen.add(mb["id"]); mobs.append(mb["id"])
            mobs = [m for m in mobs if get_geo(m) is not None]
            nm = min(len(mobs), 8)
            ring = max(800.0, nm * 280.0)
            for k in range(nm):
                ang = (k / float(nm)) * 2.0 * np.pi if nm > 1 else 0.0
                ox = np.cos(ang) * ring if nm > 1 else 0.0
                oy = np.sin(ang) * ring if nm > 1 else 0.0
                p = [npc["pos"][0] + ox, npc["pos"][1] + oy, npc["pos"][2]]
                q = (0.0, 0.0, float(np.sin((ang + np.pi) / 2)), float(np.cos((ang + np.pi) / 2)))
                place(mobs[k], p, q, [1, 1, 1])
                n_mob += 1

    posed_glb = os.path.join(udir, "_npcs_posed.glb")
    glb.write(posed_glb, root_children)
    posed_fbx = os.path.join(udir, "npcs_posed.fbx")
    subprocess.run([BLENDER, "--background", "--python",
                    os.path.join(_HERE, "blender_npc_posed.py"), "--", posed_glb, posed_fbx],
                   check=True)
    if os.path.exists(posed_glb):
        os.remove(posed_glb)

    manifest = {"zone": key,
                "posed_fbx": "Unity/npcs_posed.fbx",
                "anim_dir": "Unity/Anim",
                "rose_to_unity": {"rotate_x_deg": -90, "scale": 0.01},
                "characters": chars_meta,
                "placements": len(root_children)}
    with open(os.path.join(bundle, "NPCs", "npcs_unity.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return {"characters": len(chars_meta), "npc_placements": n_npc,
            "monster_placements": n_mob, "nodes": len(root_children)}


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = os.path.join(_HERE, "exports", "%s_fbx" % k)
    os.makedirs(os.path.join(out, "NPCs"), exist_ok=True)
    print(json.dumps(build(k, out), indent=2))
