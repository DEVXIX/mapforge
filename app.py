"""mapforge — ROSE/SHO map reader + editor (1:1 client & server).

Backend HTTP layer. All map semantics live in the rose_* / zone modules;
this file is just routing + the proven asset endpoints (ZSC packs, ZMS
meshes, DDS->PNG) ported from the previous editor, which were the hard-won
rendering bits.

Run:  python app.py     ->  http://127.0.0.1:5051
"""

from __future__ import annotations

import os
import sys
import struct
from io import BytesIO

from flask import Flask, jsonify, request, abort, Response, send_from_directory, send_file

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import config                       # noqa: E402
import zone as Z                    # noqa: E402
import rose_ifo as RI               # noqa: E402
import rose_chr                     # noqa: E402
import rose_zmd                     # noqa: E402
import rose_zmo                     # noqa: E402
from rose_zsc import read_zsc       # noqa: E402
from rose_zms import read_zms       # noqa: E402
from parse_stb import StbFile       # noqa: E402

app = Flask(__name__, static_folder=os.path.join(_HERE, "static"), static_url_path="/static")


# --------------------------------------------------------------------------
# Asset path resolution (case-insensitive walk under ASSET_ROOT)
# --------------------------------------------------------------------------
def _resolve(rel: str) -> str | None:
    if not rel:
        return None
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if parts and parts[0].lower() == "3ddata":
        parts = parts[1:]
    cand = os.path.join(config.ASSET_ROOT, *parts)
    if os.path.exists(cand):
        return cand
    cur = config.ASSET_ROOT
    for part in parts:
        if not os.path.isdir(cur):
            return None
        m = [e for e in os.listdir(cur) if e.lower() == part.lower()]
        if not m:
            return None
        cur = os.path.join(cur, m[0])
    return cur if os.path.exists(cur) else None


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# --------------------------------------------------------------------------
# Zones
# --------------------------------------------------------------------------
@app.route("/api/zones")
def api_zones():
    return jsonify([{"key": z["key"], "name": z["name"]} for z in Z.list_zones()])


@app.route("/api/zone/<key>")
def api_zone(key: str):
    try:
        return jsonify(Z.load_zone(key))
    except KeyError:
        abort(404)


@app.route("/api/zone/<key>/mov")
def api_zone_mov(key: str):
    try:
        return jsonify(Z.load_mov(key))
    except KeyError:
        abort(404)


@app.route("/api/zone/<key>/save", methods=["POST"])
def api_zone_save(key: str):
    global _CATALOG
    data = request.get_json(force=True)
    ops = data.get("ops", [])
    if not ops:
        return jsonify({"written": [], "roots": []})
    try:
        result = Z.save_ops(key, ops)
    except KeyError:
        abort(404)
    # A "place" op may have appended models to a pack — invalidate the parsed
    # pack + catalog caches so the next packs/zone fetch reflects the change.
    if any(o.get("op") == "place" for o in ops):
        _ZSC_CACHE.clear()
        _CATALOG = None
    return jsonify(result)


# --------------------------------------------------------------------------
# ZSC packs (object_id -> model -> parts -> mesh/material)
# --------------------------------------------------------------------------
_ZSC_CACHE: dict = {}


def _get_zsc(rel: str):
    if rel in _ZSC_CACHE:
        return _ZSC_CACHE[rel]
    p = _resolve(rel)
    zsc = read_zsc(p) if p else None
    _ZSC_CACHE[rel] = zsc
    return zsc


def _zsc_to_dict(zsc) -> dict:
    models = []
    for m in zsc.models:
        parts = []
        for p in m.parts:
            mesh_rel = zsc.meshes[p.mesh_idx] if 0 <= p.mesh_idx < len(zsc.meshes) else None
            mat = zsc.materials[p.mat_idx] if 0 <= p.mat_idx < len(zsc.materials) else None
            flags = None
            if mat:
                flags = {
                    "two_side": bool(mat.is_two_side), "alpha": bool(mat.is_alpha),
                    "alpha_test": bool(mat.alpha_test), "alpha_ref": mat.alpha_ref,
                    "blend": mat.blend_type,
                }
            parts.append({
                "mesh": mesh_rel, "mat": mat.path if mat else None, "flags": flags,
                "pos": list(p.position), "rot": list(p.rotate), "scl": list(p.scale),
                "parent": p.parent,
            })
        models.append({"parts": parts, "bb_min": list(m.bb_min), "bb_max": list(m.bb_max)})
    return {"models": models, "meshes": zsc.meshes, "materials": [m.path for m in zsc.materials]}


# --------------------------------------------------------------------------
# NPCs (MOB lump): object_id -> LIST_NPC.CHR character -> PART_NPC.ZSC models.
# Composed into the same {models:{id:{parts}}} shape the object renderer uses,
# so MOB placements can draw real meshes instead of cone markers.
# --------------------------------------------------------------------------
_CHR = None
_NPC_PACK_CACHE: dict = {}


def _get_chr():
    global _CHR
    if _CHR is None:
        p = _resolve("NPC/LIST_NPC.CHR")
        _CHR = rose_chr.read_chr(p) if p else None
    return _CHR


def _zsc_model_parts(zsc, model_idx: int, base: int) -> list:
    """Serialize one ZSC model's parts, offsetting parent indices by `base` so
    several models can be concatenated into one part list (an NPC = head+body…)."""
    out = []
    if not (0 <= model_idx < len(zsc.models)):
        return out
    for p in zsc.models[model_idx].parts:
        mesh_rel = zsc.meshes[p.mesh_idx] if 0 <= p.mesh_idx < len(zsc.meshes) else None
        mat = zsc.materials[p.mat_idx] if 0 <= p.mat_idx < len(zsc.materials) else None
        flags = None
        if mat:
            flags = {"two_side": bool(mat.is_two_side), "alpha": bool(mat.is_alpha),
                     "alpha_test": bool(mat.alpha_test), "alpha_ref": mat.alpha_ref,
                     "blend": mat.blend_type}
        out.append({
            "mesh": mesh_rel, "mat": mat.path if mat else None, "flags": flags,
            "pos": list(p.position), "rot": list(p.rotate), "scl": list(p.scale),
            "parent": (p.parent + base) if p.parent >= 0 else -1,
        })
    return out


def _npc_pack(z) -> dict:
    key = z["key"]
    if key in _NPC_PACK_CACHE:
        return _NPC_PACK_CACHE[key]
    chrf = _get_chr()
    zsc = _get_zsc("NPC/PART_NPC.ZSC")
    models = {}
    if chrf and zsc:
        ids = set()
        for x, y, stem in Z._tiles_in(z["dir"]):
            try:
                ifo = RI.read_ifo(stem + ".IFO")
            except Exception:
                continue
            ml = ifo.lumps.get(RI.LUMP_MOB)          # fixed NPCs
            if ml:
                for ob in ml.objects:
                    ids.add(ob.object_id)
            rl = ifo.lumps.get(RI.LUMP_REGEN)         # monster spawn points
            if rl:
                for ob in rl.objects:
                    for m in ob.extra.get("basic", []) + ob.extra.get("tactics", []):
                        ids.add(m.mob_id)
        for nid in ids:
            if not (0 <= nid < len(chrf.characters)):
                continue
            ch = chrf.characters[nid]
            if not ch or not ch.objects:
                continue
            parts = []
            for mi in ch.objects:
                parts.extend(_zsc_model_parts(zsc, mi, len(parts)))
            if parts:
                models[str(nid)] = {"parts": parts}
    out = {"models": models}
    _NPC_PACK_CACHE[key] = out
    return out


@app.route("/api/zone/<key>/packs")
def api_zone_packs(key: str):
    z = Z.find_zone(key)
    if not z:
        abort(404)
    out = {}
    for lump_name, pack_rel in (("OBJECT", z["deco_pack"]), ("CNST", z["cnst_pack"])):
        if not pack_rel:
            continue
        zsc = _get_zsc(pack_rel)
        out[lump_name] = ({"error": f"not found: {pack_rel}"} if zsc is None
                          else {"pack_path": pack_rel, **_zsc_to_dict(zsc)})

    try:
        out["NPC"] = _npc_pack(z)
    except Exception as e:
        out["NPC"] = {"error": str(e)}

    morph_stb = os.path.join(config.STB_DIR, "LIST_MORPH_OBJECT.STB")
    if os.path.exists(morph_stb):
        try:
            stb = StbFile(morph_stb)
            rows = []
            for r in range(stb.row_count):
                mesh = stb.get(r, 1) if stb.col_count > 1 else ""
                rows.append(None if not mesh else {
                    "name": stb.get(r, 0), "mesh": mesh,
                    "mat": stb.get(r, 3) if stb.col_count > 3 else None,
                    "mot": stb.get(r, 2) if stb.col_count > 2 else None,
                })
            out["MORPH"] = {"rows": rows}
        except Exception as e:
            out["MORPH"] = {"error": str(e)}
    return jsonify(out)


# --------------------------------------------------------------------------
# Rigs (skeleton + idle animation) for NPC/monster ids, so the viewer can
# skin + play the standing animation. Quaternions come out (x,y,z,w) ready for
# three.js; bones/anim are in raw mesh units (the rig is scaled x100 at place).
# --------------------------------------------------------------------------
_RIG_CACHE: dict = {}


def _idle_motion(chrf, ch):
    a = next((a for a in ch.animations if a[0] == 0), ch.animations[0] if ch.animations else None)
    return chrf.motions[a[1]] if a and 0 <= a[1] < len(chrf.motions) else None


def _zone_npc_ids(z):
    ids = set()
    for x, y, stem in Z._tiles_in(z["dir"]):
        try:
            ifo = RI.read_ifo(stem + ".IFO")
        except Exception:
            continue
        for ob in (ifo.lumps.get(RI.LUMP_MOB).objects if ifo.lumps.get(RI.LUMP_MOB) else []):
            ids.add(ob.object_id)
        rl = ifo.lumps.get(RI.LUMP_REGEN)
        if rl:
            for ob in rl.objects:
                for m in ob.extra.get("basic", []) + ob.extra.get("tactics", []):
                    ids.add(m.mob_id)
    return ids


@app.route("/api/zone/<key>/rig")
def api_zone_rig(key: str):
    z = Z.find_zone(key)
    if not z:
        abort(404)
    if key in _RIG_CACHE:
        return jsonify(_RIG_CACHE[key])
    chrf = _get_chr()
    out = {}
    if chrf:
        for nid in _zone_npc_ids(z):
            if not (0 <= nid < len(chrf.characters)):
                continue
            ch = chrf.characters[nid]
            if not ch or not (0 <= ch.skeleton < len(chrf.skeletons)):
                continue
            skp = _resolve(chrf.skeletons[ch.skeleton])
            if not skp:
                continue
            try:
                zmd = rose_zmd.read_zmd(skp)
            except Exception:
                continue
            bones = [{"parent": b.parent, "pos": list(b.position),
                      "rot": [b.rotation[1], b.rotation[2], b.rotation[3], b.rotation[0]]}
                     for b in zmd.bones]
            nb = len(zmd.bones)
            anim = None
            motrel = _idle_motion(chrf, ch)
            mp = _resolve(motrel) if motrel else None
            if mp:
                try:
                    zmo = rose_zmo.read_zmo(mp)
                    rot = [None] * nb
                    pos = [None] * nb
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
            out[str(nid)] = {"bones": bones, "anim": anim}
    _RIG_CACHE[key] = out
    return jsonify(out)


# --------------------------------------------------------------------------
# Mesh + texture
# --------------------------------------------------------------------------
@app.route("/api/zone/<key>/export")
def api_zone_export(key: str):
    """Bake the whole zone (terrain + objects + collision, no NPCs) to a single
    .glb and stream it as a download. Takes ~30-60s for a town."""
    if not Z.find_zone(key):
        abort(404)
    import export_map
    out_dir = os.path.join(_HERE, "exports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{key}.glb")
    export_map.export(key, out)
    return send_file(out, as_attachment=True, download_name=f"{key}.glb",
                     mimetype="model/gltf-binary")


@app.route("/api/pack")
def api_pack():
    """Parse any ZSC pack by relative path (for the object browser preview)."""
    rel = request.args.get("path", "")
    zsc = _get_zsc(rel)
    if zsc is None:
        abort(404)
    return jsonify({"pack_path": rel, **_zsc_to_dict(zsc)})


_CATALOG = None


@app.route("/api/catalog")
def api_catalog():
    """Every placeable model across every zone's DECO + CNST packs, plus the
    global MORPH table. Cached after first build."""
    global _CATALOG
    if _CATALOG is not None:
        return jsonify(_CATALOG)

    seen_packs = {}     # pack_rel -> [ {index, name} ]

    def model_names(pack_rel):
        if pack_rel in seen_packs:
            return seen_packs[pack_rel]
        zsc = _get_zsc(pack_rel)
        out = []
        if zsc:
            for i, m in enumerate(zsc.models):
                fm = next((zsc.meshes[p.mesh_idx] for p in m.parts
                           if 0 <= p.mesh_idx < len(zsc.meshes)), "")
                nm = os.path.basename(fm).rsplit(".", 1)[0] if fm else f"#{i}"
                out.append({"index": i, "name": nm, "parts": len(m.parts)})
        seen_packs[pack_rel] = out
        return out

    entries = []
    for z in Z.list_zones():
        for kind, pack_rel in (("OBJECT", z["deco_pack"]), ("CNST", z["cnst_pack"])):
            if not pack_rel:
                continue
            for m in model_names(pack_rel):
                if m["parts"] == 0:
                    continue
                entries.append({
                    "zone": z["key"], "kind": kind, "pack": pack_rel,
                    "index": m["index"], "name": m["name"], "parts": m["parts"],
                })

    # MORPH — global STB, portable across all zones.
    morph_stb = os.path.join(config.STB_DIR, "LIST_MORPH_OBJECT.STB")
    morph = []
    if os.path.exists(morph_stb):
        try:
            stb = StbFile(morph_stb)
            for r in range(stb.row_count):
                mesh = stb.get(r, 1) if stb.col_count > 1 else ""
                if mesh:
                    morph.append({"zone": "(global)", "kind": "MORPH", "pack": "MORPH",
                                  "index": r, "name": stb.get(r, 0) or os.path.basename(mesh),
                                  "parts": 1})
        except Exception:
            pass

    _CATALOG = {"models": entries, "morph": morph,
                "zones": sorted({e["zone"] for e in entries})}
    return jsonify(_CATALOG)


@app.route("/api/anim")
def api_anim():
    """Per-frame vertex positions for a MORPH object's ZMO, in the same vertex
    order + scale (×100 for v7) as /api/mesh. Binary:
        u32 magic 'RANM'  u32 frames  u32 nverts  f32 fps
        frames × nverts × f32×3 positions
    Returns 204 if the ZMO is a UV/texture-flow clip (no vertex animation)."""
    import rose_zmo
    zmo_rel = request.args.get("zmo", "")
    mesh_rel = request.args.get("mesh", "")
    zp = _resolve(zmo_rel)
    mp = _resolve(mesh_rel)
    if not zp or not mp:
        abort(404)
    zms = read_zms(mp)
    zmo = rose_zmo.read_zmo(zp)
    pos_ch = {c.refer_id: c for c in zmo.channels if c.ctype == rose_zmo.CT_POSITION}
    if not pos_ch:
        return ("", 204)
    nv = len(zms.positions)
    mesh_scale = 100.0 if zms.version >= 7 else 1.0   # only for non-animated verts
    F = zmo.num_frames
    out = bytearray()
    out += struct.pack("<IIIf", 0x4D4E4152, F, nv, float(zmo.fps))
    rest = zms.positions
    for f in range(F):
        for v in range(nv):
            ch = pos_ch.get(v)
            if ch:                                   # ZMO positions are already external scale
                p = ch.frames[f]
                out += struct.pack("<fff", p[0], p[1], p[2])
            else:                                    # rest vertex -> match with mesh scale
                p = rest[v]
                out += struct.pack("<fff", p[0] * mesh_scale, p[1] * mesh_scale, p[2] * mesh_scale)
    return Response(bytes(out), mimetype="application/octet-stream")


@app.route("/api/mesh")
def api_mesh():
    rel = request.args.get("path", "")
    # skin=1 -> append per-vertex skin (skinIndex u16x4 mapped to skeleton bone +
    # skinWeight f32x4) AND serve RAW positions (the rigged object is scaled x100
    # at placement, so its mesh + bones must share the same unscaled space).
    want_skin = request.args.get("skin") == "1"
    p = _resolve(rel)
    if not p or not os.path.exists(p):
        abort(404)
    zms = read_zms(p)
    nv, nf = len(zms.positions), len(zms.faces)
    has_skin = want_skin and bool(zms.weights) and bool(zms.bones) and bool(zms.bone_indices)
    flags = (1 if zms.uvs else 0) | (2 if has_skin else 0)
    pos_scale = 1.0 if want_skin else (100.0 if zms.version >= 7 else 1.0)   # ZZ_SCALE_IN=0.01
    out = bytearray()
    out += struct.pack("<IIII", 0x4D534D5A, nv, nf, flags)
    for x, y, z in zms.positions:
        out += struct.pack("<fff", x * pos_scale, y * pos_scale, z * pos_scale)
    if zms.normals:
        for x, y, z in zms.normals:
            out += struct.pack("<fff", x, y, z)
    else:
        out += b"\x00" * (nv * 12)
    if zms.uvs:
        for u, v in zms.uvs[0]:
            out += struct.pack("<ff", u, v)
    else:
        out += b"\x00" * (nv * 8)
    for a, b, c in zms.faces:
        out += struct.pack("<HHH", a, b, c)
    if has_skin:
        pal = zms.bone_indices
        for i in range(nv):
            bi, w = zms.bones[i], zms.weights[i]
            for k in range(4):
                j = bi[k] if k < len(bi) else 0
                out += struct.pack("<H", pal[j] if 0 <= j < len(pal) else 0)
            for k in range(4):
                out += struct.pack("<f", w[k] if k < len(w) else 0.0)
    return Response(bytes(out), mimetype="application/octet-stream")


_PNG_CACHE: dict = {}


@app.route("/api/texture")
def api_texture():
    rel = request.args.get("path", "")
    keep_alpha = request.args.get("alpha") == "1"
    ck = (rel, keep_alpha)
    if ck in _PNG_CACHE:
        return Response(_PNG_CACHE[ck], mimetype="image/png")
    p = _resolve(rel)
    if not p or not os.path.exists(p):
        abort(404)
    try:
        from PIL import Image
        im = Image.open(p); im.load()
        im = im.convert("RGBA" if keep_alpha else "RGB")
        buf = BytesIO(); im.save(buf, format="PNG")
        png = buf.getvalue()
    except Exception:
        abort(415)
    _PNG_CACHE[ck] = png
    return Response(png, mimetype="image/png")


if __name__ == "__main__":
    print("mapforge -> http://127.0.0.1:5051")
    app.run(host="127.0.0.1", port=5051, debug=True, use_reloader=False)
