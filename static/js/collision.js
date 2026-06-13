// Collision overlay — the server's .MOV walkability grid drawn as a
// semi-transparent DataTexture plane per tile, floating just above terrain.
// In paint mode, clicking a cell recolors it and queues a 'mov' op.
import * as THREE from 'three';
import { scene } from './scene.js';
import { sampleGroundZ } from './terrain.js';

export const collisionGroup = new THREE.Group();
collisionGroup.name = 'collision';
collisionGroup.visible = false;
scene.add(collisionGroup);

// attr -> rgba. Walkable is fully transparent so the overlay never tints the
// floor/grass — only obstacles (red) and mob-blocks (orange) show.
const CELL_RGBA = {
  0: [0, 0, 0, 0],          // walkable   (invisible)
  1: [220, 50, 50, 150],    // not walkable (red)
  2: [240, 160, 40, 130],   // mob-not-walkable (orange)
};

const _tiles = new Map();   // "x,y" -> {plane, tex, data, grid}

function texFromGrid(grid) {
  const h = grid.cells.length, w = grid.cells[0].length;
  const data = new Uint8Array(w * h * 4);
  for (let row = 0; row < h; row++) {
    for (let col = 0; col < w; col++) {
      // Texture row 0 = bottom. Our grid row 0 = south = lowest worldY, and
      // the plane's +V maps to +worldY, so row maps directly to texel row.
      const rgba = CELL_RGBA[grid.cells[row][col]] || CELL_RGBA[0];
      const ti = (row * w + col) * 4;
      data[ti] = rgba[0]; data[ti + 1] = rgba[1]; data[ti + 2] = rgba[2]; data[ti + 3] = rgba[3];
    }
  }
  const tex = new THREE.DataTexture(data, w, h, THREE.RGBAFormat);
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  return { tex, data };
}

export function buildCollision(mov) {
  clearCollision();
  const size = (mov.cell || 500) * 32;   // 16000
  for (const grid of mov.tiles) {
    const { tex, data } = texFromGrid(grid);
    const geo = new THREE.PlaneGeometry(size, size);
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false });
    const plane = new THREE.Mesh(geo, mat);
    const [ox, oy] = grid.origin;
    const z = sampleGroundZ(ox + size / 2, oy + size / 2) + 300;
    plane.position.set(ox + size / 2, oy + size / 2, z);
    // grid.x/grid.y are the FILE tile (for save); the overlay sits at a
    // Y-flipped world origin, so key the lookup by WORLD tile.
    const wtx = Math.round(ox / 16000), wty = Math.round(oy / 16000);
    plane.userData = { kind: 'mov', grid, data, tex, origin: [ox, oy], size,
                       fileTile: [grid.x, grid.y] };
    collisionGroup.add(plane);
    _tiles.set(`${wtx},${wty}`, plane.userData);
  }
}

export function clearCollision() {
  for (const m of [...collisionGroup.children]) {
    collisionGroup.remove(m);
    m.geometry?.dispose();
    m.material?.map?.dispose();
    m.material?.dispose();
  }
  _tiles.clear();
}

export function setCollisionVisible(v) { collisionGroup.visible = v; }
export function isCollisionVisible() { return collisionGroup.visible; }

// Paint a cell at world (wx,wy) to attr `val`. Returns an op or null.
export function paintAt(wx, wy, val) {
  const tx = Math.floor(wx / 16000), ty = Math.floor(wy / 16000);
  const ud = _tiles.get(`${tx},${ty}`);
  if (!ud) return null;
  const cell = ud.grid.cell || 500;
  const lx = wx - ud.origin[0], ly = wy - ud.origin[1];
  const col = Math.floor(lx / cell), row = Math.floor(ly / cell);
  const w = ud.grid.cells[0].length, h = ud.grid.cells.length;
  if (col < 0 || col >= w || row < 0 || row >= h) return null;
  if (ud.grid.cells[row][col] === val) return null;
  ud.grid.cells[row][col] = val;
  const rgba = CELL_RGBA[val];
  const ti = (row * w + col) * 4;
  ud.data[ti] = rgba[0]; ud.data[ti + 1] = rgba[1]; ud.data[ti + 2] = rgba[2]; ud.data[ti + 3] = rgba[3];
  ud.tex.needsUpdate = true;
  // Save op targets the FILE tile, not the world tile.
  return { op: 'mov', tile: ud.fileTile, cells: [[row, col, val]] };
}

export const collisionMeshes = () => collisionGroup.children;
