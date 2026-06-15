"""Import the NPC/monster crowd as a Vertex-Animation-Texture (VAT) — STATIC meshes
that idle-animate via a World-Position-Offset material. Built by export_npc_vat.py.

Why this works where skeletal didn't: the meshes import through the SAME static-mesh
path as the map (1:1 placement, proven), and the motion is a baked texture pushed in
the vertex shader — no skeletal glTF import, no rescale/rotate quirks.

SAFE FALLBACK: this does NOT touch import_npcs_ue.py. If the animation looks wrong,
just re-run import_npcs_ue.py to restore the perfect 1:1 *static* posed crowd.

RUN:  Tools > Execute Python Script...  ->  this file.   (Run the map importer first.)
"""

import os
import json
import unreal

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVEL = "/Game/ThirdPerson/Lvl_ThirdPerson"
PKG = "/Game/ROSE_VAT"
MESH_PKG = PKG + "/Mesh"
TEX_PKG = PKG + "/Tex"
MAT_PKG = PKG + "/Mat"
MASTER = MAT_PKG + "/M_ROSE_VAT"

at = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary
mel = unreal.MaterialEditingLibrary
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def log(m): unreal.log("[ROSE-VAT] " + str(m))


# ---------------------------------------------------------------- master material
def build_master():
    if eal.does_asset_exist(MASTER):
        eal.delete_asset(MASTER)
    mat = at.create_asset("M_ROSE_VAT", MAT_PKG, unreal.Material,
                           unreal.MaterialFactoryNew())
    mat.set_editor_property("two_sided", True)

    def expr(cls, x, y):
        return mel.create_material_expression(mat, cls, x, y)

    def conn(a, ao, b, bi):
        # Pin names vary by UE version (single-output: "" vs "Result"; single-input:
        # "" vs "Input"). Try the candidates and keep whichever actually connects
        # (connect_material_expressions returns True on success). Named pins
        # ("A"/"B"/"R"/"G"/"RGB"/"VAT"/"UV") are used verbatim.
        outs = ["", "Result"] if ao in ("", "Result") else [ao]
        ins = ["", "Input", "A"] if bi in ("", "Input") else [bi]
        for o in outs:
            for i in ins:
                try:
                    if mel.connect_material_expressions(a, o, b, i):
                        return True
                except Exception:
                    pass
        log("WARN: could not connect -> %s.%s" % (b.get_name(), bi))
        return False

    def conn_prop(a, ao, prop):
        for o in (["", "Result"] if ao in ("", "Result") else [ao]):
            try:
                if mel.connect_material_property(a, o, prop):
                    return True
            except Exception:
                pass
        log("WARN: could not connect property %s" % prop)
        return False

    def scalar(name, val, x, y):
        s = expr(unreal.MaterialExpressionScalarParameter, x, y)
        s.set_editor_property("parameter_name", name)
        s.set_editor_property("default_value", float(val))
        return s

    # --- base colour (mesh UV0) ---
    base = expr(unreal.MaterialExpressionTextureSampleParameter2D, -400, -320)
    base.set_editor_property("parameter_name", "BaseColor")
    conn_prop(base, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)

    # --- U = frac(Time * AnimSpeed) ---
    t = expr(unreal.MaterialExpressionTime, -1000, 0)
    speed = scalar("AnimSpeed", 0.5, -1000, 140)
    mulT = expr(unreal.MaterialExpressionMultiply, -800, 30)
    conn(t, "", mulT, "A"); conn(speed, "", mulT, "B")
    frac = expr(unreal.MaterialExpressionFrac, -650, 30)
    conn(mulT, "", frac, "")

    # --- V = (row + 0.5) / TotalVerts,  row = vcol.R*65280 + vcol.G*255 ---
    vcol = expr(unreal.MaterialExpressionVertexColor, -1000, 280)
    hi = expr(unreal.MaterialExpressionMultiply, -800, 250)
    conn(vcol, "R", hi, "A"); hi.set_editor_property("const_b", 65280.0)
    lo = expr(unreal.MaterialExpressionMultiply, -800, 330)
    conn(vcol, "G", lo, "A"); lo.set_editor_property("const_b", 255.0)
    rsum = expr(unreal.MaterialExpressionAdd, -650, 280)
    conn(hi, "", rsum, "A"); conn(lo, "", rsum, "B")
    rc = expr(unreal.MaterialExpressionAdd, -520, 280)
    conn(rsum, "", rc, "A"); rc.set_editor_property("const_b", 0.5)
    total = scalar("TotalVerts", 1.0, -650, 400)
    V = expr(unreal.MaterialExpressionDivide, -380, 300)
    conn(rc, "", V, "A"); conn(total, "", V, "B")

    # --- UV float2 ---
    uv = expr(unreal.MaterialExpressionAppendVector, -200, 120)
    conn(frac, "", uv, "A"); conn(V, "", uv, "B")

    # --- VAT sample at explicit mip 0 (Custom node => valid in vertex shader) ---
    vatobj = expr(unreal.MaterialExpressionTextureObjectParameter, 0, 300)
    vatobj.set_editor_property("parameter_name", "VAT")
    cust = expr(unreal.MaterialExpressionCustom, 150, 140)
    cust.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    cust.set_editor_property("code", "return Texture2DSampleLevel(VAT, VATSampler, UV, 0).rgb;")
    ci_vat = unreal.CustomInput(); ci_vat.set_editor_property("input_name", "VAT")
    ci_uv = unreal.CustomInput(); ci_uv.set_editor_property("input_name", "UV")
    cust.set_editor_property("inputs", [ci_vat, ci_uv])
    conn(vatobj, "", cust, "VAT")
    conn(uv, "", cust, "UV")

    # --- decode: offset = rgb * DecodeRange + DecodeMin ---
    drange = scalar("DecodeRange", 1.0, 150, 360)
    dmin = scalar("DecodeMin", 0.0, 150, 440)
    o1 = expr(unreal.MaterialExpressionMultiply, 350, 180)
    conn(cust, "", o1, "A"); conn(drange, "", o1, "B")
    off = expr(unreal.MaterialExpressionAdd, 500, 180)
    conn(o1, "", off, "A"); conn(dmin, "", off, "B")

    # --- rotate local offset into world, drive WPO ---
    xf = expr(unreal.MaterialExpressionTransform, 660, 180)
    try:
        xf.set_editor_property("transform_source_type",
                               unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_LOCAL)
    except Exception:
        pass
    try:
        xf.set_editor_property("transform_type",
                               unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
    except Exception:
        pass
    conn(off, "", xf, "")
    conn_prop(xf, "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)

    mel.recompile_material(mat)
    eal.save_asset(MASTER)
    log("master material built: %s" % MASTER)
    return mat


# ---------------------------------------------------------------- texture import
def import_textures(files, dest, vat):
    tasks = []
    for f in files:
        t = unreal.AssetImportTask()
        t.set_editor_property("filename", f)
        t.set_editor_property("destination_path", dest)
        t.set_editor_property("replace_existing", True)
        t.set_editor_property("automated", True)
        t.set_editor_property("save", True)
        tasks.append(t)
    if tasks:
        at.import_asset_tasks(tasks)
    out = {}
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        ap = "%s/%s" % (dest, name)
        tex = eal.load_asset(ap)
        if isinstance(tex, unreal.Texture2D):
            if vat:
                tex.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
                tex.set_editor_property("mip_gen_settings",
                                        unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
                tex.set_editor_property("compression_settings",
                                        unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
                tex.set_editor_property("srgb", False)
                try:
                    tex.set_editor_property("never_stream", True)
                except Exception:
                    pass
                eal.save_asset(ap)
            out[os.path.basename(f)] = tex
    return out


# ---------------------------------------------------------------- main
def main():
    man_path = os.path.join(BUNDLE, "VAT", "manifest.json")
    glb = os.path.join(BUNDLE, "npcs_vat.glb")
    if not (os.path.exists(man_path) and os.path.exists(glb)):
        log("VAT bundle missing (npcs_vat.glb / VAT/manifest.json) — rebuild it.")
        raise SystemExit
    man = json.load(open(man_path))

    try:
        les.load_level(LEVEL)
    except Exception as e:
        log("load_level: %s" % e)

    for a in list(eas.get_all_level_actors()):
        try:
            if a.get_actor_label().startswith("ROSE_NPC_"):
                eas.destroy_actor(a)
        except Exception:
            pass

    if eal.does_directory_exist(PKG):
        eal.delete_directory(PKG)

    # 1. geometry
    log("importing geometry…")
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", glb)
    task.set_editor_property("destination_path", MESH_PKG)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    at.import_asset_tasks([task])

    mesh_by_id = {}
    for p in eal.list_assets(MESH_PKG, recursive=True):
        o = eal.load_asset(p)
        if isinstance(o, unreal.StaticMesh):
            nm = o.get_name()
            if "NPCVAT_" in nm:
                try:
                    cid = int(nm.split("NPCVAT_")[1].split("_")[0])
                    mesh_by_id.setdefault(cid, o)
                except Exception:
                    pass
    log("static meshes: %d" % len(mesh_by_id))

    # 2. textures (VAT position maps + base colour)
    vat_files = [os.path.join(BUNDLE, "VAT", "%d.png" % c["id"]) for c in man["characters"]]
    vat_files = [f for f in vat_files if os.path.exists(f)]
    vat_tex = import_textures(vat_files, TEX_PKG + "/VAT", vat=True)
    base_files = []
    for c in man["characters"]:
        for prt in c.get("parts", []):
            if prt.get("tex"):
                fp = os.path.join(BUNDLE, "VAT", "Tex", prt["tex"])
                if os.path.exists(fp) and fp not in base_files:
                    base_files.append(fp)
    base_tex = import_textures(base_files, TEX_PKG + "/Base", vat=False)
    log("textures: %d VAT, %d base" % (len(vat_tex), len(base_tex)))

    # 3. master material
    try:
        build_master()
        master_ok = eal.does_asset_exist(MASTER)
    except Exception as e:
        master_ok = False
        log("MASTER MATERIAL FAILED (%s) — meshes will import static (still 1:1). "
            "Re-run import_npcs_ue.py for the posed crowd." % e)
    master = eal.load_asset(MASTER) if master_ok else None

    # 4. per-(character,slot) material instances
    mi_by_char = {}
    if master:
        for c in man["characters"]:
            cid = c["id"]
            vtex = vat_tex.get("%d.png" % cid)
            if vtex is None:
                continue
            slots = []
            for si, prt in enumerate(c.get("parts", [])):
                mi = at.create_asset("MI_%d_%d" % (cid, si), MAT_PKG,
                                     unreal.MaterialInstanceConstant,
                                     unreal.MaterialInstanceConstantFactoryNew())
                mel.set_material_instance_parent(mi, master)
                mel.set_material_instance_texture_parameter_value(mi, "VAT", vtex)
                bt = base_tex.get(prt["tex"]) if prt.get("tex") else None
                if bt:
                    mel.set_material_instance_texture_parameter_value(mi, "BaseColor", bt)
                mel.set_material_instance_scalar_parameter_value(mi, "AnimSpeed", c["anim_speed"])
                mel.set_material_instance_scalar_parameter_value(mi, "TotalVerts", float(c["verts"]))
                mel.set_material_instance_scalar_parameter_value(mi, "DecodeRange", c["decode_range"])
                mel.set_material_instance_scalar_parameter_value(mi, "DecodeMin", c["decode_min"])
                eal.save_asset(mi.get_path_name())
                slots.append(mi)
            mi_by_char[cid] = slots

    # 5. place every NPC (actor carries world pos + yaw; mesh is pre-baked upright)
    placed = 0
    for pl in man["placements"]:
        sm = mesh_by_id.get(pl["id"])
        if not sm:
            continue
        loc = unreal.Vector(pl["x"], pl["y"], pl["z"])
        rot = unreal.Rotator(roll=0.0, pitch=0.0, yaw=pl["yaw"])
        a = eas.spawn_actor_from_object(sm, loc, rot)
        if not a:
            continue
        a.set_actor_label("ROSE_NPC_%d" % placed)
        comp = a.static_mesh_component if hasattr(a, "static_mesh_component") \
            else a.get_component_by_class(unreal.StaticMeshComponent)
        slots = mi_by_char.get(pl["id"])
        if comp and slots:
            for si, mi in enumerate(slots):
                try:
                    comp.set_material(si, mi)
                except Exception:
                    pass
        placed += 1
    log("placed %d animated NPCs/monsters" % placed)

    try:
        les.save_current_level()
    except Exception:
        pass
    if master:
        log("DONE — press Play; NPCs should idle-animate. If a character looks wrong, "
            "re-run import_npcs_ue.py for the safe 1:1 static crowd.")
    else:
        log("DONE (static fallback).")


main()
