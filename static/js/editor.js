// Editor — selection, transform gizmo, the pending-ops queue, and save.
// Works on both ZSC object instances (InstancedMesh) and server-layer markers
// (individual meshes). All edits funnel into a deduped op queue that, on save,
// the backend writes to every data root so client & server stay 1:1.
import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { scene, camera, renderer, controls } from './scene.js';
import { saveOps } from './api.js';

const gizmo = new TransformControls(camera, renderer.domElement);
gizmo.setSpace('world');
scene.add(gizmo);
gizmo.addEventListener('dragging-changed', e => { controls.enabled = !e.value; });

const state = {
  pick: null, kind: null,
  inst: null, instId: -1,
  marker: null, proxy: null, wire: null,
  rec: null,
};

// Pending edits.
const updates = new Map();     // key -> op
const structural = [];         // add/delete ops in order
const movCells = new Map();    // "tx,ty" -> Map("row,col" -> val)

let _zoneKey = null;
let _onChange = () => {};
let _onSelect = () => {};

export function initEditor(zoneKey, { onChange, onSelect }) {
  _zoneKey = zoneKey;
  _onChange = onChange || (() => {});
  _onSelect = onSelect || (() => {});
  clearQueue();
  clearSelection();
}

function key(pick) { return `${pick.lump}:${pick.tile[0]},${pick.tile[1]}:${pick.idx}`; }

// ---- selection ----------------------------------------------------------
export function selectInstance(inst, instId, pick, rec) {
  clearSelection();
  state.kind = 'object'; state.inst = inst; state.instId = instId; state.pick = pick; state.rec = rec;
  const m = new THREE.Matrix4(); inst.getMatrixAt(instId, m);
  const proxy = new THREE.Object3D();
  m.decompose(proxy.position, proxy.quaternion, proxy.scale);
  scene.add(proxy); state.proxy = proxy;
  attachWire(inst.geometry, m);
  gizmo.attach(proxy);
  bindGizmo();
  _onSelect(pick, rec);
}

export function selectMarker(mesh, pick, rec) {
  clearSelection();
  state.kind = 'marker'; state.marker = mesh; state.pick = pick; state.rec = rec;
  attachWire(mesh.geometry, mesh.matrixWorld);
  gizmo.attach(mesh);
  bindGizmo();
  _onSelect(pick, rec);
}

function attachWire(geom, mat4) {
  const wire = new THREE.LineSegments(
    new THREE.EdgesGeometry(geom),
    new THREE.LineBasicMaterial({ color: 0xffe14d, depthTest: false, transparent: true }));
  wire.renderOrder = 999;
  wire.applyMatrix4(mat4);
  scene.add(wire); state.wire = wire;
}

function bindGizmo() {
  gizmo.removeEventListener('objectChange', onGizmo);
  gizmo.addEventListener('objectChange', onGizmo);
}

function onGizmo() {
  const src = state.kind === 'object' ? state.proxy : state.marker;
  if (!src) return;
  src.updateMatrixWorld(true);
  const pos = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  (state.kind === 'object' ? src.matrixWorld : src.matrixWorld).decompose(pos, q, s);

  if (state.kind === 'object') {
    const m = new THREE.Matrix4().compose(pos, q, s);
    state.inst.setMatrixAt(state.instId, m);
    state.inst.instanceMatrix.needsUpdate = true;
  }
  // For markers the visual is the marker mesh itself (already moved). But the
  // record's logical position excludes the +size cosmetic z-offset we added.
  const zAdj = state.kind === 'marker' ? markerZAdjust(state.pick.lump) : 0;
  state.rec.pos = [pos.x, pos.y, pos.z - zAdj];
  state.rec.rot = [q.x, q.y, q.z, q.w];
  state.rec.scale = [s.x, s.y, s.z];

  if (state.wire) { state.wire.position.copy(pos); state.wire.quaternion.copy(q); state.wire.scale.copy(s); }

  queueUpdate({ op: 'update', tile: state.pick.tile, lump: state.pick.lump, idx: state.pick.idx,
                pos: state.rec.pos, rot: state.rec.rot, scale: state.rec.scale });
}

function markerZAdjust(lump) {
  const sz = { REGEN: 700, MOB: 600, WARP: 600, EVENT: 500, AREA: 500, SOUND: 450, EFFECT: 450, COLLISION: 500 }[lump] || 0;
  return sz * 0.7;
}

export function clearSelection() {
  gizmo.detach();
  if (state.proxy) { scene.remove(state.proxy); state.proxy = null; }
  if (state.wire) { scene.remove(state.wire); state.wire.geometry.dispose(); state.wire.material.dispose(); state.wire = null; }
  state.kind = state.pick = state.inst = state.marker = state.rec = null;
  state.instId = -1;
  _onSelect(null, null);
}

export function setGizmoMode(mode) { gizmo.setMode(mode); }
export function hasSelection() { return !!state.pick; }
export function selectedPick() { return state.pick; }
export function selectedRec() { return state.rec; }

// ---- queue --------------------------------------------------------------
function queueUpdate(op) { updates.set(key(op), op); _onChange(pendingCount()); }

// Called by the inspector when an editable field changes.
export function queueFieldEdit(pick, fields) {
  const k = key(pick);
  const existing = updates.get(k) || { op: 'update', tile: pick.tile, lump: pick.lump, idx: pick.idx };
  Object.assign(existing, fields);
  updates.set(k, existing);
  _onChange(pendingCount());
}

export function queueDelete(pick) {
  structural.push({ op: 'delete', tile: pick.tile, lump: pick.lump, idx: pick.idx });
  updates.delete(key(pick));
  _onChange(pendingCount());
}

export function queueAdd(op) { structural.push({ ...op, op: 'add' }); _onChange(pendingCount()); }

// Queue a cross-map "place" op (backend appends the model + adds the record).
// Returns the live op object so the caller can mutate pos as the gizmo moves.
export function queuePlace(op) { const o = { ...op, op: 'place' }; structural.push(o); _onChange(pendingCount()); return o; }

// Generic gizmo attach for non-IFO objects (placed previews). Clears any
// normal selection first; onChange fires with the object as it's dragged.
let _extCb = null;
export function attachGizmo(obj, onChange) {
  clearSelection();
  if (_extCb) gizmo.removeEventListener('objectChange', _extCb);
  _extCb = () => onChange(obj);
  gizmo.addEventListener('objectChange', _extCb);
  gizmo.attach(obj);
}
export function detachGizmo() {
  if (_extCb) { gizmo.removeEventListener('objectChange', _extCb); _extCb = null; }
  gizmo.detach();
}

export function queueMov(op) {
  const tk = `${op.tile[0]},${op.tile[1]}`;
  let cm = movCells.get(tk);
  if (!cm) { cm = new Map(); movCells.set(tk, cm); }
  for (const [r, c, v] of op.cells) cm.set(`${r},${c}`, v);
  _onChange(pendingCount());
}

export function pendingCount() {
  let mov = 0; for (const cm of movCells.values()) mov += cm.size;
  return updates.size + structural.length + mov;
}

function clearQueue() { updates.clear(); structural.length = 0; movCells.clear(); }

function buildOps() {
  const ops = [...updates.values(), ...structural];
  for (const [tk, cm] of movCells) {
    const [tx, ty] = tk.split(',').map(Number);
    ops.push({ op: 'mov', tile: [tx, ty], cells: [...cm.entries()].map(([rc, v]) => { const [r, c] = rc.split(',').map(Number); return [r, c, v]; }) });
  }
  return ops;
}

export async function save() {
  const ops = buildOps();
  if (!ops.length) return null;
  const res = await saveOps(_zoneKey, ops);
  clearQueue();
  _onChange(0);
  return res;
}
