// Water — renders the OCEAN lump's blocks as translucent water planes at each
// block's z level. ROSE ocean blocks are axis-aligned tile-sized rects sharing
// a flat water height; a soft blue surface marks where water belongs.
import * as THREE from 'three';
import { scene } from './scene.js';

export const waterGroup = new THREE.Group();
waterGroup.name = 'water';
scene.add(waterGroup);

function waterMaterial() {
  return new THREE.MeshLambertMaterial({
    color: 0x2f6f9f, emissive: 0x0a2233, transparent: true, opacity: 0.62,
    side: THREE.DoubleSide, depthWrite: false,
  });
}

export function buildWater(zone) {
  clearWater();
  const mat = waterMaterial();
  for (const t of zone.tiles) {
    const oc = t.ifo.lumps.OCEAN;
    if (!oc || !oc.blocks) continue;
    for (const [s, e] of oc.blocks) {
      const x0 = Math.min(s[0], e[0]), x1 = Math.max(s[0], e[0]);
      const y0 = Math.min(s[1], e[1]), y1 = Math.max(s[1], e[1]);
      const z = Math.max(s[2], e[2]);
      const w = x1 - x0, h = y1 - y0;
      if (w <= 0 || h <= 0) continue;
      const geo = new THREE.PlaneGeometry(w, h);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, z);
      mesh.userData = { kind: 'water' };
      waterGroup.add(mesh);
    }
  }
}

export function clearWater() {
  for (const m of [...waterGroup.children]) {
    waterGroup.remove(m);
    m.geometry?.dispose();
  }
}
