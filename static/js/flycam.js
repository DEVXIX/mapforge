// UE5-style free-fly camera. Hold RIGHT mouse button to look around (mouse) +
// fly with WASD (Q/E or Space/Ctrl for down/up, Shift to boost, wheel to change
// speed). Rotation and movement are exponentially smoothed so right-click
// left/right swings glide instead of snapping. Releasing RMB hands control back
// to OrbitControls (its target is re-seated in front of the camera).
import * as THREE from 'three';
import { camera, renderer, controls, onFrame } from './scene.js';

const ROT_TAU = 0.07;     // rotation smoothing time constant (s) — higher = smoother
const MOVE_TAU = 0.10;    // velocity smoothing time constant (s)

const s = {
  active: false,
  curYaw: 0, curPitch: 0, tgtYaw: 0, tgtPitch: 0,
  keys: {},
  speed: 7000,            // world units / sec
  vel: new THREE.Vector3(),
  last: 0,
};

const el = renderer.domElement;

// Right mouse is now "look"; let OrbitControls keep left=orbit, middle=pan.
controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: null };
el.addEventListener('contextmenu', e => e.preventDefault());

export function isFlying() { return s.active; }

function syncFromCamera() {
  const d = new THREE.Vector3();
  camera.getWorldDirection(d);
  s.tgtYaw = s.curYaw = Math.atan2(d.y, d.x);
  s.tgtPitch = s.curPitch = Math.asin(THREE.MathUtils.clamp(d.z, -1, 1));
}

el.addEventListener('pointerdown', (e) => {
  if (e.button !== 2) return;
  s.active = true;
  controls.enabled = false;
  syncFromCamera();
  try { el.setPointerCapture(e.pointerId); } catch {}
});

window.addEventListener('pointerup', (e) => {
  if (e.button !== 2 || !s.active) return;
  s.active = false;
  controls.enabled = true;
  // Re-seat the orbit pivot in front so orbiting resumes smoothly.
  const d = new THREE.Vector3();
  camera.getWorldDirection(d);
  controls.target.copy(camera.position).addScaledVector(d, 8000);
});

el.addEventListener('pointermove', (e) => {
  if (!s.active) return;
  const sens = 0.0026;
  s.tgtYaw   -= e.movementX * sens;
  s.tgtPitch -= e.movementY * sens;
  const lim = Math.PI / 2 - 0.02;
  s.tgtPitch = Math.max(-lim, Math.min(lim, s.tgtPitch));
});

window.addEventListener('keydown', (e) => { s.keys[e.code] = true; });
window.addEventListener('keyup',   (e) => { s.keys[e.code] = false; });

el.addEventListener('wheel', (e) => {
  if (!s.active) return;
  e.preventDefault();
  s.speed = THREE.MathUtils.clamp(s.speed * (e.deltaY < 0 ? 1.15 : 0.87), 200, 300000);
}, { passive: false });

onFrame(() => {
  const now = performance.now();
  let dt = (now - s.last) / 1000;
  s.last = now;
  if (!Number.isFinite(dt) || dt <= 0) return;
  if (dt > 0.05) dt = 0.05;

  if (!s.active) { s.vel.multiplyScalar(Math.exp(-dt / MOVE_TAU)); return; }

  // Smooth rotation toward target.
  const rl = 1 - Math.exp(-dt / ROT_TAU);
  s.curYaw   += (s.tgtYaw   - s.curYaw)   * rl;
  s.curPitch += (s.tgtPitch - s.curPitch) * rl;

  const cy = Math.cos(s.curYaw), sy = Math.sin(s.curYaw);
  const cp = Math.cos(s.curPitch), sp = Math.sin(s.curPitch);
  const fwd = new THREE.Vector3(cp * cy, cp * sy, sp);
  const right = new THREE.Vector3(sy, -cy, 0);   // horizontal strafe
  camera.lookAt(camera.position.clone().add(fwd));

  // Movement intent from keys (camera-relative).
  const k = s.keys;
  const move = new THREE.Vector3();
  if (k['KeyW']) move.add(fwd);
  if (k['KeyS']) move.sub(fwd);
  if (k['KeyD']) move.add(right);
  if (k['KeyA']) move.sub(right);
  if (k['KeyE'] || k['Space']) move.z += 1;
  if (k['KeyQ'] || k['ControlLeft']) move.z -= 1;
  if (move.lengthSq() > 0) move.normalize();
  const boost = (k['ShiftLeft'] || k['ShiftRight']) ? 3.5 : 1;

  // Smooth velocity toward target.
  const ml = 1 - Math.exp(-dt / MOVE_TAU);
  s.vel.lerp(move.multiplyScalar(s.speed * boost), ml);
  camera.position.addScaledVector(s.vel, dt);
});
