// Thin fetch wrappers around the mapforge backend.
export async function getZones() {
  return (await fetch('/api/zones')).json();
}
export async function getZone(key) {
  const r = await fetch(`/api/zone/${encodeURIComponent(key)}`);
  if (!r.ok) throw new Error(`zone ${key}: HTTP ${r.status}`);
  return r.json();
}
export async function getPacks(key) {
  return (await fetch(`/api/zone/${encodeURIComponent(key)}/packs`)).json();
}
export async function getMov(key) {
  return (await fetch(`/api/zone/${encodeURIComponent(key)}/mov`)).json();
}
export async function saveOps(key, ops) {
  const r = await fetch(`/api/zone/${encodeURIComponent(key)}/save`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ops }),
  });
  if (!r.ok) throw new Error(`save: HTTP ${r.status}`);
  return r.json();
}

// Binary mesh loader -> {positions, normals, uvs, indices}
const _meshCache = new Map();
export async function getMesh(path) {
  if (_meshCache.has(path)) return _meshCache.get(path);
  const p = (async () => {
    const buf = await (await fetch(`/api/mesh?path=${encodeURIComponent(path)}`)).arrayBuffer();
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== 0x4D534D5A) throw new Error('bad mesh magic');
    const nv = dv.getUint32(4, true), nf = dv.getUint32(8, true), flags = dv.getUint32(12, true);
    let o = 16;
    const positions = new Float32Array(buf, o, nv * 3); o += nv * 12;
    const normals   = new Float32Array(buf, o, nv * 3); o += nv * 12;
    const uvs       = new Float32Array(buf, o, nv * 2); o += nv * 8;
    const indices   = new Uint16Array(buf, o, nf * 3);
    return { positions, normals, uvs, indices, hasUV: !!(flags & 1) };
  })();
  _meshCache.set(path, p);
  return p;
}

export function texUrl(path, alpha) {
  return `/api/texture?path=${encodeURIComponent(path)}${alpha ? '&alpha=1' : ''}`;
}

// MORPH vertex animation: per-frame positions matching the mesh vertex order.
export async function getAnim(zmoPath, meshPath) {
  const r = await fetch(`/api/anim?zmo=${encodeURIComponent(zmoPath)}&mesh=${encodeURIComponent(meshPath)}`);
  if (r.status === 204 || !r.ok) return null;
  const buf = await r.arrayBuffer();
  const dv = new DataView(buf);
  if (dv.getUint32(0, true) !== 0x4D4E4152) return null;
  const frames = dv.getUint32(4, true), nverts = dv.getUint32(8, true), fps = dv.getFloat32(12, true);
  return { frames, nverts, fps, positions: new Float32Array(buf, 16, frames * nverts * 3) };
}
