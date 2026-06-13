"""Safe ZSC editing — append a model from one pack into another.

Cross-map object placement needs the foreign model added to the target zone's
ZSC pack (the IFO object_id indexes that pack). Editing a live pack is risky,
so the strategy is "re-serialize the deterministic parts, preserve the rest":

  * mesh list, material list, effect list  -> re-serialized from parsed data
    (these have no hidden ordering, so this is byte-exact)
  * each existing MODEL block               -> copied verbatim as raw bytes
    (part TAG order isn't recoverable from the parse, so we never rewrite them)
  * the newly appended model                -> synthesized from the source

`selftest()` rewrites a pack with no changes and asserts byte-identical output,
proving the round-trip before any real append touches disk.
"""

from __future__ import annotations

import os
import struct
import sys
import shutil

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from rose_zsc import read_zsc, ZscMaterial  # noqa: E402
import config  # noqa: E402


# --------------------------------------------------------------------------
# Low-level
# --------------------------------------------------------------------------
def _cstr(buf, p):
    e = buf.index(b"\x00", p)
    return buf[p:e].decode("cp949", errors="replace"), e + 1


def _wcstr(out, s):
    out += (s or "").encode("cp949", errors="replace")
    out += b"\x00"


def _skip_tags(buf, p):
    while True:
        tag = buf[p]; p += 1
        if tag == 0:
            return p
        ln = buf[p]; p += 1
        p += ln


def _skip_model(buf, p):
    """Return the end offset of the model block starting at p."""
    p += 12                       # cr, cx, cy
    npart = struct.unpack_from("<h", buf, p)[0]; p += 2
    if npart == 0:
        return p
    for _ in range(npart):
        p += 4                    # mesh_idx, mat_idx
        p = _skip_tags(buf, p)
    ndummy = struct.unpack_from("<h", buf, p)[0]; p += 2
    for _ in range(ndummy):
        p += 4                    # eff_idx, eff_type
        p = _skip_tags(buf, p)
    p += 24                       # bb_min, bb_max
    return p


# --------------------------------------------------------------------------
# Editable parse — structured lists + raw model spans
# --------------------------------------------------------------------------
class EditablePack:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            buf = f.read()
        self.buf = buf
        p = 0
        n = struct.unpack_from("<h", buf, p)[0]; p += 2
        self.meshes = []
        for _ in range(n):
            s, p = _cstr(buf, p); self.meshes.append(s)
        n = struct.unpack_from("<h", buf, p)[0]; p += 2
        self.materials = []
        for _ in range(n):
            s, p = _cstr(buf, p)
            vals = struct.unpack_from("<9h", buf, p); p += 18
            av = struct.unpack_from("<f", buf, p)[0]; p += 4
            gt = struct.unpack_from("<h", buf, p)[0]; p += 2
            gc = struct.unpack_from("<3f", buf, p); p += 12
            self.materials.append(ZscMaterial(s, *vals[:9], av, gt, gc))
        n = struct.unpack_from("<h", buf, p)[0]; p += 2
        self.effects = []
        for _ in range(n):
            s, p = _cstr(buf, p); self.effects.append(s)
        n = struct.unpack_from("<h", buf, p)[0]; p += 2
        self.model_raws = []
        for _ in range(n):
            start = p
            p = _skip_model(buf, p)
            self.model_raws.append(buf[start:p])
        self._tail = buf[p:]      # should be empty

    # -- serialization --
    def _header_bytes(self):
        out = bytearray()
        out += struct.pack("<h", len(self.meshes))
        for m in self.meshes:
            _wcstr(out, m)
        out += struct.pack("<h", len(self.materials))
        for mt in self.materials:
            _wcstr(out, mt.path)
            out += struct.pack("<9h", mt.is_skin, mt.is_alpha, mt.is_two_side,
                               mt.alpha_test, mt.alpha_ref, mt.z_test, mt.z_write,
                               mt.blend_type, mt.specular)
            out += struct.pack("<f", mt.alpha_value)
            out += struct.pack("<h", mt.glow_type)
            out += struct.pack("<3f", *mt.glow_color)
        out += struct.pack("<h", len(self.effects))
        for e in self.effects:
            _wcstr(out, e)
        return out

    def to_bytes(self):
        out = self._header_bytes()
        out += struct.pack("<h", len(self.model_raws))
        for r in self.model_raws:
            out += r
        out += self._tail
        return bytes(out)

    def write(self, path=None):
        data = self.to_bytes()
        path = path or self.path
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)


def selftest(path):
    ep = EditablePack(path)
    return ep.to_bytes() == ep.buf


# --------------------------------------------------------------------------
# Synthesize a model block from a parsed (rose_zsc) model
# --------------------------------------------------------------------------
def _tag(out, tag, payload):
    out += bytes([tag, len(payload)])
    out += payload


def _synth_part(out, part, mesh_idx, mat_idx):
    out += struct.pack("<hh", mesh_idx, mat_idx)
    _tag(out, 1, struct.pack("<3f", *part.position))           # POS
    _tag(out, 2, struct.pack("<4f", *part.rotate))             # ROT (w,x,y,z)
    _tag(out, 3, struct.pack("<3f", *part.scale))              # SCALE
    if part.parent is not None and part.parent >= 0:
        _tag(out, 7, struct.pack("<h", part.parent + 1))       # PARENT (engine -1)
    for (t, raw) in part.extras:
        _tag(out, t, raw)
    out += bytes([0])                                          # TAG_END


def synth_model(model, mesh_index_map, mat_index_map):
    out = bytearray()
    out += struct.pack("<iii", model.cylinder_radius, model.cylinder_x, model.cylinder_y)
    out += struct.pack("<h", len(model.parts))
    if not model.parts:
        return bytes(out)
    for part in model.parts:
        mi = mesh_index_map[part.mesh_idx]
        ai = mat_index_map.get(part.mat_idx, 0)
        _synth_part(out, part, mi, ai)
    out += struct.pack("<h", 0)                                # num_dummy = 0 (drop effects)
    out += struct.pack("<3f", *model.bb_min)
    out += struct.pack("<3f", *model.bb_max)
    return bytes(out)


# --------------------------------------------------------------------------
# Append a foreign model into a target pack
# --------------------------------------------------------------------------
def append_model(target_pack_rel: str, source_pack_rel: str, source_model_idx: int,
                 resolve) -> int:
    """Append source pack's model #idx into the target pack (re-indexing its
    meshes/materials, de-duplicating by path). Writes the pack to every data
    root. `resolve(rel)->abspath|None` resolves a pack-relative path.
    Returns the new object_id (model index) in the target pack."""
    tgt_abs = resolve(target_pack_rel)
    src_abs = resolve(source_pack_rel)
    if not tgt_abs or not src_abs:
        raise FileNotFoundError("pack not found")

    ep = EditablePack(tgt_abs)
    src = read_zsc(src_abs)
    model = src.models[source_model_idx]

    # mesh path -> existing index (dedup)
    mesh_pos = {m.lower(): i for i, m in enumerate(ep.meshes)}
    mat_pos = {m.path.lower(): i for i, m in enumerate(ep.materials)}

    mesh_index_map = {}
    mat_index_map = {}
    for part in model.parts:
        mp = src.meshes[part.mesh_idx] if 0 <= part.mesh_idx < len(src.meshes) else ""
        key = mp.lower()
        if key in mesh_pos:
            mesh_index_map[part.mesh_idx] = mesh_pos[key]
        else:
            mesh_pos[key] = len(ep.meshes)
            mesh_index_map[part.mesh_idx] = len(ep.meshes)
            ep.meshes.append(mp)
        if 0 <= part.mat_idx < len(src.materials):
            smat = src.materials[part.mat_idx]
            mkey = smat.path.lower()
            if mkey in mat_pos:
                mat_index_map[part.mat_idx] = mat_pos[mkey]
            else:
                mat_pos[mkey] = len(ep.materials)
                mat_index_map[part.mat_idx] = len(ep.materials)
                ep.materials.append(smat)

    new_id = len(ep.model_raws)
    ep.model_raws.append(synth_model(model, mesh_index_map, mat_index_map))

    # Write to every data root that has this pack file.
    written = []
    for label, root in config.WRITE_ROOTS:
        parts = [p for p in target_pack_rel.replace("\\", "/").split("/") if p]
        if parts and parts[0].lower() == "3ddata":
            parts = parts[1:]
        dst = os.path.join(root, *parts)
        if os.path.isfile(dst):
            bak = dst + ".mapforge.bak"
            if not os.path.exists(bak):
                shutil.copy2(dst, bak)
            ep.write(dst)
            written.append(label)
    return new_id
