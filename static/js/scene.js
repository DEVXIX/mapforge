// Scene/camera/renderer/lights + render loop. Z-up to match ROSE world space.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export const scene = new THREE.Scene();
scene.background = new THREE.Color(0x10141b);
scene.fog = new THREE.Fog(0x10141b, 120000, 700000);

const el = document.getElementById('viewport');
export const camera = new THREE.PerspectiveCamera(
  58, el.clientWidth / el.clientHeight, 50, 6_000_000);
camera.up.set(0, 0, 1);
camera.position.set(520000, 470000, 60000);

export const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(el.clientWidth, el.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
el.appendChild(renderer.domElement);

export const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.screenSpacePanning = false;
controls.target.set(520000, 520000, 0);

// Lighting — a 3-point-ish rig so the shaded terrain + objects read well.
scene.add(new THREE.HemisphereLight(0xbcd4ff, 0x222a33, 0.9));
const sun = new THREE.DirectionalLight(0xfff4e0, 1.15);
sun.position.set(0.6, 0.4, 1).multiplyScalar(100000);
scene.add(sun);
const fill = new THREE.DirectionalLight(0x88aaff, 0.35);
fill.position.set(-0.5, -0.3, 0.4).multiplyScalar(100000);
scene.add(fill);

addEventListener('resize', () => {
  camera.aspect = el.clientWidth / el.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(el.clientWidth, el.clientHeight);
});

const _loopCbs = [];
export function onFrame(cb) { _loopCbs.push(cb); }

function tick() {
  requestAnimationFrame(tick);
  controls.update();
  for (const cb of _loopCbs) cb();
  renderer.render(scene, camera);
}
tick();

// Frame the camera on a world-space bounding box.
export function frameBox(box) {
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3());
  const s = box.getSize(new THREE.Vector3());
  const r = Math.max(s.x, s.y, s.z) * 0.6 + 8000;
  controls.target.copy(c);
  camera.position.set(c.x, c.y - r * 1.1, c.z + r * 0.9);
  camera.updateProjectionMatrix();
}
