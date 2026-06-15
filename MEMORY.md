# mapforge — ROSE / SHO format & pipeline notes

Living reference for everything we've reverse-engineered while building the web
map editor + Unity/UE exporters. Update it whenever we learn something new.

Stack: Flask backend (`app.py`, port 5051) + modular three.js frontend
(`static/js/*`). Format readers live in `../` (parent `scripts/`): `rose_zsc.py`,
`rose_zms.py`, `rose_map.py`, and in this dir: `rose_ifo.py`, `rose_zmd.py`,
`rose_zmo.py`, `rose_chr.py`, `rose_effect.py`, `rose_mov.py`.

Assets are read from `config.ASSET_ROOT` (extracted client, e.g.
`D:\sky_rose_extracted`). Paths in the data use `3DDATA\...` with `\` and
arbitrary case — always resolve case-insensitively and strip a leading `3ddata`.

---

## Coordinate system & scale (the thing that bites every time)

- ROSE world is **Z-up, centimetres**. three.js viewer keeps ROSE coords as-is
  (Z-up world). glTF/Unity are Y-up/metres, so the GLB root node `ROSE_zone`
  carries a single matrix `R = rotateX(-90°) · scale(0.01)`. Everything else is
  authored in **raw ROSE coords** as children of that root, so it inherits the
  conversion. Parent new content under that group and use ROSE coords directly.
- ⚠️ **In the imported FBX (Unity) the bake is different and lives in each node's
  LOCAL transform, not on a single root.** The glTF→Blender-FBX→Unity round-trip
  applies an X-flip + Y/Z-swap, so a ROSE point maps to Unity world as
  **`(x,y,z) → (-x, z, y) × 0.01`** (verified: fountain ROSE `(551474,523960,0)`
  → Unity `(-5514.741, 0, 5239.602)`). The map root / `Objects` group is
  identity; every mesh node holds the baked local position + its own ROSE scale.
  So to drop NEW content (effects, markers) at a ROSE coord in Unity, parent a
  container under `Objects` with `localScale = 0.01` and
  `localRotation = AngleAxis(180, (0,1,1).normalized)` (= the `(-x,z,y)` basis
  flip) and give children RAW ROSE coords. (RoseEffects.cs does exactly this.)
  Do NOT assume a single 0.01 root scale — the cube/effects were misplaced to
  ~551k units until this was fixed.
- **ZMS mesh scale**: version 7 meshes are ×100 vs v8. We feed v7 positions
  through `ZZ_SCALE_IN = 0.01` → multiply by 100 (`pos_scale = 100 if version>=7`).
- **Skeleton vs mesh scale (skinned NPCs/monsters)**: the ZMD skeleton is at
  *file* scale (~100× the raw mesh). Correct combo: **mesh ×100, bones ×1, no
  group scale**. Bind AFTER placing the transform so the bind matrix captures it.
- **PTL/effect distances** (emit radius, velocity, gravity, spawn dir) are
  `×ZZ_SCALE_IN` then world `×100` in the engine → the raw file value already
  equals viewer/world units. Keep as-is.

---

## ZSC — model+material pack (`rose_zsc.py`)

`LIST_DECO_*.ZSC` (OBJECT pack) / `LIST_CNST_*.ZSC` (CNST pack), per-zone from the
zone STB. Layout: `int16 num_meshes`(paths) → `num_materials`(path+12 flags) →
**`num_effects`(.EFT paths)** → `num_models`(CMODEL blocks).

Each model = parts + **dummy points** + bbox. Part TAG stream: 1=POS(vec3),
2=ROT(quat **w,x,y,z**), 3=SCALE, 5=BONEIDX, 6=DUMMYIDX, 7=PARENT(−1), 0=end.

### ⭐ Object-attached effects live in ZSC dummy points
*(the friend's tip: "the ZSC calls upon an EFT file linked to the object")*

Each model's **dummies** each have `effect_idx` (into the model's `effects`
list = a `.EFT`), `effect_type`, and a **local transform (pos + rotate + scale)**
= the attach point + orientation. This is how nearly all ambient map FX are
placed. To world-space a dummy effect:

```
ifoM  = compose(o.pos, o.rot, o.scale)            # IFO object, rot is x,y,z,w
dM    = compose(d.position, (w,x,y,z→x,y,z,w), d.scale)
world = ifoM @ dM        # position = world[:3,3]; rotation = mat→quat(world)
```

`dummy.effect_idx == -1` → a non-effect attach point (skip).

**JPT01-1 (Junon) examples** (`LIST_DECO_JPT.ZSC`, 259 models, 84 effects):
| model | what | effect |
|---|---|---|
| 177 | **fountain** — 6 dummies at z≈1502 (petal holes) | `bunsudae01.eft` (Korean *bunsudae* = fountain) |
| 172/173 | streetlights | `streetlight01l.eft` |
| 237 | port vessel (8 dummies) | `portvesseldieyl.eft` |
| 240/241 | agit braziers (40/24 dummies) | `_agit_fire01.eft` |
| 243 | misc | `HALOSMOKE.eft`, `_FIRE_05.eft` |

`bunsudae01.eft`: 1 emitter, `dust_01.dds`, 40 particles, spawn z+400,
vel x≈150 (radial out, oriented by each dummy's rotation) z 50–80 (up),
gravity z −150 → a fountain spout from each hole. The **standing basin water has
NO effect** (those dummies are `effect_idx=-1`); we add a synthetic translucent
disc for it.

---

## EFT / PTL — effects & particles (`rose_effect.py`)

`.EFT` references one or more `.PTL`. PTL = `u32 seq_count` + event-sequences.
Each sequence (= one particle layer): name, life[min,max], emit_rate, loops,
spawn_dir[6], emit_radius[6] (min xyz / max xyz **box**), gravity[6], texture,
num_particles, blend (src,dst,op), then timed events.

Event types: 1 SIZE(4f: min xy,max xy), 2 TIMER, 3 RED 4 GREEN 5 BLUE 6 ALPHA(2f),
7 COLOR(8f: min rgba,max rgba), 8–10 VELX/Y/Z, 11 VEL(6f), 12 TEXTURE, 13 ROTATION.

- **life is in frames** → seconds ≈ life/30 in the viewers.
- **blend [4,5,...] = SRCALPHA/INVSRCALPHA = normal alpha**; anything else we
  treat as additive.
- Many ambient FX are `num_particles=1`, zero velocity, `size=[0,0,0,0]` → a
  single pulsing glow billboard (streetlight/fire); fall back to a default sprite
  size when size is 0.

### Effects pipeline (data-driven)
`export_effects.py`:
- `compute(key)` → `{placements:[{pos, rot(xyzw), emitters:[flattened PTL]}], fountains:[{pos,scale}]}`
  from **both** ZSC dummies (object-attached) **and** the IFO `EFFECT` lump
  (standalone). `texture` = raw asset path.
- `build(key, bundle)` → ships textures as PNG into `Effects/Textures/`, rewrites
  `texture` to the PNG name, writes `Effects/effects.json` (+ generated
  `fountain_soft.png` for the basin pool).
- Web: `/api/zone/<key>/effects` → `compute()`; `static/js/effects.js` spawns a
  THREE.Points system per emitter at `pos`, oriented by `quat(rot)` (so the 6
  fountain jets aim radially), + a `CircleGeometry` basin pool per fountain.
- Unity: `fbx_templates/RoseEffects.cs` (`ROSE > Build Effects`) builds a
  `ParticleSystem` per emitter under the "Objects" group (Local sim + **Hierarchy
  scaling** so sizes/speeds inherit the 0.01 map scale), `localRotation = rot`;
  fountains get a translucent Quad pool.

---

## IFO — per-tile placements (`rose_ifo.py`)

Lumps: OBJECT (deco), CNST (construction), NPC, MOB/REGEN (monster spawns),
EFFECT (standalone effect placements; `extra.effect_file` = the `.EFT`), SOUND,
collision boxes, OCEAN (water blocks). Object record: `object_id` (→ ZSC model),
`pos`, `rot` (**quat x,y,z,w**), `scale`. Fountain IFO scale in JPT01-1 = 0.4
(apply to any synthetic geometry placed at a fountain).

---

## Skeletal animation (NPCs / monsters)

- **ZMD** (`rose_zmd.py`): magic `"ZMD0002"` (7 bytes, no null; count at off 7).
  Bone = `parent(i32)`, name(cstr), pos(3f ×ZZ_SCALE_IN), rot(4f **w,x,y,z**).
  Root = bone where `parent_id == index`.
- **ZMS skin**: bone_indices palette + per-vertex bones×4 + weights×4.
- **ZMO** motion: magic `ZMO0002`, per-bone ROTATION/POSITION channels by
  `refer_id`. **POSITION channels are already external scale → use ×1** (the
  MORPH 100× oversize bug was double-scaling these).
- Engine skinning: `boneTM = worldTM × offsetTM`, `offsetTM = world_inverseTM`
  (inverse bind). three.js: `SkinnedMesh` + `Skeleton` + `AnimationMixer`; build
  bones → transform group → `new THREE.Skeleton(bones)` → bind.
- `/api/zone/<key>/rig` returns bones + idle anim (quaternions converted
  w,x,y,z → x,y,z,w). Track names `b<i>.quaternion` / `b<i>.position`.

## CHR — character composition (`rose_chr.py`)
`LIST_NPC.CHR`: each character's `objects` = `PART_NPC.ZSC` model indices to
assemble the full body. Render character parts **opaque + two-sided** — their DDS
alpha is near-zero so an alpha cutout discards whole limbs (the "heads without
bodies" bug).

---

## Unity export bundle (`export_fbx.py` + `fbx_templates/`)

Pipeline: `export_map.export()` → `.glb` → Blender headless → `.fbx` (named
material slots) → dedup textures → `materials.json` → copy editor scripts/shaders
/README → bake animated MORPH objects → `Animations/` → zip.

- **URP required** — shaders `ROSE/URP/Lit`, `ROSE/Water`, `ROSE/Skybox`. Create
  the Unity project from the editor's **URP template**
  (`3d-cross-platform-17.x.tgz`, = "Universal 3D"), not `-createProject` (built-in).
- `AssignRoseMaterials.cs`: `ROSE > Assign Materials` / `Apply Sky` (public static).
- `RoseAnimatedObjects.cs`: `ROSE > Setup Scene` = materials → add map →
  overlay animated MORPH prefabs (looping Animator) → MeshColliders → sky.
  Animated MORPH meshes are named `MORPH__<stem>__<n>` (static, no blendshapes);
  the script parents the *animated* FBX under each and hides the static mesh.
- `RoseEffects.cs`: `ROSE > Build Effects` (also call from Setup Scene / batch).
- **Headless import**: extract URP template → copy bundle into `Assets/` → run
  `Unity.exe -batchmode -quit -projectPath … -executeMethod RoseAnimatedObjects.BatchSetupAndSave`.

## UE5
- **Coords**: UE5 is Z-up / centimetres, same as ROSE — far simpler than Unity.
  The map FBX imports near ROSE world scale. `import_rose_map_ue.py` self-calibrates
  ROSE→UE with a **per-axis linear remap** of the ROSE map bounds (`export_npcs.py`
  → `npcs.json` `rose_bounds`, computed from the tile grid: X∈[minTx·16000,(maxTx+1)·16000],
  Y∈[(2cy−maxTy)·16000,(2cy−minTy+1)·16000]) onto the imported mesh bounds, then a
  downward complex trace for Z. `SWAP_XY` flag if markers land rotated 90°.
- **Collision**: `CTF_USE_COMPLEX_AS_SIMPLE` (Use Complex As Simple) collides on the
  real triangles and stops floating. `CTF_USE_SIMPLE_AND_COMPLEX` keeps the simple
  hull too — the character's capsule sweep then rides the simple box → floats. The
  importer defaults to Simple+Complex (user asked) with a `COLLISION` switch and a
  PlayerStart standing diagnostic (logs spawn Z, ground Z under it, the gap, map Z
  range) to see floating. line-trace the PlayerStart down to the floor.
- **NPCs/spawns — animated**: `export_npcs.py` → `NPCs/npcs.json` (placements +
  rose_bounds). `export_npc_models.py` builds one skinned, idle-animated **glTF per
  unique character** (skeleton from .ZMD bone TRS; skin from .ZMS positions×100 +
  JOINTS/WEIGHTS via the palette→global-bone map; idle clip from the standing .ZMO),
  with a root node `rotateX(-90)·scale(0.01)` so it imports upright + at map scale —
  then `blender_glb_to_fbx_skinned.py` (ARMATURE + `bake_anim=True` + embedded
  textures, **not** the static map converter) → `NPCs/Models/<id>.fbx`. Verified one
  FBX = 42 bones + Skin/Cluster deformers + AnimStack + textures.
  `import_rose_map_ue.py` imports each as a Skeletal Mesh and spawns animated
  `SkeletalMeshActor`s (`comp.play_animation(anim, looping=True)`): NPCs at MOB
  points, monsters in a ring at each REGEN point; marker-cylinder fallback if an id
  has no model. Same bone/mesh scale split as the web (bones×1, mesh×100).

---

## SHO server scripts
Lua **4.0.1** — no `pairs`/`ipairs`/generic-for (they silently no-op); iterate
with `next()` in a `while` loop.

## STB tables
Appending rows to `LIST_*.STB` requires **lockstep matching ZSC + STL entries**;
a mismatch crashes the client.

---

## Toolchain / env
- Python: `C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe`
- Blender 5.0: `C:/Program Files/Blender Foundation/Blender 5.0/blender.exe`
- Unity 6000.1.11f1: `C:/Program Files/Unity/Hub/Editor/6000.1.11f1/Editor/Unity.exe`
- Repo: github.com/DEVXIX/mapforge (author `devxix@users.noreply.github.com`;
  **no "Co-Authored-By: Claude"** in commits).
