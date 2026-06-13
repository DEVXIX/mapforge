"""Build a distributable FBX bundle for a zone.

Pipeline:
  1. export_map.export()  -> .glb (+ material manifest)   [reuses the glTF path]
  2. Blender (headless)   -> .fbx with named material slots
  3. deduped textures     -> Textures/<tex>.png
  4. materials.json       -> material name -> texture + alpha mode + 2-sided
  5. Unity + UE editor scripts + README copied in
  6. zip the whole folder -> exports/<zone>_fbx.zip   (one top folder inside)

Usage:  python export_fbx.py JPT01-1
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import zipfile
import subprocess

from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import export_map

BLENDER = os.environ.get("BLENDER_EXE",
                         r"C:/Program Files/Blender Foundation/Blender 5.0/blender.exe")
TEMPLATES = os.path.join(_HERE, "fbx_templates")


def _sanitize(s):
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in s)[:48]


def build(key, out_root=None):
    out_root = out_root or os.path.join(_HERE, "exports")
    bundle = os.path.join(out_root, "%s_fbx" % key)
    if os.path.isdir(bundle):
        shutil.rmtree(bundle)
    os.makedirs(os.path.join(bundle, "Textures"))
    os.makedirs(os.path.join(bundle, "Editor"))      # Unity special folder: editor scripts
    os.makedirs(os.path.join(bundle, "Shaders"))     # runtime shader (URP)
    os.makedirs(os.path.join(bundle, "UE5"))

    # 1. glTF (intermediate) + material info
    glb = os.path.join(out_root, "%s.glb" % key)
    print("[fbx] building glb…")
    stats = export_map.export(key, glb)
    minfo = stats["material_info"]

    # 2. Blender glb -> fbx
    fbx = os.path.join(bundle, "%s.fbx" % key)
    print("[fbx] converting to FBX via Blender…")
    if not os.path.exists(BLENDER):
        raise FileNotFoundError("Blender not found at %s (set BLENDER_EXE)" % BLENDER)
    subprocess.run([BLENDER, "--background", "--python",
                    os.path.join(_HERE, "blender_glb_to_fbx.py"), "--", glb, fbx],
                   check=True)
    if not os.path.exists(fbx):
        raise RuntimeError("Blender did not produce the FBX")

    # 3. deduped textures + rewrite manifest texture names
    print("[fbx] writing textures…")
    texmap = {}        # (src.lower, alpha) -> filename
    used = {}          # filename -> source key (to disambiguate name clashes)
    for m in minfo:
        src = m.get("texture_src")
        if not src:
            m["texture"] = None
            m.pop("texture_src", None)
            continue
        keyt = (src.lower(), bool(m["alpha"]))
        if keyt not in texmap:
            base = _sanitize(os.path.splitext(os.path.basename(src))[0])
            suf = "a" if m["alpha"] else "rgb"
            fn = "%s_%s.png" % (base, suf)
            n = 1
            while fn in used and used[fn] != keyt:     # different source, same name
                fn = "%s_%s_%d.png" % (base, suf, n); n += 1
            used[fn] = keyt
            try:
                im = Image.open(src); im.load()
                im = im.convert("RGBA" if m["alpha"] else "RGB")
                im.save(os.path.join(bundle, "Textures", fn), "PNG")
            except Exception as e:
                print("  tex fail %s (%s)" % (src, e))
            texmap[keyt] = fn
        m["texture"] = texmap[keyt]
        m.pop("texture_src", None)

    # 4. manifest
    with open(os.path.join(bundle, "materials.json"), "w") as f:
        json.dump({"zone": key, "fbx": "%s.fbx" % key, "materials": minfo}, f, indent=1)

    # 5. editor scripts + shader + readme
    shutil.copy2(os.path.join(TEMPLATES, "AssignRoseMaterials.cs"),
                 os.path.join(bundle, "Editor", "AssignRoseMaterials.cs"))
    shutil.copy2(os.path.join(TEMPLATES, "ROSE_URP_Lit.shader"),
                 os.path.join(bundle, "Shaders", "ROSE_URP_Lit.shader"))
    shutil.copy2(os.path.join(TEMPLATES, "ROSE_Skybox.shader"),
                 os.path.join(bundle, "Shaders", "ROSE_Skybox.shader"))
    shutil.copy2(os.path.join(TEMPLATES, "assign_rose_materials_ue.py"),
                 os.path.join(bundle, "UE5", "assign_rose_materials_ue.py"))
    shutil.copy2(os.path.join(TEMPLATES, "README.txt"), os.path.join(bundle, "README.txt"))

    # cleanup the intermediate glb + its sidecar (bundle is FBX-only)
    for p in (glb, glb + ".materials.json"):
        if os.path.exists(p):
            os.remove(p)

    # 6. zip (one top folder inside)
    zip_path = os.path.join(out_root, "%s_fbx.zip" % key)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(bundle):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, out_root)
                z.write(full, arc)

    return {
        "bundle": bundle, "zip": zip_path,
        "fbx_bytes": os.path.getsize(fbx),
        "textures": len(os.listdir(os.path.join(bundle, "Textures"))),
        "materials": len(minfo),
        "zip_bytes": os.path.getsize(zip_path),
    }


if __name__ == "__main__":
    k = sys.argv[1] if len(sys.argv) > 1 else "JPT01-1"
    print("building FBX bundle for %s" % k)
    print(json.dumps(build(k), indent=2))
