// Editor: one-click setup of the ROSE map scene.
//
//   Menu  ROSE > Setup Scene   does everything, in order:
//     1. Assign Materials   (ROSE/URP/Lit materials + textures — AssignRoseMaterials.cs)
//     2. add the map to the open scene (if it isn't already)
//     3. overlay the animated objects (waving banners, streaming water) and wire a
//        looping Animator + a RoseAnimationSpeed component onto each
//     4. Apply Sky          (ROSE skybox)
//   Then press Play — the banners wave and water streams. Adjust playback with the
//   RoseAnimationSpeed component (speed 0–8) on any animated object.
//
// You can also run the steps individually via the other ROSE/* menu items, and
// revert the animation overlay with  ROSE > Remove Animated Objects.
//
// How the animation works: the map FBX holds each banner/water as a *static* mesh
// named MORPH__<stem>__<n> (no blend shapes). This finds each one, parents the
// matching *animated* FBX (which carries the blend shapes + clip) under it, hides
// the static mesh, and drives the animated copy. That's why putting the clip
// straight onto MORPH__... by hand fails — that mesh has no blend shapes.
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class RoseAnimatedObjects
{
    // Local rotation offset applied to each spawned animated mesh. Should be zero
    // (the exporter already matches the map's orientation); change only if needed
    // (try (90,0,0), (-90,0,0) or (0,180,0)) and re-run.
    static readonly Vector3 EXTRA_EULER = Vector3.zero;

    [System.Serializable] class AnimObj
    {
        public int oid; public string stem; public string fbx; public string match;
        public int frames; public int fps; public int verts; public int placements;
    }
    [System.Serializable] class Manifest { public string zone; public AnimObj[] objects; }

    // ---------------------------------------------------------------- one click
    [MenuItem("ROSE/Setup Scene")]
    static void SetupScene()
    {
        TryMenu("ROSE/Assign Materials");                 // 1. materials (asset-level)
        bool mapAdded = EnsureMapInScene(out bool mapFound);  // 2. map into the scene
        int spawned = 0, missing = 0;
        if (mapFound || SceneHasMap()) AnimateCore(ref spawned, ref missing);  // 3. animations
        TryMenu("ROSE/Apply Sky");                        // 4. sky

        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        string msg = "Setup complete.\n" +
            "• Materials assigned\n" +
            (mapFound ? (mapAdded ? "• Map added to the scene\n" : "• Map already in the scene\n")
                      : "• Map FBX not found — import the whole bundle folder, then re-run.\n") +
            $"• {spawned} animated object(s) placed" + (missing > 0 ? $" ({missing} unmatched — see Console)" : "") + "\n" +
            "• Sky applied\n\nPress Play to see the banners/water animate.\n" +
            "Adjust speed via the RoseAnimationSpeed component.";
        EditorUtility.DisplayDialog("ROSE — Setup Scene", msg, "OK");
    }

    // ---------------------------------------------------------- individual steps
    [MenuItem("ROSE/Animate Map Objects")]
    static void Animate()
    {
        int spawned = 0, missing = 0;
        if (!SceneHasMap()) { EditorUtility.DisplayDialog("ROSE", "The map isn't in the scene yet. Drag the map FBX in (or use ROSE > Setup Scene).", "OK"); return; }
        AnimateCore(ref spawned, ref missing);
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        EditorUtility.DisplayDialog("ROSE",
            $"Placed {spawned} animated object(s)." +
            (missing > 0 ? $"\n{missing} object(s) had no static placement — see Console." : "") +
            "\nPress Play to see them animate; tweak speed via RoseAnimationSpeed.", "OK");
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

    // ------------------------------------------------------------------- helpers
    static void TryMenu(string path) { try { EditorApplication.ExecuteMenuItem(path); } catch { } }

    static string FindManifestPath() =>
        AssetDatabase.FindAssets("animations t:TextAsset")
            .Select(AssetDatabase.GUIDToAssetPath)
            .FirstOrDefault(p => p.EndsWith("/animations.json"));

    static bool SceneHasMap()
    {
        foreach (var root in SceneManager.GetActiveScene().GetRootGameObjects())
            if (root.GetComponentsInChildren<Transform>(true).Any(t => t != null && t.name.Contains("MORPH__")))
                return true;
        return false;
    }

    // Add the <zone>.fbx (sits one folder above Animations/) to the open scene if
    // no MORPH__ mesh is present yet. mapAdded=true if we instantiated it.
    static bool EnsureMapInScene(out bool mapFound)
    {
        mapFound = false;
        if (SceneHasMap()) { mapFound = true; return false; }

        string jsonPath = FindManifestPath();
        if (jsonPath == null) return false;
        var manifest = JsonUtility.FromJson<Manifest>(File.ReadAllText(jsonPath));
        string animFolder = Path.GetDirectoryName(jsonPath).Replace("\\", "/");
        string bundleRoot = animFolder.Substring(0, animFolder.LastIndexOf('/'));
        string mapPath = bundleRoot + "/" + manifest.zone + ".fbx";

        var mapAsset = AssetDatabase.LoadAssetAtPath<GameObject>(mapPath);
        if (mapAsset == null)
            mapAsset = AssetDatabase.FindAssets(manifest.zone + " t:Model")
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => p.EndsWith("/" + manifest.zone + ".fbx"))
                .Select(AssetDatabase.LoadAssetAtPath<GameObject>)
                .FirstOrDefault();
        if (mapAsset == null) return false;

        mapFound = true;
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(mapAsset);
        inst.name = manifest.zone;
        Undo.RegisterCreatedObjectUndo(inst, "ROSE Add Map");
        return true;
    }

    // Overlay + wire the animated objects onto the static MORPH__ placements.
    static void AnimateCore(ref int spawned, ref int missing)
    {
        string jsonPath = FindManifestPath();
        if (jsonPath == null) { Debug.LogWarning("[ROSE] animations.json not found"); return; }
        string folder = Path.GetDirectoryName(jsonPath).Replace("\\", "/");
        var manifest = JsonUtility.FromJson<Manifest>(File.ReadAllText(jsonPath));
        if (manifest?.objects == null) return;

        var all = new List<Transform>();
        foreach (var root in SceneManager.GetActiveScene().GetRootGameObjects())
            all.AddRange(root.GetComponentsInChildren<Transform>(true));
        var rot = Quaternion.Euler(EXTRA_EULER);

        foreach (var obj in manifest.objects)
        {
            string fbxPath = folder + "/" + obj.fbx;
            var fbxAsset = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
            if (fbxAsset == null) { Debug.LogWarning($"[ROSE] missing FBX {fbxPath}"); continue; }

            var ctrl = BuildLoopingController(fbxPath, folder, obj.stem);
            if (ctrl == null) { Debug.LogWarning($"[ROSE] no clip in {obj.fbx}"); continue; }

            var targets = all.Where(t => t != null && t.name.Contains(obj.match)
                                         && !t.name.StartsWith("ANIM__")).ToList();
            if (targets.Count == 0) { missing++; Debug.LogWarning($"[ROSE] no static placements for {obj.stem} (looked for '{obj.match}')"); }

            int n = 0;
            foreach (var stat in targets)
            {
                // idempotent: skip a static that already has an animated child
                bool already = false;
                foreach (Transform c in stat) if (c.name.StartsWith("ANIM__")) { already = true; break; }
                if (already) continue;

                var inst = (GameObject)Object.Instantiate(fbxAsset);
                inst.name = "ANIM__" + obj.stem + "__" + (n++);
                inst.transform.SetParent(stat, false);                 // inherit static's world transform
                inst.transform.localPosition = Vector3.zero;
                inst.transform.localRotation = rot;
                inst.transform.localScale = Vector3.one;

                if (!inst.TryGetComponent<Animator>(out var anim))     // TryGetComponent avoids ?? fake-null
                    anim = inst.AddComponent<Animator>();
                anim.runtimeAnimatorController = ctrl;
                if (!inst.TryGetComponent<RoseAnimationSpeed>(out _))
                    inst.AddComponent<RoseAnimationSpeed>();

                // The animated FBX ships with no material — reuse the static
                // banner/water material (already assigned by ROSE > Assign Materials)
                // so the animated copy looks identical, not the default grey.
                var sr = stat.GetComponent<Renderer>();
                if (sr != null && sr.sharedMaterials.Length > 0)
                    foreach (var ar in inst.GetComponentsInChildren<Renderer>(true))
                        ar.sharedMaterials = Enumerable.Repeat(sr.sharedMaterials[0], ar.sharedMaterials.Length).ToArray();

                foreach (var r in stat.GetComponents<Renderer>()) r.enabled = false;  // hide static
                Undo.RegisterCreatedObjectUndo(inst, "ROSE Animate");
                spawned++;
            }
        }
        AssetDatabase.SaveAssets();
    }

    // Force the FBX morph animation to import, loop it, and wrap it in a controller.
    static AnimatorController BuildLoopingController(string fbxPath, string folder, string stem)
    {
        var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
        if (importer != null)
        {
            // 1. A blend-shape-only mesh has no skeleton, so Unity may default
            //    animationType to None and generate NO clip (a 0-length anim). Apply
            //    Generic + import flags and reimport FIRST — only then is the take's
            //    real frame range known.
            bool dirty = false;
            if (importer.animationType != ModelImporterAnimationType.Generic) { importer.animationType = ModelImporterAnimationType.Generic; dirty = true; }
            if (!importer.importAnimation) { importer.importAnimation = true; dirty = true; }
            if (!importer.importBlendShapes) { importer.importBlendShapes = true; dirty = true; }
            if (dirty) importer.SaveAndReimport();

            // 2. Now the take range is known — set every clip to loop. (Reading this
            //    before step 1's reimport is what locked in a 0-length clip before.)
            var clips = importer.defaultClipAnimations;
            for (int i = 0; i < clips.Length; i++) clips[i].loopTime = true;
            if (clips.Length > 0) { importer.clipAnimations = clips; importer.SaveAndReimport(); }
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
