// Inspector — context-sensitive property panel per lump type. Editable fields
// funnel through editor.queueFieldEdit so they ride the same save path.
import { queueFieldEdit } from './editor.js';

const titleEl = document.getElementById('insp-title');
const bodyEl = document.getElementById('insp-body');
const panel = document.getElementById('right');

export function showInspector(pick, rec) {
  if (!pick || !rec) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';
  titleEl.textContent = `${pick.lump} · tile ${pick.tile[0]},${pick.tile[1]} · #${pick.idx}`;
  bodyEl.innerHTML = '';

  const e = rec.extra || {};
  // --- common transform readout ---
  bodyEl.appendChild(kv('pos', rec.pos.map(n => n.toFixed(0)).join(', ')));
  bodyEl.appendChild(kv('rot', rec.rot.map(n => n.toFixed(2)).join(', ')));
  bodyEl.appendChild(kv('scale', rec.scale.map(n => n.toFixed(2)).join(', ')));
  hr();

  if (pick.lump === 'OBJECT' || pick.lump === 'CNST' || pick.lump === 'MORPH') {
    numField('object_id', rec.object_id, v => queueFieldEdit(pick, { object_id: v }),
             'model index — changes the mesh on next reload');
    textField('name', rec.name, v => queueFieldEdit(pick, { name: v }));
  } else if (pick.lump === 'MOB') {
    note('NPC / mob placement (server spawns this).');
    numField('object_id', rec.object_id, v => queueFieldEdit(pick, { object_id: v }), 'LIST_NPC.STB id');
    numField('ai_index', e.ai_index ?? 0, v => queueFieldEdit(pick, { ai_index: v }));
    textField('quest', e.quest_name ?? '', v => queueFieldEdit(pick, { quest_name: v }));
  } else if (pick.lump === 'WARP') {
    note('Warp gate.');
    numField('warp_id', rec.warp_id, v => queueFieldEdit(pick, { warp_id: v }));
    numField('event_id', rec.event_id, v => queueFieldEdit(pick, { event_id: v }));
  } else if (pick.lump === 'EVENT' || pick.lump === 'AREA') {
    note(pick.lump === 'EVENT' ? 'Event trigger (server eventID).' : 'Named area.');
    numField('event_id', rec.event_id, v => queueFieldEdit(pick, { event_id: v }));
    textField('str1', e.str1 ?? '', v => queueFieldEdit(pick, { /* tail via str1 */ }));
    textField('str2', e.str2 ?? '', v => queueFieldEdit(pick, {}));
    note('(string tails are display-only for now)');
  } else if (pick.lump === 'SOUND') {
    textField('file', e.sound_file ?? '', () => {});
    bodyEl.appendChild(kv('range', e.range ?? 0));
    bodyEl.appendChild(kv('interval', e.interval ?? 0));
  } else if (pick.lump === 'EFFECT') {
    textField('file', e.effect_file ?? '', () => {});
  } else if (pick.lump === 'REGEN') {
    regenPanel(pick, rec);
  }
}

// --- REGEN: the server-critical spawn editor ----------------------------
function regenPanel(pick, rec) {
  const e = rec.extra;
  note('Monster spawn point (server only).');
  textField('name', e.regen_name ?? '', v => queueFieldEdit(pick, { regen_name: v }));
  numField('interval', e.interval ?? 0, v => queueFieldEdit(pick, { interval: v }), 'seconds between waves');
  numField('limit', e.limit ?? 0, v => queueFieldEdit(pick, { limit: v }), 'max alive');
  numField('range', e.range ?? 0, v => queueFieldEdit(pick, { range: v }), 'spawn radius (m)');

  mobList(pick, rec, 'basic', 'Basic mobs');
  mobList(pick, rec, 'tactics', 'Tactics mobs');
}

function mobList(pick, rec, grp, label) {
  const e = rec.extra;
  const h = document.createElement('h2'); h.textContent = label; bodyEl.appendChild(h);
  const list = e[grp] || (e[grp] = []);
  const wrap = document.createElement('div');
  bodyEl.appendChild(wrap);

  const rebuild = () => {
    wrap.innerHTML = '';
    list.forEach((m, i) => {
      const row = document.createElement('div'); row.className = 'mob';
      const id = mini(m.mob_id, 'id', v => { m.mob_id = v; commit(); });
      const cnt = mini(m.count, '×', v => { m.count = v; commit(); });
      const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = m.name || '';
      const del = document.createElement('button'); del.textContent = '✕'; del.style.flex = '0 0 auto';
      del.onclick = () => { list.splice(i, 1); rebuild(); commit(); };
      row.append(id, cnt, nm, del);
      wrap.appendChild(row);
    });
    const add = document.createElement('button');
    add.textContent = '+ mob'; add.style.marginTop = '4px';
    add.onclick = () => { list.push({ mob_id: 1, count: 1, name: '' }); rebuild(); commit(); };
    wrap.appendChild(add);
  };
  const commit = () => queueFieldEdit(pick, { basic: rec.extra.basic || [], tactics: rec.extra.tactics || [] });
  rebuild();
}

// --- tiny DOM helpers ----------------------------------------------------
function kv(k, v) {
  const dl = document.createElement('dl');
  dl.innerHTML = `<dt>${k}</dt><dd>${v}</dd>`;
  return dl;
}
function hr() { bodyEl.appendChild(document.createElement('hr')); }
function note(t) { const d = document.createElement('div'); d.className = 'muted'; d.textContent = t; d.style.marginBottom = '4px'; bodyEl.appendChild(d); }

function numField(label, val, on, hint) {
  const f = document.createElement('div'); f.className = 'field';
  const l = document.createElement('label'); l.textContent = label;
  const inp = document.createElement('input'); inp.type = 'number'; inp.value = val;
  inp.title = hint || '';
  inp.onchange = () => on(parseFloat(inp.value));
  f.append(l, inp); bodyEl.appendChild(f);
  if (hint) { const h = document.createElement('div'); h.className = 'muted'; h.style.margin = '-2px 0 4px 70px'; h.textContent = hint; bodyEl.appendChild(h); }
}
function textField(label, val, on) {
  const f = document.createElement('div'); f.className = 'field';
  const l = document.createElement('label'); l.textContent = label;
  const inp = document.createElement('input'); inp.type = 'text'; inp.value = val;
  inp.onchange = () => on(inp.value);
  f.append(l, inp); bodyEl.appendChild(f);
}
function mini(val, ph, on) {
  const inp = document.createElement('input'); inp.type = 'number'; inp.value = val; inp.placeholder = ph;
  inp.onchange = () => on(parseInt(inp.value, 10) || 0);
  return inp;
}
