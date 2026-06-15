"""Headless Blender: import a skinned+animated .glb (one ROSE character) and export
a skeletal .fbx with its armature + idle action baked, textures embedded.

    blender --background --python blender_glb_to_fbx_skinned.py -- <in.glb> <out.fbx>

Unlike blender_glb_to_fbx.py (static map), this keeps ARMATURE objects and bakes
the animation, so UE5/Unity import a Skeletal Mesh + AnimSequence.
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

# Make the imported idle action loop-friendly + ensure it's the active action.
for ob in bpy.data.objects:
    if ob.type == 'ARMATURE' and ob.animation_data and ob.animation_data.action:
        ob.animation_data.action.use_frame_range = True

frame_end = 1
for act in bpy.data.actions:
    frame_end = max(frame_end, int(act.frame_range[1]))
bpy.context.scene.frame_start = 0
bpy.context.scene.frame_end = frame_end

bpy.ops.export_scene.fbx(
    filepath=fbx_path,
    use_selection=False,
    apply_scale_options='FBX_SCALE_NONE',
    apply_unit_scale=True,
    global_scale=1.0,
    object_types={'ARMATURE', 'MESH', 'EMPTY'},
    use_mesh_modifiers=True,
    add_leaf_bones=False,
    primary_bone_axis='Y',
    secondary_bone_axis='X',
    mesh_smooth_type='FACE',
    use_tspace=False,
    path_mode='COPY',
    embed_textures=True,
    bake_anim=True,
    bake_anim_use_all_bones=True,
    bake_anim_use_nla_strips=False,
    bake_anim_use_all_actions=False,
    bake_anim_force_startend_keying=True,
    axis_forward='-Z',
    axis_up='Y',
)
print("[blender] skeletal FBX written: %s" % fbx_path)
