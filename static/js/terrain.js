// Terrain — faithful two-layer blend, matching engine/shader/terrain.psh:
//     final = mix(layer1, layer2, layer2.alpha)
// layer1 (down) = tile_types[no][0] + [2];  layer2 (up) = [1] + [3].
// The up-layer's DXT3 alpha is the blend mask (grass merging over ground).
// put-type ([5], 1..4) rotates the up-layer UV 0/90/180/270.
import * as THREE from 'three';
import { scene } from './scene.js';
import { texUrl } from './api.js';

export const terrainGroup = new THREE.Group();
terrainGroup.name = 'terrain';
scene.add(terrainGroup);

const PATCHES = 16, GPP = 4;
const STEP = 16000 / (PATCHES * GPP);

const texLoader = new THREE.TextureLoader();
const TEX = new Map();
// alpha=true keeps the blend mask; colorSpace handled per use.
function tile(path, alpha, srgb) {
  const k = `${path}|${alpha ? 'a' : 'rgb'}|${srgb ? 's' : 'l'}`;
  if (TEX.has(k)) return TEX.get(k);
  const t = texLoader.load(texUrl(path, alpha));
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.flipY = false;
  t.anisotropy = 4;
  TEX.set(k, t);
  return t;
}

function layerPaths(zone, tileNo) {
  const ty = zone.tile_types[tileNo];
  if (!ty) return null;
  const tx = zone.tile_textures;
  const dn = ty[0] + ty[2], up = ty[1] + ty[3], put = ty[5] | 0;
  const dnP = (dn >= 0 && dn < tx.length) ? tx[dn] : null;
  const upP = (up >= 0 && up < tx.length) ? tx[up] : null;
  return { dnP, upP, put, blended: dn !== up };
}

// Rotate (u,v) around (0.5,0.5) by put-type quarter turns.
function rotUV(u, v, put) {
  const q = ((put - 1) % 4 + 4) % 4;
  let du = u - 0.5, dv = v - 0.5;
  for (let i = 0; i < q; i++) { const t = du; du = -dv; dv = t; }
  return [du + 0.5, dv + 0.5];
}

// Two-layer material via onBeforeCompile on a Lambert (keeps scene lighting).
function blendMaterial(dnTex, upTex) {
  const m = new THREE.MeshLambertMaterial({ map: dnTex, side: THREE.DoubleSide });
  m.onBeforeCompile = (sh) => {
    sh.uniforms.map2 = { value: upTex };
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute vec2 uvUp;\nvarying vec2 vUvUp;')
      .replace('#include <uv_vertex>', '#include <uv_vertex>\n  vUvUp = uvUp;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>',
        '#include <common>\nuniform sampler2D map2;\nvarying vec2 vUvUp;\nvec3 toLin(vec3 c){return pow(c,vec3(2.2));}')
      .replace('#include <map_fragment>', `
        vec4 c1 = texture2D(map, vMapUv);
        vec4 c2 = texture2D(map2, vUvUp);
        vec3 blended = mix(toLin(c1.rgb), toLin(c2.rgb), c2.a);
        diffuseColor.rgb *= blended;
      `);
  };
  m.customProgramCacheKey = () => 'terrainBlend';
  return m;
}

function buildTile(t, zone) {
  const H = t.heights, N = t.verts, [ox, oy] = t.origin, mats = t.materials;
  // bucket key: "dn|up" (blend) or "dn" (single)
  const buckets = new Map();
  const push = (key, meta) => {
    let b = buckets.get(key);
    if (!b) { b = { ...meta, pos: [], uv: [], uvUp: [], idx: [], n: 0 }; buckets.set(key, b); }
    return b;
  };

  for (let pr = 0; pr < PATCHES; pr++) {
    for (let pc = 0; pc < PATCHES; pc++) {
      const lp = layerPaths(zone, mats[pr][pc]);
      if (!lp) continue;
      const blended = lp.blended && lp.upP;
      const key = blended ? `${lp.dnP}|${lp.upP}|${lp.put}` : `${lp.dnP}`;
      const b = push(key, { dnP: lp.dnP, upP: lp.upP, put: lp.put, blended });
      const base = b.n;
      for (let iy = 0; iy <= GPP; iy++) {
        for (let ix = 0; ix <= GPP; ix++) {
          const gr = pr * GPP + iy, gc = pc * GPP + ix;
          b.pos.push(ox + gc * STEP, oy + gr * STEP, H[gr * N + gc]);
          const u = ix / GPP, v = iy / GPP;
          b.uv.push(u, v);
          if (blended) { const [ru, rv] = rotUV(u, v, lp.put); b.uvUp.push(ru, rv); }
        }
      }
      for (let iy = 0; iy < GPP; iy++) {
        for (let ix = 0; ix < GPP; ix++) {
          const v0 = base + iy * (GPP + 1) + ix, v1 = v0 + 1, v2 = v0 + GPP + 1, v3 = v2 + 1;
          b.idx.push(v0, v2, v1, v1, v2, v3);
        }
      }
      b.n += (GPP + 1) * (GPP + 1);
    }
  }

  const meshes = [];
  for (const b of buckets.values()) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(b.pos), 3));
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(b.uv), 2));
    g.setIndex(b.idx);
    let mat;
    if (b.blended) {
      g.setAttribute('uvUp', new THREE.BufferAttribute(new Float32Array(b.uvUp), 2));
      mat = blendMaterial(tile(b.dnP, false, false), tile(b.upP, true, false));
    } else {
      mat = new THREE.MeshLambertMaterial({ side: THREE.DoubleSide,
        map: b.dnP ? tile(b.dnP, false, true) : null,
        color: b.dnP ? 0xffffff : 0x9a9a8a });
    }
    g.computeVertexNormals();
    const mesh = new THREE.Mesh(g, mat);
    mesh.frustumCulled = false;
    mesh.userData = { kind: 'terrain', tile: [t.x, t.y] };
    meshes.push(mesh);
  }
  return meshes;
}

export function buildTerrain(zone) {
  clearTerrain();
  const box = new THREE.Box3();
  for (const t of zone.tiles) {
    for (const m of buildTile(t, zone)) {
      terrainGroup.add(m);
      m.geometry.computeBoundingBox();
      box.union(m.geometry.boundingBox);
    }
  }
  return box;
}

export function clearTerrain() {
  for (const m of [...terrainGroup.children]) {
    terrainGroup.remove(m);
    m.geometry?.dispose();
    m.material?.dispose();
  }
}

const _ray = new THREE.Raycaster();
export function sampleGroundZ(wx, wy) {
  _ray.set(new THREE.Vector3(wx, wy, 1_000_000), new THREE.Vector3(0, 0, -1));
  const hit = _ray.intersectObjects(terrainGroup.children, false)[0];
  return hit ? hit.point.z : 0;
}
