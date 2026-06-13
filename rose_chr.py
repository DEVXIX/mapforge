"""LIST_NPC.CHR — ROSE character/NPC composition file.

Maps each NPC id to the body-part models (indices into PART_NPC.ZSC) that make
it up, plus its skeleton and animation/effect references. The map's MOB lump
stores an object_id that indexes LIST_NPC.STB / this file 1:1, so to draw a real
NPC instead of a marker: chr.characters[object_id].objects -> PART_NPC.ZSC models.

Format (little-endian), validated byte-exact against LIST_NPC.CHR:
    u16 nSkeleton;  nSkeleton x cstr   (.ZMD bone files)
    u16 nMotion;    nMotion   x cstr   (.ZMO motion files)
    u16 nEffect;    nEffect   x cstr   (.EFT effect files)
    u16 nCharacter
      per character:
        u8  enabled                    (0 = empty slot)
        if enabled:
          i16  skeleton_index
          cstr name
          u16  nObject;     nObject    x i16 model_index   (into PART_NPC.ZSC)
          u16  nAnimation;  nAnimation x (i16 type, i16 motion_index)
          u16  nEffect;     nEffect    x (i16 type, i16 effect_index)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Character:
    skeleton: int
    name: str
    objects: List[int] = field(default_factory=list)        # PART_NPC.ZSC model indices
    animations: List[Tuple[int, int]] = field(default_factory=list)   # (type, motion idx)
    effects: List[Tuple[int, int]] = field(default_factory=list)      # (type, effect idx)


@dataclass
class Chr:
    skeletons: List[str]
    motions: List[str]
    effect_files: List[str]
    characters: List[Optional[Character]]   # indexed by NPC id; None = empty slot


class _Reader:
    def __init__(self, b: bytes):
        self.b = b
        self.o = 0

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.b, self.o)[0]; self.o += 2; return v

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.b, self.o)[0]; self.o += 2; return v

    def u8(self) -> int:
        v = self.b[self.o]; self.o += 1; return v

    def cstr(self) -> str:
        s = self.o
        while self.b[self.o] != 0:
            self.o += 1
        v = self.b[s:self.o].decode("latin1")
        self.o += 1
        return v


def read_chr(path: str) -> Chr:
    with open(path, "rb") as f:
        r = _Reader(f.read())

    skeletons = [r.cstr() for _ in range(r.u16())]
    motions = [r.cstr() for _ in range(r.u16())]
    effect_files = [r.cstr() for _ in range(r.u16())]

    n = r.u16()
    characters: List[Optional[Character]] = []
    for _ in range(n):
        if r.u8() == 0:
            characters.append(None)
            continue
        skel = r.i16()
        name = r.cstr()
        objects = [r.i16() for _ in range(r.u16())]
        animations = [(r.i16(), r.i16()) for _ in range(r.u16())]
        effects = [(r.i16(), r.i16()) for _ in range(r.u16())]
        characters.append(Character(skel, name, objects, animations, effects))

    return Chr(skeletons, motions, effect_files, characters)
