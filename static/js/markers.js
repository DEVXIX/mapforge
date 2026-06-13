// Marker layers for the lumps that have no render mesh of their own but DO
// drive the server: spawns (REGEN), NPCs/mobs (MOB), warps, events, areas,
// sounds, effects, collision boxes. Each marker is individually selectable.
import * as THREE from 'three';
import { scene } from './scene.js';

// lump -> {color, geom factory, size}
const STYLE = {
  // REGEN (monster spawns) + MOB (NPCs) now render as real composed meshes
  // (see objects.js / spawnGroup + npcGroup) instead of placeholder markers.
  WARP:      { color: 0xff3cf0, shape: 'torus', size: 600 },
  EVENT:     { color: 0x39d0ff, shape: 'box',   size: 500 },
  AREA:      { color: 0x39ffd0, shape: 'wbox',  size: 500 },
  SOUND:     { color: 0xffe14d, shape: 'sphere', size: 450 },
  EFFECT:    { color: 0x8cff5a, shape: 'sphere', size: 450 },
  COLLISION: { color: 0xd83a3a, shape: 'wbox',  size: 500 },
};

export const markerGroups = {};   // lump -> THREE.Group

function makeGeom(shape, s) {
  switch (shape) {
    case 'octa':   return new THREE.OctahedronGeometry(s * 0.6);
    case 'cone':   return new THREE.ConeGeometry(s * 0.5, s * 1.4, 8);
    case 'torus':  return new THREE.TorusGeometry(s * 0.5, s * 0.16, 8, 16);
    case 'box':    return new THREE.BoxGeometry(s, s, s);
    case 'wbox':   return new THREE.BoxGeometry(s, s, s);
    case 'sphere': return new THREE.SphereGeometry(s * 0.5, 12, 8);
  }
}

function makeMarker(lump, rec) {
  const st = STYLE[lump];
  const geom = makeGeom(st.shape, st.size);
  let mat;
  if (st.shape === 'wbox') {
    mat = new THREE.MeshBasicMaterial({ color: st.color, wireframe: true, transparent: true, opacity: 0.85 });
  } else {
    mat = new THREE.MeshLambertMaterial({ color: st.color, emissive: st.color, emissiveIntensity: 0.35 });
  }
  const mesh = new THREE.Mesh(geom, mat);
  mesh.position.set(rec.pos[0], rec.pos[1], rec.pos[2] + st.size * 0.7);
  // COLLISION/AREA boxes honour record scale so they cover their real area.
  if (st.shape === 'wbox') {
    mesh.scale.set(Math.max(0.2, rec.scale[0]), Math.max(0.2, rec.scale[1]), Math.max(0.2, rec.scale[2]));
  }
  mesh.userData = { kind: 'marker', pick: { tile: rec._tile, lump, idx: rec.idx }, rec };
  return mesh;
}

export function buildMarkers(zone) {
  clearMarkers();
  for (const lump of Object.keys(STYLE)) {
    const g = new THREE.Group(); g.name = `markers:${lump}`;
    markerGroups[lump] = g;
    scene.add(g);
  }
  for (const t of zone.tiles) {
    for (const lump of Object.keys(STYLE)) {
      const recs = t.ifo.lumps[lump];
      if (!recs) continue;
      for (const rec of recs) {
        rec._tile = [t.x, t.y];
        markerGroups[lump].add(makeMarker(lump, rec));
      }
    }
  }
}

export function clearMarkers() {
  for (const g of Object.values(markerGroups)) {
    scene.remove(g);
    g.traverse(o => { o.geometry?.dispose(); o.material?.dispose(); });
  }
  for (const k of Object.keys(markerGroups)) delete markerGroups[k];
}

export function markerCount(zone, lump) {
  let n = 0;
  for (const t of zone.tiles) n += (t.ifo.lumps[lump] || []).length;
  return n;
}
