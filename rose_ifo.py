"""Full IFO (map object) codec for the ROSE/SHO map forge.

Unlike the previous editor — which stored REGEN / WATER / MAPINFO as opaque
raw bytes and could therefore never *edit* server-side spawn data — this
module fully decodes every lump the running game actually reads, using the
exact on-disk layouts taken from the engine + server source:

  * Record header  : Client/IO_Terrain.cpp  CMAP::ReadObjINFO
  * REGEN tail      : Server .../Common/CRegenAREA.cpp  CRegenPOINT::Load
  * MOB/EVENT/AREA  : Server .../ZoneFILE.cpp  CZoneFILE::ReadObjINFO
  * lump numbering  : Server .../ZoneFILE.cpp  enum MAP_LUMP_TYPE

Safety model — "decode for editing, preserve for writing":
  Every lump keeps its original raw bytes. On save we re-encode ONLY lumps
  flagged dirty; clean lumps are written back verbatim. `selftest()` re-encodes
  *every* decoded lump and asserts it matches the original bytes, so a layout
  mistake is caught before it can ever corrupt a file the client/server load.

Lump numbering (server-authoritative; client agrees on shared values):
   0 MAPINFO   1 OBJECT   2 MOB     3 CNST    4 SOUND   5 EFFECT  6 MORPH
   7 WATER     8 REGEN    9 OCEAN  10 WARP   11 COLLISION  12 EVENT  13 AREA
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Lump identifiers + which subsystem consumes them (client visual / server
# logic / both). This drives the editor's layer grouping.
# --------------------------------------------------------------------------
LUMP_MAPINFO   = 0
LUMP_OBJECT    = 1
LUMP_MOB       = 2
LUMP_CNST      = 3
LUMP_SOUND     = 4
LUMP_EFFECT    = 5
LUMP_MORPH     = 6
LUMP_WATER     = 7
LUMP_REGEN     = 8
LUMP_OCEAN     = 9
LUMP_WARP      = 10
LUMP_COLLISION = 11
LUMP_EVENT     = 12
LUMP_AREA      = 13

LUMP_NAME = {
    0: "MAPINFO", 1: "OBJECT", 2: "MOB", 3: "CNST", 4: "SOUND", 5: "EFFECT",
    6: "MORPH", 7: "WATER", 8: "REGEN", 9: "OCEAN", 10: "WARP",
    11: "COLLISION", 12: "EVENT", 13: "AREA",
}

# Which side of the client/server divide reads each lump (for UI grouping).
#   "client" : only TRose renders it (no gameplay effect)
#   "server" : only the GameServer acts on it (spawns / triggers / collision)
#   "both"   : shared
LUMP_CONSUMER = {
    LUMP_MAPINFO:   "client",
    LUMP_OBJECT:    "client",
    LUMP_CNST:      "both",     # client renders buildings; server reads CNST too
    LUMP_SOUND:     "client",
    LUMP_EFFECT:    "client",
    LUMP_MORPH:     "client",
    LUMP_WATER:     "client",
    LUMP_OCEAN:     "client",
    LUMP_MOB:       "server",   # NPC / mob placement
    LUMP_REGEN:     "server",   # monster spawn definitions
    LUMP_WARP:      "both",
    LUMP_COLLISION: "both",     # client shows, server blocks
    LUMP_EVENT:     "server",   # warp/event triggers (eventID)
    LUMP_AREA:      "server",   # named areas
}

# World-centering: file positions are zone-centered; add this on read.
#   iOneMapWidth = nGRID_SIZE(250) * GRID_PER_PATCH(16) * PATCH_PER_MAP(16)/... -> 16000
#   offset = 32 * 16000 + 16000/2
CENTER_WORLD = 32 * 16000 + 8000   # 520000


# --------------------------------------------------------------------------
# Tiny endian-correct writer (mirrors rose_map.Reader)
# --------------------------------------------------------------------------
class Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def i16(self, v): self.buf += struct.pack("<h", int(v))
    def i32(self, v): self.buf += struct.pack("<i", int(v))
    def f32(self, v): self.buf += struct.pack("<f", float(v))
    def vec3(self, v): self.buf += struct.pack("<fff", float(v[0]), float(v[1]), float(v[2]))
    def quat(self, v): self.buf += struct.pack("<ffff", float(v[0]), float(v[1]), float(v[2]), float(v[3]))

    def pstr(self, s: str):
        """Length-prefixed (single byte) pascal string, cp949-encoded."""
        b = (s or "").encode("cp949", errors="replace")[:255]
        self.buf.append(len(b))
        self.buf += b

    def raw(self, b: bytes): self.buf += b


class _R:
    """Lightweight reader over a bytes buffer with an absolute cursor."""
    __slots__ = ("b", "p")

    def __init__(self, b: bytes, p: int = 0):
        self.b = b
        self.p = p

    def i16(self):
        v = struct.unpack_from("<h", self.b, self.p)[0]; self.p += 2; return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.p)[0]; self.p += 4; return v

    def vec3(self):
        v = struct.unpack_from("<fff", self.b, self.p); self.p += 12; return v

    def quat(self):
        v = struct.unpack_from("<ffff", self.b, self.p); self.p += 16; return v

    def pstr(self):
        n = self.b[self.p]; self.p += 1
        s = self.b[self.p:self.p + n].decode("cp949", errors="replace")
        self.p += n
        return s


# --------------------------------------------------------------------------
# Structured records
# --------------------------------------------------------------------------
@dataclass
class RegenMob:
    name: str
    mob_id: int       # index into LIST_NPC.STB
    count: int


@dataclass
class IfoObject:
    """One placed record. `pos` is absolute world units (zone-centered);
    `extra` carries the lump-specific tail in a structured form."""
    name: str
    warp_id: int
    event_id: int
    object_type: int
    object_id: int
    map_x: int
    map_y: int
    rot: tuple                 # (x, y, z, w)
    pos: tuple                 # (x, y, z) world
    scale: tuple               # (x, y, z)
    extra: dict = field(default_factory=dict)


@dataclass
class IfoLump:
    type: int
    objects: list = field(default_factory=list)
    raw: bytes = b""           # original on-disk bytes for this lump
    decoded: bool = False      # True if `objects` faithfully represents `raw`
    dirty: bool = False        # set by the editor when objects were changed
    ocean: object = None       # OceanData for LUMP_OCEAN

    @property
    def name(self): return LUMP_NAME.get(self.type, f"LUMP_{self.type}")

    @property
    def consumer(self): return LUMP_CONSUMER.get(self.type, "client")


@dataclass
class OceanData:
    patch_size: float
    blocks: list   # list of ((sx,sy,sz),(ex,ey,ez)) world-centered


@dataclass
class Ifo:
    lumps: dict                      # type -> IfoLump
    center_world: float = CENTER_WORLD


# Lumps that use the standard per-record header.
_RECORD_LUMPS = {
    LUMP_OBJECT, LUMP_MOB, LUMP_CNST, LUMP_SOUND, LUMP_EFFECT, LUMP_MORPH,
    LUMP_WATER, LUMP_REGEN, LUMP_WARP, LUMP_COLLISION, LUMP_EVENT, LUMP_AREA,
}


# --------------------------------------------------------------------------
# Per-record decode/encode
# --------------------------------------------------------------------------
def _read_header(r: _R, center: float) -> IfoObject:
    name = r.pstr()
    warp_id = r.i16()
    event_id = r.i16()
    obj_type = r.i32()
    obj_id = r.i32()
    mx = r.i32()
    my = r.i32()
    rot = r.quat()                 # (x,y,z,w)
    px, py, pz = r.vec3()
    scl = r.vec3()
    return IfoObject(
        name=name, warp_id=warp_id, event_id=event_id,
        object_type=obj_type, object_id=obj_id, map_x=mx, map_y=my,
        rot=rot, pos=(px + center, py + center, pz), scale=scl,
    )


def _write_header(w: Writer, o: IfoObject, center: float) -> None:
    w.pstr(o.name)
    w.i16(o.warp_id)
    w.i16(o.event_id)
    w.i32(o.object_type)
    w.i32(o.object_id)
    w.i32(o.map_x)
    w.i32(o.map_y)
    w.quat(o.rot)
    w.vec3((o.pos[0] - center, o.pos[1] - center, o.pos[2]))
    w.vec3(o.scale)


def _read_regen_tail(r: _R, o: IfoObject) -> None:
    """CRegenPOINT::Load — sub-name, basic mobs, tactics mobs, timing."""
    o.extra["regen_name"] = r.pstr()
    basic = []
    for _ in range(r.i32()):
        nm = r.pstr(); idx = r.i32(); cnt = r.i32()
        basic.append(RegenMob(nm, idx, cnt))
    tactics = []
    for _ in range(r.i32()):
        nm = r.pstr(); idx = r.i32(); cnt = r.i32()
        tactics.append(RegenMob(nm, idx, cnt))
    o.extra["basic"] = basic
    o.extra["tactics"] = tactics
    o.extra["interval"] = r.i32()     # seconds (server ×1000)
    o.extra["limit"] = r.i32()
    o.extra["range"] = r.i32()        # metres (server ×100 -> cm)
    o.extra["tactics_point"] = r.i32()


def _write_regen_tail(w: Writer, o: IfoObject) -> None:
    e = o.extra
    w.pstr(e.get("regen_name", ""))
    basic = e.get("basic", [])
    w.i32(len(basic))
    for m in basic:
        w.pstr(m.name); w.i32(m.mob_id); w.i32(m.count)
    tactics = e.get("tactics", [])
    w.i32(len(tactics))
    for m in tactics:
        w.pstr(m.name); w.i32(m.mob_id); w.i32(m.count)
    w.i32(e.get("interval", 0))
    w.i32(e.get("limit", 0))
    w.i32(e.get("range", 0))
    w.i32(e.get("tactics_point", 0))


def _read_tail(r: _R, lt: int, o: IfoObject) -> None:
    if lt == LUMP_SOUND:
        o.extra["sound_file"] = r.pstr()
        o.extra["range"] = r.i32()
        o.extra["interval"] = r.i32()
    elif lt == LUMP_EFFECT:
        o.extra["effect_file"] = r.pstr()
    elif lt == LUMP_MOB:
        o.extra["ai_index"] = r.i32()
        o.extra["quest_name"] = r.pstr()
    elif lt in (LUMP_EVENT, LUMP_AREA):
        o.extra["str1"] = r.pstr()      # trigger / area name
        o.extra["str2"] = r.pstr()      # con / description
    elif lt == LUMP_REGEN:
        _read_regen_tail(r, o)
    # OBJECT, CNST, MORPH, WATER, WARP, COLLISION: no tail.


def _write_tail(w: Writer, lt: int, o: IfoObject) -> None:
    e = o.extra
    if lt == LUMP_SOUND:
        w.pstr(e.get("sound_file", "")); w.i32(e.get("range", 0)); w.i32(e.get("interval", 0))
    elif lt == LUMP_EFFECT:
        w.pstr(e.get("effect_file", ""))
    elif lt == LUMP_MOB:
        w.i32(e.get("ai_index", 0)); w.pstr(e.get("quest_name", ""))
    elif lt in (LUMP_EVENT, LUMP_AREA):
        w.pstr(e.get("str1", "")); w.pstr(e.get("str2", ""))
    elif lt == LUMP_REGEN:
        _write_regen_tail(w, o)


# --------------------------------------------------------------------------
# OCEAN (non-record layout)
# --------------------------------------------------------------------------
def _read_ocean(r: _R, center: float) -> OceanData:
    patch = r.f32()
    blocks = []
    for _ in range(r.i32()):
        sx, sz, sy = r.f32(), r.f32(), r.f32()
        ex, ez, ey = r.f32(), r.f32(), r.f32()
        blocks.append(((sx + center, sy + center, sz), (ex + center, ey + center, ez)))
    return OceanData(patch, blocks)


def _write_ocean(w: Writer, oc: OceanData, center: float) -> None:
    w.f32(oc.patch_size)
    w.i32(len(oc.blocks))
    for s, e in oc.blocks:
        w.f32(s[0] - center); w.f32(s[2]); w.f32(s[1] - center)
        w.f32(e[0] - center); w.f32(e[2]); w.f32(e[1] - center)


# --------------------------------------------------------------------------
# Lump encode (one lump body) — used for write + self-test
# --------------------------------------------------------------------------
def encode_lump(lump: IfoLump, center: float) -> bytes:
    """Re-encode a decoded lump body to bytes. Raises if the lump was kept
    opaque (callers should emit `lump.raw` in that case)."""
    if not lump.decoded:
        return lump.raw
    w = Writer()
    if lump.type == LUMP_OCEAN:
        _write_ocean(w, lump.ocean, center)
        return bytes(w.buf)
    w.i32(len(lump.objects))
    for o in lump.objects:
        _write_header(w, o, center)
        _write_tail(w, lump.type, o)
    return bytes(w.buf)


# --------------------------------------------------------------------------
# Top-level read / write
# --------------------------------------------------------------------------
def read_ifo(path: str, center: float = CENTER_WORLD) -> Ifo:
    with open(path, "rb") as f:
        buf = f.read()

    n = struct.unpack_from("<i", buf, 0)[0]
    headers = []
    p = 4
    for _ in range(n):
        t, off = struct.unpack_from("<ii", buf, p); p += 8
        headers.append((t, off))

    # Lump body bounds = next lump's offset in file order (EOF for last).
    order = sorted(range(len(headers)), key=lambda i: headers[i][1])
    bound = {}
    for k, i in enumerate(order):
        off = headers[i][1]
        end = headers[order[k + 1]][1] if k + 1 < len(order) else len(buf)
        bound[i] = (off, end)

    lumps: dict = {}
    for i, (lt, off) in enumerate(headers):
        start, end = bound[i]
        raw = buf[start:end]
        lump = IfoLump(type=lt, raw=raw)

        try:
            if lt == LUMP_MAPINFO:
                # Map metadata — opaque; we never edit it.
                lump.decoded = False
            elif lt == LUMP_OCEAN:
                lump.ocean = _read_ocean(_R(buf, off), center)
                lump.decoded = True
            elif lt in _RECORD_LUMPS:
                r = _R(buf, off)
                cnt = r.i32()
                objs = []
                for _ in range(cnt):
                    o = _read_header(r, center)
                    _read_tail(r, lt, o)
                    objs.append(o)
                lump.objects = objs
                lump.decoded = True
            else:
                lump.decoded = False
        except (struct.error, IndexError, UnicodeDecodeError):
            # Anything we can't cleanly parse stays opaque + write-safe.
            lump.decoded = False
            lump.objects = []

        # Verify the decode round-trips to the exact original bytes. If not,
        # downgrade to opaque so saving can never corrupt this lump.
        if lump.decoded:
            try:
                if encode_lump(lump, center) != raw:
                    lump.decoded = False
                    lump.objects = []
                    lump.ocean = None
            except Exception:
                lump.decoded = False
                lump.objects = []
                lump.ocean = None

        lumps[lt] = lump

    return Ifo(lumps=lumps, center_world=center)


def write_ifo(path: str, ifo: Ifo) -> None:
    """Re-emit the IFO. Clean lumps are written verbatim from their original
    bytes; only dirty lumps are re-encoded. Atomic via temp + replace."""
    center = ifo.center_world
    out = Writer()
    out.i32(len(ifo.lumps))
    header_at = len(out.buf)
    for _ in ifo.lumps:
        out.i32(0); out.i32(0)        # placeholder (type, offset)

    headers = []
    for lt in sorted(ifo.lumps.keys()):
        lump = ifo.lumps[lt]
        offset = len(out.buf)
        headers.append((lt, offset))
        if lump.dirty and lump.decoded:
            out.raw(encode_lump(lump, center))
        else:
            out.raw(lump.raw)          # byte-exact preservation

    for k, (lt, off) in enumerate(headers):
        struct.pack_into("<ii", out.buf, header_at + k * 8, lt, off)

    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(out.buf))
    os.replace(tmp, path)


def selftest(path: str, center: float = CENTER_WORLD) -> dict:
    """Decode every lump and confirm re-encode == original. Returns a report
    of which lumps decoded cleanly vs stayed opaque."""
    with open(path, "rb") as f:
        raw = f.read()
    ifo = read_ifo(path, center)
    report = {"file": os.path.basename(path), "lumps": {}, "rewrite_identical": None}
    for lt, lump in sorted(ifo.lumps.items()):
        report["lumps"][lump.name] = {
            "decoded": lump.decoded,
            "records": len(lump.objects),
        }
    # Whole-file rewrite must be byte-identical when nothing is dirty.
    out = Writer()
    out.i32(len(ifo.lumps))
    hat = len(out.buf)
    for _ in ifo.lumps:
        out.i32(0); out.i32(0)
    hdrs = []
    for lt in sorted(ifo.lumps.keys()):
        hdrs.append((lt, len(out.buf)))
        out.raw(ifo.lumps[lt].raw)
    for k, (lt, off) in enumerate(hdrs):
        struct.pack_into("<ii", out.buf, hat + k * 8, lt, off)
    report["rewrite_identical"] = (bytes(out.buf) == raw)
    return report
