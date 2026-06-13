ROSE map -> FBX bundle
======================

Contents:
  <zone>.fbx              the whole map (terrain + objects + collision), one FBX.
  Textures/               every texture as PNG (deduplicated).
  materials.json          manifest: material name -> texture + alpha mode + 2-sided.
  Editor/                 Unity editor scripts (assign materials, place animations).
  Shaders/                custom URP shader (ROSE/URP/Lit) the materials use.
  RoseAnimationSpeed.cs   runtime script to scale an animated object's playback speed.
  Animations/             vertex-animated objects (waving banners, streaming water)
                          as FBX clips + animations.json (the placement manifest).
  UE5/                    UE5 editor script that auto-assigns materials.

Materials are NOT baked into the FBX on purpose — the material SLOTS are named
(M<index>_<texture>) and the editor scripts wire the textures to them for you.

--------------------------------------------------------------------------
UNITY (Universal Render Pipeline)
--------------------------------------------------------------------------
Requires a URP project (the shader targets URP). Built-in pipeline falls back
to a stock shader automatically.

1. Copy this whole folder into your project's  Assets/  (e.g. Assets/ROSE/).
   Unity imports the FBX, textures, the Editor script and the URP shader.
   (The Editor/ and Shaders/ folder names matter — keep them.)
2. Wait for scripts + shaders to compile.
3. Menu:  ROSE > Assign Materials
   -> builds a material per slot using the custom shader  ROSE/URP/Lit,
      wires the texture, sets opaque / cutout / transparent + two-sided, and
      remaps the FBX material slots. Drag the FBX into your scene.
4. (Optional) Menu:  ROSE > Apply Sky
   -> creates a ROSE/Skybox material (soft blue gradient + drifting clouds)
      and sets it as the scene's Environment skybox.
5. (Optional) Animations — waving banners + streaming water:
   With the map in your scene, menu:  ROSE > Animate Map Objects
   -> for every static banner/water in the map it drops the matching animated
      mesh on top (perfectly aligned — it reuses the static placement), wires a
      looping Animator + a RoseAnimationSpeed component, and hides the static one.
      Adjust speed via the RoseAnimationSpeed component (1 = normal, 0 = paused).
      Revert with  ROSE > Remove Animated Objects.
   If an animated mesh comes in rotated, open Editor/RoseAnimatedObjects.cs and
   tweak EXTRA_EULER (e.g. (90,0,0)), then re-run.

--------------------------------------------------------------------------
UNREAL ENGINE 5
--------------------------------------------------------------------------
1. Drag  <zone>.fbx  into the Content Browser to import it (accept defaults;
   tick Build Nanite for performance).
2. Enable the Python plugin if needed (Edit > Plugins > Python Editor Script).
3. Tools > Execute Python Script... >  UE5/assign_rose_materials_ue.py
   -> imports the textures, builds a Material per slot, and assigns them to the
      imported static meshes by slot name.

--------------------------------------------------------------------------
NOTES
--------------------------------------------------------------------------
- Axis/scale: exported Y-up for Unity/Maya. If it comes in rotated or huge,
  adjust the import scale / rotation on the asset (FBX scale conventions vary).
- The FBX carries a 2nd UV set (lightmap UVs) + normals.
- Collision is included as visual geometry; set it up per engine if you want
  real physics collision (UE: Use Complex Collision As Simple).
