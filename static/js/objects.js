// Object rendering — OBJECT + CNST (ZSC-backed) and MORPH (STB-backed) lumps,
// drawn as InstancedMesh batches keyed by (mesh,material). Each instance keeps
// a pick {tile, lump, idx} so selection maps straight back to the IFO record.
import * as THREE from 'three';
import { scene, onFrame } from './scene.js';
import { getMesh, texUrl, getAnim } from './api.js';

// MORPH vertex animations (banners waving, water streams). Each animated MORPH
// mesh shares one geometry across its instances, so updating the geometry's
// positions animates every placement in sync (as ROSE does).
const morphAnims = [];     // { geom, anim, start }
export let morphSpeed = 1.0;
export function setMorphSpeed(s) { morphSpeed = s; }

function registerMorphAnim(geom, anim) {
  geom.attributes.position.setUsage(THREE.DynamicDrawUsage);
  morphAnims.push({ geom, anim, start: performance.now() });
}

onFrame(() => {
  if (!morphAnims.length) return;
  const now = performance.now();
  for (const m of morphAnims) {
    const { anim, geom } = m;
    const t = (now - m.start) / 1000 * anim.fps * morphSpeed;
    const f0 = ((Math.floor(t) % anim.frames) + anim.frames) % anim.frames;
    const f1 = (f0 + 1) % anim.frames;
    const frac = t - Math.floor(t);
    const V3 = anim.nverts * 3, P = anim.positions, o0 = f0 * V3, o1 = f1 * V3;
    const pos = geom.attributes.position.array;
    for (let i = 0; i < V3; i++) pos[i] = P[o0 + i] * (1 - frac) + P[o1 + i] * frac;
    geom.attributes.position.needsUpdate = true;
    geom.computeVertexNormals();
  }
});

export const objectsGroup = new THREE.Group();
objectsGroup.name = 'objects';
scene.add(objectsGroup);

// NPCs (MOB lump) render as real composed meshes in their own group so the
// "NPCs (MOB)" layer toggle can show/hide them independently of static objects.
export const npcGroup = new THREE.Group();
npcGroup.name = 'npcs';
scene.add(npcGroup);

// REGEN spawn points render the monster variety they spawn, clustered in a ring
// around the point — controlled by the "Spawns (REGEN)" layer toggle.
export const spawnGroup = new THREE.Group();
spawnGroup.name = 'spawns';
scene.add(spawnGroup);

const ZSC_LUMPS = ['OBJECT', 'CNST'];
const texLoader = new THREE.TextureLoader();
const TEX = new Map();
let texturesHidden = false;

function loadTex(path, alpha) {
  const key = `${path}|${alpha ? 'a' : 'rgb'}`;
  if (TEX.has(key)) return TEX.get(key);
  const tex = texLoader.load(texUrl(path, alpha));
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  // ROSE meshes author UVs in DirectX convention (V origin top-left). three.js
  // defaults to flipY=true (OpenGL, V-up), which vertically flips every texture
  // — invisible on tiling textures but on an atlas it swaps regions (stone vs
  // floor-tile), painting the "carpet" onto walls/ceiling. flipY=false makes
  // the authored UVs sample the correct region.
  tex.flipY = false;
  TEX.set(key, tex);
  return tex;
}

function ifoMatrix(o, m) {
  const p = o.pos, r = o.rot, s = o.scale;
  m.compose(new THREE.Vector3(p[0], p[1], p[2]),
            new THREE.Quaternion(r[0], r[1], r[2], r[3]),
            new THREE.Vector3(s[0], s[1], s[2]));
  return m;
}
function partMatrix(part, m) {
  const p = part.pos, r = part.rot, s = part.scl;   // ZSC rot = (w,x,y,z)
  m.compose(new THREE.Vector3(p[0], p[1], p[2]),
            new THREE.Quaternion(r[1], r[2], r[3], r[0]),
            new THREE.Vector3(s[0], s[1], s[2]));
  return m;
}

// Build one batch dict: key -> {mesh, mat, flags, matrices[], picks[]}
function collectBatches(zone, packs) {
  const byKey = new Map();
  const ifoMat = new THREE.Matrix4(), localMat = new THREE.Matrix4();

  for (const t of zone.tiles) {
    const lumps = t.ifo.lumps;
    for (const lumpName of ZSC_LUMPS) {
      const recs = lumps[lumpName];
      const pack = packs[lumpName];
      if (!recs || !pack || !pack.models) continue;
      for (const rec of recs) {
        const model = pack.models[rec.object_id];
        if (!model || !model.parts) continue;
        ifoMatrix(rec, ifoMat);
        const world = new Array(model.parts.length);
        for (let i = 0; i < model.parts.length; i++) {
          const part = model.parts[i];
          partMatrix(part, localMat);
          const out = new THREE.Matrix4();
          if (part.parent < 0 || part.parent >= i || !world[part.parent])
            out.multiplyMatrices(ifoMat, localMat);
          else
            out.multiplyMatrices(world[part.parent], localMat);
          world[i] = out;
          if (!part.mesh) continue;
          const key = `${part.mesh}|${part.mat || ''}`;
          let b = byKey.get(key);
          if (!b) { b = { mesh: part.mesh, mat: part.mat, flags: part.flags || {}, matrices: [], picks: [] }; byKey.set(key, b); }
          b.matrices.push(out);
          b.picks.push({ tile: [t.x, t.y], lump: lumpName, idx: rec.idx });
        }
      }
    }
    // MOB — NPCs composed from LIST_NPC.CHR -> PART_NPC.ZSC models (head+body…),
    // walked exactly like a ZSC model. Routed to npcGroup via the isNpc flag.
    const mob = lumps['MOB'];
    const npcPack = packs.NPC;
    if (mob && npcPack && npcPack.models) {
      for (const rec of mob) {
        const model = npcPack.models[rec.object_id];
        if (!model || !model.parts) continue;
        ifoMatrix(rec, ifoMat);
        const world = new Array(model.parts.length);
        for (let i = 0; i < model.parts.length; i++) {
          const part = model.parts[i];
          partMatrix(part, localMat);
          const out = new THREE.Matrix4();
          if (part.parent < 0 || part.parent >= i || !world[part.parent])
            out.multiplyMatrices(ifoMat, localMat);
          else
            out.multiplyMatrices(world[part.parent], localMat);
          world[i] = out;
          if (!part.mesh) continue;
          const key = `NPC|${part.mesh}|${part.mat || ''}`;
          let b = byKey.get(key);
          if (!b) { b = { mesh: part.mesh, mat: part.mat, flags: part.flags || {}, matrices: [], picks: [], isNpc: true }; byKey.set(key, b); }
          b.matrices.push(out);
          b.picks.push({ tile: [t.x, t.y], lump: 'MOB', idx: rec.idx });
        }
      }
    }

    // REGEN — monster spawn points. Draw each distinct monster the point spawns
    // (basic + tactics) once, arranged in a ring around the spawn centre.
    const regen = lumps['REGEN'];
    if (regen && npcPack && npcPack.models) {
      const spawnQ = new THREE.Quaternion(), up = new THREE.Vector3(0, 0, 1);
      for (const rec of regen) {
        const e = rec.extra || {};
        const seen = new Set(), mobs = [];
        for (const list of [e.basic, e.tactics])
          for (const m of (list || []))
            if (m && m.mob_id != null && !seen.has(m.mob_id) && npcPack.models[m.mob_id]) {
              seen.add(m.mob_id); mobs.push(m.mob_id);
            }
        const n = Math.min(mobs.length, 8);
        const R = Math.max(800, n * 280);
        for (let k = 0; k < n; k++) {
          const model = npcPack.models[mobs[k]];
          const ang = n > 1 ? (k / n) * Math.PI * 2 : 0;
          const ox = n > 1 ? Math.cos(ang) * R : 0, oy = n > 1 ? Math.sin(ang) * R : 0;
          spawnQ.setFromAxisAngle(up, ang + Math.PI);      // face the centre
          ifoMat.compose(new THREE.Vector3(rec.pos[0] + ox, rec.pos[1] + oy, rec.pos[2]),
                         spawnQ, new THREE.Vector3(1, 1, 1));
          const world = new Array(model.parts.length);
          for (let i = 0; i < model.parts.length; i++) {
            const part = model.parts[i];
            partMatrix(part, localMat);
            const out = new THREE.Matrix4();
            if (part.parent < 0 || part.parent >= i || !world[part.parent])
              out.multiplyMatrices(ifoMat, localMat);
            else
              out.multiplyMatrices(world[part.parent], localMat);
            world[i] = out;
            if (!part.mesh) continue;
            const key = `SPAWN|${part.mesh}|${part.mat || ''}`;
            let b = byKey.get(key);
            if (!b) { b = { mesh: part.mesh, mat: part.mat, flags: part.flags || {}, matrices: [], picks: [], isSpawn: true }; byKey.set(key, b); }
            b.matrices.push(out);
            b.picks.push({ tile: [t.x, t.y], lump: 'REGEN', idx: rec.idx });
          }
        }
      }
    }

    // MORPH — flat STB lookup, single mesh per record.
    const morph = lumps['MORPH'];
    const rows = packs.MORPH && packs.MORPH.rows;
    if (morph && rows) {
      for (const rec of morph) {
        const row = rows[rec.object_id];
        if (!row || !row.mesh) continue;
        const out = ifoMatrix(rec, new THREE.Matrix4());
        const key = `MORPH|${row.mesh}|${row.mat || ''}`;
        let b = byKey.get(key);
        if (!b) { b = { mesh: row.mesh, mat: row.mat, flags: { alpha_test: true, two_side: true }, matrices: [], picks: [], morphMot: row.mot }; byKey.set(key, b); }
        b.matrices.push(out);
        b.picks.push({ tile: [t.x, t.y], lump: 'MORPH', idx: rec.idx });
      }
    }
  }
  return byKey;
}

export async function buildObjects(zone, packs) {
  clearObjects();
  const batches = collectBatches(zone, packs);
  const box = new THREE.Box3();
  await Promise.all([...batches.values()].map(async (b) => {
    let geo;
    try { geo = await buildGeometry(b.mesh); } catch { return; }
    const flags = b.flags || {};
    const wantAlpha = !!(flags.alpha_test || flags.alpha);
    const side = (flags.two_side || wantAlpha) ? THREE.DoubleSide : THREE.FrontSide;
    let mat;
    if (b.mat) {
      const tex = loadTex(b.mat, wantAlpha);
      mat = new THREE.MeshLambertMaterial({
        map: texturesHidden ? null : tex, color: texturesHidden ? 0xcfcfcf : 0xffffff,
        side, alphaTest: flags.alpha_test ? (flags.alpha_ref ?? 128) / 255 : 0,
      });
      mat.userData.tex = tex;
    } else {
      mat = new THREE.MeshLambertMaterial({ color: 0xbcbcbc, side });
    }
    const inst = new THREE.InstancedMesh(geo, mat, b.matrices.length);
    for (let i = 0; i < b.matrices.length; i++) inst.setMatrixAt(i, b.matrices[i]);
    inst.instanceMatrix.needsUpdate = true;
    inst.userData = { kind: 'object', picks: b.picks };
    inst.frustumCulled = false;
    (b.isNpc ? npcGroup : b.isSpawn ? spawnGroup : objectsGroup).add(inst);
    geo.computeBoundingBox();
    box.union(geo.boundingBox);
    // MORPH with a motion clip -> play its vertex animation on this geometry.
    if (b.morphMot) {
      getAnim(b.morphMot, b.mesh).then(anim => { if (anim) registerMorphAnim(geo, anim); }).catch(() => {});
    }
  }));
  return box;
}

async function buildGeometry(meshPath) {
  const d = await getMesh(meshPath);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(d.positions, 3));
  if (d.normals) g.setAttribute('normal', new THREE.BufferAttribute(d.normals, 3));
  if (d.hasUV) g.setAttribute('uv', new THREE.BufferAttribute(d.uvs, 2));
  g.setIndex(new THREE.BufferAttribute(d.indices, 1));
  if (!d.normals) g.computeVertexNormals();
  return g;
}

export function clearObjects() {
  morphAnims.length = 0;
  for (const g of [objectsGroup, npcGroup, spawnGroup])
    for (const m of [...g.children]) {
      g.remove(m);
      m.geometry?.dispose();
      m.material?.dispose();
    }
}

// Build one material for a part (shared by instanced render + previews).
export function makePartMaterial(matPath, flags = {}) {
  const wantAlpha = !!(flags.alpha_test || flags.alpha);
  const side = (flags.two_side || wantAlpha) ? THREE.DoubleSide : THREE.FrontSide;
  if (matPath) {
    const tex = loadTex(matPath, wantAlpha);
    const mat = new THREE.MeshLambertMaterial({
      map: texturesHidden ? null : tex, color: texturesHidden ? 0xcfcfcf : 0xffffff,
      side, alphaTest: flags.alpha_test ? (flags.alpha_ref ?? 128) / 255 : 0,
    });
    mat.userData.tex = tex;
    return mat;
  }
  return new THREE.MeshLambertMaterial({ color: 0xbcbcbc, side });
}

// Build a single model (from a /api/pack or /api/packs dict) as a THREE.Group
// at the model's origin, textured. Used for the browser preview and for
// placing new objects in the scene.
export async function buildModelGroup(pack, modelIndex) {
  const group = new THREE.Group();
  const model = pack.models && pack.models[modelIndex];
  if (!model || !model.parts) return group;
  const world = new Array(model.parts.length);
  const localMat = new THREE.Matrix4();
  const box = new THREE.Box3();
  for (let i = 0; i < model.parts.length; i++) {
    const part = model.parts[i];
    partMatrix(part, localMat);
    const out = new THREE.Matrix4();
    if (part.parent < 0 || part.parent >= i || !world[part.parent]) out.copy(localMat);
    else out.multiplyMatrices(world[part.parent], localMat);
    world[i] = out;
    if (!part.mesh) continue;
    let geo;
    try { geo = await buildGeometry(part.mesh); } catch { continue; }
    const mesh = new THREE.Mesh(geo, makePartMaterial(part.mat, part.flags || {}));
    mesh.applyMatrix4(out);
    group.add(mesh);
    geo.computeBoundingBox();
    box.union(geo.boundingBox.clone().applyMatrix4(out));
  }
  group.userData.bbox = box;
  return group;
}

export function setTexturesHidden(hide) {
  texturesHidden = hide;
  objectsGroup.traverse(o => {
    const mat = o.material;
    if (!mat || !('map' in mat)) return;
    if (hide) { mat.map = null; mat.color.setHex(0xcfcfcf); }
    else if (mat.userData.tex) { mat.map = mat.userData.tex; mat.color.setHex(0xffffff); }
    mat.needsUpdate = true;
  });
}
