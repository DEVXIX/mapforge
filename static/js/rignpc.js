// Rigged NPCs / monsters — build a skeleton from the zone rig, skin each body
// part to it, and play the idle (standing) animation. Replaces the static
// instanced NPC/monster render when a rig is available. Meshes + bones come in
// raw mesh units; each placement group is scaled x100 to match the world.
import * as THREE from 'three';
import { onFrame } from './scene.js';
import { npcGroup, spawnGroup } from './objects.js';
import { getSkinnedMesh, texUrl } from './api.js';

const clock = new THREE.Clock();
const mixers = [];
const geoCache = new Map();   // mesh path -> shared BufferGeometry
const texLoader = new THREE.TextureLoader();
const TEX = new Map();

onFrame(() => {
  if (!mixers.length) return;
  const dt = clock.getDelta();
  for (const m of mixers) m.update(dt);
});

function charTex(path) {
  if (TEX.has(path)) return TEX.get(path);
  const tex = texLoader.load(texUrl(path, false));   // characters are opaque (alpha is junk)
  tex.flipY = false; tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.anisotropy = 4;
  TEX.set(path, tex);
  return tex;
}

function charMaterial(matPath) {
  if (matPath) return new THREE.MeshLambertMaterial({ map: charTex(matPath), side: THREE.DoubleSide });
  return new THREE.MeshLambertMaterial({ color: 0xbcbcbc, side: THREE.DoubleSide });
}

async function partGeometry(meshPath) {
  if (geoCache.has(meshPath)) return geoCache.get(meshPath);
  const p = (async () => {
    const d = await getSkinnedMesh(meshPath);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(d.positions, 3));
    if (d.normals) g.setAttribute('normal', new THREE.BufferAttribute(d.normals, 3));
    if (d.hasUV) g.setAttribute('uv', new THREE.BufferAttribute(d.uvs, 2));
    if (d.skinIndex) g.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(d.skinIndex, 4));
    if (d.skinWeight) g.setAttribute('skinWeight', new THREE.Float32BufferAttribute(d.skinWeight, 4));
    g.setIndex(new THREE.BufferAttribute(d.indices, 1));
    if (!d.normals) g.computeVertexNormals();
    return g;
  })();
  geoCache.set(meshPath, p);
  return p;
}

function buildClip(anim) {
  const { fps, frames, rot, pos } = anim;
  const times = new Float32Array(frames);
  for (let i = 0; i < frames; i++) times[i] = i / fps;
  const tracks = [];
  rot.forEach((frs, i) => {
    if (!frs) return;
    const v = new Float32Array(frames * 4);
    for (let f = 0; f < frames; f++) { const q = frs[f]; v[f*4]=q[0]; v[f*4+1]=q[1]; v[f*4+2]=q[2]; v[f*4+3]=q[3]; }
    tracks.push(new THREE.QuaternionKeyframeTrack(`b${i}.quaternion`, times, v));
  });
  pos.forEach((frs, i) => {
    if (!frs) return;
    const v = new Float32Array(frames * 3);
    for (let f = 0; f < frames; f++) { const p = frs[f]; v[f*3]=p[0]; v[f*3+1]=p[1]; v[f*3+2]=p[2]; }
    tracks.push(new THREE.VectorKeyframeTrack(`b${i}.position`, times, v));
  });
  return new THREE.AnimationClip('idle', frames / fps, tracks);
}

// Build one placed, animated NPC/monster. `matrix` is the full world placement
// (already includes the x100 mesh scale). Returns the THREE.Group.
async function buildOne(rig, parts, matrix, pick) {
  const grp = new THREE.Group();
  const bones = rig.bones.map((b, i) => {
    const bone = new THREE.Bone();
    bone.name = 'b' + i;
    bone.position.set(b.pos[0], b.pos[1], b.pos[2]);
    bone.quaternion.set(b.rot[0], b.rot[1], b.rot[2], b.rot[3]);
    return bone;
  });
  let root = bones[0];
  rig.bones.forEach((b, i) => {
    if (b.parent >= 0 && b.parent < bones.length && b.parent !== i) bones[b.parent].add(bones[i]);
    else root = bones[i];
  });
  grp.add(root);

  // Transform the group to its final world placement FIRST, so binding captures
  // the x100 placement in bindMatrix — otherwise three.js applies it twice.
  matrix.decompose(grp.position, grp.quaternion, grp.scale);
  grp.updateMatrixWorld(true);
  const skeleton = new THREE.Skeleton(bones);   // inverse-bind from the world bind pose

  const sms = [];
  for (const part of parts) {
    if (!part.mesh) continue;
    let geo;
    try { geo = await partGeometry(part.mesh); } catch { continue; }
    const sm = new THREE.SkinnedMesh(geo, charMaterial(part.mat));
    sm.frustumCulled = false;
    sm.userData = { kind: 'object', picks: [pick] };
    grp.add(sm); sms.push(sm);
  }
  grp.updateMatrixWorld(true);
  for (const sm of sms) sm.bind(skeleton);       // bindMatrix = final world transform

  if (rig.anim && rig.anim.frames > 1) {
    const mixer = new THREE.AnimationMixer(grp);
    mixer.clipAction(buildClip(rig.anim)).play();
    mixers.push(mixer);
  }
  return grp;
}

function ifoMatrix(o) {
  return new THREE.Matrix4().compose(
    new THREE.Vector3(o.pos[0], o.pos[1], o.pos[2]),
    new THREE.Quaternion(o.rot[0], o.rot[1], o.rot[2], o.rot[3]),
    new THREE.Vector3(o.scale[0], o.scale[1], o.scale[2]));
}
const S100 = new THREE.Matrix4().makeScale(100, 100, 100);

export async function buildRiggedNpcs(zone, packs, rig) {
  clearRigged();
  if (!rig || !packs || !packs.NPC || !packs.NPC.models) return;
  const models = packs.NPC.models;
  const jobs = [];

  for (const t of zone.tiles) {
    const lumps = t.ifo.lumps;
    // MOB — one NPC at the record transform
    for (const rec of (lumps.MOB || [])) {
      const r = rig[rec.object_id], parts = models[rec.object_id] && models[rec.object_id].parts;
      if (!r || !parts) continue;
      const m = ifoMatrix(rec).multiply(S100);
      jobs.push(buildOne(r, parts, m, { tile: [t.x, t.y], lump: 'MOB', idx: rec.idx })
        .then(g => npcGroup.add(g)));
    }
    // REGEN — the monsters it spawns, in a ring around the point
    for (const rec of (lumps.REGEN || [])) {
      const e = rec.extra || {};
      const seen = new Set(), mobs = [];
      for (const list of [e.basic, e.tactics])
        for (const mb of (list || []))
          if (mb && mb.mob_id != null && !seen.has(mb.mob_id) && rig[mb.mob_id] && models[mb.mob_id]) {
            seen.add(mb.mob_id); mobs.push(mb.mob_id);
          }
      const n = Math.min(mobs.length, 8), R = Math.max(800, n * 280);
      for (let k = 0; k < n; k++) {
        const id = mobs[k], parts = models[id].parts;
        const ang = n > 1 ? (k / n) * Math.PI * 2 : 0;
        const ox = n > 1 ? Math.cos(ang) * R : 0, oy = n > 1 ? Math.sin(ang) * R : 0;
        const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), ang + Math.PI);
        const m = new THREE.Matrix4().compose(
          new THREE.Vector3(rec.pos[0] + ox, rec.pos[1] + oy, rec.pos[2]), q, new THREE.Vector3(1, 1, 1)).multiply(S100);
        jobs.push(buildOne(rig[id], parts, m, { tile: [t.x, t.y], lump: 'REGEN', idx: rec.idx })
          .then(g => spawnGroup.add(g)));
      }
    }
  }
  await Promise.all(jobs);
}

export function clearRigged() {
  mixers.length = 0;
  for (const g of [npcGroup, spawnGroup])
    for (const m of [...g.children]) {
      g.remove(m);
      m.traverse(o => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
    }
  geoCache.clear();
}
