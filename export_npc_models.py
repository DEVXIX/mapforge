"""Build one skinned, idle-animated glTF (.glb) per unique NPC / monster character,
then (via Blender) a skeletal .fbx — for import into UE5/Unity as animated actors.

Mirrors the web viewer's rig assembly (rignpc.js + /api/zone/<key>/rig):
  - skeleton from the character's .ZMD (bone local TRS bind pose; rot w,x,y,z)
  - skin from each body part's .ZMS (positions x100 for v7, JOINTS/WEIGHTS from the
    per-vertex palette resolved to global bone indices)
  - idle clip from the character's standing .ZMO (rotation/position channels)
Characters are built at file scale (bones x1, mesh x100) — the same space the web
binds in — so the skinning is correct.

Output: <bundle>/NPCs/Models/<id>.glb  and  <id>.fbx  (+ a models index in npcs.json).
"""

from __future__ import annotations

import os
import sys
import json
import struct
import base64
import subprocess

import numpy as np
from PIL import Image
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import zone as Z
import rose_ifo as RI
import rose_chr
import rose_zmd
import rose_zmo
from rose_zsc import read_zsc
from rose_zms import read_zms

BLENDER = os.environ.get("BLENDER_EXE",
                         r"C:/Program Files/Blender Foundation/Blender 5.0/blender.exe")


# ----------------------------------------------------------------- assets ----
def _resolve(rel):
    if not rel:
        return None
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


# ----------------------------------------------------------------- math ------
def _quat_xyzw_to_mat(x, y, z, w):
    n = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w),     0],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w),     0],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1]], dtype=np.float64)


def _bone_globals(bones):
    """bones: list of dicts {parent, pos(3), rot(xyzw)}. Returns world rest matrices."""
    g = [None] * len(bones)
    for i, b in enumerate(bones):
        L = _quat_xyzw_to_mat(*b["rot"])
        L[0, 3], L[1, 3], L[2, 3] = b["pos"]
        p = b["parent"]
        g[i] = (g[p] @ L) if (0 <= p < i and g[p] is not None) else L
    return g


def _qmul(a, b):                                   # hamilton product, xyzw * xyzw
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


# ----------------------------------------------------- glTF (skin + anim) ----
class Gltf:
    def __init__(self):
        self.bin = bytearray()
        self.bufferViews = []
        self.accessors = []
        self.nodes = []
        self.meshes = []
        self.materials = []
        self.textures = []
        self.images = []
        self.samplers = [{"magFilter": 9729, "minFilter": 9729}]

    def _align(self, n=4):
        while len(self.bin) % n:
            self.bin += b"\x00"

    def _view(self, data, target=None):
        self._align()
        off = len(self.bin)
        self.bin += data
        bv = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bufferViews.append(bv)
        return len(self.bufferViews) - 1

    def acc_f32(self, arr, type_, target=None, minmax=False):
        arr = np.asarray(arr, dtype=np.float32)
        bv = self._view(arr.tobytes(), target)
        comp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[type_]
        a = {"bufferView": bv, "componentType": 5126, "count": int(arr.size // comp), "type": type_}
        if minmax and arr.size:
            v = arr.reshape(-1, comp)
            a["min"] = [float(x) for x in v.min(0)]
            a["max"] = [float(x) for x in v.max(0)]
        self.accessors.append(a)
        return len(self.accessors) - 1

    def acc_u16(self, arr, type_="SCALAR", target=None):
        arr = np.asarray(arr, dtype=np.uint16)
        bv = self._view(arr.tobytes(), target)
        comp = {"SCALAR": 1, "VEC4": 4}[type_]
        self.accessors.append({"bufferView": bv, "componentType": 5123,
                               "count": int(arr.size // comp), "type": type_})
        return len(self.accessors) - 1

    def image_png(self, png_bytes):
        bv = self._view(png_bytes)
        self.images.append({"bufferView": bv, "mimeType": "image/png"})
        self.textures.append({"sampler": 0, "source": len(self.images) - 1})
        return len(self.textures) - 1

    def material(self, name, tex_idx, two_sided):
        m = {"name": name, "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0},
             "doubleSided": bool(two_sided)}
        if tex_idx is not None:
            m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_idx}
        self.materials.append(m)
        return len(self.materials) - 1

    def node(self, **kw):
        self.nodes.append(kw)
        return len(self.nodes) - 1

    def to_glb(self):
        gltf = {
            "asset": {"version": "2.0", "generator": "mapforge"},
            "scene": 0, "scenes": [{"nodes": self._scene_nodes}],
            "nodes": self.nodes, "meshes": self.meshes,
            "materials": self.materials, "accessors": self.accessors,
            "bufferViews": self.bufferViews, "buffers": [{"byteLength": len(self.bin)}],
        }
        if self.images:
            gltf["images"] = self.images
            gltf["textures"] = self.textures
            gltf["samplers"] = self.samplers
        if getattr(self, "skins", None):
            gltf["skins"] = self.skins
        if getattr(self, "animations", None):
            gltf["animations"] = self.animations
        jchunk = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        while len(jchunk) % 4:
            jchunk += b" "
        bchunk = bytes(self.bin)
        while len(bchunk) % 4:
            bchunk += b"\x00"
        out = bytearray()
        out += struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(jchunk) + 8 + len(bchunk))
        out += struct.pack("<II", len(jchunk), 0x4E4F534A) + jchunk
        out += struct.pack("<II", len(bchunk), 0x004E4942) + bchunk
        return bytes(out)


# ----------------------------------------------------------- texture cache ---
_TEX_PNG = {}


def _png_for(mat_rel):
    if not mat_rel:
        return None
    k = mat_rel.lower()
    if k in _TEX_PNG:
        return _TEX_PNG[k]
    ab = _resolve(mat_rel)
    out = None
    if ab:
        try:
            im = Image.open(ab); im.load()
            buf = BytesIO(); im.convert("RGB").save(buf, "PNG"); out = buf.getvalue()
        except Exception:
            out = None
    _TEX_PNG[k] = out
    return out


# ----------------------------------------------------------- build one -------
def _build_char_glb(bones, anim, parts, out_glb):
    g = Gltf()

    # ROSE is Z-up; glTF/UE expect a Y-up conversion. Bake ONLY the rotation
    # rotateX(-90) into the bones/mesh/animation (NOT scale — a scale node makes
    # UE shrink the character once the animation drives the root). Scale is applied
    # on the UE actor instead. (x,y,z) -> (x, z, -y).
    RX = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    QRX = (-0.7071067811865476, 0.0, 0.0, 0.7071067811865476)    # quat xyzw of rotateX(-90)
    R4 = np.eye(4); R4[:3, :3] = RX

    # --- skeleton nodes ---
    globals_ = _bone_globals(bones)
    is_root = [not (0 <= b["parent"] < len(bones) and b["parent"] != i) for i, b in enumerate(bones)]
    bone_nodes = []
    children = {i: [] for i in range(len(bones))}
    roots = []
    for i, b in enumerate(bones):
        if is_root[i]:                       # bake the rotation into the root bone only
            t = [float(v) for v in (RX @ np.array(b["pos"], dtype=np.float64))]
            q = [float(v) for v in _qmul(QRX, b["rot"])]
        else:
            t = [float(v) for v in b["pos"]]
            q = [float(v) for v in b["rot"]]
        bone_nodes.append(g.node(name="b%d" % i, translation=t, rotation=q))
    for i, b in enumerate(bones):
        p = b["parent"]
        if 0 <= p < len(bones) and p != i:
            children[p].append(bone_nodes[i])
        else:
            roots.append(bone_nodes[i])
    for i in range(len(bones)):
        if children[i]:
            g.nodes[bone_nodes[i]]["children"] = children[i]

    # inverse bind matrices (column-major) — in the rotation-baked world space
    ibm = []
    for gm in globals_:
        inv = np.linalg.inv(R4 @ gm)
        ibm.extend([float(x) for x in inv.flatten(order="F")])
    ibm_acc = g.acc_f32(ibm, "MAT4")
    g.skins = [{"joints": bone_nodes, "inverseBindMatrices": ibm_acc, "skeleton": roots[0]}]

    # --- skinned mesh parts ---
    mesh_nodes = []
    nb = len(bones)
    for (mesh_rel, mat_rel, two_sided) in parts:
        ab = _resolve(mesh_rel)
        if not ab:
            continue
        try:
            zms = read_zms(ab)
        except Exception:
            continue
        nv = len(zms.positions)
        if nv == 0 or not zms.faces:
            continue
        scale = 100.0 if zms.version >= 7 else 1.0
        pos = ((np.array(zms.positions, dtype=np.float64) * scale) @ RX.T).astype(np.float32)
        nrm = ((np.array(zms.normals, dtype=np.float64) @ RX.T).astype(np.float32) if zms.normals
               else np.zeros((nv, 3), np.float32))
        uv = (np.array(zms.uvs[0], dtype=np.float32) if zms.uvs else np.zeros((nv, 2), np.float32))
        idx = np.array(zms.faces, dtype=np.uint16).reshape(-1)

        # JOINTS/WEIGHTS — resolve palette -> global bone idx; default to root
        joints = np.zeros((nv, 4), np.uint16)
        weights = np.zeros((nv, 4), np.float32)
        if zms.bones and zms.weights and zms.bone_indices:
            pal = zms.bone_indices
            for i in range(nv):
                bi, w = zms.bones[i], zms.weights[i]
                for k in range(4):
                    j = bi[k] if k < len(bi) else 0
                    gj = pal[j] if 0 <= j < len(pal) else 0
                    joints[i, k] = gj if 0 <= gj < nb else 0
                    weights[i, k] = w[k] if k < len(w) else 0.0
            s = weights.sum(1, keepdims=True)
            weights = np.where(s > 0, weights / s, np.array([1, 0, 0, 0], np.float32))
        else:
            weights[:, 0] = 1.0          # rigid-attach to root bone

        attrs = {
            "POSITION": g.acc_f32(pos, "VEC3", target=34962, minmax=True),
            "NORMAL": g.acc_f32(nrm, "VEC3", target=34962),
            "TEXCOORD_0": g.acc_f32(uv, "VEC2", target=34962),
            "JOINTS_0": g.acc_u16(joints, "VEC4", target=34962),
            "WEIGHTS_0": g.acc_f32(weights, "VEC4", target=34962),
        }
        ix = g.acc_u16(idx, "SCALAR", target=34963)
        png = _png_for(mat_rel)
        tex_idx = g.image_png(png) if png else None
        mname = "M_" + os.path.splitext(os.path.basename(mat_rel or "char"))[0]
        mat_idx = g.material(mname, tex_idx, two_sided)
        g.meshes.append({"primitives": [{"attributes": attrs, "indices": ix, "material": mat_idx}]})
        mesh_nodes.append(g.node(mesh=len(g.meshes) - 1, skin=0, name="part%d" % len(mesh_nodes)))

    if not mesh_nodes:
        return False

    # --- idle animation ---
    if anim and anim["frames"] > 1:
        fps = anim["fps"] or 30
        nfr = anim["frames"]
        times = np.arange(nfr, dtype=np.float32) / float(fps)
        t_acc = g.acc_f32(times, "SCALAR")
        samplers, channels = [], []
        for i in range(nb):
            if is_root[i]:
                # Do NOT animate the root bone. Its rest transform has the up-axis
                # rotation baked in; animating it makes UE's skeletal/glTF import
                # float + shrink the whole NPC. Idle root motion is a tiny sway, so
                # leaving it at rest keeps the character grounded + full-size while
                # every other bone still animates.
                continue
            r = anim["rot"][i] if i < len(anim["rot"]) else None
            if r:
                out = np.array(r, dtype=np.float32).reshape(-1)   # xyzw per frame
                s_acc = g.acc_f32(out, "VEC4")
                samplers.append({"input": t_acc, "output": s_acc, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": bone_nodes[i], "path": "rotation"}})
            p = anim["pos"][i] if i < len(anim["pos"]) else None
            if p:
                out = np.array(p, dtype=np.float32).reshape(-1)
                s_acc = g.acc_f32(out, "VEC3")
                samplers.append({"input": t_acc, "output": s_acc, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": bone_nodes[i], "path": "translation"}})
        if channels:
            g.animations = [{"name": "idle", "samplers": samplers, "channels": channels}]

    # No root transform node — the rotation is baked into the bones/mesh/anim, so
    # the skeletal mesh imports upright and animates correctly. Scale -> UE actor.
    g._scene_nodes = roots + mesh_nodes
    with open(out_glb, "wb") as f:
        f.write(g.to_glb())
    return True


# ----------------------------------------------------------- rig + parts -----
def _idle_motion(chrf, ch):
    a = next((a for a in ch.animations if a[0] == 0), ch.animations[0] if ch.animations else None)
    return chrf.motions[a[1]] if a and 0 <= a[1] < len(chrf.motions) else None


def _char_bones_anim(chrf, ch):
    if not (0 <= ch.skeleton < len(chrf.skeletons)):
        return None, None
    skp = _resolve(chrf.skeletons[ch.skeleton])
    if not skp:
        return None, None
    zmd = rose_zmd.read_zmd(skp)
    bones = [{"parent": b.parent, "pos": list(b.position),
              "rot": [b.rotation[1], b.rotation[2], b.rotation[3], b.rotation[0]]} for b in zmd.bones]
    nb = len(zmd.bones)
    anim = None
    mp = _resolve(_idle_motion(chrf, ch))
    if mp:
        try:
            zmo = rose_zmo.read_zmo(mp)
            rot = [None] * nb; pos = [None] * nb
            for c in zmo.channels:
                if not (0 <= c.refer_id < nb):
                    continue
                if c.ctype == rose_zmo.CT_ROTATION:
                    rot[c.refer_id] = [[f[1], f[2], f[3], f[0]] for f in c.frames]
                elif c.ctype == rose_zmo.CT_POSITION:
                    pos[c.refer_id] = [list(f) for f in c.frames]
            anim = {"fps": zmo.fps, "frames": zmo.num_frames, "rot": rot, "pos": pos}
        except Exception:
            anim = None
    return bones, anim


def _char_parts(zsc, ch):
    parts = []
    for mi in ch.objects:
        if not (0 <= mi < len(zsc.models)):
            continue
        for p in zsc.models[mi].parts:
            mesh = zsc.meshes[p.mesh_idx] if 0 <= p.mesh_idx < len(zsc.meshes) else None
            mat = zsc.materials[p.mat_idx] if 0 <= p.mat_idx < len(zsc.materials) else None
            if mesh:
                parts.append((mesh, mat.path if mat else None, bool(mat.is_two_side) if mat else False))
    return parts


def _unique_char_ids(z):
    ids = set()
    for x, y, stem in Z._tiles_in(z["dir"]):
        try:
            ifo = RI.read_ifo(stem + ".IFO")
        except Exception:
            continue
        ml = ifo.lumps.get(RI.LUMP_MOB)
        if ml:
            for o in ml.objects:
                ids.add(o.object_id)
        rl = ifo.lumps.get(RI.LUMP_REGEN)
        if rl:
            for o in rl.objects:
                for m in (o.extra.get("basic", []) + o.extra.get("tactics", [])):
                    ids.add(m.mob_id)
    return ids


# ----------------------------------------------------------- top level -------
def build(key, bundle, run_blender=True):
    z = Z.find_zone(key)
    if not z:
        raise KeyError(key)
    chrp = _resolve("NPC/LIST_NPC.CHR")
    zscp = _resolve("NPC/PART_NPC.ZSC")
    if not chrp or not zscp:
        return {"error": "LIST_NPC.CHR / PART_NPC.ZSC not found", "models": []}
    chrf = rose_chr.read_chr(chrp)
    zsc = read_zsc(zscp)

    out_dir = os.path.join(bundle, "NPCs", "Models")
    os.makedirs(out_dir, exist_ok=True)

    made = []
    for nid in sorted(_unique_char_ids(z)):
        if not (0 <= nid < len(chrf.characters)):
            continue
        ch = chrf.characters[nid]
        if not ch or not ch.objects:
            continue
        bones, anim = _char_bones_anim(chrf, ch)
        if not bones:
            continue
        parts = _char_parts(zsc, ch)
        if not parts:
            continue
        glb = os.path.join(out_dir, "%d.glb" % nid)
        try:
            if _build_char_glb(bones, anim, parts, glb):
                made.append(nid)
        except Exception as e:
            print("  [npc] %d failed: %s" % (nid, e))

    # glb -> skeletal fbx (Blender)
    fbx_made = []
    if run_blender and made and os.path.exists(BLENDER):
        conv = os.path.join(_HERE, "blender_glb_to_fbx_skinned.py")
        for nid in made:
            glb = os.path.join(out_dir, "%d.glb" % nid)
            fbx = os.path.join(out_dir, "%d.fbx" % nid)
            try:
                subprocess.run([BLENDER, "--background", "--python", conv, "--", glb, fbx],
                               check=True, capture_output=True)
                if os.path.exists(fbx):
                    fbx_made.append(nid)
            except Exception as e:
                print("  [npc] blender %d failed: %s" % (nid, e))

    with open(os.path.join(bundle, "NPCs", "models.json"), "w") as f:
        json.dump({"zone": key, "models": made, "fbx": fbx_made}, f, indent=1)
    return {"glb": len(made), "fbx": len(fbx_made), "models": made}


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = os.path.join(_HERE, "exports", "%s_fbx" % k)
    os.makedirs(out, exist_ok=True)
    print(json.dumps(build(k, out, run_blender="--noblender" not in sys.argv), indent=2))
