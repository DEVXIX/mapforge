#!/usr/bin/env python3
"""
Parse ROSE Online .STB (data table) and .STL (string table) files.

.STB file layout (version 0 / 1, little-endian):
  uint32  magic 'STBx' where x = '0' or '1' (version)
  uint32  data_offset
  int32   row_count   (includes the header row at index 0)
  int32   col_count   (includes the row-name column at index 0)
  int32   <skipped>
  version 0: int32        <skipped>
  version 1: int16[col_count+1]  <skipped>
  for each column index 0..col_count-1:
     int16  name_length
     bytes  name (column header text)
  int16  <skipped>  (extra column-title length)
  bytes  <skipped>  (extra column-title text)
  for each row index 0..row_count-2:
     int16  name_length
     bytes  row_name (NUL-padded; use slice)
  (file seek to data_offset)
  for each row index 0..row_count-1:
     for each col index 0..col_count-1:
        int16  cell_length
        bytes  cell_value (NUL-padded utf-8 / mbcs)

.STL file layout:
  int32  col_count
  int32  row_count
  for each row r in 0..row_count-1:
     for each col c in 0..col_count-1:
        int32  file_pos_absolute   (subtract header size to get data-region offset)
        int16  byte_length
  <wide-char data region; each (r,c) cell -> bytes_length wide chars at offset>
"""
import os
import struct
import sys


class StbFile:
    """Parses an STB file. Rows accessed by index. Each row is a list[str]."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self._buf = f.read()
        self._parse()

    def _read_u32(self, off: int) -> tuple[int, int]:
        return struct.unpack_from("<I", self._buf, off)[0], off + 4

    def _read_i32(self, off: int) -> tuple[int, int]:
        return struct.unpack_from("<i", self._buf, off)[0], off + 4

    def _read_i16(self, off: int) -> tuple[int, int]:
        return struct.unpack_from("<h", self._buf, off)[0], off + 2

    def _read_bytes(self, off: int, n: int) -> tuple[bytes, int]:
        return self._buf[off:off + n], off + n

    def _decode(self, b: bytes) -> str:
        if not b:
            return ""
        for enc in ("utf-8", "cp949", "latin-1"):
            try:
                return b.decode(enc).rstrip("\x00")
            except UnicodeDecodeError:
                continue
        return b.decode("latin-1", errors="replace").rstrip("\x00")

    def _parse(self) -> None:
        magic, off = self._read_u32(0)
        if (magic & 0x00FFFFFF) != ord("S") | (ord("T") << 8) | (ord("B") << 16):
            raise ValueError(f"{self.path}: not an STB file (magic {magic:08X})")
        version = (magic >> 24) - ord("0")  # 'STB0' -> 0, 'STB1' -> 1
        data_offset, off = self._read_u32(off)
        # raw row/col counts include the header row + name column
        raw_row_count, off = self._read_i32(off)
        raw_col_count, off = self._read_i32(off)
        off += 4  # skip
        if version == 0:
            off += 4
        else:
            off += 2 * (raw_col_count + 1)

        # column names (raw_col_count of them, all skipped)
        self.col_names: list[str] = []
        for _ in range(raw_col_count):
            n, off = self._read_i16(off)
            b, off = self._read_bytes(off, n)
            self.col_names.append(self._decode(b))

        # column-title row (header row name; we skip it)
        n, off = self._read_i16(off)
        off += n

        # row names (raw_row_count - 1 of them = the actual data row names)
        self.row_names: list[str] = []
        for _ in range(raw_row_count - 1):
            n, off = self._read_i16(off)
            b, off = self._read_bytes(off, n)
            self.row_names.append(self._decode(b))

        # The C++ decrements both counts AFTER reading the header sections.
        # Data section therefore has (raw_row_count - 1) rows by
        # (raw_col_count - 1) columns.
        self.row_count = raw_row_count - 1
        self.col_count = raw_col_count - 1

        off = data_offset
        self.rows: list[list[str]] = []
        for _ in range(self.row_count):
            row: list[str] = []
            for _ in range(self.col_count):
                n, off = self._read_i16(off)
                if n > 0:
                    b, off = self._read_bytes(off, n)
                    row.append(self._decode(b))
                else:
                    row.append("")
            self.rows.append(row)

    def __len__(self) -> int:
        return self.row_count

    def get(self, row: int, col: int) -> str:
        if 0 <= row < self.row_count and 0 <= col < self.col_count:
            return self.rows[row][col]
        return ""

    def get_int(self, row: int, col: int) -> int:
        v = self.get(row, col)
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0


class StlFile:
    """Parses an STL string-table file (UTF-16 wide-char strings)."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self._buf = f.read()
        self._parse()

    def _parse(self) -> None:
        off = 0
        self.col_count = struct.unpack_from("<i", self._buf, off)[0]; off += 4
        self.row_count = struct.unpack_from("<i", self._buf, off)[0]; off += 4
        header_size = 8 + self.col_count * self.row_count * (4 + 2)
        # Each cell: (file_pos_absolute, byte_length)
        # file_pos is absolute into the whole file; we keep both forms.
        self.cells: list[list[tuple[int, int]]] = []
        for _ in range(self.row_count):
            row: list[tuple[int, int]] = []
            for _ in range(self.col_count):
                fp = struct.unpack_from("<i", self._buf, off)[0]; off += 4
                ln = struct.unpack_from("<h", self._buf, off)[0]; off += 2
                row.append((fp, ln))
            self.cells.append(row)
        # The remaining buffer (from header_size onward) holds wide-char data;
        # but cell file_pos values are absolute into self._buf. We just slice
        # directly when fetching.
        self._header_size = header_size

    def get(self, row: int, col: int = 1) -> str:
        """Default col=1 because col 0 is usually the row name; the actual
           translated string is at col 1 (LANGUAGE_USA after enum +1).
           Many tables put English at col 1, others at col 2 or 3. Callers
           specify the column when needed."""
        if row < 0 or row >= self.row_count:
            return ""
        if col < 0 or col >= self.col_count:
            return ""
        fp, ln = self.cells[row][col]
        if fp <= 0 or ln <= 0:
            return ""
        data = self._buf[fp:fp + ln]
        # bytes are UTF-16LE wide chars
        try:
            return data.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return data.decode("utf-16-le", errors="replace").rstrip("\x00")

    def get_by_row_name(self, key: str, col: int = 1) -> str:
        """Some STLs use the row's column-0 cell as a key string.
           Returns the cell at <col> when col-0 matches <key>."""
        for r in range(self.row_count):
            k = self.get(r, 0)
            if k == key:
                return self.get(r, col)
        return ""
