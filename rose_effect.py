"""EFT / PTL (ROSE effect + particle) readers — full parse of the engine format.

An EFFECT lump entry points at an .EFT, which references one or more .PTL
particle files. A PTL is a particle emitter: u32 sequence_count + that many
"event sequences". Each sequence is one particle layer: lifetime, emit rate,
emit radius/spawn dir/gravity (all *ZZ_SCALE_IN), a texture, particle count,
blend modes, and a list of timed events (size / colour / alpha / velocity /
rotation curves). See engine/src/zz_particle_event_sequence.cpp + ..._event.cpp.

Distances (emit radius, spawn dir, gravity, velocity) are *ZZ_SCALE_IN (0.01) in
the engine and the world is then *100, so the raw file value already equals the
viewer's external units — we keep them as-is.
"""

from __future__ import annotations

import os
import re
import struct
from typing import List, Dict

# event types (engine EVENT_TYPE enum)
EV_SIZE, EV_TIMER, EV_RED, EV_GREEN, EV_BLUE, EV_ALPHA, EV_COLOR = 1, 2, 3, 4, 5, 6, 7
EV_VELX, EV_VELY, EV_VELZ, EV_VEL, EV_TEXTURE, EV_ROTATION = 8, 9, 10, 11, 12, 13


class _R:
    def __init__(self, b):
        self.b = b
        self.o = 0

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.o)[0]; self.o += 4; return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.o)[0]; self.o += 4; return v

    def u8(self):
        v = self.b[self.o]; self.o += 1; return v

    def lstr(self):
        n = self.u32()
        s = self.b[self.o:self.o + n].decode("latin1", "replace"); self.o += n
        return s.strip('"')

    def skip(self, n):
        self.o += n


def _read_event(r: _R) -> Dict:
    etype = r.u32()
    tmin, tmax = r.f32(), r.f32()
    fade = r.u8() != 0
    ev = {"type": etype, "t": [tmin, tmax], "fade": fade}
    if etype == EV_SIZE:
        ev["size"] = [r.f32(), r.f32(), r.f32(), r.f32()]          # min xy, max xy
    elif etype == EV_COLOR:
        ev["color"] = [r.f32() for _ in range(8)]                 # min rgba, max rgba
    elif etype in (EV_RED, EV_GREEN, EV_BLUE, EV_ALPHA, EV_TIMER, EV_VELX, EV_VELY, EV_VELZ, EV_TEXTURE, EV_ROTATION):
        ev["v"] = [r.f32(), r.f32()]
    elif etype == EV_VEL:
        ev["vel"] = [r.f32() for _ in range(6)]                   # min xyz, max xyz
    return ev


def _read_sequence(r: _R) -> Dict:
    name = r.lstr()
    life = [r.f32(), r.f32()]
    emit_rate = [r.f32(), r.f32()]
    loops = r.u32()
    spawn_dir = [r.f32() for _ in range(6)]
    emit_radius = [r.f32() for _ in range(6)]
    gravity = [r.f32() for _ in range(6)]
    texture = r.lstr()
    num_particles = r.u32()
    align = r.u32()
    update_coord = r.u32()
    tex_w, tex_h = r.u32(), r.u32()
    r.skip(4)                                  # implement type
    dst_blend = r.u32()
    src_blend = r.u32()
    blend_op = r.u32()
    nevents = r.u32()
    events = [_read_event(r) for _ in range(nevents)]
    return {
        "name": name, "life": life, "emit_rate": emit_rate, "loops": loops,
        "spawn_dir": spawn_dir, "emit_radius": emit_radius, "gravity": gravity,
        "texture": texture, "num_particles": num_particles,
        "tex_w": tex_w, "tex_h": tex_h,
        "blend": [src_blend, dst_blend, blend_op],
        "events": events,
    }


def parse_ptl(path: str) -> List[Dict]:
    with open(path, "rb") as f:
        r = _R(f.read())
    n = r.u32()
    out = []
    for _ in range(n):
        try:
            out.append(_read_sequence(r))
        except Exception:
            break
    return out


def _strings(b, minlen=4):
    return [m.group().decode("latin1") for m in re.finditer(rb"[ -~]{%d,}" % minlen, b)]


def parse_eft(path: str) -> List[str]:
    with open(path, "rb") as f:
        b = f.read()
    strs = _strings(b)
    pdir = next((s for s in strs if s.lower().rstrip("\\/").endswith("particles")), r"3DData\Effect\Particles")
    pdir = pdir.rstrip("\\/")
    out, seen = [], set()
    for s in strs:
        if s.lower().endswith(".ptl"):
            fn = re.split(r"[\\/]", s)[-1]
            if fn.lower() not in seen:
                seen.add(fn.lower())
                out.append(pdir + "\\" + fn)
    return out


def parse_effect(eft_path: str, resolve) -> Dict:
    """EFT path -> {emitters:[ sequence dicts ]} (each sequence = one particle layer)."""
    emitters = []
    for ptl_rel in parse_eft(eft_path):
        ab = resolve(ptl_rel)
        if not ab or not os.path.exists(ab):
            continue
        try:
            emitters.extend(parse_ptl(ab))
        except Exception:
            continue
    return {"emitters": emitters}


if __name__ == "__main__":
    import sys
    import json
    print(json.dumps(parse_ptl(sys.argv[1]), indent=1)[:6000])
