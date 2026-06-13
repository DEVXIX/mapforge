"""Headless Blender: build a vertex-animated mesh from baked per-frame positions
and export it to FBX with blend shapes + an AnimationClip.

    blender --background --python blender_vertex_anim.py -- <data.npz> <out.fbx>

data.npz: verts (F,V,3) per-frame positions, faces (T,3), uvs (V,2), fps.
Each frame becomes a shape key; the action cross-fades consecutive keys so the
mesh plays the vertex animation. Unity imports this as blend shapes + a clip an
Animator can drive (and whose speed you can scale).
"""
import bpy
import sys
import numpy as np
import addon_utils

try:
    addon_utils.enable("io_scene_fbx")
except Exception:
    pass

argv = sys.argv[sys.argv.index("--") + 1:]
npz_path, fbx_path = argv[0], argv[1]
d = np.load(npz_path)
verts = d["verts"].astype(float)      # (F, V, 3)
faces = d["faces"].astype(int)        # (T, 3)
uvs = d["uvs"].astype(float) if "uvs" in d.files else None
fps = int(d["fps"]) if "fps" in d.files else 30
F, V, _ = verts.shape

bpy.ops.wm.read_factory_settings(use_empty=True)

mesh = bpy.data.meshes.new("rose_anim")
obj = bpy.data.objects.new("rose_anim", mesh)
bpy.context.collection.objects.link(obj)
mesh.from_pydata([tuple(v) for v in verts[0]], [], [tuple(f) for f in faces])
mesh.update()

if uvs is not None and len(uvs) == V:
    uvl = mesh.uv_layers.new(name="UV0")
    for loop in mesh.loops:
        vi = loop.vertex_index
        # ZMS UVs are DirectX (V from top). The map's static meshes go through
        # glTF (Blender's importer flips V to its bottom-origin convention), so to
        # share the map's material the animated mesh must flip V the same way —
        # otherwise the texture maps mirrored/scrambled vs the static banner.
        uvl.data[loop.index].uv = (float(uvs[vi][0]), 1.0 - float(uvs[vi][1]))

# basis + per-frame shape keys
obj.shape_key_add(name="Basis")
for f in range(F):
    sk = obj.shape_key_add(name="f%03d" % f)
    co = verts[f]
    for vi in range(V):
        sk.data[vi].co = (float(co[vi][0]), float(co[vi][1]), float(co[vi][2]))

# animate: each shape key 1.0 at its frame, 0.0 at neighbours (smooth cross-fade)
scene = bpy.context.scene
scene.render.fps = max(1, fps)
scene.frame_start = 0
scene.frame_end = max(1, F - 1)
kb = mesh.shape_keys.key_blocks
for f in range(F):
    sk = kb["f%03d" % f]
    for tf in (f - 1, f, f + 1):
        if 0 <= tf <= F - 1:
            sk.value = 1.0 if tf == f else 0.0
            sk.keyframe_insert("value", frame=tf)

# name the action (Unity sets looping on the imported clip)
ad = mesh.shape_keys.animation_data
if ad and ad.action:
    ad.action.name = "RoseAnim"

bpy.ops.export_scene.fbx(
    filepath=fbx_path,
    use_selection=False,
    apply_unit_scale=True,
    object_types={'MESH'},
    use_mesh_modifiers=False,
    add_leaf_bones=False,
    bake_anim=True,
    bake_anim_use_all_actions=False,
    bake_anim_use_nla_strips=False,
    path_mode='STRIP',
    embed_textures=False,
    axis_forward='-Z',
    axis_up='Y',
)
print("[blender] vertex-anim FBX written: %s (%d frames, %d verts)" % (fbx_path, F, V))
