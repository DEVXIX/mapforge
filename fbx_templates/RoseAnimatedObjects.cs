// Editor: drop ROSE's animated MORPH objects (waving banners, streaming water)
// onto the map. The exported map FBX already places every banner/water mesh as a
// static GameObject named "MORPH__<stem>__<n>" (e.g. MORPH__bugle01__3). This
// script finds each of those, parents the matching animated FBX under it with a
// local-identity transform (so it inherits the static's exact world placement and
// — because both FBXs come from the same Blender export — the same mesh scale),
// wires an Animator + looping clip + RoseAnimationSpeed, and hides the static mesh.
//
// Usage:
//   1. Import the map FBX bundle (this folder) into your project.
//   2. Drag/import the map FBX into your scene (File > Import Into Level, or drag).
//   3. Menu: ROSE > Animate Map Objects.
//   Adjust speed per object via the RoseAnimationSpeed component, or all at once
//   by selecting them. To revert, use ROSE > Remove Animated Objects.
//
// If the animated meshes come in rotated vs the static ones, nudge EXTRA_EULER
// below (try (90,0,0), (-90,0,0) or (0,180,0)) and re-run.
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class RoseAnimatedObjects
{
    // Local rotation offset applied to each spawned animated mesh. Should be zero
    // (the exporter already matches the map's orientation); change only if needed.
    static readonly Vector3 EXTRA_EULER = Vector3.zero;

    [System.Serializable] class AnimObj
    {
        public int oid; public string stem; public string fbx; public string match;
        public int frames; public int fps; public int verts; public int placements;
    }
    [System.Serializable] class Manifest
    {
        public string zone; public AnimObj[] objects;
    }

    [MenuItem("ROSE/Animate Map Objects")]
    static void Animate()
    {
        // Locate animations.json (ships in the bundle's Animations/ folder).
        string jsonPath = AssetDatabase.FindAssets("animations t:TextAsset")
            .Select(AssetDatabase.GUIDToAssetPath)
            .FirstOrDefault(p => p.EndsWith("/animations.json"));
        if (jsonPath == null)
        {
            EditorUtility.DisplayDialog("ROSE", "Could not find animations.json in the project.\n" +
                "Make sure the Animations/ folder from the bundle is imported.", "OK");
            return;
        }
        string folder = Path.GetDirectoryName(jsonPath).Replace("\\", "/");
        var manifest = JsonUtility.FromJson<Manifest>(File.ReadAllText(jsonPath));
        if (manifest == null || manifest.objects == null || manifest.objects.Length == 0)
        {
            EditorUtility.DisplayDialog("ROSE", "animations.json has no objects.", "OK");
            return;
        }

        // Gather every transform in the open scene once (include inactive).
        var all = new List<Transform>();
        foreach (var root in SceneManager.GetActiveScene().GetRootGameObjects())
            all.AddRange(root.GetComponentsInChildren<Transform>(true));

        int spawned = 0, missing = 0;
        var rot = Quaternion.Euler(EXTRA_EULER);

        foreach (var obj in manifest.objects)
        {
            string fbxPath = folder + "/" + obj.fbx;
            var fbxAsset = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
            if (fbxAsset == null) { Debug.LogWarning($"[ROSE] missing FBX {fbxPath}"); continue; }

            var ctrl = BuildLoopingController(fbxPath, folder, obj.stem);
            if (ctrl == null) { Debug.LogWarning($"[ROSE] no clip in {obj.fbx}"); continue; }

            // Static placements of this object: name contains "MORPH__<stem>".
            var targets = all.Where(t => t != null && t.name.Contains(obj.match)
                                         && !t.name.StartsWith("ANIM__")).ToList();
            if (targets.Count == 0) { missing++; Debug.LogWarning($"[ROSE] no static placements for {obj.stem} (looked for '{obj.match}')"); }

            int n = 0;
            foreach (var stat in targets)
            {
                var inst = (GameObject)PrefabUtility.InstantiatePrefab(fbxAsset);
                inst.name = "ANIM__" + obj.stem + "__" + (n++);
                inst.transform.SetParent(stat, false);                 // inherit static's world transform
                inst.transform.localPosition = Vector3.zero;
                inst.transform.localRotation = rot;
                inst.transform.localScale = Vector3.one;

                var anim = inst.GetComponent<Animator>() ?? inst.AddComponent<Animator>();
                anim.runtimeAnimatorController = ctrl;
                if (inst.GetComponent<RoseAnimationSpeed>() == null) inst.AddComponent<RoseAnimationSpeed>();

                // Hide the static (keep it active so the child stays placed).
                foreach (var r in stat.GetComponents<Renderer>()) r.enabled = false;
                Undo.RegisterCreatedObjectUndo(inst, "ROSE Animate");
                spawned++;
            }
        }

        AssetDatabase.SaveAssets();
        EditorUtility.DisplayDialog("ROSE",
            $"Placed {spawned} animated object(s).\n" +
            (missing > 0 ? $"{missing} object(s) had no static placement found — see Console.\n" : "") +
            "Tweak playback via the RoseAnimationSpeed component.", "OK");
    }

    [MenuItem("ROSE/Remove Animated Objects")]
    static void Remove()
    {
        int removed = 0;
        foreach (var root in SceneManager.GetActiveScene().GetRootGameObjects())
            foreach (var t in root.GetComponentsInChildren<Transform>(true).ToList())
            {
                if (t == null) continue;
                if (t.name.StartsWith("ANIM__")) { Object.DestroyImmediate(t.gameObject); removed++; }
                else if (t.name.Contains("MORPH__"))                  // re-show the static mesh
                    foreach (var r in t.GetComponents<Renderer>()) r.enabled = true;
            }
        EditorUtility.DisplayDialog("ROSE", $"Removed {removed} animated object(s) and restored the static meshes.", "OK");
    }

    // Make the FBX clip loop and wrap it in an AnimatorController (cached next to the FBX).
    static AnimatorController BuildLoopingController(string fbxPath, string folder, string stem)
    {
        var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
        if (importer != null)
        {
            importer.animationType = ModelImporterAnimationType.Generic;
            var clips = importer.defaultClipAnimations;
            bool changed = false;
            for (int i = 0; i < clips.Length; i++)
                if (!clips[i].loopTime) { clips[i].loopTime = true; changed = true; }
            if (changed) { importer.clipAnimations = clips; importer.SaveAndReimport(); }
        }
        var clip = AssetDatabase.LoadAllAssetsAtPath(fbxPath)
            .OfType<AnimationClip>().FirstOrDefault(c => !c.name.StartsWith("__preview"));
        if (clip == null) return null;

        string ctrlPath = folder + "/" + stem + ".controller";
        var ctrl = AssetDatabase.LoadAssetAtPath<AnimatorController>(ctrlPath);
        if (ctrl == null) ctrl = AnimatorController.CreateAnimatorControllerAtPathWithClip(ctrlPath, clip);
        return ctrl;
    }
}
