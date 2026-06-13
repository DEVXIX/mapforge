# mapforge

A web-based **map editor, viewer and exporter** for ROSE Online / SHO-style
servers. Load a whole zone in the browser — terrain, buildings, props, water,
spawns, NPCs, warps and collision — fly around it, edit it, and export the
entire map to **glTF / FBX** for Unity or Unreal Engine.

It reads the game's own binary formats directly (`ZON`, `HIM`, `TIL`, `IFO`,
`ZSC`, `ZMS`, `MOV`, `STB`, `DDS`) and writes edits back byte-exact, so the
files stay loadable by the real client and server.

> **You bring your own client data.** mapforge ships no game assets. It reads
> the `3DDATA` folder you extract from your own client (see Prerequisites).

---

## Features

- **Full-zone viewer** — heightmap terrain with the real two-layer tile blend
  (grass over ground), instanced building/prop meshes with textures, water, and
  a UE5-style fly camera (hold right-mouse + WASD).
- **Editor** — select, move/rotate/scale, delete, duplicate; edit object IDs;
  edit monster **spawns** (mob lists, interval, range), warps and events; paint
  the `.MOV` **collision** grid.
- **Object browser** — every model from every map, with a live textured
  preview; drag-and-drop to place models from any map into any map (it appends
  the model to the target zone's pack automatically).
- **Byte-exact saves** — only edited lumps are rewritten; everything else is
  preserved verbatim. Writes to multiple data roots so client + server stay in
  sync. A `.mapforge.bak` is made on first edit of each file.
- **Exporters**
  - `export_map.py` → a single **`.glb`** (terrain + objects + collision, with
    lightmap UVs + normals), ready for Unity/Unreal.
  - `export_fbx.py` → a distributable **`.fbx` bundle** (zipped): FBX +
    deduplicated textures + a manifest + auto-assign editor scripts for **Unity
    and Unreal**.

---

## Prerequisites

1. **Python 3.11+**
2. **Extracted client `3DDATA`.** mapforge needs the loose `3DDATA` tree
   (maps, meshes, textures, STB tables). Most clients ship these packed inside
   `GAME0.VFS … GAMEx.VFS` — unpack them with any ROSE VFS extractor so you end
   up with a folder like:

   ```
   <somewhere>\3DDATA\
       Maps\...        (per-zone HIM/TIL/IFO/MOV/ZON tiles)
       STB\LIST_ZONE.STB, LIST_MORPH_OBJECT.STB, ...
       JUNON\..., ELDEON\..., ...   (ZSC packs, ZMS meshes, DDS textures)
   ```
3. **(FBX export only) Blender 3.6+** — used headless to convert glTF → FBX.

---

## Install

```bash
git clone https://github.com/DEVXIX/mapforge.git
cd mapforge
python -m pip install -r requirements.txt
```

## Configure

```bash
cp config.example.py config.py        # Windows: copy config.example.py config.py
```

Edit `config.py` and set the paths:

```python
ASSET_ROOT = r"C:\path\to\extracted\3DDATA"   # where mapforge READS assets

WRITE_ROOTS = [                                # where saves are written
    ("client", ASSET_ROOT),
    # ("server", r"C:\path\to\server\srvDATA\3DDATA"),
]
```

For read-only viewing, just point `ASSET_ROOT` at your extracted `3DDATA` and
leave `WRITE_ROOTS` as the single client entry.

---

## Run the editor

```bash
python app.py
```

Open **http://127.0.0.1:5051**, pick a zone, click **Load**.

### Controls
- Left-drag orbit · middle-drag pan · wheel zoom
- **Hold right-click + WASD** to fly (Q/E down/up, Shift boost, wheel = speed)
- Click to select · **W/E/R** move/rotate/scale · **Del** delete · **Ctrl+D**
  duplicate · **Esc** deselect
- Drag a model from the **Object browser** onto the terrain to place it
- **Save** writes your edits back to the configured data roots

---

## Export a whole map

### glTF (`.glb`) — for Unity / Unreal / Blender
```bash
python export_map.py JPT01-1
# -> exports/JPT01-1.glb
```
Bakes terrain (two-layer grass blend), all objects with textures, and collision
(IFO collision boxes + the `.MOV` walk grid) into one file, with a second UV set
for lightmaps and a root transform that converts ROSE's Z-up/cm to glTF
Y-up/metres. NPCs/spawns are skipped.

### FBX bundle — materials assigned by editor script
```bash
python export_fbx.py JPT01-1
# -> exports/JPT01-1_fbx.zip
```
Produces a zipped folder containing:
- `JPT01-1.fbx` (named material slots, no baked materials)
- `Textures/` (deduplicated PNGs)
- `materials.json` (manifest: slot → texture + alpha mode + two-sided)
- `UnityEditor/AssignRoseMaterials.cs` and `UE5/assign_rose_materials_ue.py`
- `README.txt`

FBX conversion uses Blender. If it isn't at the default path, set the env var:
```bash
# Windows example
set BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 4.2\blender.exe
python export_fbx.py JPT01-1
```

### Use it in an engine
- **Unity:** drop the extracted bundle folder into `Assets/`, put
  `AssignRoseMaterials.cs` anywhere under `Assets/`, then menu **ROSE → Assign
  Materials** (auto-detects URP vs Built-in, wires textures, sets cutout /
  transparent / two-sided, remaps the FBX slots).
- **Unreal 5:** import the `.fbx`, then **Tools → Execute Python Script →
  `UE5/assign_rose_materials_ue.py`** (imports textures, builds a material per
  slot, assigns them by name). For collision, set the imported meshes to
  *Use Complex Collision As Simple*.

---

## How it works (formats)

| File | Role |
|------|------|
| `ZON` | zone definition — tile palette, size, start positions |
| `HIM` | per-tile heightmap (65×65) |
| `TIL` | per-patch tile-material indices |
| `IFO` | object/spawn/warp/event/collision placement (lumps) — **the shared client+server source of truth** |
| `MOV` | server walkability grid (collision) |
| `ZSC` | model packs: `object_id` → model → parts → mesh + material |
| `ZMS` | meshes (v7/v8) |
| `STB` | data tables (zone list, morph objects, NPCs) |
| `DDS` | textures (decoded to PNG via Pillow) |

The IFO codec decodes every lump and re-encodes byte-exact (verified by a
round-trip self-test), so edits never corrupt files the engine still has to
read. The ZSC editor preserves existing models verbatim and only appends new
ones, so cross-map placement is safe.

---

## Notes & caveats

- Textures use the base diffuse only — no lightmaps/specular — so the viewer
  looks "engine-lit", not like the in-game baked lighting.
- Particle effects (`.EFT`) and animations (`.ZMO`) are not rendered.
- FBX axis/scale conventions vary between tools; if a map imports rotated or
  oversized, fix the import scale/rotation on the asset.

## License

MIT — see [LICENSE](LICENSE). This project contains **no game assets**; it only
reads data you supply. ROSE Online and related names are trademarks of their
respective owners and are referenced for interoperability only.
