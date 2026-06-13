// ROSE map — auto-assign materials & textures from the bundle manifest.
//
// Builds one material per slot using the custom URP shader "ROSE/URP/Lit"
// (ships next to this script as ROSE_URP_Lit.shader), wires the texture, sets
// opaque / cutout / transparent + two-sided, and remaps the FBX material slots.
//
// SETUP: drop the whole extracted bundle folder into Assets/ (so Unity imports
//        the FBX, textures, AND the .shader). Then menu: ROSE > Assign Materials.
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public static class AssignRoseMaterials
{
    [System.Serializable] class MatEntry { public string name; public string texture; public bool alpha; public string mode; public bool twosided; public float[] color; }
    [System.Serializable] class Manifest { public string zone; public string fbx; public MatEntry[] materials; }

    [MenuItem("ROSE/Assign Materials")]
    public static void Run()
    {
        // locate materials.json under Assets
        string manifestPath = null;
        foreach (var g in AssetDatabase.FindAssets("materials t:TextAsset"))
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

        Shader rose = Shader.Find("ROSE/URP/Lit");
        Shader fallback = Shader.Find("Universal Render Pipeline/Lit");
        if (fallback == null) fallback = Shader.Find("Standard");
        bool custom = rose != null;
        Shader shader = custom ? rose : fallback;
        if (!custom) Debug.LogWarning("[ROSE] ROSE/URP/Lit not found (is the .shader imported & URP active?). Falling back to " + shader.name);

        var byName = new Dictionary<string, Material>();
        int made = 0;
        foreach (var e in manifest.materials)
        {
            var mat = new Material(shader) { name = e.name };

            Texture2D tex = null;
            if (!string.IsNullOrEmpty(e.texture))
                tex = AssetDatabase.LoadAssetAtPath<Texture2D>(root + "/Textures/" + e.texture);

            if (custom)
            {
                if (tex != null) mat.SetTexture("_BaseMap", tex);
                // base colour + alpha — color-only materials (collision boxes,
                // WalkBlocked) need this so they render as their faint colour
                // instead of solid white.
                if (e.color != null && e.color.Length >= 4)
                    mat.SetColor("_BaseColor", new Color(e.color[0], e.color[1], e.color[2], e.color[3]));
                ApplyMode(mat, e.mode);
                mat.SetFloat("_Cull", e.twosided ? 0f : 2f);   // 0 = Off (two-sided), 2 = Back
                // transparent overlays (grass biome blend + collision) draw just
                // in front of the opaque ground so they don't clip / Z-fight it.
                if (e.mode == "BLEND") { mat.SetFloat("_OffsetFactor", -1f); mat.SetFloat("_OffsetUnits", -1f); }
            }
            else // stock fallback
            {
                bool isURP = shader.name.Contains("Universal");
                if (tex != null) mat.SetTexture(isURP ? "_BaseMap" : "_MainTex", tex);
                if (e.mode == "MASK") mat.EnableKeyword("_ALPHATEST_ON");
                if (e.twosided && isURP) mat.SetFloat("_Cull", 0f);
            }

            AssetDatabase.CreateAsset(mat, matDir + "/" + e.name + ".mat");
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
        Debug.Log($"[ROSE] created {made} materials ({(custom ? "ROSE/URP/Lit" : shader.name)}), remapped {remapped} FBX slots.");
    }

    [MenuItem("ROSE/Apply Sky")]
    public static void ApplySky()
    {
        Shader sky = Shader.Find("ROSE/Skybox");
        if (sky == null) { Debug.LogError("[ROSE] ROSE/Skybox shader not found (is the bundle imported?)."); return; }

        // place the sky material next to materials.json, else Assets root
        string dir = "Assets";
        foreach (var g in AssetDatabase.FindAssets("materials t:TextAsset"))
        {
            var p = AssetDatabase.GUIDToAssetPath(g);
            if (p.EndsWith("materials.json")) { dir = Path.GetDirectoryName(p).Replace('\\', '/'); break; }
        }
        string matPath = dir + "/ROSE_Sky.mat";
        var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
        if (mat == null) { mat = new Material(sky) { name = "ROSE_Sky" }; AssetDatabase.CreateAsset(mat, matPath); }
        else mat.shader = sky;
        AssetDatabase.SaveAssets();

        RenderSettings.skybox = mat;
        DynamicGI.UpdateEnvironment();
        Debug.Log("[ROSE] sky applied -> " + matPath + " (set as Environment skybox)");
    }

    static void ApplyMode(Material mat, string mode)
    {
        switch (mode)
        {
            case "MASK":   // cutout / foliage
                mat.EnableKeyword("_ALPHATEST_ON");
                mat.SetFloat("_AlphaClip", 1f);
                mat.SetFloat("_Cutoff", 0.5f);
                mat.SetFloat("_SrcBlend", (float)BlendMode.One);
                mat.SetFloat("_DstBlend", (float)BlendMode.Zero);
                mat.SetFloat("_ZWrite", 1f);
                mat.renderQueue = (int)RenderQueue.AlphaTest;
                break;
            case "BLEND":  // transparent / grass overlay + water
                mat.DisableKeyword("_ALPHATEST_ON");
                mat.SetFloat("_AlphaClip", 0f);
                mat.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
                mat.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
                mat.SetFloat("_ZWrite", 0f);
                mat.renderQueue = (int)RenderQueue.Transparent;
                break;
            default:       // OPAQUE
                mat.DisableKeyword("_ALPHATEST_ON");
                mat.SetFloat("_AlphaClip", 0f);
                mat.SetFloat("_SrcBlend", (float)BlendMode.One);
                mat.SetFloat("_DstBlend", (float)BlendMode.Zero);
                mat.SetFloat("_ZWrite", 1f);
                mat.renderQueue = (int)RenderQueue.Geometry;
                break;
        }
    }
}
