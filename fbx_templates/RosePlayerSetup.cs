// Editor: drop a controllable camera onto the map.
//
//   ROSE > Add Fly Camera (Spawn on Map)
//     - finds the map, raycasts onto the terrain near its centre,
//     - moves the Main Camera there with a RoseFlyCamera controller,
//     - fixes the far-clip plane (the map is kilometres across),
//     - drops a "ROSE_PlayerStart" marker at the spawn.
//   Press Play: hold RIGHT mouse to look, WASD to move, Q/E down/up, Shift faster.
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class RosePlayerSetup
{
    [MenuItem("ROSE/Add Fly Camera (Spawn on Map)")]
    static void AddFlyCamera()
    {
        var mapRoot = FindMapRoot();
        if (mapRoot == null)
        {
            EditorUtility.DisplayDialog("ROSE", "No map found in the open scene. Open Assets/Scenes/ROSE_Map first.", "OK");
            return;
        }

        Bounds b = ComputeBounds(mapRoot);

        // spawn: raycast straight down onto the terrain near the map centre
        Vector3 spawn;
        Vector3 top = new Vector3(b.center.x, b.max.y + 1000f, b.center.z);
        if (IsFinite(top) && Physics.Raycast(top, Vector3.down, out RaycastHit hit, b.size.y + 5000f) && IsFinite(hit.point))
            spawn = hit.point + Vector3.up * 25f;
        else
            spawn = b.center + Vector3.up * (b.size.y * 0.5f + 50f);
        if (!IsFinite(spawn)) spawn = b.center + Vector3.up * 50f;   // last-resort guards
        if (!IsFinite(spawn)) spawn = Vector3.up * 50f;

        // reuse the existing Main Camera (avoids duplicate AudioListeners)
        var cam = Camera.main;
        if (cam == null) cam = Object.FindObjectsByType<Camera>(FindObjectsSortMode.None).FirstOrDefault();
        if (cam == null)
        {
            var go = new GameObject("Main Camera") { tag = "MainCamera" };
            cam = go.AddComponent<Camera>();
            go.AddComponent<AudioListener>();
        }

        cam.gameObject.name = "ROSE Player (Fly Camera)";
        cam.transform.position = spawn;
        cam.transform.rotation = Quaternion.Euler(20f, 0f, 0f);      // look slightly down
        cam.nearClipPlane = 0.3f;
        cam.farClipPlane = Mathf.Max(20000f, b.size.magnitude * 1.5f);
        if (!cam.TryGetComponent<RoseFlyCamera>(out _)) cam.gameObject.AddComponent<RoseFlyCamera>();

        var marker = GameObject.Find("ROSE_PlayerStart") ?? new GameObject("ROSE_PlayerStart");
        marker.transform.position = spawn;

        Selection.activeGameObject = cam.gameObject;
        if (!Application.isPlaying)      // editor-only ops — skip while in Play mode
        {
            if (SceneView.lastActiveSceneView != null) SceneView.lastActiveSceneView.FrameSelected();
            EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        }

        EditorUtility.DisplayDialog("ROSE — Fly Camera",
            "Camera placed on the map at\n" + spawn.ToString("F0") + "\n\n" +
            "Press Play, then:\n" +
            "• Hold RIGHT mouse = look\n" +
            "• W A S D = move,  Q/E (or Ctrl/Space) = down/up\n" +
            "• Left Shift = faster\n\n" +
            "Ctrl+S to save the scene with the camera.", "OK");
    }

    [MenuItem("ROSE/Add Cube Player (Spawn on Map)")]
    static void AddCubePlayer()
    {
        var mapRoot = FindMapRoot();
        if (mapRoot == null)
        {
            EditorUtility.DisplayDialog("ROSE", "No map found in the open scene. Open Assets/Scenes/ROSE_Map first.", "OK");
            return;
        }

        var prev = GameObject.Find("ROSE Cube Player");      // idempotent: replace a previous cube
        if (prev != null) Object.DestroyImmediate(prev);

        // 1. rebuild the effects FIRST so the fountain/lights are freshly placed
        //    with the current ROSE->Unity bake (else we'd anchor on a stale one).
        int fx = 0;
        try { fx = RoseEffects.BuildEffectsCore(); } catch (System.Exception e) { Debug.LogWarning("[ROSE] effects: " + e.Message); }

        // 2. the VISIBLE map geometry (mesh renderers) is the source of truth for
        //    where/how big the map actually is.
        Bounds b = ComputeBounds(mapRoot);
        float span = Mathf.Max(b.size.x, b.size.z);
        if (span <= 0.01f || float.IsNaN(span) || float.IsInfinity(span)) span = 100f;
        float cubeSize = Mathf.Clamp(span * 0.004f, 0.5f, span);

        // 3. stand on a REAL piece of map geometry near the centre (building /
        //    tree / terrain tile) — guarantees the cube lands ON the visible map.
        Renderer pick = null; float best = float.MaxValue;
        Vector2 c2 = new Vector2(b.center.x, b.center.z);
        foreach (var r in mapRoot.GetComponentsInChildren<MeshRenderer>(true))
        {
            if (r == null || !IsFinite(r.bounds.center) || !IsFinite(r.bounds.size) || r.bounds.size.sqrMagnitude <= 0f) continue;
            float d = (new Vector2(r.bounds.center.x, r.bounds.center.z) - c2).sqrMagnitude;
            if (d < best) { best = d; pick = r; }
        }
        Vector3 anchor = pick != null ? pick.bounds.center : b.center;
        float standY = pick != null ? pick.bounds.max.y : b.center.y;   // top of the picked object

        // 4. snap onto whatever surface is under the anchor (else the picked top)
        Vector3 spawn;
        if (Physics.Raycast(new Vector3(anchor.x, b.max.y + span, anchor.z), Vector3.down, out RaycastHit hit, span * 3f) && IsFinite(hit.point))
            spawn = hit.point + Vector3.up * cubeSize;
        else
            spawn = new Vector3(anchor.x, standY + cubeSize, anchor.z);
        if (!IsFinite(spawn)) spawn = IsFinite(b.center) ? b.center : Vector3.zero;

        Debug.Log($"[ROSE] map center={b.center:F0} size={b.size:F0} | stand-on='"
            + (pick != null ? pick.name : "none") + $"' @ {anchor:F0} | spawn={spawn:F0} cubeSize={cubeSize:F2} fx={fx}");

        var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.name = "ROSE Cube Player";
        cube.transform.localScale = Vector3.one * cubeSize;
        cube.transform.position = spawn;

        var mr = cube.GetComponent<MeshRenderer>();
        var sh = Shader.Find("Universal Render Pipeline/Lit");
        if (sh != null && mr != null)
        {
            var m = new Material(sh) { color = new Color(1f, 0.35f, 0.1f) };
            m.EnableKeyword("_EMISSION");
            m.SetColor("_EmissionColor", new Color(1f, 0.3f, 0.05f) * 1.5f);
            mr.sharedMaterial = m;
        }
        var box = cube.GetComponent<BoxCollider>();
        if (box != null) Object.DestroyImmediate(box);   // kinematic mover; no collider needed

        var cam = Camera.main ?? Object.FindObjectsByType<Camera>(FindObjectsSortMode.None).FirstOrDefault();
        if (cam == null)
        {
            var go = new GameObject("Main Camera") { tag = "MainCamera" };
            cam = go.AddComponent<Camera>(); go.AddComponent<AudioListener>();
        }
        if (cam.TryGetComponent<RoseFlyCamera>(out var fly)) Object.DestroyImmediate(fly);
        Vector3 camOffset = new Vector3(0f, cubeSize * 5f, -cubeSize * 10f);
        cam.nearClipPlane = Mathf.Max(0.05f, cubeSize * 0.05f);
        cam.farClipPlane = Mathf.Max(5000f, span * 3f);
        cam.transform.position = spawn + camOffset;
        cam.transform.LookAt(spawn);

        var player = cube.AddComponent<RoseCubePlayer>();
        player.followCam = cam;
        player.camOffset = camOffset;
        player.moveSpeed = Mathf.Max(5f, span * 0.08f);
        player.groundOffset = cubeSize * 0.5f;
        player.rayUp = cubeSize * 3f;     // start the ground ray JUST above the cube so it
        player.rayDown = span;            // doesn't catch arch/roof tops overhead (the float bug)

        Selection.activeGameObject = cube;
        if (!Application.isPlaying)
        {
            if (SceneView.lastActiveSceneView != null) SceneView.lastActiveSceneView.FrameSelected();
            EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        }
        EditorUtility.DisplayDialog("ROSE — Cube Player",
            $"Rebuilt {fx} effect system(s).\n" +
            "Cube placed on the map" + (pick != null ? " (on '" + pick.name + "')" : "") + " at\n"
            + spawn.ToString("F0") + "\n\n" +
            "Press Play:\n• W A S D = move • Shift = sprint\n• Z = teleport to fountain\n\n" +
            "Camera follows the cube. Ctrl+S to save.", "OK");
    }

    static GameObject FindMapRoot()
    {
        foreach (var root in SceneManager.GetActiveScene().GetRootGameObjects())
            if (root.GetComponentsInChildren<Transform>(true).Any(t => t != null && (t.name.Contains("MORPH__") || t.name == "Terrain")))
                return root;
        return null;
    }

    // Mesh renderers only, finite-checked — particle/skinned renderers can report
    // NaN/huge bounds in the editor and poison the whole encapsulation.
    static Bounds ComputeBounds(GameObject root)
    {
        bool has = false; Bounds b = new Bounds();
        foreach (var r in root.GetComponentsInChildren<Renderer>(true))
        {
            if (!(r is MeshRenderer)) continue;
            var rb = r.bounds;
            if (!IsFinite(rb.center) || !IsFinite(rb.size) || rb.size.sqrMagnitude <= 0f) continue;
            if (!has) { b = rb; has = true; } else b.Encapsulate(rb);
        }
        if (!has) b = new Bounds(root.transform.position, Vector3.one * 100f);
        return b;
    }

    static bool IsFinite(Vector3 v) =>
        !(float.IsNaN(v.x) || float.IsNaN(v.y) || float.IsNaN(v.z) ||
          float.IsInfinity(v.x) || float.IsInfinity(v.y) || float.IsInfinity(v.z));
}
