"""ZMS (ROSE mesh) reader — versions 7 and 8.

Format reverse-engineered from engine/src/zz_mesh_tool.cpp::load_mesh_8
(lines 483-803).

Vertex-format flags (bitmask, see engine/include/zz_vertex_format.h):
    ZZ_VF_NONE         = 1 << 0
    ZZ_VF_POSITION     = 1 << 1
    ZZ_VF_NORMAL       = 1 << 2
    ZZ_VF_COLOR        = 1 << 3
    ZZ_VF_BLEND_WEIGHT = 1 << 4
    ZZ_VF_BLEND_INDEX  = 1 << 5
    ZZ_VF_TANGENT      = 1 << 6
    ZZ_VF_UV0          = 1 << 7
    ZZ_VF_UV1          = 1 << 8
    ZZ_VF_UV2          = 1 << 9
    ZZ_VF_UV3          = 1 << 10

For map objects, the typical format is POSITION | NORMAL | UV0 (= 0x86) or
POSITION | NORMAL | UV0 | UV1 (= 0x186, two UV channels — one for the
base texture, one for the lightmap).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

ZZ_VF_POSITION     = 1 << 1
ZZ_VF_NORMAL       = 1 << 2
ZZ_VF_COLOR        = 1 << 3
ZZ_VF_BLEND_WEIGHT = 1 << 4
ZZ_VF_BLEND_INDEX  = 1 << 5
ZZ_VF_TANGENT      = 1 << 6
ZZ_VF_UV0          = 1 << 7
ZZ_VF_UV1          = 1 << 8
ZZ_VF_UV2          = 1 << 9
ZZ_VF_UV3          = 1 << 10

UV_BITS = (ZZ_VF_UV0, ZZ_VF_UV1, ZZ_VF_UV2, ZZ_VF_UV3)


@dataclass
class Zms:
    version: int
    vertex_format: int
    pmin: tuple
    pmax: tuple
    bone_indices: list
    positions: list                  # list of (x, y, z)
    normals:   list = field(default_factory=list)
    colors:    list = field(default_factory=list)   # (r, g, b, a) 0..1
    weights:   list = field(default_factory=list)   # (w0..w3)
    bones:     list = field(default_factory=list)   # (b0..b3) uint16
    tangents:  list = field(default_factory=list)
    uvs:       list = field(default_factory=list)   # list of channels, each is list of (u, v)
    faces:     list = field(default_factory=list)   # list of (a, b, c) uint16
    mat_ids:   list = field(default_factory=list)
    strip:     list = field(default_factory=list)


def _read_cstr(buf: bytes, pos: int) -> tuple[str, int]:
    """Read a null-terminated string starting at pos."""
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("cp949", errors="replace"), end + 1


def read_zms(path: str) -> Zms:
    with open(path, "rb") as f:
        buf = f.read()
    p = 0
    ver_str, p = _read_cstr(buf, p)
    if ver_str == "ZMS0008":
        version = 8
    elif ver_str == "ZMS0007":
        version = 7
    else:
        raise ValueError(f"Unsupported ZMS version: {ver_str!r} in {path}")

    vfmt, = struct.unpack_from("<I", buf, p); p += 4
    pmin = struct.unpack_from("<3f", buf, p); p += 12
    pmax = struct.unpack_from("<3f", buf, p); p += 12

    num_bones, = struct.unpack_from("<H", buf, p); p += 2
    bone_indices = list(struct.unpack_from(f"<{num_bones}H", buf, p))
    p += 2 * num_bones

    num_verts, = struct.unpack_from("<H", buf, p); p += 2

    has_normal  = bool(vfmt & ZZ_VF_NORMAL)
    has_color   = bool(vfmt & ZZ_VF_COLOR)
    has_skin    = (vfmt & ZZ_VF_BLEND_WEIGHT) and (vfmt & ZZ_VF_BLEND_INDEX)
    has_tangent = bool(vfmt & ZZ_VF_TANGENT)
    uv_channels = sum(1 for b in UV_BITS if vfmt & b)

    # Positions
    positions = []
    for _ in range(num_verts):
        x, y, z = struct.unpack_from("<3f", buf, p); p += 12
        positions.append((x, y, z))

    # Normals
    normals = []
    if has_normal:
        for _ in range(num_verts):
            x, y, z = struct.unpack_from("<3f", buf, p); p += 12
            normals.append((x, y, z))

    # Colors (float4)
    colors = []
    if has_color:
        for _ in range(num_verts):
            r, g, b, a = struct.unpack_from("<4f", buf, p); p += 16
            colors.append((r, g, b, a))

    # Skin: float4 weights + uint16x4 bone indices
    weights, bones = [], []
    if has_skin:
        for _ in range(num_verts):
            w = struct.unpack_from("<4f", buf, p); p += 16
            b = struct.unpack_from("<4H", buf, p); p +=  8
            weights.append(w)
            bones.append(b)

    # Tangent
    tangents = []
    if has_tangent:
        for _ in range(num_verts):
            x, y, z = struct.unpack_from("<3f", buf, p); p += 12
            tangents.append((x, y, z))

    # UVs — per channel × num_verts × float2
    uvs = []
    for _ in range(uv_channels):
        chan = []
        for _ in range(num_verts):
            u, v = struct.unpack_from("<2f", buf, p); p += 8
            chan.append((u, v))
        uvs.append(chan)

    # Faces — uint16x3 each
    num_faces, = struct.unpack_from("<H", buf, p); p += 2
    faces = []
    for _ in range(num_faces):
        a, b, c = struct.unpack_from("<3H", buf, p); p += 6
        faces.append((a, b, c))

    # Material IDs (per-material face counts) — uint16
    num_matids, = struct.unpack_from("<H", buf, p); p += 2
    mat_ids = list(struct.unpack_from(f"<{num_matids}H", buf, p))
    p += 2 * num_matids

    # Triangle strip block
    num_strip, = struct.unpack_from("<H", buf, p); p += 2
    strip = list(struct.unpack_from(f"<{num_strip}H", buf, p))
    p += 2 * num_strip

    return Zms(
        version=version, vertex_format=vfmt,
        pmin=pmin, pmax=pmax,
        bone_indices=bone_indices,
        positions=positions, normals=normals, colors=colors,
        weights=weights, bones=bones, tangents=tangents,
        uvs=uvs, faces=faces, mat_ids=mat_ids, strip=strip,
    )
