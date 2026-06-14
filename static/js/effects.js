// Particle effects — renders the EFFECT lump's .EFT/.PTL emitters as the game's
// real particle layers: each PTL "sequence" spawns its particle count inside its
// emit-radius box, drifts them by the velocity + gravity, twinkles alpha over
// life, and draws them as additive textured point sprites. Distances are already
// in world units (PTL *ZZ_SCALE_IN, world *100 cancel).
import * as THREE from 'three';
import { scene, camera, renderer, onFrame } from './scene.js';
import { getEffect, texUrl } from './api.js';

export const effectsGroup = new THREE.Group();
effectsGroup.name = 'effects';
scene.add(effectsGroup);

const SIZE_SCALE = 9;          // PTL size (e.g. 20) -> world sprite size
const systems = [];
const texLoader = new THREE.TextureLoader();
const TEX = new Map();
const clock = new THREE.Clock();

const VERT = `
  attribute float psize;
  attribute float alpha;
  uniform float uScale;
  varying float vAlpha;
  void main() {
    vAlpha = alpha;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = clamp(psize * uScale / max(-mv.z, 1.0), 1.0, 256.0);
    gl_Position = projectionMatrix * mv;
  }`;
const FRAG = `
  uniform sampler2D map;
  varying float vAlpha;
  void main() {
    vec4 t = texture2D(map, gl_PointCoord);
    gl_FragColor = vec4(t.rgb, t.a * vAlpha);
  }`;

function loadTex(path) {
  if (TEX.has(path)) return TEX.get(path);
  const tex = texLoader.load(texUrl(path, true));
  tex.colorSpace = THREE.SRGBColorSpace;
  TEX.set(path, tex);
  return tex;
}

function rand(a, b) { return a + Math.random() * (b - a); }

// Build one particle system for a PTL sequence at a world origin.
function buildSystem(seq, origin) {
  const N = Math.min(seq.num_particles || 30, 220);
  const er = seq.emit_radius || [0, 0, 0, 0, 0, 0];      // min xyz, max xyz
  const vel = (seq.events.find(e => e.type === 11) || {}).vel || [-10, -10, -10, 10, 10, 10];
  const grav = seq.gravity || [0, 0, 0, 0, 0, 0];
  const sz = (seq.events.find(e => e.type === 1) || {}).size;
  const psize = (sz ? (sz[2] + sz[3]) * 0.5 : 20) * SIZE_SCALE;
  const life = Math.max(0.6, (seq.life ? seq.life[1] : 50) / 30);

  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3), alpha = new Float32Array(N), size = new Float32Array(N);
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('alpha', new THREE.BufferAttribute(alpha, 1));
  geo.setAttribute('psize', new THREE.BufferAttribute(size, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: { map: { value: loadTex(seq.texture) }, uScale: { value: 1 } },
    vertexShader: VERT, fragmentShader: FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geo, mat);
  points.position.set(origin[0], origin[1], origin[2]);
  points.frustumCulled = false;
  effectsGroup.add(points);

  const part = [];
  const spawn = (p) => {
    p.age = 0; p.life = life * rand(0.7, 1.0);
    p.p = [rand(er[0], er[3]), rand(er[1], er[4]), rand(er[2], er[5])];
    p.v = [rand(vel[0], vel[3]), rand(vel[1], vel[4]), rand(vel[2], vel[5])];
  };
  for (let i = 0; i < N; i++) { const p = {}; spawn(p); p.age = Math.random() * p.life; part.push(p); }
  systems.push({ geo, pos, alpha, size, part, psize, grav, spawn });
}

onFrame(() => {
  if (!systems.length) return;
  const dt = Math.min(clock.getDelta(), 0.05);
  const uScale = renderer.domElement.clientHeight / (2 * Math.tan(camera.fov * Math.PI / 360));
  for (const s of systems) {
    const gz = (s.grav[2] + s.grav[5]) * 0.5;
    for (let i = 0; i < s.part.length; i++) {
      const p = s.part[i];
      p.age += dt;
      if (p.age >= p.life) s.spawn(p);
      p.v[2] += gz * dt;
      p.p[0] += p.v[0] * dt; p.p[1] += p.v[1] * dt; p.p[2] += p.v[2] * dt;
      const f = p.age / p.life;
      s.alpha[i] = Math.sin(f * Math.PI);           // twinkle: fade in then out
      s.pos[i * 3] = p.p[0]; s.pos[i * 3 + 1] = p.p[1]; s.pos[i * 3 + 2] = p.p[2];
      s.size[i] = s.psize;
    }
    s.geo.attributes.position.needsUpdate = true;
    s.geo.attributes.alpha.needsUpdate = true;
    s.geo.attributes.psize.needsUpdate = true;
  }
  for (const m of effectsGroup.children) if (m.material.uniforms) m.material.uniforms.uScale.value = uScale;
});

// ---- synthetic fountain water (jets + mist) — not in the map data, our own ----
let _soft = null;
function softSprite() {
  if (_soft) return _soft;
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.35, 'rgba(210,235,255,0.55)');
  grad.addColorStop(1, 'rgba(190,225,255,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
  _soft = new THREE.CanvasTexture(c);
  return _soft;
}

// Generic gravity-particle system reusing the onFrame updater shape.
function makeSystem(tex, origin, N, psize, grav, spawn) {
  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3), alpha = new Float32Array(N), size = new Float32Array(N);
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('alpha', new THREE.BufferAttribute(alpha, 1));
  geo.setAttribute('psize', new THREE.BufferAttribute(size, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: { map: { value: tex }, uScale: { value: 1 } },
    vertexShader: VERT, fragmentShader: FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geo, mat);
  points.position.set(origin[0], origin[1], origin[2]);
  points.frustumCulled = false;
  effectsGroup.add(points);
  const part = [];
  for (let i = 0; i < N; i++) { const p = {}; spawn(p); p.age = Math.random() * p.life; part.push(p); }
  systems.push({ geo, pos, alpha, size, part, psize, grav, spawn });
}

function findFountains(zone, packs) {
  const out = [];
  for (const kind of ['OBJECT', 'CNST']) {
    const pk = packs && packs[kind];
    if (!pk || !pk.models) continue;
    for (const t of zone.tiles)
      for (const rec of (t.ifo.lumps[kind] || [])) {
        const mdl = pk.models[rec.object_id];
        if (mdl && mdl.parts.some(p => /fountain\d/i.test(p.mesh || '')))
          out.push({ pos: rec.pos, scale: (rec.scale && rec.scale[2]) || 1 });
      }
  }
  return out;
}

// All offsets/speeds are in UNSCALED mesh units (fountain mesh: base z~134, top
// z~2491, width ~4491) multiplied by the fountain's IFO scale s, so it lands
// correctly however the fountain is scaled (this one is 0.4).
function buildFountain(f) {
  const pos = f.pos, s = f.scale || 1;
  const tex = softSprite();
  const G = [0, 0, -3600 * s, 0, 0, -3600 * s];
  // 1. top finial jet — central spout near the top, rises a touch then falls back
  makeSystem(tex, [pos[0], pos[1], pos[2] + 2300 * s], 80, 42, G, (p) => {
    p.age = 0; p.life = 1.0 + Math.random() * 0.4;
    const a = Math.random() * Math.PI * 2, r = Math.random() * 150 * s;
    const out = (150 + Math.random() * 250) * s, up = (1300 + Math.random() * 450) * s;
    p.p = [Math.cos(a) * r, Math.sin(a) * r, 0];
    p.v = [Math.cos(a) * out, Math.sin(a) * out, up];
  });
  // 2. petal-hole cascade — water leaves the shell holes, arcs out, falls to the basin
  makeSystem(tex, [pos[0], pos[1], pos[2] + 1700 * s], 170, 44, G, (p) => {
    p.age = 0; p.life = 1.0 + Math.random() * 0.35;
    const a = Math.random() * Math.PI * 2, r = (550 + Math.random() * 450) * s;
    const out = (620 + Math.random() * 430) * s, up = (340 + Math.random() * 330) * s;
    p.p = [Math.cos(a) * r, Math.sin(a) * r, 0];
    p.v = [Math.cos(a) * out, Math.sin(a) * out, up];
  });
  // 3. water "smoke" — soft low mist around the basin
  makeSystem(tex, [pos[0], pos[1], pos[2] + 300 * s], 45, 80,
    [0, 0, -300 * s, 0, 0, -300 * s], (p) => {
      p.age = 0; p.life = 1.3 + Math.random() * 0.9;
      const a = Math.random() * Math.PI * 2, r = Math.random() * 1500 * s;
      p.p = [Math.cos(a) * r, Math.sin(a) * r, Math.random() * 280 * s];
      p.v = [(Math.random() - 0.5) * 250 * s, (Math.random() - 0.5) * 250 * s, (240 + Math.random() * 240) * s];
    });
  // 4. water sitting in the basin — flat translucent pool
  const pool = new THREE.Mesh(
    new THREE.CircleGeometry(1850 * s, 44),
    new THREE.MeshBasicMaterial({ color: 0x5aa0cf, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthWrite: false }));
  pool.position.set(pos[0], pos[1], pos[2] + 470 * s);
  pool.frustumCulled = false;
  effectsGroup.add(pool);
}

export async function buildEffects(zone, packs) {
  clearEffects();
  const cache = new Map();
  for (const t of zone.tiles) {
    for (const rec of (t.ifo.lumps.EFFECT || [])) {
      const eft = rec.extra && rec.extra.effect_file;
      if (!eft) continue;
      let def = cache.get(eft);
      if (def === undefined) { def = await getEffect(eft); cache.set(eft, def); }
      if (!def || !def.emitters) continue;
      for (const seq of def.emitters) if (seq.texture) buildSystem(seq, rec.pos);
    }
  }
  for (const f of findFountains(zone, packs)) buildFountain(f);   // our synthetic fountain water
}

export function clearEffects() {
  systems.length = 0;
  for (const m of [...effectsGroup.children]) {
    effectsGroup.remove(m);
    m.geometry?.dispose();
    m.material?.dispose();
  }
}
