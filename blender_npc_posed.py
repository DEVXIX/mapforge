"""Headless Blender: import the named, posed NPC .glb and export a .fbx with the
node names preserved (NPCPOSE__<charid>__<n>) and textures EMBEDDED so the Unity
NPC importer can drop them straight into the scene as 1:1 static placements and
overlay the animated blend-shape clips on top.

    blender --background --python blender_npc_posed.py -- <in.glb> <out.fbx>
"""
import bpy
import sys
import addon_utils

try:
    addon_utils.enable("io_scene_fbx")
except Exception:
    pass

argv = sys.argv[sys.argv.index("--") + 1:]
glb_path, fbx_path = argv[0], argv[1]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb_path)

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
    path_mode='COPY',
    embed_textures=True,
    bake_anim=False,
    axis_forward='-Z',
    axis_up='Y',
)
print("[blender] posed NPC FBX written: %s" % fbx_path)
