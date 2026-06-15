"""Re-import the ROSE map + NPC/monster placements into UE5, set Simple+Complex
collision, and LOG exactly where the character will stand (to diagnose floating).

WHAT IT DOES
  1. Imports <zone>.fbx as Static Meshes into /Game/ROSE_FBX/Map (re-import safe).
  2. Sets collision to "Simple and Complex" on every map static mesh.
  3. Reads NPCs/npcs.json and drops a marker per NPC (tall cyan cylinder) and per
     monster spawn (red cube), placed by a per-axis ROSE->UE remap (UE5 is Z-up/cm
     like ROSE) + a downward trace so they sit on the ground.
  4. Puts the PlayerStart on the real ground at the map centre and logs its
     position, the ground Z under it, and the gap — so we can see any floating.

RUN:  Tools > Execute Python Script...  ->  this file.   Then press Play.

CONFIG: edit BUNDLE / LEVEL below. If markers land rotated 90° or mirrored, flip
SWAP_XY. If the character floats, switch COLLISION to "COMPLEX_AS_SIMPLE".
"""

import os
import json
import math
import unreal

# ----------------------------------------------------------------- config ----
# Bundle root = the folder this script's UE5/ subfolder lives in (has <zone>.fbx
# + NPCs/npcs.json). Auto-detected; override if you copied the script elsewhere.
BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZONE = os.environ.get("ROSE_ZONE", "")          # blank -> first <name>.fbx found
LEVEL = "/Game/ThirdPerson/Lvl_ThirdPerson"      # your playable level
PKG = "/Game/ROSE_FBX"                            # NPC skeletal meshes go here
MAP_PKG = "/Game/ROSE_GLB/Map"                    # map glb imports here (fresh, not the old grey FBX)
REIMPORT_MAP = False                             # auto-imports if missing; set True to FORCE a fresh map import + material rebuild
SWAP_XY = False                                  # flip if NPCs land rotated 90°
COLLISION = "COMPLEX_AS_SIMPLE"                  # collide on real triangles. SIMPLE_AND_COMPLEX = no
                                                 # simple hull on these meshes -> traces miss -> void.
NPC_YAW_OFFSET = 0.0                             # add to NPC facing if they look the wrong way
NPC_Z_OFFSET = -578.0                               # raise/lower NPCs if their feet sink/float
WIPE_OLD = True                                  # delete previous ROSE imports (old FBX map etc.) first
# UE5 ignores the glb root transform on skinned meshes, so apply it on the ACTOR:
NPC_SCALE = 100.0               # UE imports the animated char ~100x small; 100 restores it
NPC_ROLL = 90.0                  # UE needs a 90 roll to stand the character up
NPC_PITCH = 0.0                                # stand them up (try 90 if they lie the other way; 0 if upright)


def log(m): unreal.log("[ROSE] " + str(m))


at = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

try:
    les.load_level(LEVEL)
except Exception as e:
    log("could not load level %s (%s) — using the open level" % (LEVEL, e))


def get_world():
    """Editor world — fetched AFTER the level loads (capturing it earlier returns a
    null/stale world that crashes line traces). Falls back to a level actor's world."""
    w = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if w:
        return w
    for a in eas.get_all_level_actors():
        try:
            ww = a.get_world()
            if ww:
                return ww
        except Exception:
            pass
    return None


def find_map_glb():
    if ZONE:
        p = os.path.join(BUNDLE, ZONE + ".glb")
        return p if os.path.exists(p) else None
    for f in os.listdir(BUNDLE):
        if f.lower().endswith(".glb"):
            return os.path.join(BUNDLE, f)
    return None


# ---- 0. wipe previous ROSE imports (old FBX map / glb) for a clean run ----
if WIPE_OLD:
    for a in list(eas.get_all_level_actors()):
        try:
            if a.get_actor_label().startswith(("ROSE_MAP_", "ROSE_NPC_")):
                eas.destroy_actor(a)
        except Exception:
            pass
    for folder in ("/Game/ROSE_FBX", "/Game/ROSE_GLB"):
        if eal.does_directory_exist(folder):
            try:
                eal.delete_directory(folder)
                log("wiped %s" % folder)
            except Exception as e:
                log("could not wipe %s (%s)" % (folder, e))


# ---- 1. import the map GLB via UE5's glTF importer -------------------------
# The .glb embeds the SAME textures + per-tile terrain materials the web viewer
# renders (no FBX material-stripping / re-assign that flattened the terrain to
# grey). Skip if already imported.
if REIMPORT_MAP or not eal.does_directory_exist(MAP_PKG):
    glb = find_map_glb()
    if glb:
        log("importing map GLB %s ..." % glb)
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", glb)
        task.set_editor_property("destination_path", MAP_PKG)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", True)
        at.import_asset_tasks([task])          # glTF translator: textures+materials+meshes are embedded
        try:
            log("map GLB import done -> %s" % list(task.get_editor_property("imported_object_paths"))[:3])
        except Exception:
            log("map GLB import done")
    else:
        log("no <zone>.glb found in %s — skipping map import" % BUNDLE)


# ---- 2. Simple+Complex collision on every map static mesh -----------------
flag = (unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
        if COLLISION == "COMPLEX_AS_SIMPLE"
        else unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AND_COMPLEX)
n = 0
for path in eal.list_assets("/Game", recursive=True):
    obj = eal.load_asset(path)
    if not isinstance(obj, unreal.StaticMesh):
        continue
    bs = obj.get_editor_property("body_setup")
    if not bs:
        continue
    try:
        bs.set_editor_property("collision_trace_flag", flag)
        eal.save_asset(path, only_if_is_dirty=False)
        n += 1
    except Exception as e:
        log("  collision skip %s (%s)" % (path, e))
log("set %s collision on %d static meshes" % (COLLISION, n))


# ---- 3. place the imported map in the level + bound from IT ---------------
# Wipe any previous ROSE actors (idempotent), then drop every imported map mesh
# as its own actor (the FBX bakes world positions, so origin reconstructs the
# map). We bound from THESE actors only — not the World-Partition / template
# actors, which gave the bogus +/-1638400 bounds.
def remove_old(prefixes):
    for a in list(eas.get_all_level_actors()):
        try:
            lbl = a.get_actor_label()
            if any(lbl.startswith(p) for p in prefixes):
                eas.destroy_actor(a)
        except Exception:
            pass


remove_old(("ROSE_NPC_", "ROSE_MAP_"))

# Place the imported map. UE5's glTF importer may create a scene Blueprint (full
# hierarchy with transforms) OR plain static-mesh assets; prefer the Blueprint
# (guaranteed-correct placement), else spawn each static mesh at origin (glTF
# bakes node transforms into the meshes, so origin reconstructs the map). The
# glb already carries the web's textures + per-tile terrain materials.
map_actors = []
bp = None
for path in eal.list_assets(MAP_PKG, recursive=True):
    o = eal.load_asset(path)
    if o is None:
        continue
    cn = o.get_class().get_name()
    if isinstance(o, unreal.Blueprint) or cn in ("Blueprint", "BlueprintGeneratedClass"):
        bp = o
        break
if bp is not None:
    try:
        a = eas.spawn_actor_from_object(bp, unreal.Vector(0, 0, 0))
        if a:
            a.set_actor_label("ROSE_MAP_0")
            map_actors.append(a)
        log("spawned map scene Blueprint")
    except Exception as e:
        log("blueprint spawn failed (%s) — placing static meshes" % e)
if not map_actors:
    for path in eal.list_assets(MAP_PKG, recursive=True):
        o = eal.load_asset(path)
        if isinstance(o, unreal.StaticMesh):
            a = eas.spawn_actor_from_object(o, unreal.Vector(0, 0, 0))
            if a:
                a.set_actor_label("ROSE_MAP_%d" % len(map_actors))
                # glTF meshes import with collision OFF on the component -> traces
                # miss -> player/NPCs end up in the void. Force it solid.
                try:
                    comp = a.static_mesh_component
                    comp.set_collision_profile_name("BlockAll")
                    comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                    comp.set_collision_object_type(unreal.CollisionChannel.ECC_WORLD_STATIC)
                except Exception:
                    pass
                map_actors.append(a)
    log("placed %d map static-mesh actors (collision forced on)" % len(map_actors))
if not map_actors:
    map_actors = [a for a in eas.get_all_level_actors() if isinstance(a, unreal.StaticMeshActor)]

WORLD = get_world()
log("editor world: %s" % ("OK" if WORLD else "NULL"))

mn = [1e18, 1e18, 1e18]
mx = [-1e18, -1e18, -1e18]
for a in map_actors:
    o, e = a.get_actor_bounds(False)
    for i, ax in enumerate(("x", "y", "z")):
        mn[i] = min(mn[i], getattr(o, ax) - getattr(e, ax))
        mx[i] = max(mx[i], getattr(o, ax) + getattr(e, ax))
log("UE map bounds: min=(%.0f, %.0f, %.0f)  max=(%.0f, %.0f, %.0f)" % (mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]))
span = max(mx[0] - mn[0], mx[1] - mn[1])
log("map span = %.0f (%d map actors). A full Junon map is ~128000+; if this is tiny the glb meshes"
    " came in instanced/at-origin and need baking — tell me." % (span, len(map_actors)))


def ground_z(ux, uy):
    if WORLD is None:
        return None
    start = unreal.Vector(ux, uy, mx[2] + 5000.0)
    end = unreal.Vector(ux, uy, mn[2] - 5000.0)
    res = unreal.SystemLibrary.line_trace_single(
        WORLD, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True, [], unreal.DrawDebugTrace.NONE, True)        # True = trace COMPLEX (real triangles)
    hit = res if isinstance(res, unreal.HitResult) else (
        next((x for x in res if isinstance(x, unreal.HitResult)), None) if isinstance(res, tuple) else None)
    did = next((x for x in res if isinstance(x, bool)), None) if isinstance(res, tuple) else None
    if hit is None:
        return None
    if did is None:                                        # struct field access varies by UE version
        try:
            did = bool(hit.get_editor_property("blocking_hit"))
        except Exception:
            did = True
    if not did:
        return None
    for prop in ("impact_point", "location"):
        try:
            return hit.get_editor_property(prop).z
        except Exception:
            continue
    return None


# ---- 4. import NPC/monster skeletal meshes + spawn ANIMATED actors --------
npcs_path = os.path.join(BUNDLE, "NPCs", "npcs.json")
models_dir = os.path.join(BUNDLE, "NPCs", "Models")
_skel_cache = {}   # char id -> (SkeletalMesh, AnimSequence)


def import_npc_glb(glb, dest):
    # .glb imports via UE5's glTF/Interchange translator — textures + skeleton +
    # idle animation are all embedded, so no options/FbxImportUI needed.
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", glb)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    at.import_asset_tasks([task])


def load_skel(char_id):
    if char_id in _skel_cache:
        return _skel_cache[char_id]
    glb = os.path.join(models_dir, "%d.glb" % char_id)
    sk = anim = None
    if os.path.exists(glb):
        dest = PKG + "/NPCs/%d" % char_id
        if not eal.does_directory_exist(dest):
            import_npc_glb(glb, dest)
        for p in eal.list_assets(dest, recursive=True):
            o = eal.load_asset(p)
            if isinstance(o, unreal.SkeletalMesh) and sk is None:
                sk = o
            elif isinstance(o, unreal.AnimSequence) and anim is None:
                anim = o
    _skel_cache[char_id] = (sk, anim)
    return sk, anim


def spawn_char(char_id, ux, uy, gz, yaw, tag):
    sk, anim = load_skel(char_id)
    loc = unreal.Vector(ux, uy, gz + NPC_Z_OFFSET)
    if sk is None:                       # no model for this id -> marker so it's not invisible
        a = eas.spawn_actor_from_object(eal.load_asset("/Engine/BasicShapes/Cylinder"),
                                        unreal.Vector(ux, uy, gz + 90.0))
        if a:
            a.set_actor_scale3d(unreal.Vector(0.5, 0.5, 1.8))
            a.set_actor_label("ROSE_NPC_missing_%d" % char_id)
        return a is not None
    a = eas.spawn_actor_from_class(unreal.SkeletalMeshActor, loc, unreal.Rotator(roll=NPC_ROLL, pitch=NPC_PITCH, yaw=yaw))
    if a is None:
        return False
    a.set_actor_scale3d(unreal.Vector(NPC_SCALE, NPC_SCALE, NPC_SCALE))   # glb root xform ignored on skinned -> do it here
    comp = a.skeletal_mesh_component
    comp.set_skeletal_mesh(sk)
    if anim is not None:
        # Serialize the single-node anim so it SURVIVES Play (PIE); play_animation
        # alone only animates the editor viewport -> "Anim to Play" empty -> T-pose.
        comp.set_editor_property("animation_mode", unreal.AnimationMode.ANIMATION_SINGLE_NODE)
        try:
            ad = comp.get_editor_property("animation_data")
            ad.set_editor_property("anim_to_play", anim)
            ad.set_editor_property("saved_looping", True)
            ad.set_editor_property("saved_playing", True)
            comp.set_editor_property("animation_data", ad)
        except Exception as e:
            log("  anim-data set failed for %d (%s)" % (char_id, e))
        try:
            comp.play_animation(anim, True)
        except Exception:
            pass
    a.set_actor_label(tag)
    return True


def yaw_of(rot):
    # ROSE quaternion (x,y,z,w), mostly a Z (yaw) spin
    # UE flips Y vs ROSE -> yaw mirrors (negate). Tweak with NPC_YAW_OFFSET if needed.
    return -math.degrees(2.0 * math.atan2(rot[2], rot[3])) + NPC_YAW_OFFSET


placed = 0
if os.path.exists(npcs_path):
    data = json.load(open(npcs_path))
    rb = data["rose_bounds"]
    rmin, rmax = rb["min"], rb["max"]

    def lerp(v, a0, a1, b0, b1):
        return b0 + ((v - a0) / (a1 - a0) if a1 != a0 else 0.5) * (b1 - b0)

    def remap_xy(rx, ry):
        if SWAP_XY:
            rx, ry = ry, rx
        # UE imports the map at exact ROSE scale with Y negated: UE = (ROSE_x, -ROSE_y).
        return rx, -ry

    def remap_z(rz):
        return lerp(rz, rmin[2], rmax[2], mn[2], mx[2])

    for i, npc in enumerate(data["npcs"]):
        ux, uy = remap_xy(npc["pos"][0], npc["pos"][1])
        gz = npc["pos"][2]      # UE_Z = ROSE_Z; NPCs are server-placed on the ground
        if npc["kind"] == "NPC":
            if spawn_char(npc["object_id"], ux, uy, gz, yaw_of(npc["rot"]), "ROSE_NPC_%d" % i):
                placed += 1
        else:
            # monster spawn point -> a ring of the mobs it spawns (deduped)
            seen, mobs = set(), []
            for mb in npc.get("mobs", []):
                if mb["id"] not in seen and os.path.exists(os.path.join(models_dir, "%d.glb" % mb["id"])):
                    seen.add(mb["id"]); mobs.append(mb["id"])
            n = min(len(mobs), 6)
            ring = max(300.0, n * 180.0)
            for k in range(n):
                ang = (k / float(n)) * 2.0 * math.pi if n > 1 else 0.0
                ox = math.cos(ang) * ring if n > 1 else 0.0
                oy = math.sin(ang) * ring if n > 1 else 0.0
                if spawn_char(mobs[k], ux + ox, uy + oy, gz, math.degrees(ang) + 180.0, "ROSE_NPC_mob_%d_%d" % (i, k)):
                    placed += 1
    log("spawned %d animated NPC/monster actors" % placed)
else:
    log("no NPCs/npcs.json found (%s) — run export_npcs / rebuild the bundle" % npcs_path)


# ---- 5. PlayerStart authoritative ON the map (remove the old/template ones) -
def good_ground(cx, cy):
    """Map centre if it traces to ground, else spiral outward until something hits
    — so the PlayerStart always lands on the map, never the old level."""
    g = ground_z(cx, cy)
    if g is not None:
        return cx, cy, g
    step = max(mx[0] - mn[0], mx[1] - mn[1]) * 0.05
    for r in range(1, 9):
        for a in range(8):
            ang = a / 8.0 * 2.0 * math.pi
            x, y = cx + math.cos(ang) * step * r, cy + math.sin(ang) * step * r
            g = ground_z(x, y)
            if g is not None:
                return x, y, g
    return cx, cy, None


cx, cy = (mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0
px, py, gz = good_ground(cx, cy)
spawn_z = (gz if gz is not None else mn[2] + 300.0) + 120.0
loc = unreal.Vector(px, py, spawn_z)

starts = [a for a in eas.get_all_level_actors() if isinstance(a, unreal.PlayerStart)]
if starts:
    starts[0].set_actor_location(loc, False, False)
    ps = starts[0]
    for extra in starts[1:]:                 # kill the template / extra PlayerStarts so this one wins
        eas.destroy_actor(extra)
    if len(starts) > 1:
        log("removed %d extra PlayerStart(s) from the old level" % (len(starts) - 1))
else:
    ps = eas.spawn_actor_from_class(unreal.PlayerStart, loc, unreal.Rotator(0, 0, 0))
try:
    ps.set_actor_label("ROSE_PlayerStart")
except Exception:
    pass

log("================= CHARACTER STANDING DIAGNOSTIC =================")
log("PlayerStart count now: %d (kept 1 on the map)" % len([a for a in eas.get_all_level_actors() if isinstance(a, unreal.PlayerStart)]))
log("PlayerStart location : (%.1f, %.1f, %.1f)" % (loc.x, loc.y, loc.z))
log("ground Z under start : %s" % ("%.1f" % gz if gz is not None else "NO HIT (using fallback)"))
if gz is not None:
    log("spawned %.1f cm above ground; a UE character capsule is ~88 cm half-height, so it should settle ~88 cm up" % (loc.z - gz))
log("map Z range          : %.1f .. %.1f" % (mn[2], mx[2]))
log("collision mode       : %s   (switch COLLISION='COMPLEX_AS_SIMPLE' if it floats)" % COLLISION)
log("================================================================")

try:
    les.save_current_level()
except Exception:
    pass
log("DONE — press Play. Cyan cylinders = NPCs, red cubes = monster spawns.")
