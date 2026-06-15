// Editor: build the map's particle effects as Unity ParticleSystems.
//
//   Menu  ROSE > Build Effects   reads Effects/effects.json (exported alongside
//   the map) and, for every EFFECT placement, spawns a ParticleSystem whose
//   modules mirror the game's .PTL emitter (lifetime, emit box, velocity,
//   gravity, size, colour, additive blend + texture). Fountains get the same
//   synthetic water as the web viewer: jet + petal cascade + mist + a flat pool.
//
// Placement: everything is parented under the map's "Objects" group (the node
// that carries the z-up->y-up + 0.01 scale), with ROSE-local coords, so the
// systems land exactly where the game puts them. Simulation is Local with
// Hierarchy scaling so sizes/speeds inherit the 0.01 map scale automatically.
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

public static class RoseEffects
{
    [System.Serializable] class Emitter
    {
        public string texture; public float[] life; public float[] emit_rate;
        public float[] emit_radius; public float[] gravity; public int num_particles;
        public float[] size; public float[] alpha; public float[] color; public float[] vel;
        public bool additive;
    }
    [System.Serializable] class Placement { public float[] pos; public float[] rot; public float[] scale; public Emitter[] emitters; }
    [System.Serializable] class Fountain { public float[] pos; public float scale; }
    [System.Serializable] class Manifest { public string zone; public string soft_sprite; public Placement[] placements; public Fountain[] fountains; }

    const float SIZE_SCALE = 9f;   // matches the web viewer's PTL-size -> sprite-size

    [MenuItem("ROSE/Build Effects")]
    static void BuildEffectsMenu()
    {
        int n = BuildEffectsCore();
        EditorUtility.DisplayDialog("ROSE",
            n > 0 ? $"Built {n} effect system(s) (particles + fountain water)."
                  : "effects.json not found, or no map in the scene. Import the bundle and run ROSE > Setup Scene first.",
            "OK");
    }

    // Returns the number of systems built. Safe to call from Setup Scene / batch.
    public static int BuildEffectsCore()
    {
        string jsonPath = AssetDatabase.FindAssets("effects t:TextAsset")
            .Select(AssetDatabase.GUIDToAssetPath).FirstOrDefault(p => p.EndsWith("/effects.json"));
        if (jsonPath == null) { Debug.LogWarning("[ROSE] effects.json not found"); return 0; }
        string root = Path.GetDirectoryName(jsonPath).Replace("\\", "/");
        var man = JsonUtility.FromJson<Manifest>(File.ReadAllText(jsonPath));
        if (man == null) return 0;

        if (FindEffectsParent() == null) { Debug.LogWarning("[ROSE] no map in scene for effects"); return 0; }

        // Fresh container at the SCENE ROOT (not under a map group) carrying the
        // ROSE(cm, Z-up, RH) -> Unity(m, Y-up, LH) WORLD bake: (x,y,z) -> (-x, z, y)
        // * 0.01, i.e. uniform 0.01 scale + a 180° flip about (0,1,1). Rooting it
        // makes placement independent of any group's own transform (the MORPH /
        // banner group has a different transform than the prop meshes — parenting
        // there is what threw the particles off into space). Children get RAW ROSE
        // coords and land exactly on the visible map.
        var oldGo = GameObject.Find("ROSE_Effects");
        if (oldGo != null) Object.DestroyImmediate(oldGo);
        var container = new GameObject("ROSE_Effects");
        container.transform.SetParent(null, false);
        container.transform.localPosition = Vector3.zero;
        container.transform.localRotation = Quaternion.AngleAxis(180f, new Vector3(0f, 1f, 1f).normalized);
        container.transform.localScale = Vector3.one * 0.01f;
        Undo.RegisterCreatedObjectUndo(container, "ROSE Build Effects");

        int count = 0;
        if (man.placements != null)
            foreach (var pl in man.placements)
            {
                if (pl.emitters == null) continue;
                foreach (var em in pl.emitters)
                {
                    if (string.IsNullOrEmpty(em.texture)) continue;
                    var go = NewParticle(em, root);
                    go.transform.SetParent(container.transform, false);
                    go.transform.localPosition = V(pl.pos);
                    go.transform.localRotation = Q(pl.rot);     // aims directional FX (e.g. fountain jets)
                    count++;
                }
            }
        if (man.fountains != null)
            foreach (var f in man.fountains) { BuildFountain(f, container.transform, root); count++; }

        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        var fp = GameObject.Find("FountainPool");
        Debug.Log($"[ROSE] built {count} effect system(s). FountainPool world="
            + (fp != null ? fp.transform.position.ToString("F0") : "none")
            + " (should match the fountain mesh, e.g. ~ -5514, 0, 5239)");
        return count;
    }

    // ----------------------------------------------------------------- placement
    // Parent effects under the map's "Objects" group (same local space as the
    // placed props: raw ROSE coords). Found robustly via any MORPH__ node's
    // parent, else the Terrain group's parent (ROSE_zone), else the map root.
    static Transform FindEffectsParent()
    {
        Transform morph = null, terrain = null, mapRoot = null;
        foreach (var rootGo in SceneManager.GetActiveScene().GetRootGameObjects())
            foreach (var t in rootGo.GetComponentsInChildren<Transform>(true))
            {
                if (t == null) continue;
                if (morph == null && t.name.Contains("MORPH__")) morph = t;
                if (terrain == null && t.name == "Terrain") terrain = t;
                if (mapRoot == null && (t.name.Contains("MORPH__") || t.name == "Terrain"))
                    mapRoot = rootGo.transform;
            }
        if (morph != null && morph.parent != null) return morph.parent;       // "Objects"
        if (terrain != null) return terrain.parent != null ? terrain.parent : terrain;  // "ROSE_zone"
        return mapRoot;
    }

    static Vector3 V(float[] a) => (a != null && a.Length >= 3) ? new Vector3(a[0], a[1], a[2]) : Vector3.zero;
    static Quaternion Q(float[] a) => (a != null && a.Length >= 4) ? new Quaternion(a[0], a[1], a[2], a[3]) : Quaternion.identity;

    // ------------------------------------------------------------- PTL -> system
    static GameObject NewParticle(Emitter em, string root)
    {
        var go = new GameObject("FX");
        var ps = go.AddComponent<ParticleSystem>();
        ps.Stop();

        var main = ps.main;
        main.simulationSpace = ParticleSystemSimulationSpace.Local;
        main.scalingMode = ParticleSystemScalingMode.Hierarchy;       // inherit the map's scale
        main.playOnAwake = true;
        float life = (em.life != null && em.life.Length > 1) ? Mathf.Max(0.4f, em.life[1] / 30f) : 1.5f;
        main.startLifetime = life;
        main.startSpeed = 0f;                                         // motion comes from velocity module
        // PTL sizes are in ROSE units; many ambient FX use size 0 ("texture's own
        // size") — fall back to 20 like the web viewer, else they're a 1-unit dot.
        float avg = (em.size != null && em.size.Length >= 4) ? Mathf.Max((em.size[0] + em.size[1]) * 0.5f, (em.size[2] + em.size[3]) * 0.5f) : 0f;
        float startSize = (avg > 0.5f ? avg : 20f) * SIZE_SCALE;
        main.startSize = startSize;
        int maxP = Mathf.Clamp(em.num_particles > 0 ? em.num_particles : 40, 1, 400);
        main.maxParticles = maxP;
        main.startColor = StartColor(em);

        var emission = ps.emission;
        emission.rateOverTime = Mathf.Max(1f, maxP / life);          // keep ~maxP alive at once

        // emit box: emit_radius = [minx,miny,minz, maxx,maxy,maxz]
        var shape = ps.shape;
        shape.enabled = true;
        shape.shapeType = ParticleSystemShapeType.Box;
        var er = em.emit_radius;
        if (er != null && er.Length >= 6)
        {
            shape.scale = new Vector3(Mathf.Abs(er[3] - er[0]), Mathf.Abs(er[4] - er[1]), Mathf.Abs(er[5] - er[2]));
            shape.position = new Vector3((er[0] + er[3]) * 0.5f, (er[1] + er[4]) * 0.5f, (er[2] + er[5]) * 0.5f);
        }

        // velocity over lifetime: vel = [minx,miny,minz, maxx,maxy,maxz] ROSE u/s
        var v = em.vel;
        if (v != null && v.Length >= 6)
        {
            var vel = ps.velocityOverLifetime;
            vel.enabled = true;
            vel.space = ParticleSystemSimulationSpace.Local;
            vel.x = new ParticleSystem.MinMaxCurve(v[0], v[3]);
            vel.y = new ParticleSystem.MinMaxCurve(v[1], v[4]);
            vel.z = new ParticleSystem.MinMaxCurve(v[2], v[5]);
        }

        // gravity (z) as a constant force
        var g = em.gravity;
        if (g != null && g.Length >= 6)
        {
            float gz = (g[2] + g[5]) * 0.5f;
            if (Mathf.Abs(gz) > 0.001f)
            {
                var force = ps.forceOverLifetime;
                force.enabled = true;
                force.space = ParticleSystemSimulationSpace.Local;
                force.z = gz;
            }
        }

        FadeOverLife(ps, 0.3f);
        var rend = go.GetComponent<ParticleSystemRenderer>();
        rend.material = ParticleMat(root + "/Textures/" + em.texture, em.additive);
        rend.renderMode = ParticleSystemRenderMode.Billboard;
        ps.Play();
        return go;
    }

    static ParticleSystem.MinMaxGradient StartColor(Emitter em)
    {
        if (em.color != null && em.color.Length >= 8)
        {
            float a0 = (em.alpha != null && em.alpha.Length > 0) ? em.alpha[0] : em.color[3];
            float a1 = (em.alpha != null && em.alpha.Length > 1) ? em.alpha[1] : em.color[7];
            var c0 = new Color(em.color[0], em.color[1], em.color[2], a0);
            var c1 = new Color(em.color[4], em.color[5], em.color[6], a1);
            if (c0.maxColorComponent <= 0.001f && c1.maxColorComponent <= 0.001f)
                return new ParticleSystem.MinMaxGradient(Color.white);   // parser default 0s
            return new ParticleSystem.MinMaxGradient(c0, c1);
        }
        return new ParticleSystem.MinMaxGradient(Color.white);
    }

    static void FadeOverLife(ParticleSystem ps, float peak)
    {
        var col = ps.colorOverLifetime;
        col.enabled = true;
        var grad = new Gradient();
        grad.SetKeys(
            new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(Color.white, 1f) },
            new[] { new GradientAlphaKey(0f, 0f), new GradientAlphaKey(1f, peak), new GradientAlphaKey(0f, 1f) });
        col.color = new ParticleSystem.MinMaxGradient(grad);
    }

    // ------------------------------------------------------- fountain basin pool
    // The jets/spray come from the real bunsudae01.eft emitters (placed at the
    // model's 6 petal-hole dummies). All we add is the standing water in the
    // basin, for which no .EFT exists — a flat translucent disc.
    static void BuildFountain(Fountain f, Transform parent, string root)
    {
        float s = f.scale <= 0f ? 1f : f.scale;
        Vector3 p = V(f.pos);
        var pool = GameObject.CreatePrimitive(PrimitiveType.Quad);
        Object.DestroyImmediate(pool.GetComponent<Collider>());
        pool.name = "FountainPool";
        pool.transform.SetParent(parent, false);
        pool.transform.localPosition = p + new Vector3(0, 0, 470f * s);  // Quad lies in XY plane, faces +Z (ROSE up)
        pool.transform.localScale = new Vector3(2f * 1850f * s, 2f * 1850f * s, 1f);
        pool.GetComponent<Renderer>().sharedMaterial = WaterDiscMat();
        Undo.RegisterCreatedObjectUndo(pool, "ROSE Fountain Pool");
    }

    // ------------------------------------------------------------------ materials
    static Material ParticleMat(string texPath, bool additive)
    {
        Texture2D tex = string.IsNullOrEmpty(texPath) ? null : AssetDatabase.LoadAssetAtPath<Texture2D>(texPath);
        Shader sh = Shader.Find("Universal Render Pipeline/Particles/Unlit");
        Material m;
        if (sh != null)
        {
            m = new Material(sh);
            m.SetFloat("_Surface", 1f);                      // transparent
            m.SetFloat("_ZWrite", 0f);
            m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            if (additive)
            {
                m.SetFloat("_Blend", 2f);
                m.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
                m.SetFloat("_DstBlend", (float)BlendMode.One);
            }
            else
            {
                m.SetFloat("_Blend", 0f);
                m.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
                m.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
            }
            if (tex != null) m.SetTexture("_BaseMap", tex);
            m.renderQueue = (int)RenderQueue.Transparent;
        }
        else
        {
            m = new Material(Shader.Find("Sprites/Default"));
            if (tex != null) m.mainTexture = tex;
        }
        return m;
    }

    static Material WaterDiscMat()
    {
        Shader sh = Shader.Find("Universal Render Pipeline/Unlit");
        Material m;
        if (sh != null)
        {
            m = new Material(sh);
            m.SetFloat("_Surface", 1f);
            m.SetFloat("_Blend", 0f);
            m.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
            m.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
            m.SetFloat("_ZWrite", 0f);
            m.SetFloat("_Cull", 0f);                          // two-sided
            m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            m.SetColor("_BaseColor", new Color(0.35f, 0.63f, 0.81f, 0.6f));
            m.renderQueue = (int)RenderQueue.Transparent;
        }
        else
        {
            m = new Material(Shader.Find("Sprites/Default"));
            m.color = new Color(0.35f, 0.63f, 0.81f, 0.6f);
        }
        return m;
    }
}
