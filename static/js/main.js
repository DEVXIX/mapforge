// mapforge bootstrap — orchestrates data load, scene build, picking, layers,
// modes, hotkeys and save.
import * as THREE from 'three';
import { scene, camera, renderer, controls, frameBox } from './scene.js';
import * as API from './api.js';
import { buildTerrain, clearTerrain, terrainGroup } from './terrain.js';
import { buildWater, clearWater, waterGroup } from './water.js';
import { buildObjects, clearObjects, objectsGroup, npcGroup, spawnGroup, setTexturesHidden, setRiggedNpcs } from './objects.js';
import { buildRiggedNpcs } from './rignpc.js';
import { buildMarkers, clearMarkers, markerGroups, markerCount } from './markers.js';
import { buildCollision, clearCollision, setCollisionVisible, isCollisionVisible, paintAt, collisionMeshes } from './collision.js';
import * as Editor from './editor.js';
import { showInspector } from './inspector.js';
import { isFlying } from './flycam.js';
import { initBrowser, getDragItem, clearDragItem } from './objectbrowser.js';
import * as Placement from './placement.js';

const $ = id => document.getElementById(id);
const statusEl = $('status');
const saveBtn = $('save-btn');

let zoneData = null;
let recIndex = new Map();     // "lump:tx,ty:idx" -> rec
let mode = 'select';
let paintVal = 0;

function setStatus(s) { statusEl.textContent = s; }

// ---- boot ---------------------------------------------------------------
(async function boot() {
  try {
    const zones = await API.getZones();
    const sel = $('zone-select');
    sel.innerHTML = '';
    for (const z of zones) {
      const o = document.createElement('option');
      o.value = z.key; o.textContent = `${z.key} — ${z.name}`;
      sel.appendChild(o);
    }
    const jp = zones.find(z => z.key === 'JPT01-1');
    if (jp) sel.value = 'JPT01-1';
    setStatus(`${zones.length} zones. Pick one and Load.`);
    initBrowser().catch(e => console.error('browser:', e));
  } catch (e) { setStatus('failed to list zones: ' + e.message); }
})();

$('load-btn').onclick = () => loadZone($('zone-select').value);
$('fit-btn').onclick = () => { if (zoneData) frameBox(worldBox()); };
saveBtn.onclick = doSave;
$('export-btn').onclick = () => {
  const key = zoneData?.key || $('zone-select').value;
  setStatus(`exporting ${key} to .glb — this takes ~30-60s, the download starts when ready…`);
  window.location = `/api/zone/${encodeURIComponent(key)}/export`;
};

// ---- load ---------------------------------------------------------------
async function loadZone(keyName, keepCamera = false) {
  setStatus(`loading ${keyName}…`);
  Editor.clearSelection();
  const [zone, packs, mov, rig] = await Promise.all([
    API.getZone(keyName), API.getPacks(keyName), API.getMov(keyName), API.getRig(keyName),
  ]);
  zoneData = zone;

  // index records for fast pick->rec lookup
  recIndex = new Map();
  for (const t of zone.tiles)
    for (const [lump, recs] of Object.entries(t.ifo.lumps))
      if (Array.isArray(recs))
        for (const rec of recs) { rec._tile = [t.x, t.y]; recIndex.set(`${lump}:${t.x},${t.y}:${rec.idx}`, rec); }

  const tbox = buildTerrain(zone);
  buildWater(zone);
  setStatus(`${keyName}: building objects…`);
  const useRig = rig && Object.keys(rig).length > 0;
  setRiggedNpcs(useRig);                    // skip static MOB/REGEN if we'll rig them
  const obox = await buildObjects(zone, packs);
  if (useRig) { setStatus(`${keyName}: rigging NPCs…`); await buildRiggedNpcs(zone, packs, rig); }
  buildMarkers(zone);
  buildCollision(mov);

  Editor.initEditor(keyName, { onChange: onQueueChange, onSelect: showInspector });
  Placement.initPlacement(keyName, setStatus);
  Placement.setFlipCenter(zone.center_y || 32);
  buildLayers(zone);
  if (!keepCamera) frameBox(tbox.union(obox));

  const recs = countRecords(zone);
  setStatus(`${zone.name}\n${zone.tiles.length} tiles · ${recs.OBJECT|0} obj · ${recs.REGEN|0} spawns · ${recs.MOB|0} npc · ${recs.COLLISION|0} colbox`);
}

function worldBox() {
  const b = new THREE.Box3();
  terrainGroup.children.forEach(m => { m.geometry.computeBoundingBox(); b.union(m.geometry.boundingBox); });
  return b;
}

function countRecords(zone) {
  const c = {};
  for (const t of zone.tiles)
    for (const [lump, recs] of Object.entries(t.ifo.lumps))
      if (Array.isArray(recs)) c[lump] = (c[lump] || 0) + recs.length;
  return c;
}

// ---- layers -------------------------------------------------------------
const LAYER_DEFS = [
  { id: 'terrain', label: 'Terrain', color: 0x6a7a6a, consumer: 'client', get: () => terrainGroup },
  { id: 'objects', label: 'Objects (OBJ+CNST+MORPH)', color: 0xb8c0c8, consumer: 'client', get: () => objectsGroup },
  { id: 'water', label: 'Water (OCEAN)', color: 0x2f6f9f, consumer: 'client', get: () => waterGroup },
  { id: 'mov', label: 'Walk grid (MOV)', color: 0xdc3a3a, consumer: 'server', mov: true },
  { id: 'REGEN', label: 'Spawns (REGEN)', color: 0xff8a3c, consumer: 'server', count: 'REGEN', get: () => spawnGroup },
  { id: 'MOB', label: 'NPCs (MOB)', color: 0xff4d4d, consumer: 'server', count: 'MOB', get: () => npcGroup },
  { id: 'WARP', label: 'Warps', color: 0xff3cf0, consumer: 'both', marker: 'WARP' },
  { id: 'EVENT', label: 'Events', color: 0x39d0ff, consumer: 'server', marker: 'EVENT' },
  { id: 'AREA', label: 'Areas', color: 0x39ffd0, consumer: 'server', marker: 'AREA' },
  { id: 'COLLISION', label: 'Collision boxes', color: 0xd83a3a, consumer: 'both', marker: 'COLLISION' },
  { id: 'SOUND', label: 'Sounds', color: 0xffe14d, consumer: 'client', marker: 'SOUND' },
  { id: 'EFFECT', label: 'Effects', color: 0x8cff5a, consumer: 'client', marker: 'EFFECT' },
];
const DEFAULT_OFF = new Set(['SOUND', 'EFFECT', 'mov']);

function buildLayers(zone) {
  const host = $('layers'); host.innerHTML = '';
  const counts = countRecords(zone);
  for (const L of LAYER_DEFS) {
    const grp = L.marker ? markerGroups[L.marker] : (L.get ? L.get() : null);
    const row = document.createElement('label'); row.className = 'layer';
    const cb = document.createElement('input'); cb.type = 'checkbox';
    cb.checked = !DEFAULT_OFF.has(L.id);
    if (L.mov) setCollisionVisible(cb.checked);
    else if (grp) grp.visible = cb.checked;
    cb.onchange = () => {
      if (L.mov) setCollisionVisible(cb.checked);
      else if (grp) grp.visible = cb.checked;
    };
    const sw = document.createElement('span'); sw.className = 'sw';
    sw.style.background = '#' + L.color.toString(16).padStart(6, '0');
    const lbl = document.createElement('span'); lbl.textContent = L.label;
    const tag = document.createElement('span'); tag.className = 'tag ' + L.consumer; tag.textContent = L.consumer;
    const ct = document.createElement('span'); ct.className = 'ct';
    ct.textContent = (L.marker || L.count) ? (counts[L.marker || L.count] || 0) : '';
    row.append(cb, sw, lbl, tag, ct);
    host.appendChild(row);
  }
  // texture-hide toggle
  const trow = document.createElement('label'); trow.className = 'layer';
  const tcb = document.createElement('input'); tcb.type = 'checkbox';
  tcb.onchange = () => setTexturesHidden(tcb.checked);
  const tl = document.createElement('span'); tl.textContent = 'Hide textures';
  trow.append(tcb, document.createElement('span'), tl);
  host.appendChild(trow);
}

// ---- mode + paint -------------------------------------------------------
$('mode-seg').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.mode;
  [...e.currentTarget.children].forEach(x => x.classList.toggle('on', x === b));
  $('paint-tools').style.display = mode === 'paint' ? 'block' : 'none';
  if (mode === 'paint') { setCollisionVisible(true); Editor.clearSelection(); }
});
$('paint-seg').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  paintVal = parseInt(b.dataset.val, 10);
  [...e.currentTarget.children].forEach(x => x.classList.toggle('on', x === b));
});

// ---- picking ------------------------------------------------------------
const ray = new THREE.Raycaster();
const ptr = new THREE.Vector2();
let painting = false;

renderer.domElement.addEventListener('pointerdown', e => {
  if (e.button !== 0) return;
  if (Editor.gizmoBusy()) return;   // grabbing a move/rotate/scale handle — let the gizmo drag it
  setPtr(e);
  if (mode === 'paint') { painting = true; doPaint(); return; }
  pickSelect();
});
renderer.domElement.addEventListener('pointermove', e => {
  if (painting) { setPtr(e); doPaint(); }
});
addEventListener('pointerup', () => { painting = false; });

function setPtr(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ptr.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  ptr.y = -((e.clientY - r.top) / r.height) * 2 + 1;
}

function doPaint() {
  ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObjects(collisionMeshes(), false)[0];
  if (!hit) return;
  const op = paintAt(hit.point.x, hit.point.y, paintVal);
  if (op) Editor.queueMov(op);
}

// Drag-and-drop a catalog model onto the terrain.
renderer.domElement.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
renderer.domElement.addEventListener('drop', e => {
  e.preventDefault();
  const item = getDragItem();
  if (!item) return;
  setPtr(e);
  ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObjects(terrainGroup.children, false)[0];
  if (!hit) { setStatus('drop onto the terrain'); return; }
  Placement.placeModel(item, hit.point).catch(err => setStatus('place failed: ' + err.message));
  clearDragItem();
});

function pickSelect() {
  ray.setFromCamera(ptr, camera);
  const targets = [...objectsGroup.children];
  if (npcGroup.visible) targets.push(...npcGroup.children);      // NPCs (MOB) are selectable
  if (spawnGroup.visible) targets.push(...spawnGroup.children);  // monsters (REGEN) are selectable
  for (const g of Object.values(markerGroups)) if (g.visible) targets.push(...g.children);
  // include placed (unsaved) previews
  scene.children.forEach(o => { if (o.userData && o.userData.kind === 'placed') targets.push(...o.children); });
  const hit = ray.intersectObjects(targets, false)[0];
  if (!hit) { Editor.clearSelection(); return; }
  const ud = hit.object.userData;
  if (Placement.isPlacedHit(ud)) { Placement.selectFromHit(ud); return; }
  if (ud.kind === 'object') {
    const pick = ud.picks[hit.instanceId];
    const rec = recIndex.get(`${pick.lump}:${pick.tile[0]},${pick.tile[1]}:${pick.idx}`);
    if (rec) Editor.selectInstance(hit.object, hit.instanceId, pick, rec);
  } else if (ud.kind === 'marker') {
    Editor.selectMarker(hit.object, ud.pick, ud.rec);
  }
}

// ---- hotkeys ------------------------------------------------------------
addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  // Undo/redo work regardless of fly mode.
  if (e.ctrlKey && e.code === 'KeyZ' && !e.shiftKey) { e.preventDefault(); setStatus(Editor.undo() ? 'undo' : 'nothing to undo'); return; }
  if (e.ctrlKey && (e.code === 'KeyY' || (e.code === 'KeyZ' && e.shiftKey))) { e.preventDefault(); setStatus(Editor.redo() ? 'redo' : 'nothing to redo'); return; }
  if (isFlying()) return;          // WASD belongs to the fly camera while RMB held
  if (e.code === 'KeyW') Editor.setGizmoMode('translate');
  if (e.code === 'KeyE') Editor.setGizmoMode('rotate');
  if (e.code === 'KeyR') Editor.setGizmoMode('scale');
  if (e.code === 'Escape') Editor.clearSelection();
  if ((e.code === 'Delete' || e.code === 'Backspace') && Editor.hasSelection()) {
    e.preventDefault();
    Editor.queueDelete(Editor.selectedPick());
    Editor.clearSelection();
    setStatus('queued delete — Save to write');
  }
  if (e.ctrlKey && e.code === 'KeyD' && Editor.hasSelection()) {
    e.preventDefault();
    const p = Editor.selectedPick(), r = Editor.selectedRec();
    Editor.queueAdd({ tile: p.tile, lump: p.lump, object_id: r.object_id, object_type: r.object_type,
                      name: r.name, pos: [r.pos[0] + 500, r.pos[1] + 500, r.pos[2]], rot: r.rot, scale: r.scale,
                      ...(r.extra || {}) });
    setStatus('queued duplicate — Save + reload to see it');
  }
});

// ---- save ---------------------------------------------------------------
function onQueueChange(n) {
  saveBtn.disabled = n === 0;
  saveBtn.textContent = n ? `Save (${n})` : 'Save';
}

async function doSave() {
  try {
    setStatus('saving…');
    const res = await Editor.save();
    if (!res) { setStatus('nothing to save'); return; }
    const roots = (res.written || []).length;
    setStatus(`saved ${roots} tile-writes to roots: ${res.roots.join(', ')}\nreloading…`);
    await loadZone(zoneData.key, true);
    setStatus(`saved to: ${res.roots.join(', ')} (client & server in sync)`);
  } catch (e) { setStatus('save failed: ' + e.message); }
}
