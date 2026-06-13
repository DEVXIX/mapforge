"""ZSC (ROSE mesh+material pack) reader.

Format reverse-engineered from Client/IO_Model.h::CModelDATA::Load
(lines 262-394) and Client/IO_Model.cpp::CFixedPART::Load (line 315).

Layout:
  int16  num_meshes;          for each: NULL-terminated path string
  int16  num_materials;       for each: NULL-terminated path + 12 material flags
  int16  num_effects;         for each: NULL-terminated path string
  int16  num_models;          for each: CMODEL block

Each CMODEL block:
  int32  cylinder_radius
  int32  cylinder_x
  int32  cylinder_y
  int16  num_parts            ← if 0, this model is empty; the rest of the
                                 block is omitted (Load returns early).
  for each part:
    int16 mesh_idx, int16 mat_idx
    TAG byte-stream until tag=0:
      BYTE tag, BYTE len, len bytes of tag-specific data
  int16  num_dummy_points (only when num_parts > 0)
  for each dummy point:
    int16 effect_idx, int16 effect_type
    same TAG byte-stream until tag=0
  float3 bb_min, float3 bb_max (only when num_parts > 0)

Part TAGs (from IO_Model.cpp):
  0  SWITCH_NULL    (end marker)
  1  SWITCH_POS     (12 bytes — vec3)
  2  SWITCH_ROT     (16 bytes — quaternion w,x,y,z)
  3  SWITCH_SCALE   (12 bytes — vec3)
  4  SWITCH_ROTAXIS (16 bytes — quaternion)
  5  SWITCH_BONEIDX (2 bytes — int16)
  6  SWITCH_DUMMYIDX(2 bytes — int16)
  7  SWITCH_PARENT  (2 bytes — int16; engine then decrements by 1)
  ...higher tags exist (collision, lightmap flags, animation), kept opaque.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Part TAGs we know how to decode.
TAG_END        = 0
TAG_POS        = 1
TAG_ROT        = 2
TAG_SCALE      = 3
TAG_ROTAXIS    = 4
TAG_BONEIDX    = 5
TAG_DUMMYIDX   = 6
TAG_PARENT     = 7
TAG_COLLISION  = 29   # from cpp (default branch, but value isn't a named #define here)
# Higher tags (range-set, lightmap, ZMO link) are captured as raw bytes.


@dataclass
class ZscMaterial:
    path: str
    is_skin: int
    is_alpha: int
    is_two_side: int
    alpha_test: int
    alpha_ref: int
    z_test: int
    z_write: int
    blend_type: int
    specular: int
    alpha_value: float
    glow_type: int
    glow_color: tuple


@dataclass
class ZscPart:
    mesh_idx: int                    # index into Zsc.meshes
    mat_idx:  int                    # index into Zsc.materials
    position: tuple = (0.0, 0.0, 0.0)
    rotate:   tuple = (1.0, 0.0, 0.0, 0.0)   # (w, x, y, z) — note quaternion order
    scale:    tuple = (1.0, 1.0, 1.0)
    rotaxis:  tuple | None = None
    parent:   int = -1
    bone_idx: int = -1
    dummy_idx: int = -1
    collision: int = 0
    extras:   list = field(default_factory=list)  # (tag, raw_bytes) for unknown tags


@dataclass
class ZscDummy:
    effect_idx: int                  # index into Zsc.effects
    effect_type: int
    position: tuple = (0.0, 0.0, 0.0)
    rotate:   tuple = (1.0, 0.0, 0.0, 0.0)
    scale:    tuple = (1.0, 1.0, 1.0)
    parent:   int = -1
    extras:   list = field(default_factory=list)


@dataclass
class ZscModel:
    cylinder_radius: int
    cylinder_x: int
    cylinder_y: int
    parts: list = field(default_factory=list)
    dummies: list = field(default_factory=list)
    bb_min:  tuple = (0.0, 0.0, 0.0)
    bb_max:  tuple = (0.0, 0.0, 0.0)


@dataclass
class Zsc:
    path: str
    meshes:    list                  # list of .zms paths
    materials: list                  # list of ZscMaterial
    effects:   list                  # list of .eft paths
    models:    list                  # list of ZscModel


# -----------------------------------------------------------------------------
# Reader helpers
# -----------------------------------------------------------------------------
def _read_cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("cp949", errors="replace"), end + 1


def _read_part_tags(buf: bytes, p: int, target):
    """Read TAG / LEN / payload triplets until TAG=0. Mutates `target`
    (a ZscPart or ZscDummy) in place. Returns the new cursor pos."""
    while True:
        tag = buf[p]; p += 1
        if tag == TAG_END:
            return p
        ln = buf[p]; p += 1
        if   tag == TAG_POS:
            target.position = struct.unpack_from("<3f", buf, p)
        elif tag == TAG_ROT:
            target.rotate   = struct.unpack_from("<4f", buf, p)   # (w, x, y, z)
        elif tag == TAG_SCALE:
            target.scale    = struct.unpack_from("<3f", buf, p)
        elif tag == TAG_ROTAXIS:
            if hasattr(target, "rotaxis"):
                target.rotaxis = struct.unpack_from("<4f", buf, p)
        elif tag == TAG_PARENT:
            target.parent = struct.unpack_from("<h", buf, p)[0] - 1
        elif tag == TAG_BONEIDX and hasattr(target, "bone_idx"):
            target.bone_idx = struct.unpack_from("<h", buf, p)[0]
        elif tag == TAG_DUMMYIDX and hasattr(target, "dummy_idx"):
            target.dummy_idx = struct.unpack_from("<h", buf, p)[0]
        else:
            target.extras.append((tag, buf[p:p + ln]))
        p += ln


# -----------------------------------------------------------------------------
# Top-level
# -----------------------------------------------------------------------------
def read_zsc(path: str) -> Zsc:
    with open(path, "rb") as f:
        buf = f.read()
    p = 0

    # ---- Mesh list
    n, = struct.unpack_from("<h", buf, p); p += 2
    meshes = []
    for _ in range(n):
        s, p = _read_cstr(buf, p)
        meshes.append(s)

    # ---- Material list
    n, = struct.unpack_from("<h", buf, p); p += 2
    materials = []
    for _ in range(n):
        s, p = _read_cstr(buf, p)
        is_skin     = struct.unpack_from("<h", buf, p)[0]; p += 2
        is_alpha    = struct.unpack_from("<h", buf, p)[0]; p += 2
        is_two_side = struct.unpack_from("<h", buf, p)[0]; p += 2
        alpha_test  = struct.unpack_from("<h", buf, p)[0]; p += 2
        alpha_ref   = struct.unpack_from("<h", buf, p)[0]; p += 2
        z_test      = struct.unpack_from("<h", buf, p)[0]; p += 2
        z_write     = struct.unpack_from("<h", buf, p)[0]; p += 2
        blend_type  = struct.unpack_from("<h", buf, p)[0]; p += 2
        specular    = struct.unpack_from("<h", buf, p)[0]; p += 2
        alpha_value = struct.unpack_from("<f", buf, p)[0]; p += 4
        glow_type   = struct.unpack_from("<h", buf, p)[0]; p += 2
        glow_color  = struct.unpack_from("<3f", buf, p);   p += 12
        materials.append(ZscMaterial(
            path=s, is_skin=is_skin, is_alpha=is_alpha, is_two_side=is_two_side,
            alpha_test=alpha_test, alpha_ref=alpha_ref, z_test=z_test, z_write=z_write,
            blend_type=blend_type, specular=specular,
            alpha_value=alpha_value, glow_type=glow_type, glow_color=glow_color,
        ))

    # ---- Effect list
    n, = struct.unpack_from("<h", buf, p); p += 2
    effects = []
    for _ in range(n):
        s, p = _read_cstr(buf, p)
        effects.append(s)

    # ---- Models
    n, = struct.unpack_from("<h", buf, p); p += 2
    models = []
    for _ in range(n):
        cr = struct.unpack_from("<i", buf, p)[0]; p += 4
        cx = struct.unpack_from("<i", buf, p)[0]; p += 4
        cy = struct.unpack_from("<i", buf, p)[0]; p += 4
        npart = struct.unpack_from("<h", buf, p)[0]; p += 2

        model = ZscModel(cylinder_radius=cr, cylinder_x=cx, cylinder_y=cy)
        if npart == 0:
            # Engine returns early on empty models — no dummy/bbox follows.
            models.append(model)
            continue

        for _ in range(npart):
            mesh_idx = struct.unpack_from("<h", buf, p)[0]; p += 2
            mat_idx  = struct.unpack_from("<h", buf, p)[0]; p += 2
            part = ZscPart(mesh_idx=mesh_idx, mat_idx=mat_idx)
            p = _read_part_tags(buf, p, part)
            model.parts.append(part)

        ndummy = struct.unpack_from("<h", buf, p)[0]; p += 2
        for _ in range(ndummy):
            eff_idx  = struct.unpack_from("<h", buf, p)[0]; p += 2
            eff_type = struct.unpack_from("<h", buf, p)[0]; p += 2
            dummy = ZscDummy(effect_idx=eff_idx, effect_type=eff_type)
            p = _read_part_tags(buf, p, dummy)
            model.dummies.append(dummy)

        model.bb_min = struct.unpack_from("<3f", buf, p); p += 12
        model.bb_max = struct.unpack_from("<3f", buf, p); p += 12
        models.append(model)

    return Zsc(path=path, meshes=meshes, materials=materials,
               effects=effects, models=models)
