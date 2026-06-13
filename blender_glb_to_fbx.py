"""Headless Blender: import a .glb and export a .fbx with named material slots.

Invoked by export_fbx.py:
    blender --background --python blender_glb_to_fbx.py -- <in.glb> <out.fbx>

Material slots keep their glTF names (M<idx>_<tex>) so the Unity / UE editor
scripts can auto-assign textures by name. Textures are NOT embedded (path_mode
STRIP) — they ship as separate files the editor scripts wire up.
"""
import bpy
import sys
import addon_utils

# Ensure the FBX exporter addon is on (it ships with Blender, usually enabled).
try:
    addon_utils.enable("io_scene_fbx")
except Exception:
    pass

argv = sys.argv[sys.argv.index("--") + 1:]
glb_path, fbx_path = argv[0], argv[1]

# Empty scene.
bpy.ops.wm.read_factory_settings(use_empty=True)

# Import glTF (brings in meshes, named materials, UV0 + UV1, normals).
bpy.ops.import_scene.gltf(filepath=glb_path)

# Export FBX targeting Unity/Maya axis conventions (Y-up, -Z forward).
bpy.ops.export_scene.fbx(
    filepath=fbx_path,
    use_selection=False,
    apply_scale_options='FBX_SCALE_NONE',
    apply_unit_scale=True,
    global_scale=1.0,
    object_types={'MESH', 'EMPTY'},
    use_mesh_modifiers=True,
    mesh_smooth_type='FACE',
    use_tspace=False,
    path_mode='STRIP',
    embed_textures=False,
    bake_anim=False,
    axis_forward='-Z',
    axis_up='Y',
)

print("[blender] FBX written: %s" % fbx_path)
