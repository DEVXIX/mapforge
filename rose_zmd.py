"""ZMD (ROSE skeleton) reader — the bone hierarchy NPC/monster meshes skin to.

Format (little-endian), validated byte-exact:
    char[7] "ZMD0002"
    i32 bone_count
      per bone: i32 parent, cstr name, f32[3] position, f32[4] rotation (w,x,y,z)
    i32 dummy_count           (attach points — not needed for skinning, skipped)
      ...

Bone position/rotation are LOCAL (relative to parent), i.e. the bind pose. The
ZMO motion channels are keyed by bone index (refer_id) and replace these per
frame. Rotation is stored (w,x,y,z); convert to (x,y,z,w) for glTF/three.js.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Bone:
    parent: int
    name: str
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]   # (w, x, y, z)


@dataclass
class Zmd:
    bones: List[Bone]


def read_zmd(path: str) -> Zmd:
    with open(path, "rb") as f:
        b = f.read()
    o = 7  # skip the 7-char identifier ("ZMD0002"); there is no null terminator

    def i32():
        nonlocal o
        v = struct.unpack_from("<i", b, o)[0]; o += 4; return v

    def f32():
        nonlocal o
        v = struct.unpack_from("<f", b, o)[0]; o += 4; return v

    def cstr():
        nonlocal o
        s = o
        while b[o] != 0:
            o += 1
        v = b[s:o].decode("latin1"); o += 1
        return v

    n = i32()
    bones = []
    for _ in range(n):
        parent = i32()
        name = cstr()
        pos = (f32(), f32(), f32())
        rot = (f32(), f32(), f32(), f32())   # (w, x, y, z)
        bones.append(Bone(parent, name, pos, rot))
    return Zmd(bones)
