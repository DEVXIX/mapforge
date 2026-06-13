"""ZMO (ROSE motion) parser.

Format (engine/src/zz_motion.cpp):
    char[8] "ZMO0002\0"
    u32 fps
    u32 num_frames
    u32 num_channels
    per channel:  u32 channel_type   u32 refer_id
    per frame:    per channel: payload sized by the channel's format

channel types (zz_channel.h):                       payload
    POSITION 1<<1  NORMAL 1<<3                       3f (xyz)   * ZZ_SCALE_IN
    ROTATION 1<<2                                    4f (wxyz)
    ALPHA 1<<4  TEXTUREANIM 1<<9  SCALE 1<<10        1f (x)
    UV0..3  1<<5..1<<8                               2f (xy)

`refer_id` is the target: a bone index for skeletal clips, or a vertex index
for vertex-morph clips (banners, water). Position values are stored in cm; the
engine multiplies by ZZ_SCALE_IN (0.01) — we keep them in cm (×1) to match the
ZMS world coords we already emit at ×100 for v7 meshes (i.e. ZMO positions are
already in the same space as ×100 v7 vertices)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

CT_NONE = 1 << 0
CT_POSITION = 1 << 1
CT_ROTATION = 1 << 2
CT_NORMAL = 1 << 3
CT_ALPHA = 1 << 4
CT_UV0 = 1 << 5
CT_UV1 = 1 << 6
CT_UV2 = 1 << 7
CT_UV3 = 1 << 8
CT_TEXTUREANIM = 1 << 9
CT_SCALE = 1 << 10

CT_NAME = {
    CT_POSITION: "POSITION", CT_ROTATION: "ROTATION", CT_NORMAL: "NORMAL",
    CT_ALPHA: "ALPHA", CT_UV0: "UV0", CT_UV1: "UV1", CT_UV2: "UV2",
    CT_UV3: "UV3", CT_TEXTUREANIM: "TEXTUREANIM", CT_SCALE: "SCALE",
}

# floats per frame for each type
_FMT = {
    CT_POSITION: 3, CT_NORMAL: 3, CT_ROTATION: 4,
    CT_ALPHA: 1, CT_TEXTUREANIM: 1, CT_SCALE: 1,
    CT_UV0: 2, CT_UV1: 2, CT_UV2: 2, CT_UV3: 2,
}


@dataclass
class ZmoChannel:
    ctype: int
    refer_id: int
    frames: list = field(default_factory=list)   # per-frame value (tuple or float)

    @property
    def name(self):
        return CT_NAME.get(self.ctype, f"0x{self.ctype:x}")


@dataclass
class Zmo:
    fps: int
    num_frames: int
    channels: list

    def types(self):
        from collections import Counter
        return Counter(c.name for c in self.channels)


def read_zmo(path: str) -> Zmo:
    with open(path, "rb") as f:
        b = f.read()
    if b[:7] != b"ZMO0002":
        raise ValueError(f"not a ZMO0002: {b[:8]!r}")
    p = 8
    fps, num_frames, num_channels = struct.unpack_from("<III", b, p)
    p += 12

    chans = []
    for _ in range(num_channels):
        ctype, refer = struct.unpack_from("<II", b, p)
        p += 8
        chans.append(ZmoChannel(ctype, refer))

    nfloats = [_FMT.get(c.ctype) for c in chans]
    if any(n is None for n in nfloats):
        bad = next(c for c, n in zip(chans, nfloats) if n is None)
        raise ValueError(f"unknown channel type 0x{bad.ctype:x} in {path}")

    for _ in range(num_frames):
        for c, n in zip(chans, nfloats):
            vals = struct.unpack_from("<%df" % n, b, p)
            p += 4 * n
            c.frames.append(vals if n > 1 else vals[0])

    return Zmo(fps=fps, num_frames=num_frames, channels=chans)
