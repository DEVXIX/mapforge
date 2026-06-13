"""MOV (movement / walkability) grid reader.

Server-authoritative collision. Layout (Server .../Zone/MovGrid.cpp):
    int  Width, Height            (32 x 32 per tile)
    byte attr[Height][Width]      rows stored top-to-bottom; world Y is
                                  flipped at load:  FlippedY = (H-1) - y

Each cell is 500 world units (= 250*4 patch / GRID_PER_TILE(2)). A tile is
32 cells = 16000 units, matching the IFO tile world size, so a cell at grid
index (gx, gy) sits at absolute world centre ((gx+0.5)*500, (gy+0.5)*500) —
the same world space IFO objects use. No extra centering needed.

Attr values:  0 Walkable   1 NotWalkable   2 MobNotWalkable
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

CELL_SIZE = 500.0            # world units per cell
CELLS_PER_TILE = 32          # GRID_TILE_CNT = MAP_TILE_CNT(16) * GRID_PER_TILE(2)

WALKABLE = 0
NOT_WALKABLE = 1
MOB_NOT_WALKABLE = 2


@dataclass
class MovTile:
    tile_x: int
    tile_y: int
    width: int
    height: int
    # cells in WORLD orientation: cells[row][col], row 0 = lowest world Y.
    cells: list

    def origin_world(self):
        """South-west corner of this tile in world units."""
        return (self.tile_x * CELLS_PER_TILE * CELL_SIZE,
                self.tile_y * CELLS_PER_TILE * CELL_SIZE)


def read_mov(path: str, tile_x: int, tile_y: int) -> MovTile:
    with open(path, "rb") as f:
        b = f.read()
    w, h = struct.unpack_from("<ii", b, 0)
    flat = b[8:8 + w * h]
    # Undo the engine's load-time Y flip so row 0 = lowest world Y. The engine
    # writes m_GridData[base + (H-1-y)] = file_row[y]; we replicate by reading
    # file rows in reverse into world rows.
    cells = [[0] * w for _ in range(h)]
    for y in range(h):
        world_row = (h - 1) - y
        off = y * w
        cells[world_row] = list(flat[off:off + w])
    return MovTile(tile_x=tile_x, tile_y=tile_y, width=w, height=h, cells=cells)


def write_mov(path: str, mov: MovTile) -> None:
    """Re-emit a MOV tile (re-applying the engine's Y flip)."""
    import os
    w, h = mov.width, mov.height
    out = bytearray(struct.pack("<ii", w, h))
    for y in range(h):
        world_row = (h - 1) - y
        out += bytes(bytearray(mov.cells[world_row][:w]))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(out))
    os.replace(tmp, path)
