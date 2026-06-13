// Object browser — every model from every map, with a live textured preview
// and drag-to-place. Drag a row onto the viewport to drop it into the map.
import * as THREE from 'three';
import { buildModelGroup } from './objects.js';

let catalog = null;
let dragItem = null;
const packCache = new Map();

export function getDragItem() { return dragItem; }

// ---- mini preview renderer ---------------------------------------------
let pscene, pcam, prenderer, pgroup = null, spin = true;
function initPreview(canvas) {
  pscene = new THREE.Scene();
  pscene.background = new THREE.Color(0x15171c);
  pcam = new THREE.PerspectiveCamera(45, 1, 1, 5_000_000);
  pcam.up.set(0, 0, 1);
  prenderer = new THREE.WebGLRenderer({ antialias: true, canvas, alpha: false });
  prenderer.outputColorSpace = THREE.SRGBColorSpace;
  pscene.add(new THREE.HemisphereLight(0xbcd4ff, 0x222a33, 1.1));
  const d = new THREE.DirectionalLight(0xfff4e0, 1.2); d.position.set(1, 0.6, 1.4); pscene.add(d);
  const tick = () => {
    requestAnimationFrame(tick);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w || canvas.height !== h) { prenderer.setSize(w, h, false); pcam.aspect = w / h; pcam.updateProjectionMatrix(); }
    if (pgroup && spin) pgroup.rotation.z += 0.012;
    prenderer.render(pscene, pcam);
  };
  tick();
}

async function showPreview(item, label) {
  label.textContent = `${item.name}  ·  ${item.zone}/${item.kind}`;
  if (item.kind === 'MORPH') { label.textContent += '  (preview n/a)'; return; }
  let pack = packCache.get(item.pack);
  if (!pack) { pack = fetch(`/api/pack?path=${encodeURIComponent(item.pack)}`).then(r => r.json()); packCache.set(item.pack, pack); }
  pack = await pack;
  const group = await buildModelGroup(pack, item.index);
  if (pgroup) pscene.remove(pgroup);
  pgroup = group; pscene.add(group);
  // fit camera to bbox
  const box = group.userData.bbox || new THREE.Box3().setFromObject(group);
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
  group.position.sub(c);                 // center at origin
  const r = Math.max(s.x, s.y, s.z) || 1000;
  pcam.position.set(r * 1.6, -r * 1.8, r * 1.2);
  pcam.lookAt(0, 0, 0);
}

// ---- catalog list -------------------------------------------------------
export async function initBrowser() {
  const search = document.getElementById('ob-search');
  const zoneSel = document.getElementById('ob-zone');
  const list = document.getElementById('ob-list');
  const label = document.getElementById('ob-label');
  initPreview(document.getElementById('ob-preview'));

  catalog = await (await fetch('/api/catalog')).json();
  const all = [...catalog.models, ...catalog.morph];
  for (const zk of ['(all)', '(global)', ...catalog.zones]) {
    const o = document.createElement('option'); o.value = zk; o.textContent = zk; zoneSel.appendChild(o);
  }

  function repaint() {
    const q = search.value.toLowerCase();
    const zf = zoneSel.value;
    list.innerHTML = '';
    let shown = 0;
    for (const m of all) {
      if (zf !== '(all)' && m.zone !== zf) continue;
      if (q && !m.name.toLowerCase().includes(q)) continue;
      if (shown >= 250) break;
      const row = document.createElement('div');
      row.className = 'ob-row'; row.draggable = true;
      row.innerHTML = `<span class="k ${m.kind}">${m.kind}</span><span class="nm">${m.name}</span><span class="zn">${m.zone}</span>`;
      row.addEventListener('click', () => showPreview(m, label));
      row.addEventListener('dragstart', (e) => { dragItem = m; showPreview(m, label); e.dataTransfer.setData('text/plain', m.name); e.dataTransfer.effectAllowed = 'copy'; });
      row.addEventListener('dragend', () => { /* keep dragItem until drop handler clears */ });
      list.appendChild(row); shown++;
    }
    if (shown >= 250) {
      const more = document.createElement('div'); more.className = 'ob-more';
      more.textContent = `… refine to see more (${all.length} total)`; list.appendChild(more);
    }
  }
  search.addEventListener('input', repaint);
  zoneSel.addEventListener('change', repaint);
  repaint();
}

export function clearDragItem() { dragItem = null; }
