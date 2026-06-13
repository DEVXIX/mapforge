// Placement — drops a catalog model into the scene as a textured preview and
// queues a backend "place" op (which appends the model to this zone's pack and
// adds the IFO record on save). Placed previews are gizmo-movable until saved.
import * as THREE from 'three';
import { scene } from './scene.js';
import { buildModelGroup } from './objects.js';
import * as Editor from './editor.js';

const TILE = 16000;
const packCache = new Map();        // pack_rel -> Promise<pack dict>
const placed = [];                  // { id, group, op }
let _idSeq = 1;
let _zoneKey = null;
let _onStatus = () => {};

export function initPlacement(zoneKey, onStatus) {
  _zoneKey = zoneKey;
  _onStatus = onStatus || (() => {});
  for (const p of placed) { scene.remove(p.group); }
  placed.length = 0;
}

async function loadPack(item) {
  if (item.kind === 'MORPH') return null;     // MORPH builds from packs.MORPH elsewhere
  if (packCache.has(item.pack)) return packCache.get(item.pack);
  const p = fetch(`/api/pack?path=${encodeURIComponent(item.pack)}`).then(r => r.json());
  packCache.set(item.pack, p);
  return p;
}

// Drop a catalog item at a world point.
export async function placeModel(item, worldPos) {
  let group;
  if (item.kind === 'MORPH') {
    _onStatus('MORPH placement: save then reload to see it'); // rare; handled server-side
    group = new THREE.Group();
  } else {
    const pack = await loadPack(item);
    group = await buildModelGroup(pack, item.index);
  }
  group.position.copy(worldPos);
  const id = _idSeq++;
  group.userData = { kind: 'placed', placeId: id };
  group.traverse(o => { if (o.isMesh) o.userData = { kind: 'placed', placeId: id }; });
  scene.add(group);

  const tx = Math.floor(worldPos.x / TILE), ty = Math.floor(worldPos.y / TILE);
  // tile here is the WORLD tile; the backend save maps to the right file via
  // the same flip used for terrain. We pass the FILE tile by un-flipping Y.
  const fileTile = [tx, worldTileToFileY(ty)];
  const op = Editor.queuePlace({
    tile: fileTile, source_pack: item.pack, source_kind: item.kind,
    source_model: item.index, pos: [worldPos.x, worldPos.y, worldPos.z],
    rot: [0, 0, 0, 1], scale: [1, 1, 1],
  });
  placed.push({ id, group, op });
  selectPlaced(id);
  _onStatus(`placed ${item.name} — drag the gizmo to position, then Save`);
}

let _flipCenterY = 32;
export function setFlipCenter(cy) { _flipCenterY = cy; }
function worldTileToFileY(worldTileY) { return 2 * _flipCenterY - worldTileY; }

export function selectPlaced(id) {
  const p = placed.find(x => x.id === id);
  if (!p) return;
  Editor.attachGizmo(p.group, (obj) => {
    p.op.pos = [obj.position.x, obj.position.y, obj.position.z];
    p.op.rot = [obj.quaternion.x, obj.quaternion.y, obj.quaternion.z, obj.quaternion.w];
    p.op.scale = [obj.scale.x, obj.scale.y, obj.scale.z];
    // keep file tile in sync if dragged across tiles
    p.op.tile = [Math.floor(obj.position.x / TILE), worldTileToFileY(Math.floor(obj.position.y / TILE))];
  });
}

export function isPlacedHit(ud) { return ud && ud.kind === 'placed'; }
export function selectFromHit(ud) { if (ud && ud.placeId) selectPlaced(ud.placeId); }
