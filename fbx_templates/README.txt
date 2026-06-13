ROSE map -> FBX bundle
======================

Contents:
  <zone>.fbx              the whole map (terrain + objects + collision), one FBX.
  Textures/               every texture as PNG (deduplicated).
  materials.json          manifest: material name -> texture + alpha mode + 2-sided.
  UnityEditor/            Unity editor script that auto-assigns materials.
  UE5/                    UE5 editor script that auto-assigns materials.

Materials are NOT baked into the FBX on purpose — the material SLOTS are named
(M<index>_<texture>) and the editor scripts wire the textures to them for you.

--------------------------------------------------------------------------
UNITY
--------------------------------------------------------------------------
1. Copy this whole folder into your project's  Assets/  (e.g. Assets/ROSE/).
   Unity imports the FBX + textures.
2. Put  UnityEditor/AssignRoseMaterials.cs  anywhere under  Assets/  (it's an
   Editor script). Wait for it to compile.
3. Menu:  ROSE > Assign Materials
   -> builds a material per slot (URP Lit or Standard, auto-detected), points
      it at the right texture, sets cutout/transparent, and remaps the FBX
      material slots. Drag the FBX into your scene.

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
