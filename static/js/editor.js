// Editor — multi-selection, a shared transform gizmo, the pending-ops queue, and
// save. Selection is a SET of items (ZSC object instances and/or server-layer
// markers); the gizmo drives a pivot at the selection centroid and the same
// translate/rotate/scale delta is applied to every selected item, so you can
// grab a whole building (and its props) and move/rotate/scale it as one. All
// edits funnel into a deduped op queue that, on save, the backend writes to
// every data root so client & server stay 1:1.
import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { scene, camera, renderer, controls } from './scene.js';
import { saveOps } from './api.js';

const gizmo = new TransformControls(camera, renderer.domElement);
gizmo.setSpace('world');
scene.add(gizmo);

// ---- selection set ------------------------------------------------------
// Each item: { kind:'object'|'marker', pick, rec, wire,
//              inst, instId,   // objects (InstancedMesh instance)
//              marker }        // markers (Mesh)
const sel = [];
let pivot = null;          // Object3D the gizmo drives (selection centroid)
let dragSnaps = null;      // per-item world matrices captured at gizmo drag start
let beforeSnaps = null;    // selection transforms captured for undo
let batching = false;      // suppress per-add gizmo rebuilds during box-select

const WIRE_MAT = () => new THREE.LineBasicMaterial({ color: 0xffe14d, depthTest: false, transparent: true });
const _edgeCache = new Map();   // source geometry -> shared EdgesGeometry (box-select can add hundreds)
function edgesFor(geom) { let e = _edgeCache.get(geom); if (!e) { e = new THREE.EdgesGeometry(geom); _edgeCache.set(geom, e); } return e; }

// Undo/redo: snapshot all selected transforms at drag start; on drag end push an
// entry that restores (undo) or re-applies (redo) every one of them.
gizmo.addEventListener('dragging-changed', e => {
  controls.enabled = !e.value;
  if (!pivot || !sel.length) return;          // external gizmo (placement) handles itself
  if (e.value) { beforeSnaps = snapshotAll(); startGroupDrag(); }
  else {
    finishGroupDrag();
    if (beforeSnaps) {
      const after = snapshotAll();
      if (!sameAll(beforeSnaps, after)) {
        const before = beforeSnaps;
        pushUndo({ undo: () => before.forEach(applyTransform), redo: () => after.forEach(applyTransform) });
      }
      beforeSnaps = null;
    }
  }
});
gizmo.addEventListener('objectChange', onGroupDrag);

const state = { rec: null };   // back-compat shim for inspector's selectedRec()

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
function findItem(pick) { return sel.find(it => key(it.pick) === key(pick)); }

// ---- item world transforms ---------------------------------------------
function itemWorldMatrix(it) {
  if (it.kind === 'object') { const m = new THREE.Matrix4(); it.inst.getMatrixAt(it.instId, m); return m; }
  return it.marker.matrixWorld.clone();
}
function itemWorldPos(it) { return new THREE.Vector3().setFromMatrixPosition(itemWorldMatrix(it)); }

// Write a new WORLD matrix onto an item's instance/marker, sync its record +
// highlight, and queue the edit. Markers carry a cosmetic +z size offset that's
// excluded from the logical record position.
function writeItemWorld(it, m) {
  const pos = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  m.decompose(pos, q, s);
  if (it.kind === 'object') {
    it.inst.setMatrixAt(it.instId, m);
    it.inst.instanceMatrix.needsUpdate = true;
    it.rec.pos = [pos.x, pos.y, pos.z];
  } else {
    it.marker.position.set(pos.x, pos.y, pos.z);
    it.marker.quaternion.copy(q);
    it.marker.scale.copy(s);
    it.marker.updateMatrixWorld(true);
    const zAdj = markerZAdjust(it.pick.lump);
    it.rec.pos = [pos.x, pos.y, pos.z - zAdj];
  }
  it.rec.rot = [q.x, q.y, q.z, q.w];
  it.rec.scale = [s.x, s.y, s.z];
  updateWire(it, m);
  queueUpdate({ op: 'update', tile: it.pick.tile, lump: it.pick.lump, idx: it.pick.idx,
                pos: it.rec.pos, rot: it.rec.rot, scale: it.rec.scale });
}

// ---- group drag ---------------------------------------------------------
function startGroupDrag() {
  if (!pivot) return;
  pivot.updateMatrixWorld(true);
  dragSnaps = { pivot0: pivot.matrixWorld.clone(), items: sel.map(it => ({ it, world0: itemWorldMatrix(it) })) };
}
function onGroupDrag() {
  if (!dragSnaps || !pivot) return;
  pivot.updateMatrixWorld(true);
  const delta = pivot.matrixWorld.clone().multiply(dragSnaps.pivot0.clone().invert());  // cur * start^-1
  for (const { it, world0 } of dragSnaps.items) writeItemWorld(it, delta.clone().multiply(world0));
}
function finishGroupDrag() { dragSnaps = null; }

// ---- selection mutation -------------------------------------------------
function makeWire(it) {
  const geom = it.kind === 'object' ? it.inst.geometry : it.marker.geometry;
  const wire = new THREE.LineSegments(edgesFor(geom), WIRE_MAT());
  wire.renderOrder = 999;
  scene.add(wire);
  it.wire = wire;
  updateWire(it, itemWorldMatrix(it));
}
function updateWire(it, m) {
  if (!it.wire) return;
  m = m || itemWorldMatrix(it);
  const pos = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  m.decompose(pos, q, s);
  it.wire.position.copy(pos); it.wire.quaternion.copy(q); it.wire.scale.copy(s);
}
function dropWire(it) { if (it.wire) { scene.remove(it.wire); it.wire.material.dispose(); it.wire = null; } }  // geometry is shared/cached

function addObject(inst, instId, pick, rec) { const it = { kind: 'object', inst, instId, pick, rec }; sel.push(it); makeWire(it); return it; }
function addMarker(marker, pick, rec) { const it = { kind: 'marker', marker, pick, rec }; sel.push(it); makeWire(it); return it; }
function removeItem(it) { const i = sel.indexOf(it); if (i < 0) return; sel.splice(i, 1); dropWire(it); }

// `additive` (Ctrl/Shift): toggle this item in/out of the set instead of replacing.
export function selectInstance(inst, instId, pick, rec, additive = false) {
  if (!additive) clearSelectionInternal();
  const ex = findItem(pick);
  if (ex && additive) removeItem(ex);
  else if (!ex) addObject(inst, instId, pick, rec);
  if (!batching) rebuildGizmo();
}
export function selectMarker(mesh, pick, rec, additive = false) {
  if (!additive) clearSelectionInternal();
  const ex = findItem(pick);
  if (ex && additive) removeItem(ex);
  else if (!ex) addMarker(mesh, pick, rec);
  if (!batching) rebuildGizmo();
}

// Box-select wraps many add calls between these to rebuild the gizmo once.
export function beginBatch() { batching = true; }
export function endBatch() { batching = false; rebuildGizmo(); }

function clearSelectionInternal() { for (const it of sel) dropWire(it); sel.length = 0; }
export function clearSelection() {
  clearSelectionInternal();
  gizmo.detach();
  if (pivot) { scene.remove(pivot); pivot = null; }
  state.rec = null;
  _onSelect(null, null);
}

// Rebuild the pivot at the selection centroid and re-attach the gizmo.
function rebuildGizmo() {
  gizmo.detach();
  if (pivot) { scene.remove(pivot); pivot = null; }
  if (!sel.length) { state.rec = null; _onSelect(null, null); return; }
  const c = new THREE.Vector3();
  for (const it of sel) c.add(itemWorldPos(it));
  c.multiplyScalar(1 / sel.length);
  pivot = new THREE.Object3D();
  pivot.position.copy(c);
  scene.add(pivot);
  pivot.updateMatrixWorld(true);
  gizmo.attach(pivot);
  const last = sel[sel.length - 1];        // inspector shows the most-recent pick
  state.rec = last.rec;
  _onSelect(last.pick, last.rec);
}

function markerZAdjust(lump) {
  const sz = { REGEN: 700, MOB: 600, WARP: 600, EVENT: 500, AREA: 500, SOUND: 450, EFFECT: 450, COLLISION: 500 }[lump] || 0;
  return sz * 0.7;
}

export function setGizmoMode(mode) { gizmo.setMode(mode); }
// True while a gizmo handle is hovered or being dragged — callers must NOT run
// pick-selection then, or it detaches the gizmo mid-drag (objects won't move).
export function gizmoBusy() { return gizmo.dragging || gizmo.axis != null; }
export function hasSelection() { return sel.length > 0; }
export function selectionCount() { return sel.length; }
export function isSelected(pick) { return !!findItem(pick); }
export function selectedPick() { return sel.length ? sel[sel.length - 1].pick : null; }
export function selectedRec() { return sel.length ? sel[sel.length - 1].rec : null; }
export function selectedPicks() { return sel.map(it => it.pick); }
export function selectedItems() { return sel.map(it => ({ pick: it.pick, rec: it.rec })); }

// ---- undo / redo --------------------------------------------------------
function snapshotAll() {
  return sel.map(it => ({ kind: it.kind, inst: it.inst, instId: it.instId, marker: it.marker, pick: it.pick,
    pos: [...it.rec.pos], rot: [...it.rec.rot], scale: [...it.rec.scale] }));
}
function sameXform(a, b) {
  const eq = (x, y) => x.every((v, i) => Math.abs(v - y[i]) < 1e-4);
  return eq(a.pos, b.pos) && eq(a.rot, b.rot) && eq(a.scale, b.scale);
}
function sameAll(a, b) { return a.length === b.length && a.every((s, i) => sameXform(s, b[i])); }

// Restore one snapshot's transform onto its instance/marker, re-queue the edit,
// and refresh its highlight if it's still selected.
function applyTransform(snap) {
  const pos = new THREE.Vector3(snap.pos[0], snap.pos[1], snap.pos[2]);
  const q = new THREE.Quaternion(snap.rot[0], snap.rot[1], snap.rot[2], snap.rot[3]);
  const s = new THREE.Vector3(snap.scale[0], snap.scale[1], snap.scale[2]);
  const m = new THREE.Matrix4().compose(pos, q, s);
  if (snap.kind === 'object' && snap.inst) {
    snap.inst.setMatrixAt(snap.instId, m);
    snap.inst.instanceMatrix.needsUpdate = true;
  } else if (snap.kind === 'marker' && snap.marker) {
    const zAdj = markerZAdjust(snap.pick.lump);
    snap.marker.position.set(pos.x, pos.y, pos.z + zAdj);
    snap.marker.quaternion.copy(q); snap.marker.scale.copy(s);
    snap.marker.updateMatrixWorld(true);
  }
  queueUpdate({ op: 'update', tile: snap.pick.tile, lump: snap.pick.lump, idx: snap.pick.idx,
                pos: snap.pos, rot: snap.rot, scale: snap.scale });
  const it = findItem(snap.pick);
  if (it) { it.rec.pos = [...snap.pos]; it.rec.rot = [...snap.rot]; it.rec.scale = [...snap.scale]; updateWire(it); }
}

function pushUndo(entry) { undoStack.push(entry); redoStack.length = 0; }

export function undo() {
  const e = undoStack.pop();
  if (!e) return false;
  e.undo(); redoStack.push(e);
  if (sel.length) rebuildGizmo();
  _onChange(pendingCount());
  return true;
}
export function redo() {
  const e = redoStack.pop();
  if (!e) return false;
  e.redo(); undoStack.push(e);
  if (sel.length) rebuildGizmo();
  _onChange(pendingCount());
  return true;
}

// ---- queue --------------------------------------------------------------
const updates = new Map();     // key -> op
const structural = [];         // add/delete ops in order
const movCells = new Map();    // "tx,ty" -> Map("row,col" -> val)
const undoStack = [];
const redoStack = [];

function queueUpdate(op) { updates.set(key(op), op); _onChange(pendingCount()); }

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

export function queuePlace(op) { const o = { ...op, op: 'place' }; structural.push(o); _onChange(pendingCount()); return o; }

// Generic gizmo attach for non-IFO objects (placed previews). Clears any normal
// selection first; onChange fires with the object as it's dragged.
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
