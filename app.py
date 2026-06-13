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
    p = _resolve(rel)
    if not p or not os.path.exists(p):
        abort(404)
    zms = read_zms(p)
    nv, nf = len(zms.positions), len(zms.faces)
    flags = 1 if zms.uvs else 0
    pos_scale = 100.0 if zms.version >= 7 else 1.0   # ZZ_SCALE_IN=0.01
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
