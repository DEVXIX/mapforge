"""ROSE Online map readers.

Parses the four per-zone file formats so we can inspect/build a map
editor without touching the DX9 client. Format references:

  ZON: Client/IO_Terrain.cpp Read{Zone,Event,Tile,TileType,Economy}INFO
       (CTERRAIN::LoadZONE at line 2549).
  HIM: Client/IO_Terrain.cpp around line 1849-1922.
  TIL: same file, ~line 1943-1973.
  IFO: same file, CMAP::ReadObjINFO at line 1472 (lump container).

Coordinate system: a zone is conceptually 64x64 tiles (MAP_COUNT_PER_ZONE_AXIS).
Each tile is 16x16 patches, each patch 4x4 grids, each grid = nGRID_SIZE world
units (250 by default — read from ZON). One tile world side = 16*4*250 = 16000.

IFO Position is *= MAGNIFICATION_RATE (= 1) then offset by:
    center = (MAP_COUNT_PER_ZONE_AXIS/2) * tile_world_size + tile_world_size/2
so for default constants center = 32*16000 + 8000 = 520000. The IFO's
on-disk position is relative to that center.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

# -----------------------------------------------------------------------------
# Shared low-level reader
# -----------------------------------------------------------------------------
class Reader:
    """Tiny seekable byte cursor with the same primitive set the ROSE
    CFileSystem exposes."""
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def seek(self, p): self.pos = p
    def tell(self): return self.pos
    def skip(self, n): self.pos += n

    def _read(self, fmt: str, n: int):
        v = struct.unpack_from(fmt, self.buf, self.pos)
        self.pos += n
        return v[0]

    def i8(self):  return self._read("<b", 1)
    def u8(self):  return self._read("<B", 1)
    def i16(self): return self._read("<h", 2)
    def u16(self): return self._read("<H", 2)
    def i32(self): return self._read("<i", 4)
    def u32(self): return self._read("<I", 4)
    def f32(self): return self._read("<f", 4)

    def vec3(self):
        v = struct.unpack_from("<fff", self.buf, self.pos)
        self.pos += 12
        return v

    def quat(self):
        # D3DXQUATERNION in memory: x, y, z, w (FLOAT[4]).
        v = struct.unpack_from("<ffff", self.buf, self.pos)
        self.pos += 16
        return v

    def pascal_byte_str(self) -> str:
        """Length prefix is a single BYTE — used for IFO names + most strings."""
        n = self.u8()
        if n == 0:
            return ""
        s = self.buf[self.pos:self.pos + n]
        self.pos += n
        return s.decode("cp949", errors="replace").rstrip("\x00")

    def pascal_varint_str(self) -> str:
        """Length prefix is a varint: 1 byte if high bit clear, else 2 bytes
        (CFileSystemNormal::ReadPascalStringLength). Used by IFO's
        LUMP_TERRAIN_EVENT_OBJECT and STL files."""
        b0 = self.u8()
        if b0 & 0x80:
            b1 = self.u8()
            n = (b1 << 7) | (b0 - 0x80)
        else:
            n = b0
        if n == 0:
            return ""
        s = self.buf[self.pos:self.pos + n]
        self.pos += n
        return s.decode("cp949", errors="replace").rstrip("\x00")


# -----------------------------------------------------------------------------
# ZON — zone manifest (small ~45 KB binary, lumps reachable by offset)
# -----------------------------------------------------------------------------
LUMP_ZONE_INFO     = 0
LUMP_EVENT_OBJECT  = 1
LUMP_ZONE_TILE     = 2
LUMP_TILE_TYPE     = 3
LUMP_ECONOMY       = 4

@dataclass
class ZonInfo:
    width: int = 0           # logical zone width  (default 64)
    height: int = 0          # logical zone height (default 64)
    grid_per_patch: int = 4  # 4×4 grids per patch
    grid_size: float = 250.0 # world units per grid edge
    center_x: int = 32       # zone-center tile X (where (0,0) world is)
    center_y: int = 32

    @property
    def tile_world_size(self) -> float:
        return self.grid_size * self.grid_per_patch * 16   # 16 = PATCH_COUNT_PER_MAP_AXIS

@dataclass
class ZonEvent:
    pos: tuple
    name: str

@dataclass
class ZonEconomy:
    zone_name: str
    is_dungeon: int
    music_file: str
    sky_model: str

@dataclass
class Zon:
    info: ZonInfo = field(default_factory=ZonInfo)
    tile_textures: list = field(default_factory=list)
    tile_types: list = field(default_factory=list)
    events: list = field(default_factory=list)
    economy: ZonEconomy | None = None


def read_zon(path: str) -> Zon:
    with open(path, "rb") as f:
        r = Reader(f.read())

    z = Zon()
    lump_count = r.i32()
    lumps = [(r.i32(), r.i32()) for _ in range(lump_count)]

    for lump_type, offset in lumps:
        r.seek(offset)
        if lump_type == LUMP_ZONE_INFO:
            r.skip(4)  # the first int (unknown — could be a version)
            z.info.width  = r.i32()
            z.info.height = r.i32()
            z.info.grid_per_patch = r.i32()
            z.info.grid_size = r.f32()
            z.info.center_x = r.i32()
            z.info.center_y = r.i32()
        elif lump_type == LUMP_EVENT_OBJECT:
            n = r.i32()
            for _ in range(n):
                pos = r.vec3()
                name = r.pascal_byte_str()
                z.events.append(ZonEvent(pos=pos, name=name))
        elif lump_type == LUMP_ZONE_TILE:
            n = r.i32()
            for _ in range(n):
                z.tile_textures.append(r.pascal_byte_str())
        elif lump_type == LUMP_TILE_TYPE:
            n = r.i32()
            for _ in range(n):
                # 7 ints per tile-type entry: layer1, layer2, offset1, offset2,
                # blend, rotation, reserved (interpreted from io_stb code).
                z.tile_types.append(tuple(r.i32() for _ in range(7)))
        elif lump_type == LUMP_ECONOMY:
            zone_name = r.pascal_byte_str()
            is_dungeon = r.i32()
            music = r.pascal_byte_str()
            sky = r.pascal_byte_str()
            z.economy = ZonEconomy(zone_name, is_dungeon, music, sky)

    return z


# -----------------------------------------------------------------------------
# HIM — per-tile heightmap (65×65 floats + per-patch AABB Z extents)
# -----------------------------------------------------------------------------
@dataclass
class Him:
    width: int            # vertex count per axis (65)
    height: int
    patch_grid_cnt: int   # 4
    patch_size: float     # 1000.0 for default zones (= 4 * 250)
    heights: list         # heights[row][col] — flipped Y (row 0 = south edge)
    patch_aabb_z: list    # [16][16] of (zmin, zmax)
    quad_aabb_z: list     # quadtree node Z extents (variable count)


def read_him(path: str) -> Him:
    with open(path, "rb") as f:
        r = Reader(f.read())

    w = r.i32()
    h = r.i32()
    pgrid = r.i32()
    psize = r.f32()

    # Heights are written south-up (row index counts down); we store
    # them row-major with row 0 at the southern edge to match the source.
    heights = [[0.0] * w for _ in range(h)]
    for row in range(h - 1, -1, -1):
        for col in range(w):
            heights[row][col] = r.f32()

    # Pascal-varint string ("quad") — engine reads it and discards.
    _name = r.pascal_varint_str()
    _ = r.i32()  # nPatch (per-patch aabb count — should be 16*16 = 256)

    patch_aabb_z = [[(0.0, 0.0)] * 16 for _ in range(16)]
    for row in range(16):
        for col in range(16):
            zmax = r.f32()
            zmin = r.f32()
            patch_aabb_z[row][col] = (zmin, zmax)

    quad_count = r.i32()
    quad_aabb_z = []
    for _ in range(quad_count):
        zmax = r.f32()
        zmin = r.f32()
        quad_aabb_z.append((zmin, zmax))

    return Him(w, h, pgrid, psize, heights, patch_aabb_z, quad_aabb_z)


# -----------------------------------------------------------------------------
# TIL — per-tile texture index map (16×16 entries = one per patch)
# -----------------------------------------------------------------------------
@dataclass
class TilEntry:
    brush: int      # which palette brush this came from in the editor
    tile_idx: int
    tile_set: int
    tile_no: int    # final material index into the zone tile palette

@dataclass
class Til:
    width: int
    height: int
    tiles: list     # tiles[row][col] -> TilEntry


def read_til(path: str) -> Til:
    with open(path, "rb") as f:
        r = Reader(f.read())
    w = r.i32()
    h = r.i32()
    tiles = [[None] * w for _ in range(h)]
    for row in range(h - 1, -1, -1):
        for col in range(w):
            b = r.u8()
            ti = r.u8()
            ts = r.u8()
            no = r.i32()
            tiles[row][col] = TilEntry(b, ti, ts, no)
    return Til(w, h, tiles)


# -----------------------------------------------------------------------------
# IFO — lump container, the soul of the map (objects/mobs/sounds/effects/etc.)
# -----------------------------------------------------------------------------
LUMP_MAPINFO              = 0
LUMP_TERRAIN_OBJECT       = 1
LUMP_TERRAIN_MOB          = 2
LUMP_TERRAIN_CNST         = 3
LUMP_TERRAIN_SOUND        = 4
LUMP_TERRAIN_EFFECT       = 5
LUMP_TERRAIN_MORPH        = 6
LUMP_TERRAIN_WATER        = 7
LUMP_TERRAIN_REGEN        = 8
LUMP_TERRAIN_OCEAN        = 9
LUMP_TERRAIN_WARP         = 10
LUMP_TERRAIN_COLLISION    = 11
LUMP_TERRAIN_EVENT_OBJECT = 12

LUMP_NAMES = {
    LUMP_MAPINFO: "MAPINFO",
    LUMP_TERRAIN_OBJECT: "OBJECT",
    LUMP_TERRAIN_MOB: "MOB",
    LUMP_TERRAIN_CNST: "CNST",
    LUMP_TERRAIN_SOUND: "SOUND",
    LUMP_TERRAIN_EFFECT: "EFFECT",
    LUMP_TERRAIN_MORPH: "MORPH",
    LUMP_TERRAIN_WATER: "WATER",
    LUMP_TERRAIN_REGEN: "REGEN",
    LUMP_TERRAIN_OCEAN: "OCEAN",
    LUMP_TERRAIN_WARP: "WARP",
    LUMP_TERRAIN_COLLISION: "COLLISION",
    LUMP_TERRAIN_EVENT_OBJECT: "EVENT_OBJECT",
}

@dataclass
class IfoObject:
    """One placed entry in an IFO lump. Position is already converted to
    absolute world units (centered on the zone). object_id indexes into
    the appropriate ZSC (per LUMP type)."""
    name: str
    warp_id: int
    event_id: int
    object_type: int
    object_id: int
    map_x_pos: int           # the on-disk minimap coords (raw, pre-center)
    map_y_pos: int
    rotate: tuple            # (x, y, z, w) quaternion
    position: tuple          # (x, y, z) world units, zone-centered
    scale: tuple             # (x, y, z) world units (1,1,1 = unscaled)
    extra: dict = field(default_factory=dict)  # lump-specific tail bytes

@dataclass
class IfoLump:
    type: int
    name: str
    objects: list

@dataclass
class Ifo:
    lumps: dict   # type -> IfoLump


@dataclass
class IfoOcean:
    """LUMP_TERRAIN_OCEAN uses a totally different layout from the
    standard ReadObjINFO path — it's a list of axis-aligned boxes."""
    patch_size: float
    blocks: list   # each: (start_xyz, end_xyz) world-units, zone-centered

def _read_ocean(r: Reader, *, center_world: float) -> IfoOcean:
    patch_size = r.f32()
    cnt = r.i32()
    blocks = []
    for _ in range(cnt):
        # Engine reads X, Z, Y for both endpoints (Y/Z swap to match the
        # right-handed coord system) — preserved verbatim here.
        sx, sz, sy = r.f32(), r.f32(), r.f32()
        ex, ez, ey = r.f32(), r.f32(), r.f32()
        sx += center_world; sy += center_world
        ex += center_world; ey += center_world
        blocks.append(((sx, sy, sz), (ex, ey, ez)))
    return IfoOcean(patch_size=patch_size, blocks=blocks)


def _write_pascal_byte_str(buf: bytearray, s: str) -> None:
    """Write a length-prefixed pascal byte string (single byte length)."""
    b = (s or "").encode("cp949", errors="replace")
    if len(b) > 255:
        b = b[:255]
    buf.append(len(b))
    buf.extend(b)


def _write_pascal_varint_str(buf: bytearray, s: str) -> None:
    """Write a varint-length pascal string (1 or 2-byte length).
    Mirror of Reader.pascal_varint_str — used by IFO EVENT_OBJECT names."""
    b = (s or "").encode("cp949", errors="replace")
    n = len(b)
    if n < 0x80:
        buf.append(n)
    else:
        buf.append(0x80 | (n & 0x7F))
        buf.append(n >> 7)
    buf.extend(b)


def write_ifo(path: str, ifo: Ifo, *, center_world: float = 32 * 16000 + 8000) -> None:
    """Serialize the parsed Ifo back to its on-disk binary form, undoing
    the world-centering we apply on read so positions match the IFO's
    file-space convention. Lumps we don't fully understand (WATER/REGEN/
    MAPINFO) are preserved via their stored raw bytes — we don't write
    placeholders that would lose data.

    The format we re-emit mirrors CMAP::ReadObjINFO's per-lump dispatch
    (see Client/IO_Terrain.cpp:1472+)."""
    out = bytearray()

    # Lump-table placeholder. We rewrite (type, offset) after each lump
    # body is appended so offsets always point at the *current* file
    # position.
    out.extend(struct.pack("<i", len(ifo.lumps)))
    header_pos = len(out)
    for _ in range(len(ifo.lumps)):
        out.extend(struct.pack("<ii", 0, 0))

    headers = []   # (type, offset) — filled in as we go

    # Stable lump order: MAPINFO first (engine assumes this), then
    # whatever else in numeric order. Doesn't have to match the source
    # ordering — offsets are explicit.
    lump_types = sorted(ifo.lumps.keys())

    for lt in lump_types:
        lump = ifo.lumps[lt]
        offset = len(out)
        headers.append((lt, offset))

        # Lumps we kept as raw bytes — write them out verbatim.
        if hasattr(lump, "raw") and lump.raw is not None and not lump.objects:
            out.extend(lump.raw)
            continue

        if lt == LUMP_MAPINFO:
            # We don't parse MAPINFO; if we captured raw bytes use those,
            # otherwise emit an empty zero-count body so the file is at
            # least parseable.
            if hasattr(lump, "raw") and lump.raw is not None:
                out.extend(lump.raw)
            else:
                out.extend(struct.pack("<i", 0))
            continue

        if lt == LUMP_TERRAIN_OCEAN:
            ocean = getattr(lump, "ocean", None)
            if ocean is None:
                out.extend(struct.pack("<fi", 0.0, 0))
                continue
            out.extend(struct.pack("<f", ocean.patch_size))
            out.extend(struct.pack("<i", len(ocean.blocks)))
            for s, e in ocean.blocks:
                sx, sy, sz = s
                ex, ey, ez = e
                # Reverse the world-centering applied at read time + the
                # Y/Z swap the engine does for ocean blocks.
                out.extend(struct.pack("<fff",
                                       sx - center_world,
                                       sz,
                                       sy - center_world))
                out.extend(struct.pack("<fff",
                                       ex - center_world,
                                       ez,
                                       ey - center_world))
            continue

        # Standard per-record lumps (OBJECT, MOB, CNST, SOUND, EFFECT,
        # MORPH, WARP, COLLISION, EVENT_OBJECT, WATER).
        objs = lump.objects or []
        out.extend(struct.pack("<i", len(objs)))
        for o in objs:
            _write_pascal_byte_str(out, o.name or "")
            out.extend(struct.pack("<hh", o.warp_id, o.event_id))
            out.extend(struct.pack("<ii", o.object_type, o.object_id))
            out.extend(struct.pack("<ii", o.map_x_pos, o.map_y_pos))
            # Quaternion: (x, y, z, w) — same order as on read.
            out.extend(struct.pack("<ffff", *o.rotate))
            # Position: re-subtract the world-centering offset on X/Y.
            wx, wy, wz = o.position
            out.extend(struct.pack("<fff", wx - center_world, wy - center_world, wz))
            out.extend(struct.pack("<fff", *o.scale))

            extra = o.extra or {}
            if lt == LUMP_TERRAIN_SOUND:
                _write_pascal_byte_str(out, extra.get("sound_file", ""))
                out.extend(struct.pack("<ii",
                                       int(extra.get("range_cm", 0)),
                                       int(extra.get("interval_s", 0))))
            elif lt == LUMP_TERRAIN_EFFECT:
                _write_pascal_byte_str(out, extra.get("effect_file", ""))
            elif lt == LUMP_TERRAIN_MOB:
                out.extend(struct.pack("<i", int(extra.get("ai_index", 0))))
                _write_pascal_byte_str(out, extra.get("quest_name", ""))
            elif lt == LUMP_TERRAIN_EVENT_OBJECT:
                _write_pascal_varint_str(out, extra.get("trigger_name", ""))
                _write_pascal_varint_str(out, extra.get("con_file", ""))
            # WATER, REGEN, OBJECT, CNST, MORPH, WARP, COLLISION: no tail.

    # Patch the lump table now that we know every lump's offset.
    for i, (ltype, off) in enumerate(headers):
        struct.pack_into("<ii", out, header_pos + i * 8, ltype, off)

    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(bytes(out))
    os.replace(tmp_path, path)


def read_ifo(path: str, *, center_world: float = 32 * 16000 + 8000) -> Ifo:
    with open(path, "rb") as f:
        buf = f.read()
    r = Reader(buf)

    n = r.i32()
    headers = [(r.i32(), r.i32()) for _ in range(n)]
    # End of each lump = start of the next one (or EOF for the last).
    bounds = {off: (hdr_next[1] if i + 1 < len(headers) else len(buf))
              for i, (lt, off) in enumerate(sorted(headers, key=lambda h: h[1]))
              for hdr_next in [sorted(headers, key=lambda h: h[1])[i + 1]
                               if i + 1 < len(headers) else (None, len(buf))]}

    # SKIPPED lumps — the engine doesn't read these (or uses formats we
    # haven't reverse-engineered yet). We preserve their raw bytes so
    # the editor can round-trip the file without losing data.
    SKIP_LUMPS = {LUMP_TERRAIN_WATER, LUMP_TERRAIN_REGEN}

    lumps: dict = {}
    for ltype, off in headers:
        r.seek(off)
        bound = bounds[off]

        # MAPINFO + OCEAN don't use the standard per-record header. We
        # capture them as opaque payloads (MAPINFO) or a typed block (OCEAN)
        # so they're at least discoverable for the editor.
        if ltype == LUMP_MAPINFO:
            lump = IfoLump(ltype, LUMP_NAMES[ltype], [])
            lump.raw = buf[off:bound]
            lumps[ltype] = lump
            continue
        if ltype == LUMP_TERRAIN_OCEAN:
            ocean = _read_ocean(r, center_world=center_world)
            lump = IfoLump(ltype, LUMP_NAMES[ltype], [])
            lump.ocean = ocean
            lumps[ltype] = lump
            continue
        if ltype in SKIP_LUMPS:
            lump = IfoLump(ltype, LUMP_NAMES[ltype], [])
            lump.raw = buf[off:bound]
            # Best-effort: read the cnt so the editor can at least show "how
            # many records" without parsing each one.
            try:
                lump.count_hint = struct.unpack_from("<i", buf, off)[0]
            except struct.error:
                lump.count_hint = 0
            lumps[ltype] = lump
            continue

        objs = []
        cnt = r.i32()
        for _ in range(cnt):
            name = r.pascal_byte_str()
            warp_id  = r.i16()
            event_id = r.i16()
            obj_type = r.i32()
            obj_id   = r.i32()
            mx = r.i32()
            my = r.i32()
            rot = r.quat()       # (x, y, z, w)
            pos = r.vec3()       # raw (already in world units; will be re-centered)
            scl = r.vec3()
            wx = pos[0] * 1 + center_world
            wy = pos[1] * 1 + center_world
            wz = pos[2]

            extra: dict = {}
            # Per-lump tail bytes — keep in sync with the cases in
            # CMAP::ReadObjINFO (IO_Terrain.cpp:1538+).
            if ltype == LUMP_TERRAIN_SOUND:
                extra["sound_file"] = r.pascal_byte_str()
                extra["range_cm"]   = r.i32()
                extra["interval_s"] = r.i32()
            elif ltype == LUMP_TERRAIN_EFFECT:
                extra["effect_file"] = r.pascal_byte_str()
            elif ltype == LUMP_TERRAIN_MOB:
                extra["ai_index"]   = r.i32()
                extra["quest_name"] = r.pascal_byte_str()
            elif ltype == LUMP_TERRAIN_EVENT_OBJECT:
                # Uses VARINT-length pascal strings (not single byte).
                extra["trigger_name"] = r.pascal_varint_str()
                extra["con_file"]     = r.pascal_varint_str()
            elif ltype == LUMP_TERRAIN_WATER:
                # The engine's case body is commented out — no extra bytes
                # are consumed per record. We leave this here as a marker
                # so the editor can still show count + transform.
                pass
            # OBJECT, CNST, MORPH, WARP, COLLISION: no extra bytes.

            objs.append(IfoObject(
                name=name, warp_id=warp_id, event_id=event_id,
                object_type=obj_type, object_id=obj_id,
                map_x_pos=mx, map_y_pos=my,
                rotate=rot, position=(wx, wy, wz), scale=scl,
                extra=extra,
            ))

        lumps[ltype] = IfoLump(ltype, LUMP_NAMES.get(ltype, f"LUMP_{ltype}"), objs)

    return Ifo(lumps=lumps)


# -----------------------------------------------------------------------------
# Zone — discover tile files in a folder + load everything for inspection
# -----------------------------------------------------------------------------
@dataclass
class TileFiles:
    x: int
    y: int
    him: str
    ifo: str
    til: str
    mov: str | None

def list_tiles(zone_dir: str) -> list[TileFiles]:
    """Find all <X>_<Y>.{HIM,IFO,TIL,MOV} quartets in a zone folder."""
    by_xy: dict = {}
    for fn in os.listdir(zone_dir):
        name, ext = os.path.splitext(fn)
        if "_" not in name: continue
        try:
            x, y = (int(p) for p in name.split("_", 1))
        except ValueError:
            continue
        e = by_xy.setdefault((x, y), {})
        e[ext.upper().lstrip(".")] = os.path.join(zone_dir, fn)

    out = []
    for (x, y), exts in sorted(by_xy.items()):
        if "HIM" in exts and "IFO" in exts and "TIL" in exts:
            out.append(TileFiles(x, y,
                                 him=exts["HIM"], ifo=exts["IFO"],
                                 til=exts["TIL"], mov=exts.get("MOV")))
    return out


# -----------------------------------------------------------------------------
# Quick CLI: dump a zone's structure
# -----------------------------------------------------------------------------
def summarize(zone_dir: str, zon_path: str | None = None) -> None:
    if zon_path is None:
        zons = [f for f in os.listdir(zone_dir) if f.lower().endswith(".zon")]
        if not zons:
            raise SystemExit(f"no .ZON in {zone_dir}")
        zon_path = os.path.join(zone_dir, zons[0])

    print(f"\n=== ZON: {zon_path} ===")
    z = read_zon(zon_path)
    print(f"  size:        {z.info.width} × {z.info.height}  (logical tile grid)")
    print(f"  patch grid:  {z.info.grid_per_patch}  grid size: {z.info.grid_size}")
    print(f"  center tile: ({z.info.center_x}, {z.info.center_y})")
    print(f"  tile-world:  {z.info.tile_world_size:.0f} world units per tile")
    if z.economy:
        print(f"  zone name:   {z.economy.zone_name!r}")
        print(f"  is dungeon:  {z.economy.is_dungeon}")
        print(f"  music:       {z.economy.music_file}")
        print(f"  sky model:   {z.economy.sky_model}")
    print(f"  tile textures: {len(z.tile_textures)}  (sample: {z.tile_textures[:3]})")
    print(f"  tile types:    {len(z.tile_types)}")
    print(f"  events:        {len(z.events)}")
    if z.events[:3]:
        for ev in z.events[:3]:
            print(f"    @ ({ev.pos[0]:.0f}, {ev.pos[1]:.0f}, {ev.pos[2]:.0f})  {ev.name!r}")

    tiles = list_tiles(zone_dir)
    if not tiles:
        print("  (no tile files found — empty zone)")
        return

    xs = [t.x for t in tiles]; ys = [t.y for t in tiles]
    print(f"\n=== TILES: {len(tiles)} on disk, bbox X[{min(xs)}..{max(xs)}] Y[{min(ys)}..{max(ys)}] ===")

    # Roll up IFO contents across every tile to give a single zone-wide view.
    totals: dict = {}
    sample_objects: dict = {}
    sample_mobs: list = []
    sample_warps: list = []
    sample_events: list = []
    for t in tiles:
        ifo = read_ifo(t.ifo)
        for lt, lump in ifo.lumps.items():
            totals[lt] = totals.get(lt, 0) + len(lump.objects)
            if lt == LUMP_TERRAIN_OBJECT and len(sample_objects) < 5:
                for o in lump.objects[:1]:
                    sample_objects[o.object_id] = (t, o)
            elif lt == LUMP_TERRAIN_MOB and len(sample_mobs) < 5:
                sample_mobs.extend(lump.objects[:1])
            elif lt == LUMP_TERRAIN_WARP and len(sample_warps) < 5:
                sample_warps.extend(lump.objects[:1])
            elif lt == LUMP_TERRAIN_EVENT_OBJECT and len(sample_events) < 5:
                sample_events.extend(lump.objects[:1])

    print("\n  IFO lump totals across all tiles:")
    for lt in sorted(totals):
        print(f"    {LUMP_NAMES.get(lt, f'LUMP_{lt}'):14s} {totals[lt]:5d}")

    if sample_objects:
        print("\n  Sample placed objects:")
        for oid, (t, o) in list(sample_objects.items())[:5]:
            print(f"    tile {t.x:02d}_{t.y:02d}  id={o.object_id:4d}  "
                  f"pos=({o.position[0]:7.0f},{o.position[1]:7.0f},{o.position[2]:6.0f})  "
                  f"scl=({o.scale[0]:.2f},{o.scale[1]:.2f},{o.scale[2]:.2f})  name={o.name!r}")

    if sample_mobs:
        print("\n  Sample mob spawns:")
        for o in sample_mobs[:5]:
            print(f"    id={o.object_id:4d}  pos=({o.position[0]:7.0f},{o.position[1]:7.0f})  name={o.name!r}")

    if sample_warps:
        print("\n  Sample warp gates:")
        for o in sample_warps[:5]:
            print(f"    warp_id={o.warp_id:3d}  pos=({o.position[0]:7.0f},{o.position[1]:7.0f})  name={o.name!r}")

    # Heightmap stats from the first tile
    print(f"\n=== HIM: {tiles[0].him} (sampling first tile) ===")
    him = read_him(tiles[0].him)
    flat = [v for row in him.heights for v in row]
    print(f"  vertices:  {him.width} × {him.height}")
    print(f"  patch:     {him.patch_grid_cnt} grids/side, patch size {him.patch_size:.0f}")
    print(f"  height:    min={min(flat):.1f}  max={max(flat):.1f}  mean={sum(flat)/len(flat):.1f}")

    print(f"\n=== TIL: {tiles[0].til} (16×16 patch tile indices) ===")
    til = read_til(tiles[0].til)
    used = sorted({til.tiles[r][c].tile_no for r in range(til.height) for c in range(til.width)})
    print(f"  unique tile_no values: {len(used)} (e.g. {used[:8]})")


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else r"D:\sky_rose_extracted\3DDATA\Maps\Junon\JPT01"
    summarize(target)
