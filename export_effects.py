"""Export a zone's particle effects as data the Unity editor script
(RoseEffects.cs) turns into ParticleSystems.

ROSE attaches most effects to *objects*: a ZSC model carries "dummy points",
each with an effect index into the model's effect list (a .EFT) and a local
transform (the attach point + orientation). E.g. the fountain model has 6 dummies
pointing at bunsudae01.eft (Korean *bunsudae* = fountain) around its petal holes;
streetlights -> streetlight01l.eft, braziers -> _agit_fire01.eft, etc. We resolve
each placed object's dummies to world-space (pos + rotation) and parse the EFT ->
.PTL emitters. Standalone EFFECT-lump entries (not attached to a model) are added
too. Fountains additionally get a flat translucent basin pool (no particle effect
provides the standing water).

Output: <bundle>/Effects/effects.json (+ Effects/Textures/*.png). Coords are raw
ROSE; the Unity script parents under the map's "Objects" group so the
z-up->y-up + 0.01 map transform is inherited.
"""

from __future__ import annotations

import os
import re
import sys
import json

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import zone as Z
import rose_ifo as RI
import rose_effect
from rose_zsc import read_zsc

_FOUNTAIN_RE = re.compile(r"fountain\d", re.I)


# ----------------------------------------------------------------- asset lookup
def _resolve(rel):
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


# ----------------------------------------------------------------------- math
def _quat_matrix(qx, qy, qz, qw):
    n = (qx * qx + qy * qy + qz * qz + qw * qw) ** 0.5 or 1.0
    qx, qy, qz, qw = qx / n, qy / n, qz / n, qw / n
    return np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw),     2 * (qx * qz + qy * qw),     0],
        [2 * (qx * qy + qz * qw),     1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw),     0],
        [2 * (qx * qz - qy * qw),     2 * (qy * qz + qx * qw),     1 - 2 * (qx * qx + qy * qy), 0],
        [0, 0, 0, 1]], dtype=np.float64)


def _compose(pos, quat_xyzw, scale):
    M = _quat_matrix(*quat_xyzw)
    M[:3, 0] *= scale[0]; M[:3, 1] *= scale[1]; M[:3, 2] *= scale[2]
    M[0, 3], M[1, 3], M[2, 3] = pos
    return M


def _mat_to_quat_xyzw(M):
    R = np.array(M, dtype=np.float64)[:3, :3].copy()
    for c in range(3):                                   # strip scale
        n = np.linalg.norm(R[:, c]) or 1.0
        R[:, c] /= n
    t = R[0, 0] + R[1, 1] + R[2, 2]
    if t > 0:
        s = 0.5 / np.sqrt(t + 1.0)
        w, x, y, z = 0.25 / s, (R[2, 1] - R[1, 2]) * s, (R[0, 2] - R[2, 0]) * s, (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    return [float(x), float(y), float(z), float(w)]


# ------------------------------------------------------------- emitter flatten
def _event(em, etype):
    return next((e for e in em.get("events", []) if e.get("type") == etype), None)


def _emitter_params(em):
    """Flatten a parsed PTL emitter; `texture` is the raw asset path (the web
    loads it via texUrl; the bundle build() rewrites it to a shipped .png)."""
    size_ev = _event(em, rose_effect.EV_SIZE)
    alpha_ev = _event(em, rose_effect.EV_ALPHA)
    color_ev = _event(em, rose_effect.EV_COLOR)
    vel_ev = _event(em, rose_effect.EV_VEL)
    size = size_ev["size"] if size_ev else [20, 20, 20, 20]
    alpha = alpha_ev["v"] if alpha_ev else [1.0, 1.0]
    color = color_ev["color"] if color_ev else None
    vel = vel_ev["vel"] if vel_ev else [0, 0, 0, 0, 0, 0]
    src, dst, _op = em.get("blend", [5, 2, 1])
    additive = not (src == 4 and dst == 5)     # 4/5 = SRCALPHA/INVSRCALPHA -> normal alpha
    return {
        "texture": em.get("texture") or None,
        "life": em.get("life", [50, 50]),
        "emit_rate": em.get("emit_rate", [100, 100]),
        "emit_radius": em.get("emit_radius", [0, 0, 0, 0, 0, 0]),
        "gravity": em.get("gravity", [0, 0, 0, 0, 0, 0]),
        "num_particles": em.get("num_particles", 40),
        "size": size, "alpha": alpha, "color": color, "vel": vel,
        "additive": additive,
    }


def _ship_texture(rel, out_dir, texmap):
    if not rel:
        return None
    key = rel.lower()
    if key in texmap:
        return texmap[key]
    ab = _resolve(rel)
    fn = os.path.splitext(os.path.basename(rel))[0] + ".png"
    if ab:
        try:
            im = Image.open(ab); im.load()
            im.convert("RGBA").save(os.path.join(out_dir, fn), "PNG")
            texmap[key] = fn
            return fn
        except Exception:
            pass
    texmap[key] = None
    return None


# ------------------------------------------------------------------- gathering
def _model_is_fountain(zsc, oid):
    if not zsc or not (0 <= oid < len(zsc.models)):
        return False
    for p in zsc.models[oid].parts:
        mesh = zsc.meshes[p.mesh_idx] if 0 <= p.mesh_idx < len(zsc.meshes) else ""
        if _FOUNTAIN_RE.search(mesh or ""):
            return True
    return False


def compute(key):
    """Gather all of a zone's effect placements (object-dummy + standalone) and
    fountains. Emitter `texture` fields hold raw asset paths. Used directly by the
    web viewer; build() ships PNGs and rewrites the paths for the Unity bundle."""
    z = Z.find_zone(key)
    if not z:
        raise KeyError(key)

    placements, fountains, eft_cache = [], [], {}

    packs = {}
    for kind, rel in (("OBJECT", z.get("deco_pack")), ("CNST", z.get("cnst_pack"))):
        ab = _resolve(rel) if rel else None
        packs[kind] = read_zsc(ab) if ab else None

    def emitters_for(eft_rel):
        keyl = eft_rel.lower()
        if keyl not in eft_cache:
            ab = _resolve(eft_rel)
            raw = rose_effect.parse_effect(ab, _resolve)["emitters"] if ab else []
            eft_cache[keyl] = [_emitter_params(em) for em in raw]
        return [dict(e) for e in eft_cache[keyl] if e["texture"]]

    for x, y, stem in Z._tiles_in(z["dir"]):
        try:
            ifo = RI.read_ifo(stem + ".IFO")
        except Exception:
            continue

        # 1. object-attached effects (ZSC dummy points) — the bulk of map FX
        for kind, lt in (("OBJECT", RI.LUMP_OBJECT), ("CNST", RI.LUMP_CNST)):
            lump = ifo.lumps.get(lt)
            zsc = packs.get(kind)
            if not lump or not zsc:
                continue
            for o in lump.objects:
                oid = o.object_id
                if not (0 <= oid < len(zsc.models)):
                    continue
                model = zsc.models[oid]
                ifoM = _compose(o.pos, o.rot, o.scale)
                for d in model.dummies:
                    if not (0 <= d.effect_idx < len(zsc.effects)):
                        continue
                    eft_rel = zsc.effects[d.effect_idx]
                    if not eft_rel.lower().endswith(".eft"):
                        continue
                    r = d.rotate                                   # ZSC quat (w,x,y,z)
                    dM = _compose(d.position, (r[1], r[2], r[3], r[0]), d.scale)
                    world = ifoM @ dM
                    ems = emitters_for(eft_rel)
                    if not ems:
                        continue
                    placements.append({
                        "pos": [float(v) for v in world[:3, 3]],
                        "rot": _mat_to_quat_xyzw(world),
                        "emitters": ems,
                    })
                if _model_is_fountain(zsc, oid):
                    fountains.append({"pos": list(o.pos), "scale": (o.scale[2] if o.scale else 1.0)})

        # 2. standalone EFFECT-lump effects (not attached to a model)
        el = ifo.lumps.get(RI.LUMP_EFFECT)
        if el:
            for o in el.objects:
                eft = o.extra.get("effect_file")
                if not eft:
                    continue
                ems = emitters_for(eft)
                if not ems:
                    continue
                placements.append({
                    "pos": list(o.pos),
                    "rot": list(o.rot),                            # IFO quat is already x,y,z,w
                    "emitters": ems,
                })

    return {"zone": key, "placements": placements, "fountains": fountains}


# ------------------------------------------------------------- bundle exporter
def build(key, bundle):
    eff_dir = os.path.join(bundle, "Effects")
    tex_dir = os.path.join(eff_dir, "Textures")
    os.makedirs(tex_dir, exist_ok=True)

    data = compute(key)
    texmap = {}
    for pl in data["placements"]:
        for em in pl["emitters"]:
            em["texture"] = _ship_texture(em["texture"], tex_dir, texmap)
    # drop emitters whose texture failed to convert
    for pl in data["placements"]:
        pl["emitters"] = [e for e in pl["emitters"] if e["texture"]]

    # generated soft sprite for the basin pool (no .EFT provides it)
    soft = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    sp = soft.load()
    import math
    for yy in range(64):
        for xx in range(64):
            d = math.hypot(xx - 32, yy - 32) / 32.0
            a = max(0.0, 1.0 - d)
            sp[xx, yy] = (210, 235, 255, int(255 * a * a))
    soft.save(os.path.join(tex_dir, "fountain_soft.png"), "PNG")

    manifest = {
        "zone": key,
        "rose_to_unity": {"rotate_x_deg": -90, "scale": 0.01},
        "soft_sprite": "fountain_soft.png",
        "placements": data["placements"],
        "fountains": data["fountains"],
    }
    with open(os.path.join(eff_dir, "effects.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    emitter_total = sum(len(p["emitters"]) for p in data["placements"])
    return {"placements": len(data["placements"]), "emitters": emitter_total,
            "fountains": len(data["fountains"]),
            "textures": len([v for v in texmap.values() if v])}


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    out = os.path.join(_HERE, "exports", "%s_fbx" % k)
    os.makedirs(out, exist_ok=True)
    print(json.dumps(build(k, out), indent=2))
