// ROSE map — auto-assign materials & textures from the bundle manifest.
//
// SETUP: drop the whole extracted bundle folder into your project's Assets/
//        (e.g. Assets/ROSE/ containing the .fbx, materials.json, Textures/,
//        and this script). Unity will import the FBX and textures.
//
// RUN:   menu  ROSE > Assign Materials  — it reads materials.json, builds a
//        material per entry (URP Lit or Standard), points it at the matching
//        texture, sets opaque/cutout/transparent, and remaps the FBX's
//        material slots to them.
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public static class AssignRoseMaterials
{
    [System.Serializable] class MatEntry { public string name; public string texture; public bool alpha; public string mode; public bool twosided; }
    [System.Serializable] class Manifest { public string zone; public string fbx; public MatEntry[] materials; }

    [MenuItem("ROSE/Assign Materials")]
    public static void Run()
    {
        // find materials.json anywhere under Assets
        string[] hits = AssetDatabase.FindAssets("materials t:TextAsset");
        string manifestPath = null;
        foreach (var g in hits)
        {
            var p = AssetDatabase.GUIDToAssetPath(g);
            if (p.EndsWith("materials.json")) { manifestPath = p; break; }
        }
        if (manifestPath == null) { Debug.LogError("[ROSE] materials.json not found under Assets/."); return; }

        string root = Path.GetDirectoryName(manifestPath).Replace('\\', '/');
        var manifest = JsonUtility.FromJson<Manifest>(File.ReadAllText(manifestPath));
        if (manifest == null || manifest.materials == null) { Debug.LogError("[ROSE] bad manifest."); return; }

        var matDir = root + "/Materials";
        if (!AssetDatabase.IsValidFolder(matDir)) AssetDatabase.CreateFolder(root, "Materials");

        Shader urp = Shader.Find("Universal Render Pipeline/Lit");
        Shader std = Shader.Find("Standard");
        bool isURP = urp != null;
        Shader shader = isURP ? urp : std;

        var byName = new Dictionary<string, Material>();
        int made = 0;
        foreach (var e in manifest.materials)
        {
            var mat = new Material(shader) { name = e.name };

            Texture2D tex = null;
            if (!string.IsNullOrEmpty(e.texture))
                tex = AssetDatabase.LoadAssetAtPath<Texture2D>(root + "/Textures/" + e.texture);

            if (tex != null)
            {
                if (isURP) mat.SetTexture("_BaseMap", tex);
                else mat.SetTexture("_MainTex", tex);
            }

            // transparency
            if (e.mode == "MASK")
            {
                if (isURP) { mat.SetFloat("_AlphaClip", 1f); mat.SetFloat("_Cutoff", 0.5f); mat.EnableKeyword("_ALPHATEST_ON"); }
                else { mat.SetFloat("_Mode", 1f); mat.SetOverrideTag("RenderType", "TransparentCutout"); mat.EnableKeyword("_ALPHATEST_ON"); }
            }
            else if (e.mode == "BLEND")
            {
                if (isURP) { mat.SetFloat("_Surface", 1f); mat.EnableKeyword("_SURFACE_TYPE_TRANSPARENT"); }
                else { mat.SetFloat("_Mode", 3f); mat.SetOverrideTag("RenderType", "Transparent"); mat.EnableKeyword("_ALPHABLEND_ON"); }
            }

            // two-sided foliage/terrain (URP exposes _Cull; 0 = render both faces)
            if (e.twosided && isURP) mat.SetFloat("_Cull", 0f);

            var matPath = matDir + "/" + e.name + ".mat";
            AssetDatabase.CreateAsset(mat, matPath);
            byName[e.name] = mat;
            made++;
        }
        AssetDatabase.SaveAssets();

        // remap every imported FBX's material slots to our materials
        int remapped = 0;
        foreach (var g in AssetDatabase.FindAssets("t:Model", new[] { root }))
        {
            var fbxPath = AssetDatabase.GUIDToAssetPath(g);
            var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
            if (importer == null) continue;
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
            foreach (var obj in AssetDatabase.LoadAllAssetsAtPath(fbxPath))
            {
                if (!(obj is Material)) continue;
                if (byName.TryGetValue(obj.name, out var m))
                {
                    importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), obj.name), m);
                    remapped++;
                }
            }
            importer.SaveAndReimport();
        }
        Debug.Log($"[ROSE] created {made} materials, remapped {remapped} FBX slots. ({(isURP ? "URP" : "Built-in")})");
    }
}
