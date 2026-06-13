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

// Undo/redo: snapshot the selection's transform at gizmo drag start, and on
// drag end push an entry that can restore (undo) or re-apply (redo) it.
let _dragBefore = null;
gizmo.addEventListener('dragging-changed', e => {
  controls.enabled = !e.value;
  if (e.value) {
    _dragBefore = state.pick ? snapshotSelection() : null;
  } else if (_dragBefore && state.pick) {
    const after = snapshotSelection();
    if (!sameXform(_dragBefore, after)) {
      const before = _dragBefore;
      pushUndo({ undo: () => applyTransform(before), redo: () => applyTransform(after) });
    }
    _dragBefore = null;
  }
});

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
const undoStack = [];          // { undo, redo } entries
const redoStack = [];

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
// True while a gizmo handle is hovered or being dragged — callers must NOT run
// pick-selection then, or it detaches the gizmo mid-drag (objects won't move).
export function gizmoBusy() { return gizmo.dragging || gizmo.axis != null; }
export function hasSelection() { return !!state.pick; }
export function selectedPick() { return state.pick; }
export function selectedRec() { return state.rec; }

// ---- undo / redo --------------------------------------------------------
function snapshotSelection() {
  return {
    kind: state.kind, inst: state.inst, instId: state.instId, marker: state.marker, pick: state.pick,
    pos: [...state.rec.pos], rot: [...state.rec.rot], scale: [...state.rec.scale],
  };
}

function sameXform(a, b) {
  const eq = (x, y) => x.every((v, i) => Math.abs(v - y[i]) < 1e-4);
  return eq(a.pos, b.pos) && eq(a.rot, b.rot) && eq(a.scale, b.scale);
}

// Restore a snapshot's transform onto its instance/marker, re-queue the matching
// edit, and (if it's the live selection) move the gizmo proxy + highlight too.
function applyTransform(snap) {
  const pos = new THREE.Vector3(snap.pos[0], snap.pos[1], snap.pos[2]);
  const q = new THREE.Quaternion(snap.rot[0], snap.rot[1], snap.rot[2], snap.rot[3]);
  const s = new THREE.Vector3(snap.scale[0], snap.scale[1], snap.scale[2]);
  if (snap.kind === 'object' && snap.inst) {
    snap.inst.setMatrixAt(snap.instId, new THREE.Matrix4().compose(pos, q, s));
    snap.inst.instanceMatrix.needsUpdate = true;
  } else if (snap.kind === 'marker' && snap.marker) {
    const zAdj = markerZAdjust(snap.pick.lump);
    snap.marker.position.set(pos.x, pos.y, pos.z + zAdj);
    snap.marker.updateMatrixWorld(true);
  }
  queueUpdate({ op: 'update', tile: snap.pick.tile, lump: snap.pick.lump, idx: snap.pick.idx,
                pos: snap.pos, rot: snap.rot, scale: snap.scale });
  if (state.pick && key(state.pick) === key(snap.pick)) {
    if (state.kind === 'object' && state.proxy) {
      state.proxy.position.copy(pos); state.proxy.quaternion.copy(q); state.proxy.scale.copy(s);
      state.proxy.updateMatrixWorld(true);
    }
    if (state.wire) { state.wire.position.copy(pos); state.wire.quaternion.copy(q); state.wire.scale.copy(s); }
    state.rec.pos = [...snap.pos]; state.rec.rot = [...snap.rot]; state.rec.scale = [...snap.scale];
  }
}

function pushUndo(entry) { undoStack.push(entry); redoStack.length = 0; }

export function undo() {
  const e = undoStack.pop();
  if (!e) return false;
  e.undo(); redoStack.push(e); _onChange(pendingCount());
  return true;
}
export function redo() {
  const e = redoStack.pop();
  if (!e) return false;
  e.redo(); undoStack.push(e); _onChange(pendingCount());
  return true;
}

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
  const op = { op: 'delete', tile: pick.tile, lump: pick.lump, idx: pick.idx };
  const k = key(pick), prevUpdate = updates.get(k);
  structural.push(op);
  updates.delete(k);
  pushUndo({
    undo: () => { const i = structural.indexOf(op); if (i >= 0) structural.splice(i, 1);
                  if (prevUpdate) updates.set(k, prevUpdate); _onChange(pendingCount()); },
    redo: () => { structural.push(op); updates.delete(k); _onChange(pendingCount()); },
  });
  _onChange(pendingCount());
}

export function queueAdd(op) {
  const o = { ...op, op: 'add' };
  structural.push(o);
  pushUndo({
    undo: () => { const i = structural.indexOf(o); if (i >= 0) structural.splice(i, 1); _onChange(pendingCount()); },
    redo: () => { structural.push(o); _onChange(pendingCount()); },
  });
  _onChange(pendingCount());
}

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

function clearQueue() { updates.clear(); structural.length = 0; movCells.clear(); undoStack.length = 0; redoStack.length = 0; }

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
