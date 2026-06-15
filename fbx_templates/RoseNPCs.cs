// RoseNPCs.cs — import the ROSE NPC/monster crowd into Unity.
//
// Mirrors the animated-MORPH overlay: a posed-static FBX (npcs_posed.fbx) carries
// every placement as a named node NPCPOSE__<charid>__<n> at its baked 1:1 world
// position (through the same glTF->Blender bake as the map, so no coordinate
// guesswork). For each placement we overlay the character's looping blend-shape
// idle FBX (Anim/<charid>.fbx) and hide the static mesh — so the crowd idle-animates.
//
// Menu: ROSE > Setup NPCs   (run after ROSE > Setup Scene). Textures are embedded
// in the FBX, so no separate material step is needed for NPCs.
#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public static class RoseNPCs
{
    [System.Serializable] class CharMeta { public int id; public string fbx; public string match; public int frames; public float fps; public int verts; }
    [System.Serializable] class Manifest { public string zone; public string posed_fbx; public string anim_dir; public CharMeta[] characters; public int placements; }

    [MenuItem("ROSE/Setup NPCs")]
    public static void SetupNPCs()
    {
        string manifestPath = FindAsset("npcs_unity t:TextAsset");
        if (manifestPath == null) { EditorUtility.DisplayDialog("ROSE", "npcs_unity.json not found in the project. Drag the bundle into Assets first.", "OK"); return; }
        string root = Directory.GetParent(Path.GetDirectoryName(manifestPath)).FullName; // .../NPCs
        string projRoot = ToAssetPath(Path.GetDirectoryName(manifestPath));               // Assets/.../NPCs
        var man = JsonUtility.FromJson<Manifest>(File.ReadAllText(manifestPath));
        if (man == null || man.characters == null) { Debug.LogError("[ROSE-NPC] manifest parse failed"); return; }

        // 1. instantiate the posed-static crowd (baked 1:1 placements)
        string posedPath = JoinAsset(projRoot, man.posed_fbx);
        var posedPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(posedPath);
        if (posedPrefab == null) { Debug.LogError("[ROSE-NPC] posed FBX not found: " + posedPath); return; }

        var old = GameObject.Find("ROSE_NPCs");
        if (old != null) Object.DestroyImmediate(old);
        var crowd = (GameObject)PrefabUtility.InstantiatePrefab(posedPrefab);
        crowd.name = "ROSE_NPCs";

        // 2. collect placements by character id (nodes named ...NPCPOSE__<id>__<n>...)
        var byChar = new Dictionary<int, List<Transform>>();
        foreach (var t in crowd.GetComponentsInChildren<Transform>(true))
        {
            int id = ParseCharId(t.name);
            if (id < 0) continue;
            if (!byChar.TryGetValue(id, out var lst)) { lst = new List<Transform>(); byChar[id] = lst; }
            lst.Add(t);
        }

        // 3. overlay each character's looping idle FBX onto its placements
        int overlaid = 0, animChars = 0;
        foreach (var c in man.characters)
        {
            if (!byChar.TryGetValue(c.id, out var places) || places.Count == 0) continue;
            string animPath = JoinAsset(projRoot, "Unity/" + c.fbx);
            ConfigureAnimImporter(animPath, c.fps);
            var animPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(animPath);
            if (animPrefab == null) { Debug.LogWarning("[ROSE-NPC] missing anim FBX: " + animPath); continue; }
            var controller = BuildLoopingController(animPath, c.id);
            animChars++;
            foreach (var place in places)
            {
                var inst = (GameObject)PrefabUtility.InstantiatePrefab(animPrefab);
                inst.name = "NPCIDLE__" + c.id;
                inst.transform.SetParent(place, false);
                inst.transform.localPosition = Vector3.zero;
                inst.transform.localRotation = Quaternion.identity;
                inst.transform.localScale = Vector3.one;
                var anim = inst.GetComponent<Animator>() ?? inst.AddComponent<Animator>();
                if (controller != null) anim.runtimeAnimatorController = controller;
                AddSpeed(inst);
                // hide the static placeholder mesh (keep the node as the anchor)
                foreach (var r in place.GetComponents<Renderer>()) r.enabled = false;
                var mf = place.GetComponent<MeshFilter>();
                if (mf != null) { /* leave geometry; renderer disabled */ }
                overlaid++;
            }
        }

        EditorSceneManagerMarkDirty();
        Debug.Log(string.Format("[ROSE-NPC] crowd placed: {0} placements across {1} characters; {2} idle overlays.",
            man.placements, animChars, overlaid));
        EditorUtility.DisplayDialog("ROSE", string.Format("NPCs ready: {0} idle-animated placements.", overlaid), "OK");
    }

    // ---- helpers ----
    static int ParseCharId(string name)
    {
        int i = name.IndexOf("NPCPOSE__");
        if (i < 0) return -1;
        int s = i + "NPCPOSE__".Length;
        int e = name.IndexOf("__", s);
        if (e < 0) e = name.Length;
        string num = name.Substring(s, e - s);
        // strip any Unity-appended suffix
        int j = 0; while (j < num.Length && char.IsDigit(num[j])) j++;
        if (j == 0) return -1;
        return int.TryParse(num.Substring(0, j), out int id) ? id : -1;
    }

    static void ConfigureAnimImporter(string assetPath, float fps)
    {
        var imp = AssetImporter.GetAtPath(assetPath) as ModelImporter;
        if (imp == null) return;
        bool dirty = false;
        if (imp.importBlendShapes == false) { imp.importBlendShapes = true; dirty = true; }
        if (imp.animationType != ModelImporterAnimationType.Generic) { imp.animationType = ModelImporterAnimationType.Generic; dirty = true; }
        if (!imp.importAnimation) { imp.importAnimation = true; dirty = true; }
        var clips = imp.defaultClipAnimations;
        if (clips != null && clips.Length > 0)
        {
            for (int i = 0; i < clips.Length; i++) clips[i].loopTime = true;
            imp.clipAnimations = clips;
            dirty = true;
        }
        if (dirty) imp.SaveAndReimport();
    }

    static RuntimeAnimatorController BuildLoopingController(string fbxPath, int id)
    {
        AnimationClip clip = null;
        foreach (var o in AssetDatabase.LoadAllAssetsAtPath(fbxPath))
            if (o is AnimationClip c && !c.name.StartsWith("__")) { clip = c; break; }
        if (clip == null) return null;
        string dir = "Assets/ROSE_NPC_Controllers";
        if (!AssetDatabase.IsValidFolder(dir)) AssetDatabase.CreateFolder("Assets", "ROSE_NPC_Controllers");
        string cpath = dir + "/npc_" + id + ".controller";
        var ctrl = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPathWithClip(cpath, clip);
        return ctrl;
    }

    static void AddSpeed(GameObject go)
    {
        var t = System.Type.GetType("RoseAnimationSpeed");
        if (t != null && go.GetComponent(t) == null) go.AddComponent(t);
    }

    static string FindAsset(string filter)
    {
        foreach (var g in AssetDatabase.FindAssets(filter))
        {
            string p = AssetDatabase.GUIDToAssetPath(g);
            if (p.EndsWith(".json") || p.EndsWith(".txt")) return Path.GetFullPath(p);
        }
        return null;
    }

    static string ToAssetPath(string full)
    {
        full = full.Replace("\\", "/");
        int i = full.IndexOf("/Assets/");
        if (i >= 0) return full.Substring(i + 1);
        return full.EndsWith("/Assets") ? "Assets" : full;
    }

    static string JoinAsset(string npcsAssetDir, string rel)
    {
        // npcsAssetDir = Assets/.../NPCs ; rel like "Unity/npcs_posed.fbx" or "Unity/Anim/1.fbx"
        return (npcsAssetDir + "/" + rel).Replace("\\", "/").Replace("//", "/");
    }

    static void EditorSceneManagerMarkDirty()
    {
        var s = UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene();
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(s);
    }
}
#endif
