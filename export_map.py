"""Export a whole ROSE/SHO zone to a single glTF binary (.glb) for Unity/Blender.

Bakes terrain (two-layer grass blend), all OBJECT/CNST/MORPH meshes with their
textures, and collision (IFO COLLISION boxes + the .MOV walk grid). NPCs, spawns,
warps, sounds and effects are intentionally skipped.

glTF 2.0 is Y-up/metres; ROSE is Z-up/cm, so a single root node converts the
whole scene (rotate -90° X, scale 0.01). Geometry is de-duplicated so repeated
buildings/trees share one mesh (instanced via nodes) — keeps the file small.

Usage:
    python export_map.py JPT01-1 [out.glb]
"""

from __future__ import annotations

import os
import sys
import json
import struct
from io import BytesIO

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for p in (_HERE, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import zone as Z
import rose_map
import rose_ifo as RI
import rose_mov as RM
from rose_zsc import read_zsc
from rose_zms import read_zms

GEN_LIGHTMAP_UV = True     # emit a 2nd UV set (TEXCOORD_1) for UE lightmap baking

try:
    import xatlas
    _HAVE_XATLAS = True
except Exception:
    _HAVE_XATLAS = False


def compute_normals(pos: np.ndarray, idx: np.ndarray) -> np.ndarray:
    nrm = np.zeros_like(pos, dtype=np.float64)
    tris = idx.reshape(-1, 3)
    v0, v1, v2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    for i in range(3):
        np.add.at(nrm, tris[:, i], fn)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return (nrm / ln).astype(np.float32)


def lm_unwrap(pos, nrm, uv0, idx):
    """xatlas lightmap unwrap. Returns remapped (pos, nrm, uv0, uv1, idx) with a
    new vertex set (seams split). uv1 is None if unwrap is unavailable/fails."""
    if not (GEN_LIGHTMAP_UV and _HAVE_XATLAS):
        return pos, nrm, uv0, None, idx
    try:
        faces = idx.reshape(-1, 3).astype(np.uint32)
        vmap, fout, uv1 = xatlas.parametrize(pos.astype(np.float32), faces)
        pos2 = pos[vmap]
        nrm2 = nrm[vmap] if nrm is not None else None
        uv02 = uv0[vmap] if uv0 is not None else None
        return pos2, nrm2, uv02, uv1.astype(np.float32), fout.reshape(-1).astype(np.uint32)
    except Exception:
        return pos, nrm, uv0, None, idx


# glTF constants
F32, U32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_BUFFER = 34962, 34963
REPEAT = 10497


# --------------------------------------------------------------------------
# Math
# --------------------------------------------------------------------------
def quat_matrix(qx, qy, qz, qw):
    n = (qx*qx + qy*qy + qz*qz + qw*qw) ** 0.5 or 1.0
    qx, qy, qz, qw = qx/n, qy/n, qz/n, qw/n
    return np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw),   2*(qx*qz+qy*qw),   0],
        [2*(qx*qy+qz*qw),   1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw),   0],
        [2*(qx*qz-qy*qw),   2*(qy*qz+qx*qw),   1-2*(qx*qx+qy*qy), 0],
        [0, 0, 0, 1]], dtype=np.float64)


def compose(pos, quat_xyzw, scale):
    M = quat_matrix(*quat_xyzw)
    M[:3, 0] *= scale[0]; M[:3, 1] *= scale[1]; M[:3, 2] *= scale[2]
    M[0, 3], M[1, 3], M[2, 3] = pos
    return M


# --------------------------------------------------------------------------
# GLB builder
# --------------------------------------------------------------------------
class Glb:
    def __init__(self):
        self.bin = bytearray()
        self.bufferViews = []
        self.accessors = []
        self.images = []
        self.samplers = []
        self.textures = []
        self.materials = []
        self.meshes = []
        self.nodes = []
        self._geom_cache = {}     # mesh_path -> dict(POS,NORMAL,TEXCOORD_0,indices)
        self._tex_cache = {}      # (path,alpha) -> material index
        self._cube = None
        self.material_info = []   # [{name, texture, texture_src, alpha, mode, double, color}]

    def _align(self):
        while len(self.bin) % 4:
            self.bin.append(0)

    def _view(self, data: bytes, target=None):
        self._align()
        off = len(self.bin)
        self.bin += data
        bv = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bufferViews.append(bv)
        return len(self.bufferViews) - 1

    def add_vec3(self, arr: np.ndarray):
        arr = np.ascontiguousarray(arr, dtype=np.float32)
        bv = self._view(arr.tobytes(), ARRAY_BUFFER)
        self.accessors.append({"bufferView": bv, "componentType": F32, "count": len(arr),
                               "type": "VEC3", "min": arr.min(0).tolist(), "max": arr.max(0).tolist()})
        return len(self.accessors) - 1

    def add_vec2(self, arr: np.ndarray):
        arr = np.ascontiguousarray(arr, dtype=np.float32)
        bv = self._view(arr.tobytes(), ARRAY_BUFFER)
        self.accessors.append({"bufferView": bv, "componentType": F32, "count": len(arr), "type": "VEC2"})
        return len(self.accessors) - 1

    def add_indices(self, arr: np.ndarray):
        arr = np.ascontiguousarray(arr, dtype=np.uint32)
        bv = self._view(arr.tobytes(), ELEMENT_BUFFER)
        self.accessors.append({"bufferView": bv, "componentType": U32, "count": len(arr), "type": "SCALAR"})
        return len(self.accessors) - 1

    def add_image_png(self, png: bytes):
        bv = self._view(png)
        self.images.append({"bufferView": bv, "mimeType": "image/png"})
        if not self.samplers:
            self.samplers.append({"wrapS": REPEAT, "wrapT": REPEAT})
        self.textures.append({"source": len(self.images) - 1, "sampler": 0})
        return len(self.textures) - 1

    def material_for_texture(self, abs_path, alpha=False, mode="OPAQUE", cutoff=0.5, double=False, color=None, kind=None):
        key = (abs_path, alpha, mode, double, tuple(color) if color else None, kind)
        if key in self._tex_cache:
            return self._tex_cache[key]
        idx = len(self.materials)
        # Deterministic, unique, FBX-safe material name (the join key the Unity
        # / UE editor scripts use to auto-assign textures): "M<idx>_<texname>".
        base = os.path.splitext(os.path.basename(abs_path))[0] if abs_path else "color"
        base = "".join(c if (c.isalnum() or c == "_") else "_" for c in base)[:40]
        name = "M%d_%s" % (idx, base)
        mat = {"name": name, "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if abs_path:
            try:
                im = Image.open(abs_path); im.load()
                im = im.convert("RGBA" if alpha else "RGB")
                b = BytesIO(); im.save(b, "PNG")
                tex = self.add_image_png(b.getvalue())
                mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex}
            except Exception:
                mat["pbrMetallicRoughness"]["baseColorFactor"] = [0.7, 0.7, 0.7, 1]
        elif color:
            mat["pbrMetallicRoughness"]["baseColorFactor"] = list(color)
        if mode != "OPAQUE":
            mat["alphaMode"] = mode
            if mode == "MASK":
                mat["alphaCutoff"] = cutoff
        if double:
            mat["doubleSided"] = True
        self.materials.append(mat)
        self.material_info.append({
            "name": name, "texture": "%s.png" % name,
            "texture_src": abs_path, "alpha": bool(alpha), "mode": mode,
            "twosided": bool(double), "color": list(color) if color else None,
            "kind": kind,
        })
        self._tex_cache[key] = idx
        return idx

    def mesh(self, pos_acc, idx_acc, mat, normal_acc=None, uv_acc=None, uv1_acc=None):
        attr = {"POSITION": pos_acc}
        if normal_acc is not None:
            attr["NORMAL"] = normal_acc
        if uv_acc is not None:
            attr["TEXCOORD_0"] = uv_acc
        if uv1_acc is not None:
            attr["TEXCOORD_1"] = uv1_acc
        prim = {"attributes": attr, "indices": idx_acc, "mode": 4}
        if mat is not None:
            prim["material"] = mat
        self.meshes.append({"primitives": [prim]})
        return len(self.meshes) - 1

    def node(self, mesh=None, matrix=None, name=None, children=None):
        nd = {}
        if mesh is not None:
            nd["mesh"] = mesh
        if matrix is not None:
            nd["matrix"] = [float(x) for x in matrix]
        if name:
            nd["name"] = name
        if children:
            nd["children"] = children
        self.nodes.append(nd)
        return len(self.nodes) - 1

    def write(self, path, root_children):
        # Root: ROSE Z-up cm -> glTF Y-up metres.
        s = 0.01
        R = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
        R[:3, :3] *= s
        root = self.node(matrix=R.flatten(order="F"), name="ROSE_zone", children=root_children)
        gltf = {
            "asset": {"version": "2.0", "generator": "mapforge"},
            "scene": 0, "scenes": [{"nodes": [root]}],
            "nodes": self.nodes, "meshes": self.meshes, "accessors": self.accessors,
            "bufferViews": self.bufferViews, "buffers": [{"byteLength": len(self.bin)}],
        }
        for k, v in (("materials", self.materials), ("images", self.images),
                     ("textures", self.textures), ("samplers", self.samplers)):
            if v:
                gltf[k] = v
        js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        while len(js) % 4:
            js += b" "
        binc = bytes(self.bin)
        while len(binc) % 4:
            binc += b"\x00"
        glb = bytearray()
        glb += struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binc))
        glb += struct.pack("<II", len(js), 0x4E4F534A) + js
        glb += struct.pack("<II", len(binc), 0x004E4942) + binc
        with open(path, "wb") as f:
            f.write(glb)


# --------------------------------------------------------------------------
# Asset resolution
# --------------------------------------------------------------------------
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


def zms_geometry(glb: Glb, mesh_rel):
    """Return (pos_acc, nrm_acc, uv_acc, idx_acc) for a ZMS, cached."""
    if mesh_rel in glb._geom_cache:
        return glb._geom_cache[mesh_rel]
    ab = resolve(mesh_rel)
    if not ab:
        glb._geom_cache[mesh_rel] = None
        return None
    z = read_zms(ab)
    scale = 100.0 if z.version >= 7 else 1.0
    pos = np.array(z.positions, dtype=np.float32) * scale
    nrm = np.array(z.normals, dtype=np.float32) if z.normals else None
    uv = np.array(z.uvs[0], dtype=np.float32) if z.uvs else None
    idx = np.array(z.faces, dtype=np.uint32).reshape(-1)
    pos, nrm, uv, uv1, idx = lm_unwrap(pos, nrm, uv, idx)
    rec = (glb.add_vec3(pos),
           glb.add_vec3(nrm) if nrm is not None else None,
           glb.add_vec2(uv) if uv is not None else None,
           glb.add_vec2(uv1) if uv1 is not None else None,
           glb.add_indices(idx))
    glb._geom_cache[mesh_rel] = rec
    return rec


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def export(key, out_path):
    z = Z.find_zone(key)
    if not z:
        raise KeyError(key)
    zon = rose_map.read_zon(Z._find_zon(z))
    cy = zon.info.center_y or 32
    glb = Glb()

    obj_nodes, terr_nodes, col_nodes, water_nodes = [], [], [], []

    # ---- packs ----
    packs = {}
    for kind, rel in (("OBJECT", z["deco_pack"]), ("CNST", z["cnst_pack"])):
        ab = resolve(rel) if rel else None
        packs[kind] = read_zsc(ab) if ab else None
    morph_rows = []
    mstb = os.path.join(config.STB_DIR, "LIST_MORPH_OBJECT.STB")
    if os.path.exists(mstb):
        from parse_stb import StbFile
        stb = StbFile(mstb)
        for r in range(stb.row_count):
            mesh = stb.get(r, 1) if stb.col_count > 1 else ""
            morph_rows.append({"mesh": mesh, "mat": stb.get(r, 3) if stb.col_count > 3 else None} if mesh else None)

    # mesh+material bucket -> gltf mesh index
    bucket_mesh = {}
    morph_node_count = {}     # mesh stem -> running count (names the static MORPH nodes)

    def get_bucket_mesh(mesh_rel, mat_rel, flags):
        bkey = (mesh_rel, mat_rel)
        if bkey in bucket_mesh:
            return bucket_mesh[bkey]
        geom = zms_geometry(glb, mesh_rel)
        if not geom:
            bucket_mesh[bkey] = None
            return None
        pa, na, ua, u1a, ia = geom
        wantA = bool(flags.get("alpha_test") or flags.get("alpha"))
        mode = "MASK" if flags.get("alpha_test") else "OPAQUE"
        double = bool(flags.get("two_side") or wantA)
        mat = glb.material_for_texture(resolve(mat_rel) if mat_rel else None,
                                       alpha=wantA, mode=mode,
                                       cutoff=(flags.get("alpha_ref", 128) or 128)/255.0,
                                       double=double)
        mi = glb.mesh(pa, ia, mat, na, ua, u1a)
        bucket_mesh[bkey] = mi
        return mi

    def part_matrix(part):
        r = part["rot"]   # ZSC (w,x,y,z)
        return compose(part["pos"], (r[1], r[2], r[3], r[0]), part["scl"])

    def zsc_dict(zsc):
        out = []
        for m in zsc.models:
            parts = []
            for p in m.parts:
                mesh = zsc.meshes[p.mesh_idx] if 0 <= p.mesh_idx < len(zsc.meshes) else None
                mat = zsc.materials[p.mat_idx] if 0 <= p.mat_idx < len(zsc.materials) else None
                flags = {"two_side": bool(mat.is_two_side), "alpha": bool(mat.is_alpha),
                         "alpha_test": bool(mat.alpha_test), "alpha_ref": mat.alpha_ref} if mat else {}
                parts.append({"mesh": mesh, "mat": mat.path if mat else None, "flags": flags,
                              "pos": list(p.position), "rot": list(p.rotate), "scl": list(p.scale),
                              "parent": p.parent})
            out.append(parts)
        return out

    pack_parts = {k: (zsc_dict(v) if v else None) for k, v in packs.items()}

    # ---- iterate tiles ----
    for x, y, stem in Z._tiles_in(z["dir"]):
        ifo = RI.read_ifo(stem + ".IFO")

        # objects (OBJECT + CNST)
        for kind in ("OBJECT", "CNST"):
            lump = ifo.lumps.get(RI.LUMP_OBJECT if kind == "OBJECT" else RI.LUMP_CNST)
            models = pack_parts.get(kind)
            if not lump or not models:
                continue
            for o in lump.objects:
                if not (0 <= o.object_id < len(models)):
                    continue
                parts = models[o.object_id]
                ifoM = compose(o.pos, o.rot, o.scale)
                world = [None] * len(parts)
                for i, part in enumerate(parts):
                    local = part_matrix(part)
                    if part["parent"] < 0 or part["parent"] >= i or world[part["parent"]] is None:
                        world[i] = ifoM @ local
                    else:
                        world[i] = world[part["parent"]] @ local
                    if not part["mesh"]:
                        continue
                    mi = get_bucket_mesh(part["mesh"], part["mat"], part["flags"])
                    if mi is not None:
                        obj_nodes.append(glb.node(mesh=mi, matrix=world[i].flatten(order="F")))

        # MORPH — named per object stem so the Unity animation script can find
        # each static placement and drop the matching animated prefab onto it.
        ml = ifo.lumps.get(RI.LUMP_MORPH)
        if ml and morph_rows:
            for o in ml.objects:
                if not (0 <= o.object_id < len(morph_rows)):
                    continue
                row = morph_rows[o.object_id]
                if not row:
                    continue
                mi = get_bucket_mesh(row["mesh"], row["mat"], {"alpha_test": True, "two_side": True})
                if mi is not None:
                    mstem = os.path.splitext(os.path.basename(row["mesh"]))[0]
                    n = morph_node_count.get(mstem, 0)
                    morph_node_count[mstem] = n + 1
                    obj_nodes.append(glb.node(mesh=mi, name="MORPH__%s__%d" % (mstem, n),
                                              matrix=compose(o.pos, o.rot, o.scale).flatten(order="F")))

        # terrain
        t = Z.tile_terrain_json(x, y, stem, center_y=cy)
        terr_nodes += build_terrain_tile(glb, t, zon)

        # collision boxes (IFO COLLISION)
        cl = ifo.lumps.get(RI.LUMP_COLLISION)
        if cl:
            for o in cl.objects:
                col_nodes.append(glb.node(mesh=cube_mesh(glb),
                                          matrix=compose(o.pos, o.rot,
                                                         [max(1, s) for s in o.scale]).flatten(order="F")))

        # OCEAN water surfaces -> flat translucent quads (ROSE/Water URP shader)
        ol = ifo.lumps.get(RI.LUMP_OCEAN)
        if ol and ol.ocean and ol.ocean.blocks:
            water_nodes += build_water_blocks(glb, ol.ocean.blocks)

    # MOV walk grid -> collision mesh
    mov_node = build_mov_mesh(glb, z, cy)
    if mov_node is not None:
        col_nodes.append(mov_node)

    roots = []
    if obj_nodes:
        roots.append(glb.node(name="Objects", children=obj_nodes))
    if terr_nodes:
        roots.append(glb.node(name="Terrain", children=terr_nodes))
    if col_nodes:
        roots.append(glb.node(name="Collision", children=col_nodes))
    if water_nodes:
        roots.append(glb.node(name="Water", children=water_nodes))

    glb.write(out_path, roots)
    # Sidecar manifest: material name -> texture + flags (the join key for the
    # Unity / UE auto-assign editor scripts).
    with open(out_path + ".materials.json", "w") as f:
        json.dump({"zone": key, "materials": glb.material_info}, f, indent=1)
    return {"objects": len(obj_nodes), "terrain": len(terr_nodes), "collision": len(col_nodes),
            "water": len(water_nodes), "meshes": len(glb.meshes), "textures": len(glb.textures),
            "bytes": os.path.getsize(out_path), "material_info": glb.material_info}


def cube_mesh(glb):
    if glb._cube is not None:
        return glb._cube
    v = np.array([[-.5, -.5, 0], [.5, -.5, 0], [.5, .5, 0], [-.5, .5, 0],
                  [-.5, -.5, 1], [.5, -.5, 1], [.5, .5, 1], [-.5, .5, 1]], dtype=np.float32) * 1000
    f = np.array([0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
                  1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0], dtype=np.uint32)
    pa = glb.add_vec3(v); ia = glb.add_indices(f)
    mat = glb.material_for_texture(None, mode="BLEND", double=True, color=[0.85, 0.2, 0.2, 0.35])
    glb._cube = glb.mesh(pa, ia, mat)
    return glb._cube


UP_LAYER_Z_BIAS = 12.0   # lift the grass overlay above the ground to stop Z-fighting/flicker

def build_terrain_tile(glb, t, zon):
    """Down layer (opaque) + up layer (alpha blend) meshes for one tile."""
    PATCHES, GPP = 16, 4
    STEP = 16000 / (PATCHES * GPP)
    N = t["verts"]; H = t["heights"]; ox, oy = t["origin"]; mats = t["materials"]
    tx, tt = zon.tile_textures, zon.tile_types
    nodes = []

    def layer(get_idx, rot_uv, alpha):
        buckets = {}
        for pr in range(PATCHES):
            for pc in range(PATCHES):
                ty = tt[mats[pr][pc]] if mats[pr][pc] < len(tt) else None
                if not ty:
                    continue
                dn = ty[0] + ty[2]; up = ty[1] + ty[3]
                if alpha and dn == up:
                    continue
                ti = get_idx(ty)
                if not (0 <= ti < len(tx)):
                    continue
                b = buckets.setdefault(ti, {"pos": [], "uv": [], "idx": [], "n": 0})
                base = b["n"]
                put = ty[5]
                zb = UP_LAYER_Z_BIAS if alpha else 0.0
                for iy in range(GPP + 1):
                    for ix in range(GPP + 1):
                        gr, gc = pr * GPP + iy, pc * GPP + ix
                        b["pos"].append((ox + gc * STEP, oy + gr * STEP, H[gr * N + gc] + zb))
                        u, v = ix / GPP, iy / GPP
                        if rot_uv:
                            q = (put - 1) % 4
                            du, dv = u - 0.5, v - 0.5
                            for _ in range(q):
                                du, dv = -dv, du
                            u, v = du + 0.5, dv + 0.5
                        b["uv"].append((u, v))
                for iy in range(GPP):
                    for ix in range(GPP):
                        v0 = base + iy * (GPP + 1) + ix
                        # winding chosen so computed normals face UP (+Z)
                        b["idx"] += [v0, v0 + 1, v0 + GPP + 1, v0 + 1, v0 + GPP + 2, v0 + GPP + 1]
                b["n"] += (GPP + 1) * (GPP + 1)
        for ti, b in buckets.items():
            posA = np.array(b["pos"], dtype=np.float32)
            idxA = np.array(b["idx"], dtype=np.uint32)
            nrmA = compute_normals(posA, idxA)
            # planar lightmap UV: normalise the patch's world XY into [0,1]
            # (a heightfield is injective in XY, so no overlap / seams).
            u1 = None
            if GEN_LIGHTMAP_UV:
                mn = posA[:, :2].min(0); span = np.maximum(posA[:, :2].max(0) - mn, 1.0)
                u1 = ((posA[:, :2] - mn) / span).astype(np.float32)
            pa = glb.add_vec3(posA)
            na = glb.add_vec3(nrmA)
            ua = glb.add_vec2(np.array(b["uv"], dtype=np.float32))
            u1a = glb.add_vec2(u1) if u1 is not None else None
            ia = glb.add_indices(idxA)
            mat = glb.material_for_texture(resolve(tx[ti]), alpha=alpha,
                                           mode="BLEND" if alpha else "OPAQUE", double=True)
            nodes.append(glb.node(mesh=glb.mesh(pa, ia, mat, normal_acc=na, uv_acc=ua, uv1_acc=u1a)))

    layer(lambda ty: ty[0] + ty[2], False, False)   # down layer
    layer(lambda ty: ty[1] + ty[3], True, True)     # up (grass) layer
    return nodes


WATER_TILE = 2400.0   # world units per water-texture tile (UV scale for the shader)

def build_water_blocks(glb, blocks):
    """Flat translucent quads over each OCEAN block, at its water height. The
    material is flagged kind='water' so the editor scripts give it ROSE/Water."""
    nodes = []
    wmat = glb.material_for_texture(None, mode="BLEND", double=True,
                                    color=[0.10, 0.32, 0.46, 0.80], kind="water")
    for s, e in blocks:
        x0, x1 = min(s[0], e[0]), max(s[0], e[0])
        y0, y1 = min(s[1], e[1]), max(s[1], e[1])
        zz = max(s[2], e[2])
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        verts = np.array([[x0, y0, zz], [x1, y0, zz], [x1, y1, zz], [x0, y1, zz]], dtype=np.float32)
        uvs = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32) / WATER_TILE
        idx = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)         # winding -> +Z up
        nrm = np.tile([0.0, 0.0, 1.0], (4, 1)).astype(np.float32)
        pa = glb.add_vec3(verts); na = glb.add_vec3(nrm)
        ua = glb.add_vec2(uvs); ia = glb.add_indices(idx)
        nodes.append(glb.node(mesh=glb.mesh(pa, ia, wmat, normal_acc=na, uv_acc=ua), name="Water"))
    return nodes


def build_mov_mesh(glb, z, cy):
    """Flat red quads over blocked .MOV cells, at sampled ground height."""
    pos, idx = [], []
    n = 0
    for x, y, stem in Z._tiles_in(z["dir"]):
        mp = stem + ".MOV"
        if not os.path.exists(mp):
            continue
        mov = RM.read_mov(mp, x, y)
        him = rose_map.read_him(stem + ".HIM")
        oy = (2 * cy - y) * 16000
        ox = x * 16000
        cell = RM.CELL_SIZE
        for row in range(mov.height):
            for col in range(mov.width):
                if mov.cells[row][col] == 0:
                    continue
                wx, wy = ox + col * cell, oy + row * cell
                # sample terrain height at cell centre
                hc = min(64, int((col * cell + cell / 2) / 250))
                hr = min(64, int((row * cell + cell / 2) / 250))
                hz = him.heights[hr][hc] + 60
                pos += [(wx, wy, hz), (wx + cell, wy, hz), (wx + cell, wy + cell, hz), (wx, wy + cell, hz)]
                idx += [n, n + 1, n + 2, n, n + 2, n + 3]
                n += 4
    if not pos:
        return None
    pa = glb.add_vec3(np.array(pos, dtype=np.float32))
    ia = glb.add_indices(np.array(idx, dtype=np.uint32))
    mat = glb.material_for_texture(None, mode="BLEND", double=True, color=[0.9, 0.2, 0.2, 0.4])
    return glb.node(mesh=glb.mesh(pa, ia, mat), name="WalkBlocked")


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_HERE, "exports", f"{key}.glb")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f"exporting {key} -> {out}")
    stats = export(key, out)
    print("done:", json.dumps(stats, indent=2))
