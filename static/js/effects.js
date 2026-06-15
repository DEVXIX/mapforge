// Particle effects — renders the zone's data-driven effects: object-attached
// effects (ROSE ZSC "dummy points" each reference an .EFT, e.g. the fountain's
// bunsudae jets, streetlight glows, brazier/port-vessel fires) plus standalone
// EFFECT-lump entries. Each placement carries a world position + rotation (so
// directional jets aim correctly) and a set of flattened .PTL emitters. The
// backend (/api/zone/<key>/effects, export_effects.compute) does the parsing;
// here we just spawn + simulate the particles. Fountains also get a flat
// translucent basin pool (no .EFT provides the standing water).
import * as THREE from 'three';
import { scene, camera, renderer, onFrame } from './scene.js';
import { texUrl, getZoneEffects } from './api.js';

export const effectsGroup = new THREE.Group();
effectsGroup.name = 'effects';
scene.add(effectsGroup);

const SIZE_SCALE = 9;          // PTL size -> world sprite size
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
  uniform vec3 uTint;
  varying float vAlpha;
  void main() {
    vec4 t = texture2D(map, gl_PointCoord);
    gl_FragColor = vec4(t.rgb * uTint, t.a * vAlpha);
  }`;

function loadTex(path) {
  if (TEX.has(path)) return TEX.get(path);
  const tex = texLoader.load(texUrl(path, true));
  tex.colorSpace = THREE.SRGBColorSpace;
  TEX.set(path, tex);
  return tex;
}

function rand(a, b) { return a + Math.random() * (b - a); }

// Build one particle system for a flattened PTL emitter at a world origin,
// oriented by `quat` (a THREE.Quaternion) so directional FX aim correctly.
function buildSystem(em, origin, quat) {
  const N = Math.min(em.num_particles || 30, 220);
  const er = em.emit_radius || [0, 0, 0, 0, 0, 0];       // min xyz, max xyz
  const vel = em.vel || [0, 0, 0, 0, 0, 0];              // min xyz, max xyz
  const grav = em.gravity || [0, 0, 0, 0, 0, 0];
  const sz = em.size;                                    // [min x,y, max x,y]
  const avg = sz ? (sz[2] + sz[3]) * 0.5 : 0;
  const psize = (avg > 0.5 ? avg : 20) * SIZE_SCALE;     // 0-size effects -> sensible default
  const life = Math.max(0.5, (em.life ? Math.max(em.life[0], em.life[1]) : 50) / 30);
  const tint = (em.color && em.color.length >= 3 && (em.color[0] + em.color[1] + em.color[2]) > 0.01)
    ? [em.color[0], em.color[1], em.color[2]] : [1, 1, 1];

  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3), alpha = new Float32Array(N), size = new Float32Array(N);
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('alpha', new THREE.BufferAttribute(alpha, 1));
  geo.setAttribute('psize', new THREE.BufferAttribute(size, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: { map: { value: loadTex(em.texture) }, uScale: { value: 1 }, uTint: { value: new THREE.Vector3(...tint) } },
    vertexShader: VERT, fragmentShader: FRAG,
    transparent: true, depthWrite: false,
    blending: em.additive === false ? THREE.NormalBlending : THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geo, mat);
  points.position.set(origin[0], origin[1], origin[2]);
  if (quat) points.quaternion.copy(quat);
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
      s.alpha[i] = Math.sin(f * Math.PI);           // fade in then out
      s.pos[i * 3] = p.p[0]; s.pos[i * 3 + 1] = p.p[1]; s.pos[i * 3 + 2] = p.p[2];
      s.size[i] = s.psize;
    }
    s.geo.attributes.position.needsUpdate = true;
    s.geo.attributes.alpha.needsUpdate = true;
    s.geo.attributes.psize.needsUpdate = true;
  }
  for (const m of effectsGroup.children) if (m.material && m.material.uniforms) m.material.uniforms.uScale.value = uScale;
});

// Flat translucent water sitting in a fountain basin (no .EFT provides this).
function buildFountainPool(f) {
  const pos = f.pos, s = f.scale || 1;
  const pool = new THREE.Mesh(
    new THREE.CircleGeometry(1850 * s, 44),
    new THREE.MeshBasicMaterial({ color: 0x5aa0cf, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthWrite: false }));
  pool.position.set(pos[0], pos[1], pos[2] + 470 * s);
  pool.frustumCulled = false;
  effectsGroup.add(pool);
}

export async function buildEffects(key) {
  clearEffects();
  const fx = await getZoneEffects(key);
  if (!fx) return;
  const q = new THREE.Quaternion();
  for (const pl of (fx.placements || [])) {
    if (pl.rot && pl.rot.length >= 4) q.set(pl.rot[0], pl.rot[1], pl.rot[2], pl.rot[3]);
    else q.identity();
    for (const em of (pl.emitters || [])) if (em.texture) buildSystem(em, pl.pos, q.clone());
  }
  for (const f of (fx.fountains || [])) buildFountainPool(f);
}

export function clearEffects() {
  systems.length = 0;
  for (const m of [...effectsGroup.children]) {
    effectsGroup.remove(m);
    m.geometry?.dispose();
    m.material?.dispose();
  }
}
