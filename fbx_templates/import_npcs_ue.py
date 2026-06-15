"""Import the POSED NPC/monster glb (npcs_posed.glb) onto the map — 1:1 with the
web viewer, ZERO transform tweaking.

It's built by export_npc_posed.py through the SAME pipeline as the map (same
ROSE_zone root, same scale), so UE5's glTF importer places it exactly like the
map: right position, size, orientation. The NPCs are baked static meshes posed in
a natural idle stance (this sidesteps UE5's skeletal-glTF import, which silently
rescaled/rotated the skinned characters and caused all the manual-fix pain).

RUN:  Tools > Execute Python Script...  ->  this file.   (Run import_rose_map_ue.py
once for the map first.)
"""

import os
import unreal

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVEL = "/Game/ThirdPerson/Lvl_ThirdPerson"
NPC_PKG = "/Game/ROSE_GLB/NPCs"
REIMPORT = True          # re-import the glb each run; set False once it's imported for faster re-placement


def log(m): unreal.log("[ROSE-NPC] " + str(m))


at = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

try:
    les.load_level(LEVEL)
except Exception as e:
    log("could not load level %s (%s)" % (LEVEL, e))

# wipe any previous ROSE NPC actors (skeletal or posed)
for a in list(eas.get_all_level_actors()):
    try:
        if a.get_actor_label().startswith("ROSE_NPC_"):
            eas.destroy_actor(a)
    except Exception:
        pass

glb = os.path.join(BUNDLE, "npcs_posed.glb")
if not os.path.exists(glb):
    log("npcs_posed.glb not found in %s — rebuild the bundle (export_npc_posed.py)" % BUNDLE)
    raise SystemExit

# import the posed glb as static meshes (glTF translator bakes the world placement)
if REIMPORT or not eal.does_directory_exist(NPC_PKG):
    if eal.does_directory_exist(NPC_PKG):
        try:
            eal.delete_directory(NPC_PKG)
        except Exception:
            pass
    log("importing %s ..." % glb)
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", glb)
    task.set_editor_property("destination_path", NPC_PKG)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    at.import_asset_tasks([task])
    log("import done")

# place every imported static mesh at origin (the node world transforms are baked
# in, exactly like the map) -> NPCs land at their real spots, 1:1 with the map.
placed = 0
for path in eal.list_assets(NPC_PKG, recursive=True):
    o = eal.load_asset(path)
    if isinstance(o, unreal.StaticMesh):
        a = eas.spawn_actor_from_object(o, unreal.Vector(0.0, 0.0, 0.0))
        if a:
            a.set_actor_label("ROSE_NPC_%d" % placed)
            placed += 1
log("placed %d posed NPC/monster meshes (1:1 with the map)" % placed)

try:
    les.save_current_level()
except Exception:
    pass
log("DONE — NPCs are placed 1:1 with the web viewer (static posed). Press Play.")
