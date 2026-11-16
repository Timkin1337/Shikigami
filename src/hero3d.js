// Hero character: VRM model that follows the cursor, blinks, breathes, reacts to clicks; optional ASCII mode.
// Built into assets/hero3d.js by build.sh (three 0.186.1 + three-vrm 3.5.5, same as the app).
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { AsciiEffect } from 'three/examples/jsm/effects/AsciiEffect.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

export function mountHero(box, url, { onProgress, onReady, onError } = {}) {
  const W = () => box.clientWidth, H = () => box.clientHeight;
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
  catch (e) { onError && onError(e); return null; }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(W(), H());
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.className = 'gl';

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(26, W() / H(), 0.1, 20);
  camera.position.set(0, 0.95, 4.1);
  camera.lookAt(0, 0.78, 0);
  scene.add(new THREE.HemisphereLight(0xfff1e0, 0x3a2416, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(1.2, 2, 2.5); scene.add(key);
  const rim = new THREE.DirectionalLight(0xffb060, 1.6); rim.position.set(-2, 1.5, -1.5); scene.add(rim);

  const ascii = new AsciiEffect(renderer, ' .:-=+*#%@', { invert: false, resolution: Math.min(0.3, Math.max(0.22, 110 / W())) });
  ascii.setSize(W(), H());
  ascii.domElement.className = 'ascii';
  box.append(renderer.domElement, ascii.domElement);
  let mode = 'gl';

  const target = new THREE.Object3D(); scene.add(target);
  const mouse = { x: 0, y: 0, sx: 0, sy: 0 };
  addEventListener('pointermove', e => {
    const r = box.getBoundingClientRect();
    mouse.x = Math.max(-1.5, Math.min(1.5, (e.clientX - (r.left + r.width / 2)) / (innerWidth / 2)));
    mouse.y = Math.max(-1.5, Math.min(1.5, (e.clientY - (r.top + r.height * 0.22)) / (innerHeight / 2)));
  }, { passive: true });

  let vrm = null, joy = 0, wave = 0, nextBlink = 1.5, blink = 0, surprise = 0;
  const loader = new GLTFLoader();
  loader.register(p => new VRMLoaderPlugin(p));
  loader.load(url, gltf => {
    vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse(o => { o.frustumCulled = false; });
    if (vrm.lookAt) vrm.lookAt.target = target;
    scene.add(vrm.scene);
    onReady && onReady();
  }, ev => onProgress && ev.total && onProgress(ev.loaded / ev.total), e => onError && onError(e));

  const bone = n => vrm && vrm.humanoid.getNormalizedBoneNode(n);
  const expr = (n, v) => vrm && vrm.expressionManager && vrm.expressionManager.getExpression(n) && vrm.expressionManager.setValue(n, v);
  const damp = (a, b, k, dt) => a + (b - a) * (1 - Math.exp(-k * dt));

  const clock = new THREE.Clock();
  let visible = true, t = 0;
  new IntersectionObserver(es => { visible = es[0].isIntersecting; }).observe(box);

  function frame() {
    requestAnimationFrame(frame);
    const dt = Math.min(clock.getDelta(), 0.05);
    if (!visible || !vrm) return;
    t += dt;
    mouse.sx = damp(mouse.sx, mouse.x, 6, dt); mouse.sy = damp(mouse.sy, mouse.y, 6, dt);
    target.position.set(mouse.sx * 1.6, 1.35 - mouse.sy * 1.0, 2.5);

    const head = bone('head'), neck = bone('neck'), chest = bone('chest') || bone('spine'), spine = bone('spine');
    if (head) { head.rotation.y = mouse.sx * 0.35; head.rotation.x = mouse.sy * 0.18; head.rotation.z = -mouse.sx * 0.06 + Math.sin(t * 0.7) * 0.02; }
    if (neck) { neck.rotation.y = mouse.sx * 0.15; neck.rotation.x = mouse.sy * 0.08; }
    if (chest) chest.rotation.x = Math.sin(t * 1.6) * 0.012;
    if (spine) { spine.rotation.y = mouse.sx * 0.08; spine.rotation.z = Math.sin(t * 0.5) * 0.015; }

    // arms down from T-pose, gentle sway; right arm waves on click
    wave = Math.max(0, wave - dt);
    const w = Math.min(1, wave * 1.5);
    const lu = bone('leftUpperArm'), ru = bone('rightUpperArm'), ll = bone('leftLowerArm'), rl = bone('rightLowerArm');
    if (lu) { lu.rotation.z = -1.32 + Math.sin(t * 1.6) * 0.02; lu.rotation.x = 0.08; }
    if (ll) ll.rotation.y = 0.25;
    if (ru) { ru.rotation.z = (1.32 - Math.sin(t * 1.6) * 0.02) * (1 - w) + (-1.1) * w; ru.rotation.x = 0.08 * (1 - w); }
    if (rl) { rl.rotation.y = -0.25 * (1 - w); rl.rotation.z = w * (-0.5 + Math.sin(t * 12) * 0.45); }
    ['Thumb', 'Index', 'Middle', 'Ring', 'Little'].forEach((f, i) => ['left', 'right'].forEach(s => {
      const b = bone(s + f + 'Proximal'); if (b) b.rotation.z = (s == 'left' ? 1 : -1) * (i ? 0.25 : 0.1);
    }));

    // face
    nextBlink -= dt;
    if (nextBlink < 0) { blink = 0.16; nextBlink = 2 + Math.random() * 3.5; }
    blink = Math.max(0, blink - dt);
    joy = Math.max(0, joy - dt * 0.35); surprise = Math.max(0, surprise - dt * 0.8);
    expr('happy', Math.min(1, joy));
    expr('surprised', Math.min(1, surprise));
    expr('blink', joy > 0.05 || surprise > 0.05 ? 0 : (blink > 0 ? Math.sin(blink / 0.16 * Math.PI) : 0));

    vrm.update(dt);
    if (mode == 'gl') renderer.render(scene, camera); else ascii.render(scene, camera);
  }
  frame();

  addEventListener('resize', () => {
    camera.aspect = W() / H(); camera.updateProjectionMatrix();
    renderer.setSize(W(), H()); ascii.setSize(W(), H());
  });

  return {
    setMode(m) { mode = m; box.dataset.mode = m; },
    get mode() { return mode; },
    poke(kind) { if (kind == 'wave') { wave = 1.6; joy = 1.2; } else if (kind == 'surprise') surprise = 1; else joy = 1.4; }
  };
}
window.mountHero = mountHero;
