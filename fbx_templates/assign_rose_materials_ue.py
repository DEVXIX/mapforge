"""ROSE map — auto-assign materials & textures to the imported FBX (UE 5.x).

SETUP: import <zone>.fbx into your project (drag it into the Content Browser).
RUN:   Tools > Execute Python Script... > this file (it lives in the bundle's
       UE5/ folder, next to materials.json one level up).

It imports the bundle's Textures, builds one Material per manifest entry
(TextureSample -> Base Color, masked/translucent per the alpha mode, two-sided
where needed), then assigns each material to every imported Static Mesh slot
whose name matches (the FBX slot names are the manifest material names).
"""
import os
import json
import unreal

# Bundle root = parent of this UE5/ folder (contains materials.json + Textures/)
BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "/Game/ROSE_FBX"
SCAN = "/Game"          # where to look for the imported static meshes


def log(m):
    unreal.log("[ROSE-FBX] " + str(m))


at = unreal.AssetToolsHelpers.get_asset_tools()
mel = unreal.MaterialEditingLibrary
eal = unreal.EditorAssetLibrary

manifest = json.load(open(os.path.join(BUNDLE, "materials.json")))
entries = manifest["materials"]

# --- 1. import the textures ----------------------------------------------
tex_dir = os.path.join(BUNDLE, "Textures")
tex_assets = {}
files = [os.path.join(tex_dir, f) for f in os.listdir(tex_dir) if f.lower().endswith(".png")]
if files:
    data = unreal.AutomatedAssetImportData()
    data.destination_path = PKG + "/Textures"
    data.filenames = files
    data.replace_existing = True
    for obj in at.import_assets_automated(data):
        if isinstance(obj, unreal.Texture2D):
            tex_assets[os.path.basename(obj.get_name()).lower()] = obj
log("imported %d textures" % len(tex_assets))


def find_tex(fname):
    if not fname:
        return None
    stem = os.path.splitext(fname)[0].lower()
    return tex_assets.get(stem) or eal.load_asset("%s/Textures/%s" % (PKG, os.path.splitext(fname)[0]))


# --- 2. build a material per manifest entry ------------------------------
def make_material(e):
    name = "M_" + e["name"]
    apath = "%s/Materials/%s" % (PKG, name)
    if eal.does_asset_exist(apath):
        return eal.load_asset(apath)
    mat = at.create_asset(name, PKG + "/Materials", unreal.Material, unreal.MaterialFactoryNew())
    tex = find_tex(e.get("texture"))
    if tex:
        ts = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -350, 0)
        ts.set_editor_property("texture", tex)
        mel.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        if e.get("mode") == "MASK":
            mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
            mel.connect_material_property(ts, "A", unreal.MaterialProperty.MP_OPACITY_MASK)
        elif e.get("mode") == "BLEND":
            mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
            mel.connect_material_property(ts, "A", unreal.MaterialProperty.MP_OPACITY)
    if e.get("twosided"):
        mat.set_editor_property("two_sided", True)
    mel.recompile_material(mat)
    return mat


by_slot = {}
for e in entries:
    try:
        by_slot[e["name"]] = make_material(e)
    except Exception as ex:
        log("material %s failed (%s)" % (e["name"], ex))
log("built %d materials" % len(by_slot))


# --- 3. assign to imported static-mesh slots by name ----------------------
assigned = 0
for path in eal.list_assets(SCAN, recursive=True):
    obj = eal.load_asset(path)
    if not isinstance(obj, unreal.StaticMesh):
        continue
    try:
        sms = obj.get_editor_property("static_materials")
        changed = False
        for sm in sms:
            slot = str(sm.get_editor_property("material_slot_name"))
            if slot in by_slot:
                sm.set_editor_property("material_interface", by_slot[slot])
                changed = True
                assigned += 1
        if changed:
            obj.set_editor_property("static_materials", sms)
            eal.save_asset(path, only_if_is_dirty=False)
    except Exception:
        pass
log("assigned %d material slots — DONE" % assigned)
