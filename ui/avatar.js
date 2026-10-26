// {name} на рабочем столе: VRM-модель в прозрачном окне.
//
// Слои движения (каждый кадр, по порядку):
//   1. клипы — VRMA/Mixamo из data/animations (idle-петля и разовые: greet, dance, …);
//   2. жесты кодом — поклон, помахать, задуматься, скрестить руки, смутиться, потянуться, сон, «подняли»;
//   3. поверх — взгляд за курсором, дыхание, танец, дрожь от тычка; уши и хвост — свои объекты.
// Взаимодействия: погладить по голове, тычки по зонам, перетаскивание, сон без внимания, меню по ПКМ.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from "/vendor/@pixiv/three-vrm-animation/lib/three-vrm-animation.module.js";
import { createRoom, DEFAULT_LAYOUT } from "./room.js";

const K = window.shikigami || {};
let W = innerWidth, H = innerHeight;
const bubble = document.getElementById("bubble");
const stateEl = document.getElementById("state");
const menuEl = document.getElementById("menu");

// ---------- сцена ----------
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(W, H);
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(24, W / H, 0.05, 30);
// «Аниме-свет» как в Unity-компаньонах: один тёплый ключ сбоку-сверху (3/4) отбрасывает тени — от чёлки на лицо,
// от груди на одежду, от юбки на ноги; заполнение слабое, чтобы у MToon была видна теневая сторона; холодный
// контровой отделяет силуэт. 24.10 было ключ 3.0 + заливка 0.9 + контровой 0.9 «в лоб» — белое выжигалось, объёма не было.
const keyLight = new THREE.DirectionalLight(0xfff1e2, Math.PI * 0.75);
keyLight.position.set(1.4, 2.0, 1.4);
const fillLight = new THREE.HemisphereLight(0xe6edff, 0x5a4034, 0.45);
const backLight = new THREE.DirectionalLight(0xbfd6ff, 0.6);
backLight.position.set(-1.2, 1.4, -2);
const shadowTarget = new THREE.Object3D();      // середина фигуры: тень считается в рамке ±1 м вокруг неё
keyLight.target = shadowTarget;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;            // PCFSoft убран в three 0.18x — он и так подменялся этим
keyLight.castShadow = true;
Object.assign(keyLight.shadow.camera, { left: -1, right: 1, top: 1, bottom: -1, near: 0.1, far: 8 });
keyLight.shadow.mapSize.set(2048, 2048);
keyLight.shadow.bias = -0.0004;
keyLight.shadow.normalBias = 0.03;              // меньше — лицо затеняет само себя тёмной линией по подбородку
scene.add(keyLight, fillLight, backLight, shadowTarget);
const lookTarget = new THREE.Object3D();
scene.add(lookTarget);
addEventListener("resize", () => {
  W = innerWidth; H = innerHeight;
  renderer.setSize(W, H);
  camera.aspect = W / H;
  camera.updateProjectionMatrix();
  frame();
});

let vrm = null, modelH = 1.5, cfg = {}, modelKey = "", mixer = null, anim = null, seenLang = null;
const B = {};
const clips = {};            // вид → [AnimationClip]
const goff = {};             // кость → сглаженная добавка жеста (кватернион)
const BONES = ["hips", "spine", "chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder", "leftUpperArm",
  "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand", "leftUpperLeg", "rightUpperLeg",
  "leftLowerLeg", "rightLowerLeg", "leftFoot", "rightFoot"];
const st = {
  emotion: "neutral", emoUntil: 0, speaking: 0, mouth: 0, blinkAt: 2, blinkT: -1,
  thinking: false, listening: false, dancing: false, perched: false, sitting: 0,
  cursor: { x: 0, y: 0 }, win: { x: 0, y: 0, width: W, height: H }, poke: 0, wag: 0.15,
  gesture: null, gestureUntil: 0, gestureT0: 0, gw: 0, lastGesture: null,
  patting: false, sleeping: false, lastActivity: performance.now(), nextIdle: 15,
  idleAction: null, oneShot: null, lookAround: 0,
  // сглаженные величины: всё, что меняется скачком (курсор 30 раз в секунду, вкл/выкл танца), догоняется плавно
  look: { x: 0, y: 0, w: 1 }, danceW: 0, talk: 0, flinch: 0, flinchV: 0, speechOn: false,
};
const clock = new THREE.Clock();
// режим «Домой»: комната-диорама, {name} живёт в ней (см. «комната» ниже)
const home = { on: false, room: null, mode: "idle", target: null, next: 0, sit: false, seatY: 0, wow: [], seen: null,
  layoutKey: "", saved: null, dragIt: null, look: null, busyUntil: 0 };

// ---------- модель ----------
let loadChain = Promise.resolve();
function loadModel(force = false) {   // по очереди: смена модели во время загрузки не смешивает две модели
  loadChain = loadChain.then(() => doLoadModel(force)).catch((e) => say("Не загрузилась модель: " + e.message, 10000));
  return loadChain;
}
async function doLoadModel(force) {
  cfg = await fetch("/api/config").then((r) => r.json()).catch(() => ({ avatar: {} }));
  const lang = cfg.ui?.lang || "auto";
  if (seenLang && lang !== seenLang) location.reload();          // сменили язык — реплики и подписи перечитать
  seenLang = lang;
  window.applyName?.(cfg.assistant_name);                         // сменили имя — облачка с {name} сразу с новым
  const models = await fetch("/api/models").then((r) => r.json()).catch(() => []);
  const names = models.map((m) => m.name ?? m);
  const name = cfg.avatar?.model && names.includes(cfg.avatar.model) ? cfg.avatar.model
    : names[0];
  if (!name) { say("Нет модели. Добавьте .vrm в «Гардеробе».", 8000); return; }
  const per = cfg.avatar?.per_model?.[name] || {};
  const key = name;
  if (!force && key === modelKey && vrm) { frame(); applyTints(per.tint); if (home.on) syncRoom(); return; }   // размер, мебель, цвета
  const first = !vrm;
  const loader = new GLTFLoader();
  loader.register((p) => new VRMLoaderPlugin(p));
  const gltf = await loader.loadAsync(`/models/${encodeURIComponent(name)}`);
  const next = gltf.userData.vrm;
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  (VRMUtils.combineSkeletons || VRMUtils.removeUnnecessaryJoints)?.(gltf.scene);
  if (next.meta?.metaVersion === "0") VRMUtils.rotateVRM0(next);
  next.scene.traverse((o) => { o.frustumCulled = false; });
  // всё готовим до показа: клипы, первая поза; старая модель стоит, пока новая не готова
  const nextAnim = makeProxy(next);
  const nextMixer = new THREE.AnimationMixer(nextAnim.root);
  const nextClips = await buildClips(next);
  if (vrm) { poof(); scene.remove(vrm.scene); VRMUtils.deepDispose(vrm.scene); }
  modelKey = key;
  for (const k of Object.keys(goff)) delete goff[k];
  vrm = next;
  st.baseYaw = vrm.scene.rotation.y;          // у VRM 0.x модель развёрнута на 180°; прогулка добавляет свой поворот
  life.yaw = 0; life.mode = "idle";
  anim = nextAnim;
  mixer = nextMixer;
  mixer.addEventListener("finished", (e) => { if (e.action === st.oneShot) endOneShot(); });
  for (const n of BONES) B[n] = vrm.humanoid.getNormalizedBoneNode(n);
  B.hipsRest = B.hips.position.clone();
  vrm.update(0);
  const box = new THREE.Box3().setFromObject(vrm.scene);
  modelH = box.max.y - box.min.y;
  frame();
  fetch("/api/friendship").then((r) => r.json()).then((f) => { st.friend = f.enabled === false ? 0 : f.level; }).catch(() => {});
  vrm.lookAt.target = lookTarget;
  // Живой взгляд: у моделей глаза доходят до предела (8–12°) только при цели на 90° в сторону, а курсор отводит
  // взгляд градусов на 25 — глаза двигались на 2–3°, работала одна голова, и взгляд казался «стеклянным».
  // Предел достигается уже на 35°, и он чуть шире; голова поворачивается меньше — глаза ведут, голова догоняет.
  const ap = vrm.lookAt.applier;
  for (const r of [ap?.rangeMapHorizontalInner, ap?.rangeMapHorizontalOuter, ap?.rangeMapVerticalUp, ap?.rangeMapVerticalDown]) {
    if (!r) continue;
    r.inputMaxValue = Math.min(r.inputMaxValue, 35);
    r.outputScale = Math.min(r.outputScale * 1.25, 16);
  }
  setClips(nextClips);
  try { addSeatSprings(); } catch (e) { console.warn("юбка сидя:", e); }
  animeRim(next);
  animeLook(next);
  applyTints(per.tint, next);
  poseFrame(0);            // первая поза — уже из анимации покоя
  scene.add(vrm.scene);
  if (first) { setTimeout(() => gesture("wave", 2.4), 600); setTimeout(() => gesture("bow", 1.6), 3000); }
  else { setEmotion("happy", 3, false); say(line("outfit"), 4500); }
}

function frame() {
  if (home.on) return roomCamera();
  if (st.camMode && vrm) {                  // «вебка» поверх игры: голова и плечи крупным планом
    const head = (vrm.humanoid.getNormalizedBoneNode("head") || vrm.scene).getWorldPosition(new THREE.Vector3());
    const cy = head.y - modelH * 0.03;     // от груди до кончиков ушей и шляпы
    const dist = modelH * 0.46 / 2 / Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
    camera.position.set(head.x, cy, head.z + dist);
    camera.lookAt(head.x, cy, head.z);
    return;
  }
  const per = cfg.avatar?.per_model?.[modelKey] || {};
  const scale = (per.scale || 1) * (cfg.avatar?.scale || 1);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  // по высоте и по ширине окна, с запасом: уши, взмах руки, облачко сверху
  const hang = phys?.mode === "hang";
  const h = Math.max(modelH * (st.perched ? 1.12 : hang ? 1.5 : 1.32), modelH * 0.68 / camera.aspect);
  const dist = h / 2 / Math.tan(fov / 2) / scale;
  const cy = st.perched ? modelH * 0.56 : hang ? modelH * 0.6 : modelH * 0.56;
  camera.position.set(0, cy, dist);
  camera.lookAt(0, cy, 0);
}

// ---------- клипы: VRMA и Mixamo FBX ----------
const MIXAMO_MAP = {
  mixamorigHips: "hips", mixamorigSpine: "spine", mixamorigSpine1: "chest", mixamorigSpine2: "upperChest",
  mixamorigNeck: "neck", mixamorigHead: "head", mixamorigLeftShoulder: "leftShoulder", mixamorigLeftArm: "leftUpperArm",
  mixamorigLeftForeArm: "leftLowerArm", mixamorigLeftHand: "leftHand", mixamorigRightShoulder: "rightShoulder",
  mixamorigRightArm: "rightUpperArm", mixamorigRightForeArm: "rightLowerArm", mixamorigRightHand: "rightHand",
  mixamorigLeftUpLeg: "leftUpperLeg", mixamorigLeftLeg: "leftLowerLeg", mixamorigLeftFoot: "leftFoot",
  mixamorigLeftToeBase: "leftToes", mixamorigRightUpLeg: "rightUpperLeg", mixamorigRightLeg: "rightLowerLeg",
  mixamorigRightFoot: "rightFoot", mixamorigRightToeBase: "rightToes",
};

async function loadMixamo(url, vrm) {
  // Перенос анимации Mixamo на VRM (по мотивам примера three-vrm, MIT)
  const asset = await new FBXLoader().loadAsync(url);
  const clip = THREE.AnimationClip.findByName(asset.animations, "mixamo.com") || asset.animations[0];
  const tracks = [];
  const restInv = new THREE.Quaternion(), parentRest = new THREE.Quaternion(), qa = new THREE.Quaternion(), v = new THREE.Vector3();
  const motionHipsH = asset.getObjectByName("mixamorigHips").position.y;
  const vrmHipsH = Math.abs(vrm.humanoid.normalizedRestPose.hips.position[1]);
  const hipsScale = vrmHipsH / motionHipsH;
  for (const track of clip.tracks) {
    const [rig, prop] = track.name.split(".");
    const vrmBone = MIXAMO_MAP[rig];
    const node = vrmBone && vrm.humanoid.getNormalizedBoneNode(vrmBone);
    const rigNode = asset.getObjectByName(rig);
    if (!node || !rigNode) continue;
    rigNode.getWorldQuaternion(restInv).invert();
    rigNode.parent.getWorldQuaternion(parentRest);
    if (track instanceof THREE.QuaternionKeyframeTrack) {
      const vals = track.values.slice();
      for (let i = 0; i < vals.length; i += 4) {
        qa.fromArray(vals, i).premultiply(parentRest).multiply(restInv).toArray(vals, i);
      }
      const isV0 = vrm.meta?.metaVersion === "0";
      tracks.push(new THREE.QuaternionKeyframeTrack(`${node.name}.${prop}`, track.times,
        vals.map((x, i) => (isV0 && i % 2 === 0 ? -x : x))));
    } else if (track instanceof THREE.VectorKeyframeTrack && vrmBone === "hips") {
      const vals = track.values.map((x, i) => (vrm.meta?.metaVersion === "0" && i % 3 !== 1 ? -x : x) * hipsScale);
      tracks.push(new THREE.VectorKeyframeTrack(`${node.name}.${prop}`, track.times, vals));
    }
  }
  return new THREE.AnimationClip("mixamo", clip.duration, tracks);
}

// Микшер three.js не перезаписывает кость, если значение клипа не поменялось с прошлого кадра.
// Поэтому клипы крутят теневой скелет, которого больше никто не трогает, а на модель поза копируется
// каждый кадр заново — и уже поверх неё ложатся жесты, взгляд, дыхание. Иначе на неподвижных участках
// клипа добавки копились бы (или сброс оставлял кость в Т-позе).
function makeProxy(model) {
  const root = new THREE.Group(), map = [];
  let hips = null;
  for (const name of Object.keys(model.humanoid.humanBones)) {
    const node = model.humanoid.getNormalizedBoneNode(name);
    if (!node) continue;
    const p = new THREE.Object3D();
    p.name = node.name;
    p.position.copy(node.position);
    p.quaternion.copy(node.quaternion);
    root.add(p);
    map.push([node, p]);
    if (name === "hips") hips = p;
  }
  return { root, map, hips };
}

// Клип из захвата движения (devtools/bvh2kclip.py): повороты нормализованных костей VRM по кадрам
// и смещение таза в долях роста — подходит любой модели.
const LOWER = /^(hips|left(Upper|Lower)Leg|right(Upper|Lower)Leg|leftFoot|rightFoot|leftToes|rightToes)$/;
async function loadKclip(url, model, upperOnly = false) {
  const c = await fetch(url).then((r) => r.json());
  const v0 = model.meta?.metaVersion === "0";            // у VRM 0.x оси X и Z развёрнуты
  const times = Float32Array.from({ length: c.frames }, (_, i) => i / c.fps);
  const tracks = [];
  if (upperOnly && c.bones.hips && c.bones.spine) {
    // Таз берём от покоя, но у исполнителя таз наклонён, а позвоночник это компенсирует. Без таза компенсация
    // заваливала бы корпус назад — переносим наклон таза (без поворота вокруг вертикали) в позвоночник.
    const h = c.bones.hips, sp = c.bones.spine, qh = new THREE.Quaternion(), qs = new THREE.Quaternion();
    const ry = new THREE.Quaternion(), f = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
    for (let i = 0; i < c.frames; i++) {
      qh.fromArray(h, i * 4);
      f.set(0, 0, 1).applyQuaternion(qh);                   // куда смотрит таз исполнителя
      ry.setFromAxisAngle(up, -Math.atan2(f.x, f.z));       // разворачиваем обратно к зрителю
      qs.fromArray(sp, i * 4).premultiply(qh).premultiply(ry).toArray(sp, i * 4);
    }
  }
  for (const [bone, vals] of Object.entries(c.bones)) {
    const node = model.humanoid.getNormalizedBoneNode(bone);
    if (!node || (upperOnly && LOWER.test(bone))) continue;
    const qv = Float32Array.from(vals, (x, i) => (v0 && (i % 4 === 0 || i % 4 === 2) ? -x : x));
    // походка исполнительницы манерная: таз и плечи ходят сильно — выглядело «вразвалку, как CJ».
    // На ходу ослабляем повороты таза и позвоночника к нейтрали (ноги и руки — как в захвате)
    const calm = c.loop ? { hips: 0.55, spine: 0.6, chest: 0.6, upperChest: 0.6, neck: 0.7 }[bone] : null;
    if (calm) {
      const qa = new THREE.Quaternion(), id = new THREE.Quaternion();
      for (let i = 0; i < qv.length; i += 4) { qa.fromArray(qv, i); id.identity().slerp(qa, calm).toArray(qv, i); }
    }
    tracks.push(new THREE.QuaternionKeyframeTrack(`${node.name}.quaternion`, times, qv));
  }
  const hips = model.humanoid.getNormalizedBoneNode("hips");
  const rest = new THREE.Vector3().fromArray(model.humanoid.normalizedRestPose.hips.position);
  const hh = Math.abs(rest.y) || 1;
  const pv = new Float32Array(c.frames * 3);
  // цикл ходьбы: оставляем боковое покачивание таза и «пульс» шага, убираем только поступательный дрейф
  // (окно едет само); у остальных клипов (танец ходит по площадке) — только вертикаль
  const n1 = c.frames - 1, H3 = c.hips;
  const trend = (k) => (i) => H3[k] + (H3[n1 * 3 + k] - H3[k]) * (i / Math.max(1, n1));
  const tx = trend(0), tz = trend(2);
  for (let i = 0; i < c.frames; i++) {
    const dx = c.loop ? (H3[i * 3] - tx(i)) * hh * 0.45 : 0;   // раскачку таза в стороны — вдвое мягче, иначе «походка вразвалку»
    const dy = H3[i * 3 + 1] * hh;
    const dz = c.loop ? (H3[i * 3 + 2] - tz(i)) * hh : 0;
    pv[i * 3] = rest.x + (v0 ? -dx : dx); pv[i * 3 + 1] = rest.y + dy; pv[i * 3 + 2] = rest.z + (v0 ? -dz : dz);
  }
  if (!upperOnly) tracks.push(new THREE.VectorKeyframeTrack(`${hips.name}.position`, times, pv));
  const clip = new THREE.AnimationClip(url, (c.frames - 1) / c.fps, tracks);
  clip.userData = { speed: c.speed || 0, loop: !!c.loop };     // speed — в высотах таза за секунду (для ходьбы)
  if (c.loop && c.bones.leftUpperLeg) {
    // фаза шага: кадр, где левое бедро вынесено вперёд сильнее всего (вперёд — поворот вокруг X со знаком «минус»)
    let best = 0, bx = Infinity;
    for (let i = 0; i < c.frames; i++) { const x = c.bones.leftUpperLeg[i * 4]; if (x < bx) { bx = x; best = i; } }
    clip.userData.phase0 = best / Math.max(1, c.frames - 1);
  }
  return clip;
}

async function buildClips(model) {
  const out = {};
  const list = await fetch("/api/animations").then((r) => r.json()).catch(() => []);
  const gl = new GLTFLoader();
  gl.register((p) => new VRMAnimationLoaderPlugin(p));
  for (const a of list) {
    try {
      let clip;
      if (a.name.toLowerCase().endsWith(".kclip.json")) {
        // жесты — только верх тела: ноги и таз остаются от покоя (у захвата другая ширина таза — ступни заходили бы одна за другую)
        clip = await loadKclip(a.url, model, !["dance", "idle", "walk"].includes(a.kind));
      } else if (a.name.toLowerCase().endsWith(".vrma")) {
        const g = await gl.loadAsync(a.url);
        clip = createVRMAnimationClip(g.userData.vrmAnimations[0], model);
      } else {
        clip = await loadMixamo(a.url, model);
      }
      // клип без рук оставил бы их в Т-позе — такой пропускаем. Проверяем по разметке костей VRM, а не по тексту
      // имён: у моделей из Blender кости зовутся не «UpperArm» (Arm_L, upper_arm.L…), и раньше все клипы
      // на них выбрасывались — модель стояла в исходной позе с поднятыми руками.
      const armNodes = ["leftUpperArm", "rightUpperArm"].map((b) => model.humanoid.getNormalizedBoneNode(b)?.name).filter(Boolean);
      if (!clip.tracks.some((t) => armNodes.includes(t.name.slice(0, t.name.lastIndexOf("."))))) { console.warn("неполный клип пропущен", a.name); continue; }
      clip.name = a.name;
      (out[a.kind] ??= []).push(clip);
    } catch (e) { console.warn("анимация не загрузилась", a.name, e); }
  }
  return out;
}
let LOWER_NODES = new Set();
function setClips(next) {
  LOWER_NODES = new Set(Object.keys(vrm.humanoid.humanBones).filter((b) => LOWER.test(b))
    .map((b) => vrm.humanoid.getNormalizedBoneNode(b)?.name).filter(Boolean));
  mixer.stopAllAction();
  for (const k of Object.keys(clips)) delete clips[k];
  Object.assign(clips, next);
  st.oneShot = null;
  st.legsOff = false;
  const idle = clips.idle?.[0];
  st.idleAction = idle ? mixer.clipAction(idle).setLoop(THREE.LoopRepeat).play() : null;
  // Ноги и таз из покоя — отдельным слоем с полным весом: жесты без ног меняют только верх тела,
  // а при переходах ноги не проседают к исходной позе
  if (idle) {
    const lower = idle.tracks.filter((tr) => { const n = tr.name.split(".")[0]; return LOWER_NODES.has(n); });
    const legs = new THREE.AnimationClip("idle-legs", idle.duration, lower);
    st.legsAction = mixer.clipAction(legs).setLoop(THREE.LoopRepeat).play();
    st.legsAction.time = st.idleAction.time = 0;
  }
}
async function reloadClips() {     // в «Гардеробе» добавили или убрали анимацию
  if (!vrm) return;
  const model = vrm, next = await buildClips(model);
  if (model === vrm) setClips(next);
}

function playClip(kind) {
  const list = clips[kind];
  if (!list?.length || !mixer) return false;
  const clip = list[Math.floor(Math.random() * list.length)];
  const a = mixer.clipAction(clip);
  const from = st.oneShot || st.idleAction;
  if (from === a && a.isRunning()) return true;
  a.reset().setLoop(kind === "dance" || kind === "walk" ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
  a.clampWhenFinished = true;
  a.setEffectiveWeight(1).play();
  if (from && from !== a) from.crossFadeTo(a, 0.5, false); else a.fadeIn(0.5);
  // клип со своими ногами (танец) — слой ног покоя уступает ему, иначе ноги танцевали бы вполсилы
  const full = clip.tracks.some((tr) => LOWER_NODES.has(tr.name.split(".")[0]));
  if (full && st.legsAction && !st.legsOff) { st.legsAction.fadeOut(0.5); st.legsOff = true; }
  if (!full && st.legsOff) { st.legsAction.reset().fadeIn(0.5); st.legsOff = false; }
  st.oneShot = a;
  return true;
}
function endOneShot() {
  const a = st.oneShot;
  if (!a) return;
  st.oneShot = null;
  const idle = st.idleAction;
  if (!idle) { a.fadeOut(0.6); return; }
  idle.reset().setEffectiveWeight(1).play();
  if (st.legsAction) idle.time = st.legsAction.time;          // верх и ноги покоя — в одной фазе
  a.crossFadeTo(idle, 0.6, false);
  if (st.legsOff) { st.legsAction.reset().fadeIn(0.6); st.legsAction.time = idle.time; st.legsOff = false; }
}

// ---------- аниме-ободок ----------
// У MToon (шейдер почти всех VRM) есть ободок света по контуру, но многие модели его не включают.
// Мягкий тёплый ободок отделяет фигуру от фона — так выглядят аниме-кадры. Свой ободок модели не трогаем.
function animeRim(model) {
  if (cfg.avatar?.rim === false) return;
  model.scene.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (!m?.isMToonMaterial || !m.parametricRimColorFactor) continue;
      const c = m.parametricRimColorFactor;
      if (c.r + c.g + c.b > 0.05) continue;                 // автор модели задал свой — оставляем
      c.setRGB(0.32, 0.27, 0.24);
      m.parametricRimFresnelPowerFactor = 3.5;
      m.parametricRimLiftFactor = 0.04;
      m.rimLightingMixFactor = 0.6;
    }
  });
}

// Тени и обводка. Обводку VRoid задаёт в 0,8 мм — на экране её не видно, фигура сливается в пятно;
// вдвое толще — контур как в аниме. Тени — от ключевого света (см. «сцена»).
function animeLook(model) {
  const box = new THREE.Box3().setFromObject(model.scene);
  shadowTarget.position.set(0, (box.min.y + box.max.y) / 2, 0);
  const k = cfg.avatar?.outline ?? 2;
  renderer.shadowMap.enabled = keyLight.castShadow = cfg.avatar?.shadows !== false;   // выключены — карта теней не считается
  model.scene.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = o.receiveShadow = cfg.avatar?.shadows !== false;
    for (const m of [].concat(o.material || [])) {
      if (m?.isMToonMaterial && m.outlineWidthFactor) m.outlineWidthFactor *= k;
    }
  });
}

// ---------- перекраска: волосы, глаза, одежда ----------
// Умножением цвета чёрные волосы розовыми не сделать — перерисовываем текстуру: оттенок и насыщенность берём у
// выбранного цвета, светотень оставляем родной (яркость каждого пикселя масштабируется к яркости выбранного цвета).
// Части — по именам материалов (VRoid: Hair, EyeIris, Tops/Bottoms/Onepiece/Shoes…); у моделей не из VRoid может не найтись.
const TINT_PARTS = { hair: /hair|foxtail|catear|髪/i, eyes: /iris|eye_?iris|瞳/i, clothes: /cloth|tops|bottoms|onepiece|shoes|outer|dress|skirt|coat|服/i };
function tintPart(name) {
  if (/face|skin|body|mouth|brow|eyeline|eyelash|highlight|white/i.test(name) && !/hair/i.test(name)) return null;
  return Object.keys(TINT_PARTS).find((k) => TINT_PARTS[k].test(name)) || null;
}
function rgb2hsl(r, g, b) {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
  if (mx === mn) return [0, 0, l];
  const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  const h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h / 6, s, l];
}
function hsl2rgb(h, s, l) {
  if (!s) return [l, l, l];
  const q2 = l < 0.5 ? l * (1 + s) : l + s - l * s, p2 = 2 * l - q2;
  const f = (t) => { t = (t + 1) % 1; return t < 1 / 6 ? p2 + (q2 - p2) * 6 * t : t < 0.5 ? q2 : t < 2 / 3 ? p2 + (q2 - p2) * (2 / 3 - t) * 6 : p2; };
  return [f(h + 1 / 3), f(h), f(h - 1 / 3)];
}
const _tintCache = new Map();          // исходная текстура + цвет → перекрашенная (одна текстура на несколько материалов)
function recolorTex(src, hex) {
  if (!src?.image) return null;
  const key = src.uuid + hex;
  if (_tintCache.has(key)) return _tintCache.get(key);
  const img = src.image, w = img.width, h = img.height;
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, w, h), px = d.data;
  const tc = new THREE.Color(hex), [th, ts, tl] = rgb2hsl(tc.r, tc.g, tc.b);
  let sum = 0, n = 0;
  for (let i = 0; i < px.length; i += 16) if (px[i + 3] > 20) { sum += (Math.max(px[i], px[i + 1], px[i + 2]) + Math.min(px[i], px[i + 1], px[i + 2])) / 510; n++; }
  const mean = n ? Math.max(0.04, sum / n) : 0.5;
  for (let i = 0; i < px.length; i += 4) {
    if (px[i + 3] < 8) continue;
    const [, s, l] = rgb2hsl(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255);
    const [r, gg, b] = hsl2rgb(th, Math.min(1, ts * 0.7 + s * 0.3), Math.min(1, l * tl / mean));   // насыщенность мягче — без кислотности
    px[i] = r * 255; px[i + 1] = gg * 255; px[i + 2] = b * 255;
  }
  g.putImageData(d, 0, 0);
  const t = new THREE.CanvasTexture(c);
  for (const k of ["flipY", "wrapS", "wrapT", "colorSpace", "magFilter", "minFilter", "anisotropy"]) t[k] = src[k];
  t.needsUpdate = true;
  _tintCache.set(key, t);
  return t;
}
function applyTints(tint = {}, model = vrm) {
  if (!model) return;
  model.scene.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (!m?.isMToonMaterial) continue;
      const part = tintPart(m.name || "");
      if (!part) continue;
      m.userData.orig ??= { map: m.map, shade: m.shadeMultiplyTexture };
      const hex = tint?.[part];
      m.map = hex ? recolorTex(m.userData.orig.map, hex) || m.userData.orig.map : m.userData.orig.map;
      if (m.userData.orig.shade) m.shadeMultiplyTexture = hex ? recolorTex(m.userData.orig.shade, hex) || m.userData.orig.shade : m.userData.orig.shade;
      m.needsUpdate = true;
    }
  });
}
function tintParts() {                  // какие части нашлись у текущей модели — показать только их ползунки
  const found = new Set();
  vrm?.scene.traverse((o) => { for (const m of [].concat(o.material || [])) { const p = m?.isMToonMaterial && tintPart(m.name || ""); if (p) found.add(p); } });
  return [...found];
}

// ---------- фоторежим: поза + PNG с прозрачным фоном ×2 ----------
async function takePhoto(pose, emotion) {
  activity();
  if (st.pastime) endPastime();
  if (emotion) setEmotion(emotion, 4, false);
  if (pose) gesture(pose, 3.2);
  hideBubble?.();
  await new Promise((r) => setTimeout(r, 1300));             // поза и выражение успевают встать
  const r0 = renderer.getPixelRatio();
  renderer.setPixelRatio(2); renderer.setSize(innerWidth, innerHeight);
  if (vrm) { poseFrame(0); }
  renderer.render(scene, camera);           // частицы (листья, тыквы) в снимок не берём — только она сама
  const c = document.createElement("canvas");
  c.width = renderer.domElement.width; c.height = renderer.domElement.height;
  const g = c.getContext("2d");
  g.drawImage(renderer.domElement, 0, 0);
  renderer.setPixelRatio(r0); renderer.setSize(innerWidth, innerHeight);
  // обрезаем пустые поля: фигура по центру, без прозрачной пустоты вокруг
  const d = g.getImageData(0, 0, c.width, c.height).data;
  let x0 = c.width, y0 = c.height, x1 = 0, y1 = 0;
  for (let y = 0; y < c.height; y += 2) for (let x = 0; x < c.width; x += 2) if (d[(y * c.width + x) * 4 + 3] > 10) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  const pad = 24, out = document.createElement("canvas");
  if (x1 > x0) {
    x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad); x1 = Math.min(c.width, x1 + pad); y1 = Math.min(c.height, y1 + pad);
    out.width = x1 - x0; out.height = y1 - y0; out.getContext("2d").drawImage(c, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
  } else { out.width = c.width; out.height = c.height; out.getContext("2d").drawImage(c, 0, 0); }
  const png = out.toDataURL("image/png").split(",")[1];
  await fetch("/api/avatar/photo/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ png }) });
}

// ---------- юбка сидя ----------
// Сидя бёдра уходят вперёд, а передние полотна юбки физика укладывает прямо внутрь коллайдеров бёдер — и те
// выталкивают ткань, чаще всего вверх (у Goth Maid и Рэм юбка задиралась на живот). Раньше мы только сдвигали
// «положение покоя» — у одних моделей хватало, у других нет. Теперь укладываем сами: после шага физики каждое
// звено юбки тянется к верху бедра (с запасом на толщину ноги), а за коленом свисает вниз; итог отдаём физике
// как её же состояние без скорости — встаёт без рывка. «Перед» считается по направлению бёдер в моменте, так
// что работает с любой моделью: передние полотна ложатся на колени, боковые наполовину, задние не трогаем.
const seat = { joints: [], k: 0 };
const _v = [0, 1, 2, 3, 4, 5].map(() => new THREE.Vector3());
const _m4 = new THREE.Matrix4();
const UP = new THREE.Vector3(0, 1, 0);
function addSeatSprings() {
  seat.joints = [];
  const sbm = vrm.springBoneManager;
  if (!sbm?.joints?.size) return;
  const all = [...sbm.joints];
  const bones = new Set(all.map((j) => j.bone));
  const spine = vrm.humanoid.getRawBoneNode("spine") || vrm.humanoid.getRawBoneNode("hips");
  vrm.scene.updateMatrixWorld(true);
  const waist = spine.getWorldPosition(new THREE.Vector3()).y;
  for (const root of all) {
    if (bones.has(root.bone.parent)) continue;                                  // корень цепочки
    if (root.bone.getWorldPosition(_v[0]).y > waist + modelH * 0.02) continue;   // выше пояса — волосы, не юбка
    if (/tail|尻尾|しっぽ/i.test(root.bone.name)) continue;                       // хвост растёт у пояса, но это не ткань
    const chain = [root];
    for (let i = 0; i < chain.length && chain.length < 40; i++) chain.push(...all.filter((x) => x.bone.parent === chain[i].bone));
    const rp = root.bone.getWorldPosition(new THREE.Vector3());
    const reach = Math.max(...chain.map((x) => x.bone.getWorldPosition(_v[0]).distanceTo(rp)));   // длина полотна
    for (const j of chain) {
      j.__reach = reach;
      if (j.__seat) continue;
      const orig = j.update.bind(j);
      j.update = (delta) => { orig(delta); if (seat.k > 0.001 && delta > 0) drape(j, root); };
      j.__seat = true;
      seat.joints.push(j);
    }
  }
}
function legInfo(side) {
  const hip = vrm.humanoid.getRawBoneNode(side + "UpperLeg"), knee = vrm.humanoid.getRawBoneNode(side + "LowerLeg");
  if (!hip || !knee) return null;
  const h = hip.getWorldPosition(new THREE.Vector3()), kn = knee.getWorldPosition(new THREE.Vector3());
  const len = h.distanceTo(kn);
  return { h, kn, len, dir: kn.clone().sub(h).divideScalar(len || 1) };
}
function seatTick() {
  seat.k = st.sitting < 0.001 ? 0 : st.sitting;
  if (!seat.k || !seat.joints.length) return;
  seat.legs = [legInfo("left"), legInfo("right")].filter(Boolean);
  seat.r = modelH * 0.05;                                   // толщина бедра: ткань лежит сверху, а не в ноге
  const [a, b] = seat.legs;
  seat.mid = b ? a.h.clone().add(b.h).multiplyScalar(0.5) : a.h.clone();
  seat.midDir = b ? a.dir.clone().add(b.dir).setY(0).normalize() : a.dir.clone().setY(0).normalize();
}
function drape(j, root) {
  if (!seat.legs?.length) return;
  const p = _v[0].setFromMatrixPosition(j.bone.matrixWorld);
  const leg = seat.legs.length > 1 && p.distanceToSquared(seat.legs[1].h) < p.distanceToSquared(seat.legs[0].h) ? seat.legs[1] : seat.legs[0];
  // насколько это полотно «спереди»: корень цепочки относительно таза — вдоль бедра
  const rp = _v[1].setFromMatrixPosition(root.bone.matrixWorld).sub(leg.h).setY(0);
  const fd = _v[2].copy(leg.dir).setY(0);
  const front = rp.lengthSq() > 1e-8 && fd.lengthSq() > 1e-8 ? rp.normalize().dot(fd.normalize()) : 0;
  const w = seat.k * THREE.MathUtils.smoothstep(front, -0.35, 0.55);
  if (w < 0.01) return;
  const len = j._worldSpaceBoneLength || 0.05;
  const rel = _v[3].copy(p).sub(leg.h), s = rel.dot(leg.dir);
  const target = _v[4];
  if (s + len < leg.len * 0.95) {
    // над бедром: точка на бедре дальше на длину звена, приподнятая на толщину ноги; сбоку — сохраняем отступ
    const side = rel.clone().addScaledVector(leg.dir, -s);
    side.addScaledVector(UP, -side.dot(UP));
    target.copy(leg.h).addScaledVector(leg.dir, s + len).addScaledVector(UP, seat.r).add(side);
  } else {
    target.copy(p).addScaledVector(UP, -len).addScaledVector(leg.dir, len * 0.15);   // за коленом — вниз
  }
  const want = target.sub(p).normalize();
  // самые передние полотна опускаем в ложбинку между бёдрами: короткий перед юбки (Рэм) иначе кончается у сгиба
  // бедра, и камера на уровне колен смотрит под подол
  // длинную юбку и пальто не трогаем — они и так ложатся на колени, а в ложбинке стягивались конусом
  const g = THREE.MathUtils.smoothstep(front, 0.55, 0.9) * (1 - THREE.MathUtils.smoothstep((j.__reach || 0) / leg.len, 0.75, 1.0));
  if (g > 0) {
    const gap = _v[1].copy(seat.midDir).multiplyScalar(0.55).addScaledVector(UP, -0.85);
    const lat = _v[2].copy(seat.mid).sub(p);
    lat.addScaledVector(seat.midDir, -lat.dot(seat.midDir)).setY(0);
    gap.addScaledVector(lat, 4).normalize();
    want.lerp(gap, g).normalize();
  }
  const cur = _v[5].copy(j._currentTail).applyMatrix4(j._getMatrixCenterToWorld()).sub(p).normalize();
  const tail = cur.lerp(want, w).normalize().multiplyScalar(len).add(p);
  j._currentTail.copy(tail).applyMatrix4(j._getMatrixWorldToCenter());
  j._prevTail.copy(j._currentTail);
  const inv = _m4.multiplyMatrices(j._parentMatrixWorld, j._initialLocalMatrix).invert();
  j.bone.quaternion.setFromUnitVectors(j._boneAxis, tail.applyMatrix4(inv).normalize()).premultiply(j._initialLocalRotation);
  j.bone.updateMatrix();
  j.bone.matrixWorld.multiplyMatrices(j._parentMatrixWorld, j.bone.matrix);
}

// ---------- прикрепить предмет к кости (шляпа на Хэллоуин) ----------
function attach(child, bone, worldPos, worldQuat) {
  bone.updateWorldMatrix(true, false);
  child.position.copy(worldPos.clone().applyMatrix4(new THREE.Matrix4().copy(bone.matrixWorld).invert()));
  const bq = new THREE.Quaternion();
  bone.getWorldQuaternion(bq);
  child.quaternion.copy(bq.invert().multiply(worldQuat));
  bone.add(child);
}

// ---------- пальцы ----------
// Ни в покое, ни в захвате движения пальцев нет — без этого ладони стоят «дощечками».
// Расслабленная кисть: пальцы согнуты каскадом (мизинец сильнее), большой чуть к ладони; лёгкое шевеление.
// Когда машет или показывает рукой — ладонь раскрыта.
const FINGERS = [["Index", 0.8, 0.07], ["Middle", 1.0, 0], ["Ring", 1.1, -0.05], ["Little", 1.22, -0.11]];
const JOINTS = [["Proximal", 0.38], ["Intermediate", 0.62], ["Distal", 0.42]];
const OPEN_HAND = /wave|greet|call|guide|happy/;
function fingersTick(dt, t, v0) {
  const shot = st.oneShot?.getClip().name || "";
  st.curl = damp(st.curl ?? 0.75, st.ptGrip ? st.ptGrip : OPEN_HAND.test(shot) ? 0.22 : st.patting ? 0.9 : 0.75, 4, dt);
  for (const side of ["left", "right"]) {
    const s = side === "left" ? -1 : 1;                   // сгиб к ладони: у левой руки вокруг Z «минус», у правой «плюс»
    FINGERS.forEach(([f, k, spread], fi) => {
      const wob = Math.sin(t * 0.7 + fi * 0.9 + (side === "left" ? 0 : 2)) * 0.05;
      JOINTS.forEach(([j, a], ji) => {
        const node = vrm.humanoid.getNormalizedBoneNode(side + f + j);
        if (!node) return;
        const ang = (st.curl + wob) * a * k;
        node.quaternion.setFromEuler(e3.set(0, ji === 0 ? -s * spread * v0 : 0, s * ang * v0));
      });
    });
    // большой палец: к ладони и чуть согнут
    const th = [["ThumbMetacarpal", 0.25, 0.15], ["ThumbProximal", 0.1, 0.3], ["ThumbDistal", 0, 0.35]];
    for (const [j, y, z] of th) {
      const node = vrm.humanoid.getNormalizedBoneNode(side + j);
      if (node) node.quaternion.setFromEuler(e3.set(0, s * y * st.curl * v0, s * z * st.curl * v0));
    }
  }
}

// ---------- живая ходьба: верх тела поверх клипа ----------
// Сведено из ответов ChatGPT/Gemini/DeepSeek и замеров клипа: в женственной походке руки почти не машут
// (плечо 7–17°), поэтому добавляем противоход до ~22°, локоть, скручивание груди против таза, наклон по скорости,
// стабилизацию головы и «подхват» корпуса на старте и остановке. Всё умножается на вес походки — включается плавно.
const walkSt = { w: 0, pulse: 0, pulseV: 0, wasWalking: false };
function walkLayer(dt, t, v0) {
  const a = life.mode === "walk" ? st.oneShot : null;
  const clip = a?.getClip();
  const target = a && clip?.userData?.loop ? a.getEffectiveWeight() : 0;
  walkSt.w = damp(walkSt.w, target, 8, dt);
  const walking = target > 0.5;
  if (walking !== walkSt.wasWalking) {          // старт — корпус подаётся вперёд, остановка — чуть назад
    walkSt.pulseV += walking ? 2.2 : -1.4;
    walkSt.wasWalking = walking;
  }
  walkSt.pulseV += (-60 * walkSt.pulse - 10 * walkSt.pulseV) * dt;
  walkSt.pulse += walkSt.pulseV * dt;
  const w = walkSt.w;
  if (w < 0.01 && Math.abs(walkSt.pulse) < 0.002) return;
  const ph0 = clip?.userData?.phase0 ?? walkSt.lastPh0 ?? 0;
  walkSt.lastPh0 = ph0;
  const p = a ? 2 * Math.PI * (a.time / clip.duration - ph0) : walkSt.lastP ?? 0;
  walkSt.lastP = p;
  const c = Math.cos(p), D = Math.PI / 180;
  // руки: левая назад, когда левая нога впереди (p=0); локоть сгибается сильнее на махе вперёд, с запаздыванием
  const swing = 10 * D * w, elbow = 8 * D * w;
  const fwdL = -c, fwdR = c;                    // +1 — рука впереди
  const cl = Math.cos(p - 0.35);
  B.leftUpperArm?.quaternion.premultiply(qt.setFromEuler(e3.set(-swing * fwdL * v0, 0, 0)));
  B.rightUpperArm?.quaternion.premultiply(qt.setFromEuler(e3.set(-swing * 0.9 * fwdR * v0, 0, 0)));   // руки чуть несимметричны — не маятник
  addRot(B.leftLowerArm, 0, -elbow * (0.6 + 0.4 * -cl) * v0, 0);
  addRot(B.rightLowerArm, 0, elbow * (0.6 + 0.4 * cl) * v0, 0);
  // грудь против таза: правое плечо вперёд вместе с левой ногой; голова гасит 80% — взгляд держит горизонт
  const twist = 4 * D * w * Math.cos(p - 0.26);          // грудь отстаёт от таза на ~15° фазы
  addRot(B.spine, 2.5 * D * w * v0, 0, 0);                       // на ходу подаётся вперёд: у модели своя стойка с головой позади таза
  addRot(B.chest || B.spine, (3.5 * D * w + walkSt.pulse * 0.08) * v0, twist, 0);
  addRot(B.neck, 0, -twist * 0.35, 0);                   // голова гасит ~65%: жёсткая стабилизация выглядит роботом
  addRot(B.head, 0, -twist * 0.3, 0);
}

// ---------- жесты кодом ----------
// Руки задаются «для левой» (x — вперёд, отрицательный; y — поворот внутрь; z — опустить, -1.3 ≈ вдоль тела),
// правая зеркалится. Порядок Эйлера XYZ: сначала опускаем (z), потом подкручиваем (y), потом выносим вперёд (x).
const arm = (side, U, Lo, Hd) => {
  const m = side === "right" ? [1, -1, -1] : [1, 1, 1];
  const out = { [`${side}UpperArm`]: U.map((v, i) => v * m[i]), [`${side}LowerArm`]: Lo.map((v, i) => v * m[i]) };
  if (Hd) out[`${side}Hand`] = Hd.map((v, i) => v * m[i]);
  return out;
};
const both = (U, Lo, Hd) => ({ ...arm("left", U, Lo, Hd), ...arm("right", U, Lo, Hd) });
const REST = both([0, 0, -1.3], [0, -0.2, 0]);
const GESTURES = {
  bow: () => ({ ...both([-0.25, -0.75, -1.25], [0, -1.35, 0]), spine: [0.32, 0, 0], chest: [0.15, 0, 0], neck: [0.18, 0, 0] }),
  wave: (t) => ({ ...arm("left", [0, 0, -1.3], [0, -0.2, 0]), ...arm("right", [-0.3, 0, 0.25], [0, 0, 1.45 + Math.sin(t * 9) * 0.35], [0, 0, 0]),
    head: [0, 0, -0.1] }),
  think: () => ({ ...arm("left", [-0.45, -0.85, -1.3], [0, -1.6, 0]), ...arm("right", [-0.95, -0.45, -1.15], [0, -2.25, 0]),
    neck: [-0.06, 0, 0.12], head: [-0.06, 0, 0.08] }),
  shy: () => ({ ...both([-0.5, -0.85, -1.25], [0, -2.0, 0]), neck: [0.22, 0.3, 0.08], spine: [0.06, 0, 0] }),
  angry: () => ({ ...arm("left", [-0.55, -1.05, -1.3], [0, -2.05, 0]), ...arm("right", [-0.45, -1.0, -1.3], [0, -2.1, 0]),
    neck: [-0.12, -0.45, 0], spine: [-0.03, 0, 0] }),
  happy: (t) => ({ ...both([-0.45, -0.8, -1.2], [0, -1.8, 0]), neck: [0, 0, 0.12 * Math.sin(t * 3)], hipsBounce: 0.012 }),
  sad: () => ({ ...both([0, 0, -1.42], [0, -0.1, 0]), neck: [0.28, 0, 0], spine: [0.08, 0, 0] }),
  surprised: () => ({ ...both([-0.3, 0, -0.95], [0, -0.9, 0]), spine: [-0.06, 0, 0], neck: [-0.08, 0, 0] }),
  stretch: () => ({ ...both([0, 0, 1.3], [0, 0, 0.15]), spine: [-0.1, 0, 0], neck: [-0.2, 0, 0] }),
  listen: () => ({ spine: [0.05, 0, 0], neck: [0, 0, 0.1] }),
  presneeze: (t) => ({ neck: [-0.2 * Math.min(1, t / 0.8), 0, 0], head: [-0.12 * Math.min(1, t / 0.8), 0, 0.05] }),
  pat: () => ({ ...both([-0.25, -0.75, -1.25], [0, -1.35, 0]), neck: [0.08, 0, 0.14], spine: [0.04, 0, 0] }),
  sleep: (t) => ({ ...REST, neck: [0.45, 0, 0.22], head: [0.1, 0, 0.1], spine: [0.12 + Math.sin(t * 0.9) * 0.02, 0, 0] }),
  dangle: (t) => ({ ...arm("left", [-0.2, 0, -0.5 + Math.sin(t * 11) * 0.25], [0, -0.6, 0]),
    ...arm("right", [-0.2, 0, -0.5 + Math.sin(t * 11 + 1.5) * 0.25], [0, -0.6, 0]),
    leftUpperLeg: [-0.2 + Math.sin(t * 9) * 0.35, 0, 0], rightUpperLeg: [-0.2 - Math.sin(t * 9) * 0.35, 0, 0],
    leftLowerLeg: [0.6, 0, 0], rightLowerLeg: [0.6, 0, 0] }),
  land: () => ({ leftUpperLeg: [-0.55, 0, 0], rightUpperLeg: [-0.55, 0, 0], leftLowerLeg: [1.0, 0, 0], rightLowerLeg: [1.0, 0, 0],
    spine: [0.15, 0, 0], hipsDrop: 0.06 }),
};
const EMO_GESTURE = { angry: "angry", shy: "shy", happy: "happy", sad: "sad", surprised: "surprised" };

function gesture(name, sec = 2.5, fromEmotion = false) {
  if (st.sleeping && name !== "sleep") return;
  // клип всем телом (если есть файл) важнее жеста кодом; но от эмоций — не чаще раза в 25 с,
  // и не когда она сидит на окне или висит в руке
  const t = clock.elapsedTime;
  const clipOk = !st.perched && !st.dragging && (!fromEmotion || t - (st.lastClipAt ?? -99) > 25);
  if (clipOk && playClip(name)) {
    st.lastClipAt = t;
    // танец по просьбе зациклен — без таймера он не кончался никогда; под музыку его гасит опрос плеера
    if (name === "dance") {
      const shot = st.oneShot;
      clearTimeout(st.danceTimer);
      st.danceTimer = setTimeout(() => { if (st.oneShot === shot && !st.dancing) endOneShot(); }, Math.max(4, sec) * 1000);
    }
    return;
  }
  if (name === "dance") { st.forceDanceUntil = clock.elapsedTime + sec; return; }
  st.gesture = name; st.gestureUntil = clock.elapsedTime + sec; st.gestureT0 = clock.elapsedTime;
}
const q = new THREE.Quaternion(), qt = new THREE.Quaternion(), e3 = new THREE.Euler();

// Позы рук/ног кодом выглядят деревянно, поэтому жесты кодом двигают только корпус, шею и голову (GBONES),
// а руки и ноги — файлы анимаций.

// ---------- кадр ----------
const EMO = { happy: { happy: 0.8 }, shy: { happy: 0.35 }, angry: { angry: 0.6 }, sad: { sad: 0.6 },
  surprised: { surprised: 0.8 }, relaxed: { relaxed: 0.7 }, neutral: {} };
const EXPRS = ["happy", "angry", "sad", "surprised", "relaxed"];
const cur = Object.fromEntries(EXPRS.map((e) => [e, 0]));
function setEmotion(emo, sec = 5, withGesture = true) {
  st.emotion = EMO[emo] ? emo : "neutral";
  st.emoUntil = clock.elapsedTime + sec;
  if (withGesture && EMO_GESTURE[emo] && !st.patting) gesture(EMO_GESTURE[emo], Math.min(sec, 3.5), true);
}

const damp = (cur, target, rate, dt) => cur + (target - cur) * (1 - Math.exp(-rate * dt));
// критически задемпфированная пружина: плавно разгоняется и тормозит, без перелёта
function spring(s, target, w, dt) {
  s.v += (w * w * (target - s.x) - 2 * w * s.v) * dt;
  s.x += s.v * dt;
  return s.x;
}
const gtr = { name: undefined, p: 1, from: {} };   // переход между жестами
const GBONES = ["spine", "chest", "upperChest", "neck", "head"];
const clampN = THREE.MathUtils.clamp;

function animate() {
  if (st.gameMode && !st.camMode) { clock.getDelta(); setTimeout(animate, 1000); return; }   // игровой режим: ни кадра, пока игра
  if (st.camMode) setTimeout(animate, 1000 / 24);      // «вебка»: 24 кадра хватает, остальное — игре
  else requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);
  if (vrm) { poseFrame(dt); cuteTick(dt, clock.elapsedTime); }
  tickFx(dt);
  if (timers.length) timerTick(dt);
  renderer.render(scene, camera);
  fxRenderer.render(fxScene, camera);
  window.__k.onFrame?.(dt);          // для замеров плавности (avatar-debug.js)
}

function poseFrame(dt) {
  const t = clock.elapsedTime;
  if (t > st.emoUntil && st.emotion !== "neutral") st.emotion = "neutral";
  const em = st.patting ? "happy" : st.emotion;
  tickIdle(t);

  // 1. каждый кадр с нуля: поза из клипов (теневой скелет), без клипов — руки вдоль тела
  mixer?.update(dt);
  for (const [node, p] of anim.map) node.quaternion.copy(p.quaternion);
  B.hips.position.copy(anim.hips.position);
  if (!st.idleAction && !st.oneShot) for (const [b, r] of Object.entries(REST)) B[b]?.rotation.set(r[0], r[1], r[2]);
  // Поправка рук под модель: клип опускает руки строго вдоль тела, и у пышной юбки кисти
  // уходят за неё — выглядит, будто руки за спиной. Чуть вперёд и в стороны (в Гардеробе — на модель).
  const per = cfg.avatar?.per_model?.[modelKey] || {};
  // у VRM 0.x нормализованный скелет развёрнут на 180° — оси X и Z смотрят наоборот
  const v0 = vrm.meta?.metaVersion === "0" ? -1 : 1;
  // в жесте из захвата и лёжа руки не трогаем; вес сглажен — иначе в конце жеста поправка включалась
  // разом и руки дёргались на 17° за кадр (нашёл замер __k.probe по всему демо)
  const armTo = (st.oneShot ? (st.idleAction?.getEffectiveWeight() ?? 0) : 1) * (1 - (home.lieW || 0));
  st.armW = damp(st.armW ?? armTo, armTo, 5, dt);
  const idleW = st.armW;
  const af = (per.arm_forward ?? 0.28) * v0 * idleW, ao = (per.arm_out ?? 0.1) * v0 * idleW;
  B.leftUpperArm?.quaternion.premultiply(qt.setFromEuler(e3.set(-af, 0, ao)));
  B.rightUpperArm?.quaternion.premultiply(qt.setFromEuler(e3.set(-af, 0, -ao)));
  fingersTick(dt, t, v0);
  walkLayer(dt, t, v0);
  pastimeTick(dt, t, v0);
  wardrobeTick(dt, t, v0);

  // 2. жест корпусом и головой — добавкой поверх клипа. Каждая кость плавно перетекает
  //    к позе нового жеста, а не перескакивает, если один жест сменил другой.
  let name = st.gesture;
  if (st.dragging) name = "dangle";
  else if (st.sleeping) name = "sleep";
  else if (st.patting) name = "pat";
  else if (st.thinking && !name) name = "think";
  else if (st.listening && !name) name = "listen";
  if (st.gesture && t > st.gestureUntil) st.gesture = null;
  const pose = name && GESTURES[name] ? GESTURES[name](t - st.gestureT0) : null;
  st.gw = damp(st.gw, pose ? 1 : 0, pose ? 6 : 3.5, dt);
  if (pose) st.lastPose = pose;
  // переход от прежней позы к новой — по smoothstep: с разгоном и торможением, а не рывком с места
  const gkey = pose ? name : null;
  if (gkey !== gtr.name) {
    gtr.name = gkey; gtr.p = 0;
    for (const b of GBONES) (gtr.from[b] ??= new THREE.Quaternion()).copy(goff[b] ??= new THREE.Quaternion());
  }
  gtr.p = Math.min(1, gtr.p + dt / (pose ? 0.6 : 0.8));
  const ge = gtr.p * gtr.p * (3 - 2 * gtr.p);
  for (const bone of GBONES) {
    const off = goff[bone] ??= new THREE.Quaternion();
    const r = pose?.[bone];
    if (r) qt.setFromEuler(e3.set(r[0], r[1], r[2])); else qt.identity();
    off.copy(gtr.from[bone] ?? qt).slerp(qt, ge);
    B[bone]?.quaternion.multiply(off);
  }

  // 3. поверх: дыхание, танец, сидение, взгляд, кивки, вздрагивание
  st.breathPh = (st.breathPh || 0) + dt * (st.sleeping ? 1.0 : 1.7);   // фаза копится: смена темпа не дёргает
  const breath = Math.sin(st.breathPh);
  const wantDance = (st.dancing || t < (st.forceDanceUntil || 0)) && !st.perched && !st.oneShot && !pose && !st.speechOn;
  st.danceW = damp(st.danceW, wantDance ? 1 : 0, 2.2, dt);
  const dance = st.danceW;
  const beat = t * Math.PI * (112 / 60);
  addRot(B.chest || B.spine, breath * 0.015, 0, 0);
  addRot(B.spine, 0, 0, Math.sin(t * 0.6) * 0.015 + dance * Math.sin(beat) * 0.07);
  // перенос веса с ноги на ногу (период ~11 с): таз чуть наклоняется, бёдра компенсируют — ступни на месте
  const shift = (Math.sin(t * 0.57) + 0.35 * Math.sin(t * 0.23 + 1.3)) * 0.018 * (1 - st.sitting) * (1 - dance) * (1 - walkSt.w);
  addRot(B.hips, 0, 0, shift);
  addRot(B.leftUpperLeg, 0, 0, -shift);
  addRot(B.rightUpperLeg, 0, 0, -shift);
  addRot(B.spine, 0, 0, -shift * 0.8);
  // медленный дрейф шеи суммой несоизмеримых синусов — поза не выглядит замороженной
  addRot(B.neck, (Math.sin(t * 0.31) + Math.sin(t * 0.83 + 2)) * 0.008, (Math.sin(t * 0.27 + 1) + Math.sin(t * 0.71)) * 0.012, 0);
  // голова кружится: покачивание по кругу, затухает
  if (t < dz.until) { const w = Math.min(1, (dz.until - t) / 1.2); addRot(B.neck, Math.sin(t * 6) * 0.07 * w, 0, Math.cos(t * 6) * 0.08 * w); }
  let hipsY = B.hips.position.y;
  hipsY += dance * Math.abs(Math.sin(beat)) * 0.025 * modelH;
  const lp = st.lastPose;
  if (lp?.hipsBounce) hipsY += Math.abs(Math.sin(t * 6)) * lp.hipsBounce * modelH * st.gw;
  if (lp?.hipsDrop) hipsY -= lp.hipsDrop * modelH * st.gw;
  B.hips.position.y = hipsY;
  st.sitting = damp(st.sitting, st.perched || (home.on && home.sit) ? 1 : 0, 4, dt);
  seatTick();
  if (home.on) vrm.scene.position.y = home.seatY * st.sitting;
  if (st.sitting > 0.01) {
    for (const s of ["left", "right"]) {
      const swing = Math.sin(t * 1.3 + (s === "left" ? 0 : 1.6)) * 0.12;
      // у VRM 0.x нормализованные оси X развёрнуты (v0 = −1): без поправки бедро уходило назад, колени — за спину
      // колени сведены, голени чуть врозь: между бёдер нет просвета, и камера не смотрит под короткую юбку
      const sd = s === "left" ? 1 : -1;
      B[s + "UpperLeg"].quaternion.slerp(qt.setFromEuler(e3.set(-1.45 * v0, 0, -0.085 * sd * v0)), st.sitting);
      B[s + "LowerLeg"].quaternion.slerp(qt.setFromEuler(e3.set((1.45 + swing) * v0, 0.06 * sd * v0, 0)), st.sitting);
    }
  }
  // юбка сидя: у модели гравитация юбки 0 и жёсткость 0,5 — она держит форму «стоя», и шары-коллайдеры на
  // горизонтальных бёдрах задирали её колоколом. Сидя (и лёжа) — гравитация и мягкость, встала — как было.
  const drape = st.sitting > 0.5 || (home.lieW || 0) > 0.5;
  if (drape !== st.drape && vrm.springBoneManager) {
    st.drape = drape;
    for (const j of vrm.springBoneManager.joints) {
      if (!/skirt|スカート/i.test(j.bone.name)) continue;
      j.userData ??= {};
      j.userData.orig ??= { g: j.settings.gravityPower, s: j.settings.stiffness };
      j.settings.gravityPower = drape ? Math.max(1.2, j.userData.orig.g) : j.userData.orig.g;
      j.settings.stiffness = drape ? Math.min(0.15, j.userData.orig.s) : j.userData.orig.s;
    }
  }
  // висит на краю окна: руки вверх, ноги болтаются; прячется — наклон в сторону, откуда выглядывает
  if (st.joltT != null) {                                    // вздрогнула: подскок и откинулась назад, 0,45 с
    const k = (clock.elapsedTime - st.joltT) / 0.45;
    if (k >= 1) st.joltT = null;
    else {
      const j = Math.sin(Math.PI * k) * (1 - k * 0.3);
      if (B.hips) B.hips.position.y += j * modelH * 0.035;
      addRot(B.spine, -0.16 * j * v0, 0, 0);
      addRot(B.neck, -0.1 * j * v0, 0, 0);
      for (const s of ["left", "right"])                      // руки к груди
        B[s + "UpperArm"]?.quaternion.slerp(qt.setFromEuler(e3.set(-0.5 * v0, 0, (s === "left" ? 1 : -1) * -0.9 * v0)), j * 0.8);
    }
  }
  st.hangW = damp(st.hangW || 0, phys.mode === "hang" ? 1 : 0, 7, dt);
  if (st.hangW > 0.01) {
    const w = st.hangW;
    for (const s of ["left", "right"]) {
      const m = s === "left" ? 1 : -1;
      B[s + "UpperArm"]?.quaternion.slerp(qt.setFromEuler(e3.set(-0.25 * v0, 0, m * 1.42 * v0)), w);
      B[s + "LowerArm"]?.quaternion.slerp(qt.setFromEuler(e3.set(0, m * -0.25, 0)), w);
      const sw = Math.sin(t * 2.1 + (s === "left" ? 0 : 0.7)) * 0.16;
      B[s + "UpperLeg"]?.quaternion.slerp(qt.setFromEuler(e3.set((-0.12 + sw) * v0, 0, 0)), w);
      B[s + "LowerLeg"]?.quaternion.slerp(qt.setFromEuler(e3.set((0.3 + sw * 0.6) * v0, 0, 0)), w);
    }
    addRot(B.spine, -0.06 * w * v0, 0, 0);
  }
  rideTick(dt, t, v0);
  st.peekW = damp(st.peekW || 0, phys.mode === "peek" ? 1 : 0, 3, dt);
  if (st.peekW > 0.01) {
    const lean = -phys.side * st.peekW * v0;              // наклон в открытую сторону
    addRot(B.spine, 0, 0, lean * 0.12);
    addRot(B.chest || B.spine, 0, 0, lean * 0.1);
    addRot(B.neck, 0, 0, lean * 0.14);
  }
  // взгляд: курсор приходит 30 раз в секунду ступеньками — глаза догоняют быстро, голова мягче
  const wx = (st.cursor.x - st.win.x) / st.win.width, wy = (st.cursor.y - st.win.y) / st.win.height;
  let nx = clampN(wx * 2 - 1, -3, 3), ny = clampN(1 - wy * 2, -3, 3);
  if (st.lookAround > 0) { nx = Math.sin(t * 1.1) * 1.4; ny = 0.15; st.lookAround -= dt; }
  const wl = !st.lookAt && watchLook();
  if (wl) { const pp = _lv.copy(wl).project(camera); nx = clampN(pp.x, -3, 3); ny = clampN(pp.y, -3, 3); }
  if (st.lookAt) { const pp = _lv.copy(st.lookAt).project(camera); nx = clampN(pp.x, -3, 3); ny = clampN(pp.y, -3, 3); }
  const lookTo = st.sleeping || st.patting || ["bow", "shy", "angry", "sleep"].includes(name) ? 0.2
    : life.mode === "walk" || life.mode === "turn" ? 0.35 : 1;
  const L = st.look;
  L.sx ??= { x: 0, v: 0 }; L.sy ??= { x: 0, v: 0 }; L.sw ??= { x: 1, v: 0 };
  L.w = spring(L.sw, lookTo, 5, dt);
  L.x = spring(L.sx, nx, 6, dt); L.y = spring(L.sy, ny, 6, dt);
  st.eye = st.eye || { x: 0, y: 0, sx: 0, sy: 0, next: 0 };
  if (t > st.eye.next) {                     // микросаккады: глаза чуть перескакивают, а не таращатся в точку
    st.eye.next = t + 0.3 + Math.random() * 1.2;
    st.eye.sx = (Math.random() - 0.5) * 0.06; st.eye.sy = (Math.random() - 0.5) * 0.04;
  }
  st.eye.x = damp(st.eye.x, nx, 14, dt); st.eye.y = damp(st.eye.y, ny, 14, dt);
  lookTarget.position.set((st.eye.x + st.eye.sx) * 0.95 * L.w, camera.position.y + (st.eye.y + st.eye.sy) * 0.75 * L.w, 1.2);
  if (home.on) lookTarget.position.copy(home.look || roomCursorLook() || camera.position);   // дома: предмет, курсор или хозяин
  const yaw = clampN(L.x * 0.17, -0.38, 0.38) * L.w, pitch = clampN(-L.y * 0.09, -0.2, 0.2) * L.w;
  // вздрагивание от тычка — затухающая пружина вместо тряски
  st.flinchV += (-90 * st.flinch - 11 * st.flinchV) * dt;
  st.flinch += st.flinchV * dt;
  st.talk = damp(st.talk, st.speaking > 0.08 ? 1 : 0, 3, dt);
  const nod = st.talk * Math.sin(t * 5.5) * 0.035;
  addRot(B.chest || B.spine, st.flinch * 0.25, 0, 0);
  addRot(B.neck, pitch * 0.5 + nod, yaw * 0.5, dance * Math.sin(beat) * 0.06);
  addRot(B.head, pitch * 0.5 + st.flinch * 0.6, yaw * 0.5, 0);

  // выражения
  const target = { ...(EMO[st.sleeping ? "relaxed" : st.thinking ? "relaxed" : em] || {}) };
  if (st.ptHappy) target.happy = Math.max(target.happy || 0, st.ptHappy);      // занятие: улыбается книге, телефону
  for (const e of EXPRS) {
    cur[e] = damp(cur[e], target[e] || 0, 6, dt);
    vrm.expressionManager.setValue(e, cur[e]);
  }
  if (t > st.blinkAt && st.blinkT < 0) st.blinkT = 0;
  let blink = 0;
  if (st.blinkT >= 0) {
    st.blinkT += dt;
    blink = Math.sin(Math.min(1, st.blinkT / 0.16) * Math.PI);
    if (st.blinkT > 0.16) {
      st.blinkT = -1;
      // иногда моргает дважды подряд — очень «человеческая» мелочь
      st.blinkAt = Math.random() < 0.15 && !st.dbl ? t + 0.12 : t + 2 + Math.random() * 4;
      st.dbl = st.blinkAt - t < 0.5;
    }
  }
  st.eyesClosed = damp(st.eyesClosed || 0, st.sleeping || st.patting || name === "stretch" ? 1 : st.ptEyes || 0, 8, dt);
  st.winkW = damp(st.winkW || 0, t < (st.winkUntil || 0) ? 1 : 0, 9, dt);    // во сне приоткрывает один глаз
  vrm.expressionManager.setValue("blink", Math.max(st.eyesClosed * (1 - st.winkW), cur.happy > 0.6 ? 0 : blink));
  vrm.expressionManager.setValue("blinkLeft", st.eyesClosed * st.winkW);
  st.mouth = damp(st.mouth, st.speaking, 18, dt);
  vrm.expressionManager.setValue("aa", Math.max(st.mouth, st.ptMouth || 0));
  vrm.expressionManager.setValue("oh", st.mouth * 0.3 * (0.5 + 0.5 * Math.sin(t * 13)));

  vrm.update(dt);
}
function addRot(node, x, y, z) {
  if (!node) return;
  node.quaternion.multiply(q.setFromEuler(e3.set(x, y, z)));
}

// ---------- эффекты и милые мелочи ----------
// Частицы рисуются на отдельном холсте поверх модели: окно пропускает клики там, где модель прозрачна,
// и пролетающий листик на том же холсте ловил бы клик, предназначенный рабочему столу.
const fxRenderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
fxRenderer.domElement.style.cssText = "position:absolute;left:0;top:0;pointer-events:none";   // до setSize: он ставит ширину
fxRenderer.setPixelRatio(devicePixelRatio);
fxRenderer.setSize(W, H);
fxRenderer.setClearColor(0x000000, 0);
fxRenderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.insertBefore(fxRenderer.domElement, document.getElementById("bubble"));
const fxScene = new THREE.Scene();
addEventListener("resize", () => fxRenderer.setSize(innerWidth, innerHeight));

const pick = (a) => a[Math.floor(Math.random() * a.length)];
const fx = [];
function canvasTex(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}
const glowDraw = (stops) => (g) => {
  const r = g.createRadialGradient(32, 32, 0, 32, 32, 30);
  stops.forEach(([o, c]) => r.addColorStop(o, c));
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
};
const leafDraw = (c) => (g) => {
  g.translate(32, 32); g.rotate(-0.5);
  g.fillStyle = c; g.beginPath(); g.moveTo(0, -27); g.quadraticCurveTo(21, -6, 0, 27); g.quadraticCurveTo(-21, -6, 0, -27); g.fill();
  g.strokeStyle = "rgba(70,35,10,.55)"; g.lineWidth = 2; g.beginPath(); g.moveTo(0, -23); g.lineTo(0, 29); g.stroke();
};
const TEX_DRAW = {
  puff: glowDraw([[0, "rgba(255,255,255,1)"], [0.55, "rgba(255,250,245,.7)"], [1, "rgba(255,255,255,0)"]]),
  spark: glowDraw([[0, "rgba(255,255,240,1)"], [0.3, "rgba(255,240,200,.8)"], [1, "rgba(255,230,180,0)"]]),
  snow: glowDraw([[0, "rgba(255,255,255,1)"], [0.45, "rgba(240,248,255,.85)"], [1, "rgba(255,255,255,0)"]]),
  firefly: glowDraw([[0, "rgba(245,255,190,1)"], [0.2, "rgba(210,255,130,.75)"], [1, "rgba(160,255,90,0)"]]),
  heart: (g) => {
    g.fillStyle = "#ff6f9c"; g.beginPath(); g.moveTo(32, 55);
    g.bezierCurveTo(2, 34, 8, 6, 32, 21); g.bezierCurveTo(56, 6, 62, 34, 32, 55); g.fill();
    g.fillStyle = "rgba(255,255,255,.6)"; g.beginPath(); g.ellipse(21, 23, 6, 3.5, -0.6, 0, Math.PI * 2); g.fill();
  },
  star: (g) => {
    g.fillStyle = "#ffe36e"; g.strokeStyle = "#e0a325"; g.lineWidth = 2; g.beginPath();
    for (let i = 0; i < 10; i++) { const r = i % 2 ? 11 : 27, a = -Math.PI / 2 + i * Math.PI / 5; g.lineTo(32 + Math.cos(a) * r, 32 + Math.sin(a) * r); }
    g.closePath(); g.fill(); g.stroke();
  },
  petal: (g) => {
    g.fillStyle = "#ffc2d4"; g.beginPath(); g.ellipse(32, 32, 12, 22, 0.4, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#ffe4ec"; g.beginPath(); g.ellipse(29, 27, 5, 10, 0.4, 0, Math.PI * 2); g.fill();
  },
  greenleaf: (g) => {
    g.translate(32, 32); g.rotate(-0.6);
    g.fillStyle = "#6fae3c"; g.beginPath(); g.ellipse(0, 0, 11, 24, 0, 0, Math.PI * 2); g.fill();
    g.strokeStyle = "#3f7a1f"; g.lineWidth = 2; g.beginPath(); g.moveTo(0, -22); g.lineTo(0, 28); g.stroke();
  },
  note0: (g) => { g.fillStyle = "#ffd36e"; g.strokeStyle = "#a8641c"; g.lineWidth = 2; g.font = "bold 50px serif";
    g.textAlign = "center"; g.strokeText("♪", 32, 50); g.fillText("♪", 32, 50); },
  note1: (g) => { g.fillStyle = "#ffb3cf"; g.strokeStyle = "#a8406a"; g.lineWidth = 2; g.font = "bold 46px serif";
    g.textAlign = "center"; g.strokeText("♫", 32, 48); g.fillText("♫", 32, 48); },
  butterfly: (g) => {
    for (const s of [-1, 1]) {
      g.fillStyle = "#8fc8ff"; g.beginPath(); g.ellipse(32 + s * 13, 24, 12, 15, s * 0.5, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#c9a2ff"; g.beginPath(); g.ellipse(32 + s * 10, 42, 8, 10, -s * 0.4, 0, Math.PI * 2); g.fill();
      g.fillStyle = "rgba(255,255,255,.65)"; g.beginPath(); g.ellipse(32 + s * 13, 21, 4, 6, s * 0.5, 0, Math.PI * 2); g.fill();
    }
    g.fillStyle = "#3a2a4a"; g.beginPath(); g.ellipse(32, 33, 2.6, 13, 0, 0, Math.PI * 2); g.fill();
  },
  leaf0: leafDraw("#d9622b"), leaf1: leafDraw("#e8a33a"), leaf2: leafDraw("#b8402a"), leaf3: leafDraw("#c98a2c"),
};
const TEX = {};
const tex = (name) => (TEX[name] ??= canvasTex(64, 64, TEX_DRAW[name]));

// Частица: скорость и размер — в долях роста модели, чтобы эффекты выглядели одинаково на любой модели
function particle({ tex: tname, pos, vel = null, life = 2, size = 0.05, spin = 0, fade = 0.3, grow = 0, sway = 0,
  gravity = 0, drag = 0, glow = false, color = 0xffffff, upd = null, ambient = false, opacity = 1 }) {
  const m = new THREE.SpriteMaterial({ map: tex(tname), transparent: true, depthWrite: false, depthTest: false, color,
    blending: glow ? THREE.AdditiveBlending : THREE.NormalBlending, opacity: 0 });
  const s = new THREE.Sprite(m);
  s.position.copy(pos);
  const sz = size * modelH;
  s.scale.set(sz, sz, 1);
  fxScene.add(s);
  fx.push({ obj: s, v: (vel || new THREE.Vector3()).clone().multiplyScalar(modelH), life: 0, max: life, spin, fade, grow,
    sway, gravity: gravity * modelH, drag, upd, ambient, opacity, phase: Math.random() * 6.28 });
  return s;
}
function tickFx(dt) {
  for (let i = fx.length - 1; i >= 0; i--) {
    const f = fx[i];
    f.life += dt;
    if (f.life >= f.max) { fxScene.remove(f.obj); f.obj.material.dispose(); fx.splice(i, 1); continue; }
    if (f.upd) f.upd(f, dt);
    else {
      f.v.y -= f.gravity * dt;
      if (f.drag) f.v.multiplyScalar(Math.exp(-f.drag * dt));
      f.obj.position.addScaledVector(f.v, dt);
      if (f.sway) f.obj.position.x += Math.sin(f.life * 2.1 + f.phase) * f.sway * modelH * dt;
    }
    if (f.spin) f.obj.material.rotation += f.spin * dt;
    if (f.grow) { const sc = f.obj.scale.x * (1 + f.grow * dt); f.obj.scale.set(sc, sc, 1); }
    // появляется за 0,15 с, гаснет в последнюю долю жизни
    f.obj.material.opacity = f.opacity * Math.max(0, Math.min(1, f.life / 0.15, (f.max - f.life) / Math.max(0.01, f.fade * f.max)));
  }
}
const _hv = new THREE.Vector3();
function headPos(dy = 0.08) {
  vrm.humanoid.getRawBoneNode("head").getWorldPosition(_hv);
  return new THREE.Vector3(_hv.x, _hv.y + modelH * dy, _hv.z + modelH * 0.04);
}
const vec = (x, y, z = 0) => new THREE.Vector3(x, y, z);
const _lv = new THREE.Vector3();
const rnd = (a, b) => a + Math.random() * (b - a);

// «пуф!» — кицунэ переодевается в облачке дыма, с листком на макушке
function poof(at = null) {
  const c = at || (home.on && vrm ? vrm.scene.position.clone() : vec(0, modelH * 0.5, 0));
  for (let i = 0; i < 28; i++) {
    const a = Math.random() * Math.PI * 2;
    particle({ tex: "puff", pos: vec(c.x + Math.cos(a) * modelH * 0.12 * Math.random(), modelH * rnd(0.08, 0.96), c.z + 0.2),
      vel: vec(Math.cos(a) * 0.5, rnd(0.15, 0.4)), drag: 3, grow: 1.6, life: rnd(0.6, 1.1), size: rnd(0.1, 0.22),
      fade: 0.8, color: i % 4 ? 0xffffff : 0xffe2c4, opacity: 0.95 });
  }
  particle({ tex: "greenleaf", pos: vec(c.x, modelH * 1.02, c.z + 0.25), vel: vec(0, -0.18), sway: 0.35, spin: 0.6,
    life: 2.6, size: 0.09, fade: 0.25 });
}

// ---------- вздрагивает от громкого звука ----------
// Ядро слышит хлопок или грохот (только уровень громкости) — она подпрыгивает, уши встают торчком (пружины ушей
// на пару секунд жёсткие и без тяжести), лицо удивлённое. Спала — просыпается: «Я не спала!».
function startle() {
  if (!vrm || home.on || st.dragging) return;
  const wasAsleep = st.sleeping;
  activity();
  st.joltT = clock.elapsedTime;
  setEmotion("surprised", 2.2, false);
  for (const j of vrm.springBoneManager?.joints || []) {
    if (!/ear|耳/i.test(j.bone.name)) continue;
    j.userData ??= {};
    j.userData.earOrig ??= { g: j.settings.gravityPower, s: j.settings.stiffness };
    j.settings.gravityPower = 0; j.settings.stiffness = Math.max(4, j.userData.earOrig.s * 6);
    clearTimeout(j.userData.earT);
    j.userData.earT = setTimeout(() => { j.settings.gravityPower = j.userData.earOrig.g; j.settings.stiffness = j.userData.earOrig.s; }, 2200);
  }
  say(line(wasAsleep ? "startle_asleep" : "startle"), 2600);
}

// сердечки над головой, пока гладят
function heartsTick(t) {
  if (!st.patting || t < (st.nextHeart || 0)) return;
  st.nextHeart = t + rnd(0.25, 0.5) / (1 + (st.friend || 0) * 0.3);   // чем ближе дружба, тем гуще сердечки
  const p = headPos(0.1);
  p.x += rnd(-0.07, 0.07) * modelH;
  particle({ tex: "heart", pos: p, vel: vec(rnd(-0.03, 0.03), rnd(0.1, 0.16)), life: 1.7, size: rnd(0.03, 0.05),
    sway: 0.05, spin: rnd(-0.6, 0.6), fade: 0.5 });
}

// голова кружится, если крутить курсором вокруг неё
const dz = { acc: 0, lastA: null, until: 0, cool: 0 };
function dizzyTick(dt, t) {
  const h = toScreen(headPos(0));
  const dx = st.cursor.x - (st.win.x + h.x), dy = st.cursor.y - (st.win.y + h.y), r = Math.hypot(dx, dy);
  dz.acc *= Math.exp(-dt / 2.5);                       // накопленные обороты остывают
  if (r > 25 && r < 330) {
    const a = Math.atan2(dy, dx);
    if (dz.lastA !== null) { const d = a - dz.lastA; dz.acc += Math.atan2(Math.sin(d), Math.cos(d)); }
    dz.lastA = a;
  } else dz.lastA = null;
  if (Math.abs(dz.acc) < Math.PI * 2 * 2.5 || t < dz.cool || st.sleeping || st.dragging) return;
  dz.acc = 0; dz.until = t + 3.4; dz.cool = t + 25;
  say(line("dizzy"), 3600);
  setEmotion("surprised", 3.4, false);
  send({ type: "avatar.event", kind: "dizzy" });
  for (let i = 0; i < 3; i++) {
    const ph = i * Math.PI * 2 / 3;
    particle({ tex: "star", pos: headPos(0.1), life: 3.3, size: 0.032, fade: 0.25, upd: (f) => {
      const a = f.life * 4.2 + ph, c = headPos(0.12);
      f.obj.position.set(c.x + Math.cos(a) * modelH * 0.075, c.y + Math.sin(a * 2) * modelH * 0.008, c.z + Math.sin(a) * modelH * 0.05);
    } });
  }
}

// «поддельный сон»: курсор рядом — приоткрывает один глаз
function winkTick(t) {
  if (!st.sleeping) { st.winkSaid = false; return; }
  const w = st.win, c = st.cursor;
  const near = c.x > w.x - 60 && c.x < w.x + w.width + 60 && c.y > w.y - 60 && c.y < w.y + w.height + 60;
  if (!near || t < (st.winkNext || 0)) return;
  st.winkNext = t + rnd(7, 13);
  st.winkUntil = t + 1.8;
  if (!st.winkSaid) {
    st.winkSaid = true;
    say(line("dozing"), 3600);
  }
}

// чих: «а… а…», запрокидывает голову, «апчхи!» с искорками
function sneeze(reason = "") {
  if (st.sleeping || st.speechOn || st.sneezing || !vrm) return;
  st.sneezing = true;
  gesture("presneeze", 1.0);
  say(reason === "leaf" ? "А… а…" : "А… а…", 1500);
  setTimeout(() => {
    st.flinchV += 7;
    for (let i = 0; i < 12; i++) {
      particle({ tex: "spark", pos: headPos(0.02), vel: vec(rnd(-0.35, 0.35), rnd(-0.1, 0.3), 0.15), drag: 2.5,
        life: rnd(0.6, 1.0), size: rnd(0.018, 0.032), glow: true, fade: 0.6 });
    }
    say("Апчхи!", 1800);
    setTimeout(() => { send({ type: "avatar.event", kind: "sneeze" }); st.sneezing = false; }, 900);
  }, 950);
}

// времена года вокруг: осенью листья, зимой снег, весной сакура, летними ночами светлячки
// праздник сегодня: день рождения пользователя (профиль, «ДД.ММ»), Новый год (20.12–7.01), Хэллоуин
function holiday() {
  const d = new Date(), m = d.getMonth() + 1, day = d.getDate();
  const bd = String(cfg.user_birthday || "").match(/^(\d{1,2})\.(\d{1,2})/);
  if (bd && +bd[1] === day && +bd[2] === m) return "birthday";
  if ((m === 12 && day >= 20) || (m === 1 && day <= 7)) return "newyear";
  if (halloween()) return "halloween";
  return null;
}
function season() {
  const d = new Date(), m = d.getMonth() + 1, h = d.getHours();
  if (halloween()) return "halloween";
  if (m >= 9 && m <= 11) return "autumn";
  if (m === 12 || m <= 2) return "winter";
  if (m >= 3 && m <= 5) return "spring";
  return h >= 21 || h < 5 ? "fireflies" : null;
}
function seasonTick(t) {
  funTick(t);
  watchTick(t);
  if (cfg.avatar?.seasons === false || t < (st.nextAmbient || 4)) return;
  const s = season();
  if (!s) { st.nextAmbient = t + 60; return; }
  st.nextAmbient = t + (s === "winter" ? rnd(0.5, 1.4) : s === "fireflies" ? rnd(2, 4) : rnd(2.2, 4.5));
  if (fx.filter((f) => f.ambient).length > (s === "winter" ? (holiday() === "newyear" ? 26 : 16) : 7)) return;   // под Новый год снега больше
  const halfH = camera.position.z * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2), halfW = halfH * camera.aspect;
  const top = camera.position.y + halfH * 1.05, x = rnd(-0.85, 0.85) * halfW, z = rnd(-0.1, 0.25);
  const fall = (vy) => (2.1 * halfH) / (vy * modelH) + 0.5;
  if (s === "halloween") { halloweenAmbient(top, halfW, z, fall); return; }
  if (s === "autumn") {
    const vy = rnd(0.09, 0.14);
    particle({ tex: "leaf" + Math.floor(Math.random() * 4), pos: vec(x, top, z), vel: vec(rnd(-0.02, 0.04), -vy), sway: 0.28,
      spin: rnd(-1.6, 1.6), life: fall(vy), size: rnd(0.03, 0.045), fade: 0.1, ambient: true });
  } else if (s === "winter") {
    const vy = rnd(0.05, 0.09);
    particle({ tex: "snow", pos: vec(x, top, z), vel: vec(0, -vy), sway: 0.12, life: fall(vy), size: rnd(0.01, 0.022),
      fade: 0.1, ambient: true, opacity: 0.9 });
  } else if (s === "spring") {
    const vy = rnd(0.07, 0.1);
    particle({ tex: "petal", pos: vec(x, top, z), vel: vec(rnd(0.02, 0.06), -vy), sway: 0.3, spin: rnd(-2, 2),
      life: fall(vy), size: rnd(0.02, 0.03), fade: 0.1, ambient: true });
  } else {
    const home = vec(rnd(-0.5, 0.5) * halfW, modelH * rnd(0.2, 0.9), rnd(0, 0.3)), ph = Math.random() * 6.28;
    particle({ tex: "firefly", pos: home, glow: true, life: rnd(6, 9), size: rnd(0.018, 0.03), fade: 0.35, ambient: true,
      upd: (f) => {
        f.obj.position.set(home.x + Math.sin(f.life * 0.7 + ph) * modelH * 0.08, home.y + Math.sin(f.life * 1.1 + ph * 2) * modelH * 0.05, home.z);
        f.opacity = 0.55 + 0.45 * Math.sin(f.life * 2.3 + ph);   // мерцает
      } });
  }
}

function cuteTick(dt, t) {
  if (!vrm) return;
  heartsTick(t);
  dizzyTick(dt, t);
  winkTick(t);
  seasonTick(t);
  if (home.on) roomTick(dt, t); else if (!st.camMode) { physTick(dt, t); lifeTick(dt, t); }   // в «вебке» окно стоит в углу
}

// ---------- своя жизнь: гуляет по рабочему столу ----------
// Раз в минуту-две, если ничем не занята, поворачивается вполоборота, идёт вдоль панели задач и возвращает
// взгляд к хозяину. Окно едет со скоростью шага из захвата движения — ноги не проскальзывают.
const life = { mode: "idle", dir: 0, target: 0, yaw: 0, yawV: 0, next: 50, moved: 0, frac: 0, clip: null };
function lifeBusy() {
  return !K.moveAvatar || st.pastime || st.watching || st.perched || home.on || phys.mode !== "ground" || st.dragging || st.sleeping || st.speechOn || st.thinking || st.listening ||
    st.patting || demoRunning || game.kind || menuEl.classList.contains("show") || cfg.avatar?.wander === false;   // game — игра на столе: не уходить посреди «реакции»
}
function pxPerMeter() {
  const visH = 2 * camera.position.z * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
  return H / visH;
}
function startWalk(targetX = null) {
  const list = clips.walk || [];
  if (!list.length || !st.work) return false;
  const w = st.work, x = st.win.x;
  const lo = w.x - W * 0.3, hi = w.x + w.width - W * 0.7;     // можно чуть заходить за край экрана
  let target = targetX ?? lo + Math.random() * (hi - lo);
  if (targetX == null && Math.abs(target - x) < 180) target = x + (target < x ? -1 : 1) * rnd(220, 420);
  target = Math.max(lo, Math.min(hi, target));
  if (Math.abs(target - x) < 60) return false;
  // настроение выбирает походку: весёлая — вприпрыжку, ночью — усталая, обычно — женственная
  const hour = new Date().getHours(), mood = st.emotion;
  const want = hour >= 23 || hour < 6 ? "tired" : mood === "happy" ? (Math.random() < 0.5 ? "happy" : "childish") : "feminine";
  life.clip = list.find((c) => c.name.includes(want)) || list[0];
  life.target = target; life.dir = Math.sign(target - x); life.mode = "turn"; life.frac = 0;
  return true;
}
function stopWalk() {
  if (life.mode === "walk" && st.oneShot) endOneShot();
  if (life.mode !== "idle") life.mode = "back";
}
function lifeTick(dt, t) {
  const busy = lifeBusy();
  if (busy && (life.mode === "turn" || life.mode === "walk")) stopWalk();
  // поворот: вполоборота (≈70°), чтобы было видно лицо; на пружине — без рывка
  const yawTo = life.mode === "turn" || life.mode === "walk" ? life.dir * 1.2 : 0;
  life.yawV += (36 * (yawTo - life.yaw) - 12 * life.yawV) * dt;
  life.yaw += life.yawV * dt;
  // перед «телевизором» — вполоборота спиной к вам; на паузе оборачивается
  st.watchYaw = damp(st.watchYaw || 0, st.watching?.mode === "tv" && !st.watchPaused ? Math.PI * 0.78 : 0, 2.5, dt);
  vrm.scene.rotation.y = (st.baseYaw || 0) + life.yaw + st.watchYaw;
  if (life.mode === "idle") {
    if (!busy && t > life.next && !st.oneShot && clips.walk?.length) {
      life.next = t + 30;
      if (Math.random() < 0.2) startPeek().then((ok) => { if (!ok) startWalk(); });
      else startWalk();
    }
    return;
  }
  if (life.mode === "turn" && Math.abs(life.yaw - yawTo) < 0.12) {
    const a = mixer.clipAction(life.clip);
    const from = st.oneShot || st.idleAction;
    a.reset().setLoop(THREE.LoopRepeat, Infinity).setEffectiveWeight(1).play();
    if (from && from !== a) from.crossFadeTo(a, 0.35, false); else a.fadeIn(0.35);
    if (st.legsAction && !st.legsOff) { st.legsAction.fadeOut(0.35); st.legsOff = true; }
    st.oneShot = a;
    life.mode = "walk";
  }
  if (life.mode === "walk") {
    // скорость окна = скорость шага × вес походки (в начале шаг набирает силу вместе с кроссфейдом)
    const hh = Math.abs(vrm.humanoid.normalizedRestPose.hips.position[1]) || 0.9;
    const wgt = st.oneShot?.getEffectiveWeight() ?? 1;
    const v = (life.clip.userData.speed || 1) * hh * pxPerMeter() * wgt;
    life.frac += v * dt * life.dir;
    const step = Math.trunc(life.frac);
    if (step) { K.moveAvatar(step, 0); life.frac -= step; st.win.x += step; }
    if ((life.dir > 0 && st.win.x >= life.target) || (life.dir < 0 && st.win.x <= life.target)) {
      stopWalk();
      if (life.onArrive) { const f = life.onArrive; life.onArrive = null; setTimeout(f, 400); }   // дошла — дальше дело (спрятаться)
    }
  }
  if (life.mode === "back" && Math.abs(life.yaw) < 0.05 && Math.abs(life.yawV) < 0.05) {
    life.mode = "idle";
    life.next = t + rnd(45, 110);
  }
}

// ---------- занятия от скуки ----------
// Пока человек занят, она живёт своей жизнью: читает, пьёт чай, сидит в телефоне, напевает, зевает, следит за бабочкой.
// Руки — позой с плавным весом поверх клипа покоя. Кодом руки легко выходят деревянными, поэтому позы — «держит
// предмет»: с книгой или чашкой в руках поза читается естественно; пальцы сжаты. Реквизит появляется облачком, когда
// руки уже на месте. Любая помеха (заговорила, тянут, думает, пошла) — занятие плавно кончается.
const hump = (t, period, len, off = 0) => {                  // 0…1…0 раз в period секунд, длиной len
  const ph = ((t + off) % period + period) % period;
  return ph > len ? 0 : Math.min(1, Math.sin(Math.PI * ph / len) * 1.7);
};
const mix3 = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);
const breathK = (t) => {                                     // 4-7-8: 0 → 1 за вдох, держит, 1 → 0 за выдох; круг 19 с
  const ph = t % 19, ease = (x) => 0.5 - Math.cos(Math.PI * x) / 2;
  return ph < 4 ? ease(ph / 4) : ph < 11 ? 1 : 1 - ease((ph - 11) / 8);
};
const PT = {
  book: { dur: [26, 40], grip: 0.85, prop: "book",
    pose: (t) => { const g = hump(t, 9, 1.8, 4);           // поднимает глаза от книги
      return { ...both([-0.42, -0.5, -1.18], [0, -1.55, 0], [0.25, 0, 0]),         // книга у груди, не перед лицом
        neck: [0.32 - g * 0.3, 0, 0], head: [0.14 - g * 0.1, 0, 0], spine: [0.05, 0, 0] }; },
    look: (t, prop) => (hump(t, 9, 1.8, 4) > 0.4 ? null : prop), happy: (t) => hump(t, 9, 1.8, 4) * 0.45 },
  tea: { dur: [20, 30], grip: 0.9, prop: "cup",
    pose: (t) => { const s = hump(t, 6.5, 2.2, 2.5);      // глоток
      return { ...arm("left", [-0.42, -0.6, -1.2], [0, -1.55, 0], [0.1, 0, 0]),
        ...arm("right", mix3([-0.4, -0.55, -1.18], [-1.05, -0.62, -0.95], s), mix3([0, -1.85, 0], [0, -2.35, 0], s), [0.1, 0, 0]),
        neck: [0.12 - s * 0.24, 0, 0], head: [0.04 - s * 0.08, 0, 0] }; },
    eyes: (t) => hump(t, 6.5, 2.2, 2.5) * 0.9, happy: () => 0.2 },
  phone: { dur: [16, 26], grip: 0.9, prop: "phone",
    pose: (t) => { const g = hump(t, 7, 1.4, 3);           // хихикает над чем-то
      return { ...both([-0.36, -0.5, -1.22], [0, -1.7, 0], [0.35, 0, 0]),          // телефон у груди, лицо видно
        neck: [0.32, 0, 0.05], head: [0.1, 0, 0], spine: [0.03 + Math.sin(t * 17) * 0.025 * g, 0, 0] }; },
    look: (t, prop) => prop, happy: (t) => 0.15 + hump(t, 7, 1.4, 3) * 0.65 },
  hum: { dur: [10, 16], notes: true,
    pose: (t) => ({ neck: [0, 0, Math.sin(t * 2.4) * 0.09], head: [0, Math.sin(t * 1.2) * 0.06, 0], spine: [0, 0, Math.sin(t * 2.4 + 0.5) * 0.03] }),
    eyes: () => 0.55, happy: () => 0.35 },
  yawn: { dur: [4.2, 4.2],
    pose: () => ({ ...arm("left", [-1.0, -0.75, -0.95], [0, -2.45, 0], [0, 0, 0]), neck: [-0.18, 0, 0.1], head: [-0.1, 0, 0], spine: [-0.05, 0, 0] }),
    mouth: (t) => Math.min(1, Math.max(0, (t - 0.5) / 0.8)) * Math.min(1, Math.max(0, (3.4 - t) / 0.6)) * 0.9, eyes: () => 0.85 },
  butterfly: { dur: [12, 18], butterfly: true, happy: () => 0.4 },
  // мячик: подбрасывает и ловит; изредка роняет — мяч скачет по полу, она смущается
  juggle: { dur: [12, 20], grip: 0.4, juggle: true,
    pose: (t, p) => { const ph = p?.ball ? p.ball.ph : 0, up = ph < 0.15 ? ph / 0.15 : ph > 0.85 ? (1 - ph) / 0.15 : 0;
      return { ...arm("right", [-0.55 - up * 0.25, -0.5, -1.1 + up * 0.15], [0, -1.4 + up * 0.35, 0], [0.2, 0, 0]),
        neck: [0.05 - (p?.ball?.h || 0) * 0.35, 0, 0] }; },
    happy: () => 0.35 },
  fan: { dur: [10, 16], grip: 0.85, prop: "fan", hot: true,                        // жарко — обмахивается веером
    pose: (t) => ({ ...arm("right", [-0.55, -0.2, -0.95], [0, -1.85, Math.sin(t * 11) * 0.12], [0, 0, 0.45 * Math.sin(t * 11)]),   // веер у щеки, сбоку
      neck: [-0.05, 0, 0.05] }), eyes: () => 0.35 },
  // дыхание 4-7-8 вместе с вами: вдох 4 с — руки поднимаются ладонями вверх, задержка 7 с — глаза прикрыты,
  // выдох 8 с — руки опускаются. Подсказка и счёт — облачком. Только по просьбе («давай подышим»)
  breathe: { dur: [76, 76], festive: true,
    pose: (t) => { const k = breathK(t);
      return { ...both(mix3([-0.2, -0.35, -1.25], [-0.85, -0.5, -0.95], k), mix3([0, -1.25, 0], [0, -1.0, 0], k), [0.35, 0, 0]),
        spine: [-0.05 * k, 0, 0], neck: [-0.06 * k, 0, 0] }; },
    eyes: (t) => { const ph = t % 19; return ph > 4 && ph < 11 ? 0.85 : 0.3; }, happy: () => 0.25,
    label: (t) => { const ph = t % 19;
      return ph < 4 ? `Вдох носом… ${4 - Math.floor(ph)}` : ph < 11 ? `Задержи дыхание… ${11 - Math.floor(ph)}` : `Выдох ртом… ${19 - Math.floor(ph)}`; } },
  // праздничные — только по событию ядра (день рождения, Новый год), не от скуки
  cake: { dur: [9, 9], grip: 0.85, prop: "cake", confetti: true, festive: true,
    pose: () => ({ ...both([-0.7, -0.38, -1.1], [0, -1.45, 0], [0.1, 0, 0]), neck: [0.08, 0, 0.08] }), happy: () => 0.85 },
  letter: { dur: [5.5, 5.5], grip: 0.9, prop: "letter", festive: true,          // протягивает конверт вам
    pose: () => ({ ...both([-0.95, -0.72, -1.05], [0, -0.7, 0], [0.1, 0, 0]), neck: [0.06, 0, -0.08], spine: [0.05, 0, 0] }), happy: () => 0.5 },
  confetti: { dur: [6, 6], confetti: true, festive: true,
    pose: (t) => ({ ...both([-0.2, 0, 0.9 + Math.sin(t * 8) * 0.15], [0, 0, 0.3], [0, 0, 0]), spine: [-0.06, 0, 0] }), happy: () => 0.9 },
};
function ptBusy() {
  return st.watching || st.dragging || st.sleeping || st.speechOn || st.thinking || st.listening || st.patting || st.oneShot || demoRunning || game.kind ||
    home.on || life.mode !== "idle" || (phys.mode !== "ground" && phys.mode !== "perch") || menuEl.classList.contains("show");
}
function startPastime(name, force = false) {
  // дыхание по просьбе не обрывают праздничные (конверт напоминания, конфетти дружбы) — счёт сбивался на середине
  if (force && st.pastime?.name === "breathe" && !st.pastime.ending && name !== "breathe" && PT[name]?.festive) return false;
  if (force && st.pastime && !st.dragging) { dropProp(st.pastime); st.pastime = null; }
  if (!vrm || st.pastime || (!force && (ptBusy() || st.camMode)) || st.dragging) return false;   // в «вебке» реквизит не влезет в кадр
  const hour = new Date().getHours(), night = hour >= 23 || hour < 6;
  const pool = name ? [name] : night ? ["yawn", "book", "book", "tea", "hum"]
    : ["book", "tea", "phone", "hum", "hum", "butterfly", "yawn", "juggle", "juggle", ...(st.wx?.hot ? ["fan", "fan"] : [])];
  name = pool[Math.floor(Math.random() * pool.length)];
  const d = PT[name];
  if (!d) return false;
  st.pastime = { name, t0: clock.elapsedTime, dur: rnd(d.dur[0], d.dur[1]), w: 0, ending: false, prop: null, nextFx: 0 };
  st.ptGrip = 0;
  return true;
}
function endPastime() { if (st.pastime) st.pastime.ending = true; }
const _pa = new THREE.Vector3(), _pb = new THREE.Vector3();
function palm(side) {                                       // середина ладони: между запястьем и основанием среднего пальца
  const h = vrm.humanoid.getRawBoneNode(side + "Hand"), f = vrm.humanoid.getRawBoneNode(side + "MiddleProximal");
  h.getWorldPosition(_pa);
  if (f) _pa.lerp(f.getWorldPosition(_pb), 0.6);
  return _pa.clone();
}
function ptProp(kind) {
  const h = modelH, g = new THREE.Group();
  const M = (c, r = 0.6, e) => new THREE.MeshStandardMaterial({ color: c, roughness: r, ...(e ? { emissive: e, emissiveIntensity: 0.9 } : {}) });
  const head = headPos(0);
  let pos, quat, bone;
  if (kind === "cup") {
    const body = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.022, h * 0.018, h * 0.045, 24, 1, true), M(0xfaf3ea, 0.35));
    const bottom = new THREE.Mesh(new THREE.CircleGeometry(h * 0.018, 24), M(0xfaf3ea, 0.35));
    bottom.rotation.x = Math.PI / 2; bottom.position.y = -h * 0.0225;
    const tea = new THREE.Mesh(new THREE.CircleGeometry(h * 0.0205, 24), M(0x9a5a2a, 0.2));
    tea.rotation.x = -Math.PI / 2; tea.position.y = h * 0.016;
    const handle = new THREE.Mesh(new THREE.TorusGeometry(h * 0.011, h * 0.0028, 8, 16, Math.PI * 1.2), M(0xfaf3ea, 0.35));
    handle.position.x = h * 0.022; handle.rotation.z = -Math.PI * 0.6;
    g.add(body, bottom, tea, handle);
    bone = vrm.humanoid.getRawBoneNode("rightHand");
    pos = palm("right"); quat = vrm.scene.quaternion.clone();                // чашка стоит прямо
  } else {
    if (kind === "cake") {                                                       // торт со свечой
      const base = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.045, h * 0.045, h * 0.035, 28), M(0xfff1e6, 0.8));
      const cream = new THREE.Mesh(new THREE.TorusGeometry(h * 0.042, h * 0.006, 8, 28), M(0xff9ec4, 0.6));
      cream.rotation.x = Math.PI / 2; cream.position.y = h * 0.0175;
      const candle = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.003, h * 0.003, h * 0.03, 8), M(0x7ad7ff, 0.5));
      candle.position.y = h * 0.033;
      const flame = new THREE.Mesh(new THREE.SphereGeometry(h * 0.005, 10, 8), M(0xffd36e, 0.2, 0xffa42e));
      flame.scale.y = 1.6; flame.position.y = h * 0.052;
      g.add(base, cream, candle, flame);
      bone = vrm.humanoid.getRawBoneNode("upperChest") || vrm.humanoid.getRawBoneNode("chest");
      pos = palm("left").add(palm("right")).multiplyScalar(0.5).add(vec(0, h * 0.02, 0));
      attach(g, bone, pos, vrm.scene.quaternion.clone());                       // торт стоит прямо
      ptPoof(pos, 0.5);
      return g;
    }
    if (kind === "fan") {                                                        // складной веер-полукруг
      const leaf = new THREE.Mesh(new THREE.CircleGeometry(h * 0.06, 20, 0, Math.PI), M(0xff9ec4, 0.8));
      leaf.material.side = THREE.DoubleSide; g.add(leaf);
      const rib = new THREE.Mesh(new THREE.BoxGeometry(h * 0.004, h * 0.03, h * 0.004), M(0x8a5a33, 0.6));
      rib.position.y = -h * 0.012; g.add(rib);
      bone = vrm.humanoid.getRawBoneNode("rightHand");
      pos = palm("right").add(vec(0, h * 0.03, 0));
      attach(g, bone, pos, new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(head, pos, vec(0, 1, 0))));
      ptPoof(pos, 0.4);
      return g;
    }
    if (kind === "letter") {                                                     // конверт с сердечком-печатью
      g.add(new THREE.Mesh(new THREE.BoxGeometry(h * 0.075, h * 0.05, h * 0.003), M(0xfff6e8, 0.9)));
      const flap = new THREE.Mesh(new THREE.CircleGeometry(h * 0.038, 3), M(0xf3e3cc, 0.9));
      flap.rotation.z = -Math.PI / 2; flap.scale.y = 0.75; flap.position.set(0, h * 0.01, h * 0.0018); g.add(flap);
      const seal = new THREE.Mesh(new THREE.CircleGeometry(h * 0.006, 16), M(0xd8364a, 0.4));
      seal.position.set(0, -h * 0.002, h * 0.002); g.add(seal);
      bone = vrm.humanoid.getRawBoneNode("upperChest") || vrm.humanoid.getRawBoneNode("chest");
      pos = palm("left").add(palm("right")).multiplyScalar(0.5).add(vec(0, h * 0.015, h * 0.01));
      attach(g, bone, pos, new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(camera.position, pos, vec(0, 1, 0))));  // лицом к вам
      ptPoof(pos, 0.5);
      return g;
    }
    if (kind === "book") {
      for (const sd of [-1, 1]) {
        const page = new THREE.Mesh(new THREE.BoxGeometry(h * 0.07, h * 0.095, h * 0.006), M(0xfbf7ee, 0.9));
        page.position.x = sd * h * 0.034; page.rotation.y = -sd * 0.22; g.add(page);
        const cover = new THREE.Mesh(new THREE.BoxGeometry(h * 0.073, h * 0.099, h * 0.003), M(0x7a3b4a, 0.7));
        cover.position.set(sd * h * 0.035, 0, -h * 0.005); cover.rotation.y = -sd * 0.22; g.add(cover);
      }
      bone = vrm.humanoid.getRawBoneNode("upperChest") || vrm.humanoid.getRawBoneNode("chest");
      pos = palm("left").add(palm("right")).multiplyScalar(0.5).add(vec(0, h * 0.03, 0));
    } else {                                                                     // телефон
      g.add(new THREE.Mesh(new THREE.BoxGeometry(h * 0.04, h * 0.085, h * 0.005), M(0x22202a, 0.4)));
      const scr = new THREE.Mesh(new THREE.PlaneGeometry(h * 0.036, h * 0.078), M(0x9ad0ff, 0.3, 0x5aa8ff));
      scr.position.z = h * 0.0027; g.add(scr);
      bone = vrm.humanoid.getRawBoneNode("rightHand");
      pos = palm("right").add(vec(0, h * 0.012, 0));
    }
    quat = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(head, pos, vec(0, 1, 0)));   // лицом к ней
  }
  attach(g, bone, pos, quat);
  ptPoof(pos, 0.5);
  return g;
}
function dropProp(p) {
  if (!p?.prop) return;
  const w = p.prop.getWorldPosition(new THREE.Vector3());
  p.prop.parent?.remove(p.prop);
  p.prop.traverse((o) => { o.geometry?.dispose(); o.material?.dispose?.(); });
  p.prop = null;
  ptPoof(w, 0.4);
}
function ptPoof(pos, k = 1) {
  for (let i = 0; i < 6; i++) particle({ tex: "puff", pos: pos.clone(), vel: vec(rnd(-0.08, 0.08) * k, rnd(0.02, 0.1) * k, rnd(-0.03, 0.03)),
    drag: 3, grow: 1.4, life: rnd(0.4, 0.7), size: rnd(0.02, 0.035) * k, fade: 0.6, opacity: 0.8 });
}
function pastimeTick(dt, t, v0) {
  const p = st.pastime;
  if (!p) return;
  const d = PT[p.name], lt = t - p.t0;
  if (!p.ending && (lt > p.dur || (!d.festive && ptBusy()) || st.dragging)) endPastime();
  p.w = damp(p.w, p.ending ? 0 : 1, p.ending ? 3.5 : 2.4, dt);
  if (d.label) {                                             // подсказка облачком (дыхание): меняем, только когда сменилась
    const text = p.ending ? "" : d.label(lt);
    if (text !== p.label) { p.label = text; if (text) say(text, 0); else hideBubble(); }
  }
  if (p.ending && p.w < 0.03) {
    dropProp(p);
    if (p.fly) p.fly.away = true;
    if (p.ball) { scene.remove(p.ball.mesh); p.ball.mesh.geometry.dispose(); }
    st.pastime = null; st.ptEyes = st.ptMouth = st.ptGrip = st.ptHappy = 0; st.lookAt = null;
    return;
  }
  if (d.juggle) juggleTick(p, dt, lt);
  const pose = d.pose ? d.pose(lt, p) : {};
  for (const [bone, r] of Object.entries(pose)) {
    const node = B[bone];
    if (!node) continue;
    // руки — к позе (у VRM 0.x оси X и Z развёрнуты), корпус и голова — добавкой
    if (/Arm|Hand/.test(bone)) node.quaternion.slerp(qt.setFromEuler(e3.set(r[0] * v0, r[1], r[2] * v0)), p.w);
    else node.quaternion.multiply(q.setFromEuler(e3.set(r[0] * p.w * v0, r[1] * p.w, r[2] * p.w * v0)));
  }
  st.ptGrip = d.grip && p.w > 0.5 ? d.grip : 0;
  st.ptEyes = (d.eyes ? d.eyes(lt) : 0) * p.w;
  st.ptMouth = (d.mouth ? d.mouth(lt) : 0) * p.w;
  st.ptHappy = (d.happy ? d.happy(lt) : 0) * p.w;
  if (d.prop && !p.prop && !p.ending && p.w > 0.92) { vrm.scene.updateMatrixWorld(true); p.prop = ptProp(d.prop); }
  const propPos = p.prop ? p.prop.getWorldPosition(new THREE.Vector3()) : null;
  st.lookAt = d.look ? d.look(lt, propPos) : null;
  if (t > p.nextFx && !p.ending) {
    if (d.confetti) {                                        // конфетти сыплется сверху
      p.nextFx = t + 0.06;
      const hp = headPos(0.35);
      particle({ tex: ["star", "petal", "heart"][Math.floor(Math.random() * 3)], pos: hp.add(vec(rnd(-0.35, 0.35) * modelH, rnd(0, 0.1) * modelH, rnd(-0.05, 0.15))),
        vel: vec(rnd(-0.04, 0.04), -rnd(0.12, 0.22)), sway: 0.2, spin: rnd(-3, 3), life: 2.6, size: rnd(0.018, 0.03), fade: 0.25 });
    } else if (d.prop === "cup" && propPos) {                       // пар над чашкой
      p.nextFx = t + 0.35;
      particle({ tex: "puff", pos: propPos.clone().add(vec(0, modelH * 0.03, 0)), vel: vec(rnd(-0.008, 0.008), 0.05), sway: 0.02,
        life: 1.6, size: 0.012, grow: 1.2, fade: 0.7, opacity: 0.45 });
    } else if (d.notes) {                                    // ноты от головы
      p.nextFx = t + rnd(0.6, 1.0);
      const hp = headPos(0.02), sd = Math.random() < 0.5 ? -1 : 1;
      particle({ tex: Math.random() < 0.5 ? "note0" : "note1", pos: hp.add(vec(sd * modelH * 0.07, 0, 0)), vel: vec(sd * 0.03, 0.09),
        sway: 0.08, life: 1.8, size: rnd(0.028, 0.04), fade: 0.4 });
    } else if (d.butterfly && !p.fly) {                      // бабочка кружит у головы, она следит глазами
      const ph = Math.random() * 6.28, base = 0.045;
      const fly = p.fly = { away: false };
      particle({ tex: "butterfly", pos: headPos(0.02), life: 60, size: base, fade: 0.05, upd: (f, fdt) => {
        if (fly.away) {
          f.obj.position.y += modelH * 0.25 * fdt; f.obj.position.x += modelH * 0.15 * fdt;
          if (!fly.out) { fly.out = true; f.max = f.life + 1.4; }
        } else {
          const k = f.life * 0.9 + ph, hc = headPos(0.02);
          f.obj.position.set(hc.x + Math.sin(k) * modelH * 0.16, hc.y + Math.sin(k * 1.7) * modelH * 0.07 + modelH * 0.03,
            hc.z + Math.cos(k) * modelH * 0.1 + modelH * 0.06);
          fly.pos = f.obj.position;
        }
        const sz = base * modelH;
        f.obj.scale.set(sz * (0.35 + 0.65 * Math.abs(Math.sin(f.life * 16))), sz, 1);   // машет крыльями
      } });
    }
  }
  if (p.fly?.pos) st.lookAt = p.fly.pos;
  if (p.ball) st.lookAt = p.ball.mesh.position;             // следит за мячиком
}

function juggleTick(p, dt, lt) {
  if (p.w < 0.9 && !p.ball) return;
  const h = modelH;
  if (!p.ball) {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(h * 0.018, 16, 12), new THREE.MeshStandardMaterial({ color: 0xff6f9c, roughness: 0.5 }));
    mesh.castShadow = true;
    scene.add(mesh);
    p.ball = { mesh, ph: 0, period: 1.15, h: 0, drop: false, vy: 0, bounces: 0 };
  }
  const b = p.ball, hand = palm("right");
  if (b.drop) {                                              // уронила: мяч падает и скачет по полу
    b.vy -= 9.8 * 0.6 * dt; b.mesh.position.y += b.vy * h * 0.6 * dt; b.mesh.position.x += h * 0.15 * dt;
    if (b.mesh.position.y < h * 0.018) {
      b.mesh.position.y = h * 0.018; b.vy = -b.vy * 0.55;
      if (++b.bounces > 4 && !p.ending) { setEmotion("shy", 3, false); endPastime(); }
    }
    return;
  }
  b.ph += dt / b.period;
  if (b.ph >= 1) {
    b.ph -= 1;
    if (!p.ending && lt > 4 && Math.random() < 0.2) { b.drop = true; b.vy = -0.3; return; }   // примерно раз из пяти — мимо
    if (p.ending) { b.ph = 0; }
  }
  b.h = 4 * b.ph * (1 - b.ph);                               // парабола: 0 → вершина → 0
  b.mesh.position.copy(hand).add(vec(0, h * (0.02 + b.h * 0.32), h * 0.02));
}

// ---------- погодный гардероб: зонтик в дождь, шарф в мороз, веер в жару ----------
// Погоду берём у ядра (тот же Open-Meteo, что виджет) раз в полчаса; без города — ничего не надевает.
const WX_RAIN = new Set([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99]);
const WX_SNOW = new Set([71, 73, 75, 77, 85, 86]);
async function weatherPoll() {
  if (!cfg.city || cfg.avatar?.wardrobe === false) { st.wx = null; return; }
  try {
    const w = await fetch("/api/weather").then((r) => r.json());
    if (!w.now) return;
    st.wx = { rain: WX_RAIN.has(w.now.code), snow: WX_SNOW.has(w.now.code), cold: w.now.temp <= 0, hot: w.now.temp >= 27, temp: w.now.temp };
  } catch {}
}
setTimeout(weatherPoll, 8000);
setInterval(weatherPoll, 30 * 60 * 1000);
function wardrobeTick(dt, t, v0) {
  const wx = st.wx;
  const umbrellaOn = !!wx?.rain && !st.pastime && !st.oneShot && !st.dragging && !st.sleeping && !home.on && phys.mode !== "hang";
  st.umbW = damp(st.umbW || 0, umbrellaOn ? 1 : 0, 3, dt);
  if (st.umbW > 0.01) {                                      // зонтик: правая рука держит его над головой
    const r1 = arm("right", [-0.35, -0.35, -0.55], [0, -1.75, 0], [0, 0, 0]);
    for (const [bone, r] of Object.entries(r1)) B[bone]?.quaternion.slerp(qt.setFromEuler(e3.set(r[0] * v0, r[1], r[2] * v0)), st.umbW);
  }
  if (umbrellaOn && st.umbW > 0.9 && (!st.umbrella || st.umbrellaVrm !== vrm)) {
    vrm.scene.updateMatrixWorld(true);
    const h = modelH, g = new THREE.Group();
    // купол видно снизу — изнанку подсвечиваем, иначе выходит чёрный блин; ручка проходит сквозь ладонь
    const canopy = new THREE.Mesh(new THREE.ConeGeometry(h * 0.2, h * 0.07, 16, 1, true),
      new THREE.MeshStandardMaterial({ color: 0x8cc4ff, roughness: 0.6, side: THREE.DoubleSide, emissive: 0x4a7cc0, emissiveIntensity: 0.55 }));
    canopy.position.y = h * 0.36;
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.0035, h * 0.0035, h * 0.44, 6), new THREE.MeshStandardMaterial({ color: 0x5a4a3a }));
    shaft.position.y = h * 0.14;
    const crook = new THREE.Mesh(new THREE.TorusGeometry(h * 0.012, h * 0.0035, 6, 12, Math.PI), new THREE.MeshStandardMaterial({ color: 0x5a4a3a }));
    crook.position.set(h * 0.012, -h * 0.08, 0); crook.rotation.z = Math.PI;
    g.add(canopy, shaft, crook);
    const pos = palm("right");
    attach(g, vrm.humanoid.getRawBoneNode("rightHand"), pos, vrm.scene.quaternion.clone());
    st.umbrella = g; st.umbrellaVrm = vrm;
    ptPoof(pos.add(vec(0, h * 0.2, 0)), 0.6);
  }
  if (st.umbrella && (!umbrellaOn || st.umbrellaVrm !== vrm) && st.umbW < 0.5) {
    st.umbrella.parent?.remove(st.umbrella); st.umbrella = null;
  }
  // шарф в мороз (и в снег): толстое кольцо на шее + конец, свисающий на грудь; пар изо рта
  const scarfOn = !!(wx?.cold || wx?.snow) && !home.on;
  if (scarfOn && (!st.scarf || st.scarfVrm !== vrm)) {
    const h = modelH, g = new THREE.Group(), m = new THREE.MeshStandardMaterial({ color: 0xd8364a, roughness: 0.95 });
    const ring = new THREE.Mesh(new THREE.TorusGeometry(h * 0.045, h * 0.016, 10, 24), m);
    ring.rotation.x = Math.PI / 2; ring.scale.set(1, 0.85, 1);
    const tail = new THREE.Mesh(new THREE.BoxGeometry(h * 0.03, h * 0.11, h * 0.012), m);
    tail.position.set(h * 0.02, -h * 0.055, h * 0.04); tail.rotation.z = 0.12;
    g.add(ring, tail);
    const neck = vrm.humanoid.getRawBoneNode("neck");
    attach(g, neck, neck.getWorldPosition(new THREE.Vector3()).add(vec(0, -h * 0.005, h * 0.005)), vrm.scene.quaternion.clone());
    st.scarf = g; st.scarfVrm = vrm;
  }
  if (st.scarf) st.scarf.visible = scarfOn && st.scarfVrm === vrm;
  if (wx?.cold && t > (st.breathAt || 0) && !st.sleeping) {
    st.breathAt = t + rnd(2.5, 4.5);
    particle({ tex: "puff", pos: headPos(-0.06).add(vec(0, 0, modelH * 0.05)), vel: vec(rnd(-0.01, 0.01), 0.02, 0.05), drag: 1.2,
      grow: 1.8, life: 1.4, size: 0.02, fade: 0.7, opacity: 0.35 });
  }
}

// ---------- песочные часы: короткий таймер стоит рядом с ней ----------
// Песок пересыпается по настоящему времени; вышло — часы трясутся и исчезают облачком (напоминание скажет ядро).
const timers = [];
function addTimer(id, seconds) {
  const h = modelH, g = new THREE.Group();
  const glass = new THREE.MeshStandardMaterial({ color: 0xdff4ff, roughness: 0.1, transparent: true, opacity: 0.35, depthWrite: false });
  const wood = new THREE.MeshStandardMaterial({ color: 0x8a5a33, roughness: 0.7 });
  const sandM = new THREE.MeshStandardMaterial({ color: 0xf0b45a, roughness: 0.9 });
  const H = h * 0.09, Rr = h * 0.03;
  for (const sd of [-1, 1]) {
    const bulb = new THREE.Mesh(new THREE.ConeGeometry(Rr, H / 2, 20, 1, true), glass);
    bulb.position.y = H / 2 + sd * H / 4; bulb.rotation.x = sd > 0 ? Math.PI : 0; g.add(bulb);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(Rr * 1.2, Rr * 1.2, h * 0.006, 20), wood);
    cap.position.y = H / 2 + sd * (H / 2 + h * 0.003); g.add(cap);
  }
  for (const a of [0, 2.1, 4.2]) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.0025, h * 0.0025, H, 6), wood);
    post.position.set(Math.cos(a) * Rr * 1.05, H / 2, Math.sin(a) * Rr * 1.05); g.add(post);
  }
  const top = new THREE.Mesh(new THREE.ConeGeometry(Rr * 0.85, H * 0.42, 20), sandM);   // вершиной вниз — к горлышку
  top.rotation.x = Math.PI; top.position.y = H * 0.73;
  const bot = new THREE.Mesh(new THREE.ConeGeometry(Rr * 0.9, H * 0.42, 20), sandM);
  bot.position.y = H * 0.21;
  const stream = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.0012, h * 0.0012, H * 0.45, 4), sandM);
  stream.position.y = H * 0.38;
  g.add(top, bot, stream);
  const slot = timers.length;
  g.position.set(modelH * (0.24 + slot * 0.08), 0, 0.06);
  scene.add(g);
  ptPoof(g.position.clone().add(vec(0, H / 2, 0)), 0.6);
  timers.push({ id, g, top, bot, stream, total: seconds, due: performance.now() + seconds * 1000, shake: 0 });
}
function timerTick(dt) {
  for (let i = timers.length - 1; i >= 0; i--) {
    const tm = timers[i], left = Math.max(0, (tm.due - performance.now()) / 1000), k = 1 - left / tm.total;
    tm.top.scale.set(Math.max(0.05, 1 - k) ** 0.5, Math.max(0.02, 1 - k), Math.max(0.05, 1 - k) ** 0.5);
    tm.bot.scale.set(Math.max(0.05, k) ** 0.5, Math.max(0.02, k), Math.max(0.05, k) ** 0.5);
    tm.stream.visible = left > 0;
    if (left <= 0 || tm.done) {
      tm.shake += dt;
      tm.g.rotation.z = Math.sin(tm.shake * 40) * 0.15 * Math.max(0, 1 - tm.shake / 1.6);
      if (tm.shake > 1.6) { removeTimer(tm.id); }
    }
  }
}
function removeTimer(id, quiet = false) {
  const i = timers.findIndex((x) => x.id === id);
  if (i < 0) return;
  const tm = timers[i];
  if (!quiet) ptPoof(tm.g.position.clone().add(vec(0, modelH * 0.045, 0)), 0.6);
  scene.remove(tm.g);
  tm.g.traverse((o) => { o.geometry?.dispose(); });
  timers.splice(i, 1);
}

// ---------- безделье: осмотреться, потянуться, уснуть ----------
const SLEEP_AFTER = 10 * 60 * 1000;
function activity() {
  st.lastActivity = performance.now();
  if (st.sleeping) {
    st.sleeping = false;
    showState("");
    gesture("stretch", 1.6);
    send({ type: "avatar.event", kind: "wake" });
  }
}
function tickIdle(t) {
  if (!st.sleeping && performance.now() - st.lastActivity > SLEEP_AFTER && !st.speaking && !st.thinking) {
    st.sleeping = true;
    say("Zzz…", 5000);
    showState("спит");
  }
  if (t < st.nextIdle || st.gesture || st.pastime || st.sleeping || st.speaking > 0.05 || st.thinking) return;
  st.nextIdle = t + 18 + Math.random() * 25;
  if (cfg.avatar?.pastimes !== false && t > (st.nextPastime ?? 75) && startPastime()) {
    st.nextPastime = t + rnd(110, 240);              // занятие раз в 2–4 минуты: живёт, но не мельтешит
    return;
  }
  const r = Math.random(), hour = new Date().getHours();
  if (r < 0.4) st.lookAround = 3;
  else if (r < 0.55) gesture("stretch", 2);
  else if (r < 0.7) gesture("nod", 1.5);
  else if (r < 0.76) sneeze(["autumn", "halloween"].includes(season()) && Math.random() < 0.5 ? "leaf" : "");
  else if ((hour >= 23 || hour < 5) && t > (st.lateSaidAt ?? -1e9) + 1200) { st.lateSaidAt = t; say(line("late"), 4000); gesture("stretch", 2); }
}

// ---------- речь и липсинк ----------
const actx = new AudioContext();
const analyser = actx.createAnalyser();
analyser.fftSize = 512;
analyser.connect(actx.destination);
const buf = new Float32Array(analyser.fftSize);
let speechQueue = Promise.resolve();
function levelLoop() {
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (const v of buf) s += v * v;
  st.speaking = Math.min(1, Math.max(0, (Math.sqrt(s / buf.length) - 0.015) * 7));
  requestAnimationFrame(levelLoop);
}
levelLoop();
function playSpeech(m) {
  activity();
  speechQueue = speechQueue.then(() => new Promise(async (resolve) => {
    const done = () => { st.speechOn = false; send({ type: "speech.ended", id: m.id }); setTimeout(hideBubble, 2500); resolve(); };
    try {
      await actx.resume();
      const audio = await actx.decodeAudioData(await fetch(m.url).then((r) => r.arrayBuffer()));
      const src = actx.createBufferSource();
      src.buffer = audio;
      src.connect(analyser);
      src.onended = done;
      say(m.text, 0);
      if (m.emotion) setEmotion(m.emotion, audio.duration + 2);
      st.speechOn = true;
      src.start();
    } catch (e) { console.error(e); done(); }
  }));
}

// ---------- облачко ----------
let bubbleTimer = 0;
// В облачке — только реплика: ремарки в звёздочках и заикания «Н-не» хозяин счёл лишними (жесты показывает тело)
function cleanLine(text) {
  let t = String(text);
  const only = t.match(/^\s*\*([^*\n]+)\*\s*$/);           // реплика целиком из ремарки («*апчхи!*») — оставляем слова
  t = only ? only[1] : t.replace(/\*[^*\n]+\*/g, " ");
  t = t.replace(/(^|[^\p{L}-])(\p{L})-(\p{L})/gu, (m, a, b, c) => (b.toLowerCase() === c.toLowerCase() ? a + b : m));
  t = t.replace(/\s*\[(happy|shy|angry|sad|surprised|relaxed|neutral)\]\s*$/i, "");
  t = t.replace(/\s{2,}/g, " ").replace(/\s+([,.!?…])/g, "$1").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
// Сойти со всех «мест» — край окна, укрытие за окном, прогулка, «телевизор» — и просто стоять. Перед «вебкой»:
// окно уезжает в угол экрана с игрой, и раньше она оставалась там спиной к вам (смотрела видео), за окном игры
// (пряталась) или «сидящей» на окне, к которому после игры прыгала.
async function toGround() {
  if (phys.mode === "peek") {
    phys.mode = "ground"; phys.peekWin = null;
    if (game.kind === "seek") game.kind = null;
    await K.behind?.(0);
  }
  if (life.mode !== "idle") stopWalk();
  life.mode = "idle"; life.onArrive = null; life.yaw = life.yawV = 0;
  if (st.watching) stopWatching(false);
  st.perched = false; phys.mode = "ground"; phys.hwnd = 0;
  if (vrm) vrm.scene.rotation.y = st.baseYaw || 0;
}
// в «вебке» окно стоит в углу экрана с игрой: ходить, садиться на окна и прятаться некуда — так и говорит
function camBusy() {
  if (st.camMode) say(line("cam_busy"), 3000);
  return st.camMode;
}
// игровой режим: прятаться совсем или стать «вебкой» в углу экрана с игрой (behavior.game_style: auto — вебка,
// когда экран один и спрятанную её было бы негде видеть; hide; cam). Размер, угол и прозрачность — avatar.cam.
async function setGameMode(on) {
  const style = cfg.behavior?.game_style || "auto";
  let cam = false;
  if (on && style !== "hide") cam = style === "cam" || (await K.screens?.().catch(() => null))?.length === 1;
  if (on && cam) {
    if (st.pastime) endPastime();
    await toGround();                // до K.gameMode: в «вебке» оболочка уже не вернёт окно «поверх всех»
    st.sitting = 0;
  }
  const camNow = on && cam, leavingCam = st.camMode && !camNow;
  st.gameMode = on;
  if (!leavingCam) st.camMode = camNow;
  document.body.style.visibility = on && !cam ? "hidden" : "";
  document.body.classList.toggle("cam", camNow);
  document.body.classList.toggle("cam-frame", camNow && cfg.avatar?.cam?.frame !== false);
  renderer.setPixelRatio(camNow ? 1 : devicePixelRatio);
  const c = cfg.avatar?.cam || {};
  K.gameMode?.(camNow ? { on: true, cam: { corner: c.corner || "br", size: c.size || 260, opacity: c.opacity ?? 1 } } : on);
  if (!K.gameMode) dispatchEvent(new Event("resize"));
  // из «вебки»: физика и прогулка ждут, пока оболочка вернёт окно и придут его новые координаты. Иначе первым же
  // кадром она ставила окно по старым координатам угла — и оставалась там, где была «вебка»
  if (leavingCam) { await new Promise((r) => setTimeout(r, 250)); st.camMode = false; }
  if (!on) frame();
  if (st.camMode) setTimeout(() => say(line("cam_hello"), 3500), 800);
}

function say(text, ms = 6000) {
  const esc = cleanLine(text).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  bubble.innerHTML = esc;
  bubble.classList.add("show");
  clearTimeout(bubbleTimer);
  if (ms) bubbleTimer = setTimeout(hideBubble, ms);
}
function hideBubble() { bubble.classList.remove("show"); }
function showState(text) { stateEl.textContent = text || ""; stateEl.classList.toggle("show", !!text); }

// ---------- банк фраз: облачка в характере человека (core/lines.py; модель пишет их под его характер) ----------
let LINES = {};
const loadLines = () => fetch("/api/lines").then((r) => r.json()).then((d) => { LINES = d.lines || {}; }).catch(() => {});
const line = (key) => pick(LINES[key]?.length ? LINES[key] : ["…"]);
loadLines();

// ---------- связь с ядром ----------
let ws;
function send(m) { if (ws?.readyState === 1) ws.send(JSON.stringify(m)); }
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws?role=avatar`);
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    switch (m.type) {
      case "speech": if (m.player === "avatar" && m.url) playSpeech(m); else if (!m.url) say(m.text); break;
      case "chat.done": activity(); if (m.text && !m.voice) { say(m.text, 9000); setEmotion(m.emotion || "neutral", 6); } break;
      case "chat.user": activity(); break;
      case "avatar.state":
        st.thinking = m.state === "thinking";
        if (st.thinking) activity();
        showState(st.thinking ? "думает…" : st.sleeping ? "спит" : "");
        if (m.emotion && m.state !== "thinking") setEmotion(m.emotion, 5);
        break;
      case "avatar.gesture": activity(); gesture(m.name, m.seconds || 2.5); break;
      case "voice.state":
        st.listening = m.state === "listening" || m.state === "wake";
        if (st.listening) activity();
        showState({ listening: "слушаю…", wake: "слушаю…", recognizing: "распознаю…" }[m.state] || (st.thinking ? "думает…" : ""));
        break;
      case "notify": if (home.on) home.room.blink(); activity(); say(`${m.title}: ${m.text}`, 10000); setEmotion("surprised", 3); break;
      case "config.changed": loadModel(); break;
      case "animations.changed": reloadClips(); break;
      case "lines.changed": loadLines(); break;
      case "avatar.demo": demo(); break;
      case "avatar.pastime": activity(); startPastime(m.name, !!m.force); break;
      case "avatar.breathe":                                                         // дыхание 4-7-8, cycles кругов по 19 с
        activity();
        if (startPastime("breathe", true)) st.pastime.dur = Math.max(1, Math.min(8, m.cycles || 4)) * 19 + 0.5;
        break;
      case "llm.search": say(m.text, 6000); break;                                  // ищет модель — пусть видно и у неё
      case "avatar.watch": st.nextWatch = 0; st.watchAsked = true; break;           // попросили посмотреть видео вместе
      case "avatar.photo": takePhoto(m.pose, m.emotion); break;
      case "avatar.tint.parts": if (!m.parts) send({ type: "avatar.tint.parts", parts: tintParts(), model: modelKey }); break;   // отвечаем только на вопрос
      case "avatar.timer": if (vrm && timers.length < 4) addTimer(m.id, Math.max(5, m.seconds)); break;
      case "avatar.timer.cancel": removeTimer(m.id); break;
      case "avatar.timer.done": { const tm = timers.find((x) => x.id === m.id); if (tm) tm.done = true; break; }
      case "game.mode": setGameMode(!!m.on); break;
      case "avatar.screen": goScreen(m.index); break;
      case "avatar.game": deskGame(m); break;
      case "avatar.feed": feed(m.food || "🍙"); break;
      case "avatar.startle": startle(); break;
      case "friend.state": st.friend = m.level; break;
      case "friend.levelup":                                                         // новый уровень дружбы — праздник
        st.friend = m.level;
        activity();
        if (!startPastime("confetti", true)) confetti();                            // дышим — только конфетти, без позы
        setEmotion("happy", 4, false);
        say(line("levelup").split("{level}").join(window.i18n?.t?.(m.name) ?? m.name), 6000);
        break;
      case "avatar.throw": throwThing(m.what); break;
      case "avatar.seek": seekGame(); break;
      case "avatar.trick": trickStart(m.seconds || 40); break;
      case "avatar.home": setHome(m.on !== false); break;
      case "avatar.focus": focusState(m); break;
      case "avatar.hide": if (!camBusy()) startPeek().then((ok) => { if (!ok) say(line("no_hiding_place"), 2500); }); break;
    }
  };
}

// ---------- мышь ----------
let hit = false, drag = null, lastClick = 0;
const px = new Uint8Array(4);
const raycaster = new THREE.Raycaster();
const pat = { lastX: 0, dir: 0, flips: [], start: 0, longSent: false, lastMove: 0 };

function hitTest(x, y) {
  const gl = renderer.getContext();
  const dpr = renderer.getPixelRatio();
  gl.readPixels(Math.round(x * dpr), Math.round((H - y) * dpr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return px[3] > 20;
}
function toScreen(v) { const p = v.clone().project(camera); return { x: (p.x + 1) / 2 * W, y: (1 - p.y) / 2 * H }; }
function zoneAt(x, y) {
  raycaster.setFromCamera(new THREE.Vector2(x / W * 2 - 1, 1 - y / H * 2), camera);
  const head = new THREE.Vector3();
  vrm.humanoid.getRawBoneNode("head").getWorldPosition(head);
  const c = toScreen(head.clone().add(new THREE.Vector3(0, modelH * 0.06, 0)));
  const r = Math.abs(toScreen(head.clone().add(new THREE.Vector3(0, modelH * 0.13, 0))).y - toScreen(head).y);
  if (Math.hypot(x - c.x, y - c.y) < r * 1.25) return "head";
  return "body";
}
function setHit(h) {
  if (h === hit) return;
  hit = h;
  K.avatarHit?.(h || menuEl.classList.contains("show"));
  document.body.style.cursor = h ? "grab" : "default";
}

addEventListener("mousemove", (e) => {
  if (home.dragIt) { home.room.dragTo(home.dragIt, ndcOf(e), camera); return; }
  if (drag) {
    const dx = e.screenX - drag.x, dy = e.screenY - drag.y;
    K.moveAvatar?.(dx, dy);
    drag.moved += Math.abs(dx) + Math.abs(dy);
    drag.x = e.screenX; drag.y = e.screenY;
    if (drag.moved > 12 && !st.dragging) {
      st.dragging = true; setEmotion("surprised", 2, false);
      send({ type: "avatar.event", kind: "pickup" });
    }
    return;
  }
  if (menuEl.classList.contains("show")) return;
  const h = vrm && hitTest(e.clientX, e.clientY);
  setHit(h);
  if (!h) return;
  activity();
  // поглаживание: курсор над головой ходит туда-сюда
  if (zoneAt(e.clientX, e.clientY) !== "head" && zoneAt(e.clientX, e.clientY) !== "ears") return;
  const now = performance.now(), dx = e.clientX - pat.lastX;
  pat.lastX = e.clientX;
  if (Math.abs(dx) < 2) return;
  const dir = Math.sign(dx);
  if (dir !== pat.dir) { pat.flips.push(now); pat.dir = dir; }
  pat.flips = pat.flips.filter((f) => now - f < 1500);
  pat.lastMove = now;
  if (!st.patting && pat.flips.length >= 4) {
    st.patting = true; pat.start = now; pat.longSent = false;
    send({ type: "avatar.event", kind: "pat" });
  }
  if (st.patting && !pat.longSent && now - pat.start > 6000) { pat.longSent = true; send({ type: "avatar.event", kind: "pat_long" }); }
});
setInterval(() => { if (st.patting && performance.now() - pat.lastMove > 800) { st.patting = false; setEmotion("shy", 3, false); } }, 200);
addEventListener("mouseleave", () => { if (!drag && !menuEl.classList.contains("show")) setHit(false); });
addEventListener("mousedown", (e) => {
  if (e.button !== 0 || menuEl.contains(e.target)) return;
  hideMenu();
  if (!hit) return;
  if (game.kind === "reflex" && !game.done) {
    game.done = true; game.kind = null;
    if (!game.armedAt) { say("Рано!", 2500); deskResult("reflex", -1); }
    else { const ms = Math.round(performance.now() - game.armedAt); say(`${ms} мс!`, 4000); deskResult("reflex", ms); }
    return;
  }
  if (game.kind === "seek" && phys.mode === "peek") { seekFound(); return; }
  if (game.kind === "hide") { game.caught = true; say(line("caught"), 900); return; }
  if (home.on && roomPointerDown(e)) return;
  drag = { x: e.screenX, y: e.screenY, moved: 0, zone: zoneAt(e.clientX, e.clientY) };
  // схватили мышью — слезает с окна / отпускает край / выходит из-за окна
  if (phys.mode === "peek") endPeek(true);
  if (st.perched) { st.perched = false; frame(); }
  phys.mode = "ground"; phys.vy = 0;
});
addEventListener("mouseup", (e) => {
  if (home.dragIt) { roomDrop(); return; }
  if (!drag) return;
  const d = drag;
  drag = null;
  if (st.dragging) {
    st.dragging = false;
    send({ type: "avatar.event", kind: "drop" });
    release();
    return;
  }
  if (d.moved > 6) return;
  const now = performance.now();
  if (now - lastClick < 350) { K.openMain?.(); lastClick = 0; return; }
  lastClick = now;
  setTimeout(() => { if (lastClick === now) poke(d.zone); }, 360);
});
function poke(zone) {
  activity();
  if (game.kind === "pokes") { game.count++; say(`${game.count}!`, 0); st.flinchV -= 2; return; }
  st.flinchV -= zone === "head" ? 5 : 3;
  setEmotion(zone === "body" ? "angry" : "shy", 3);
  send({ type: "avatar.event", kind: `poke_${zone}` });
}

// ---------- меню по правому клику ----------
addEventListener("contextmenu", async (e) => {
  e.preventDefault();
  if (!hit) return;
  const models = await fetch("/api/models").then((r) => r.json()).catch(() => []);
  const items = [
    ...(cfg.features?.room || home.on ? [[home.on ? "На рабочий стол" : "Домой (комната)", () => setHome(!home.on)]] : []),
    ...(home.on ? [] : [[st.perched ? "Слезть с окна" : "Сесть на окно", () => setPerch(!st.perched)],
      ...(phys.mode === "peek" ? [] : [["Спрятаться за окно", () => startPeek().then((ok) => { if (!ok) say("Тут негде спрятаться…", 2500); })]])]),
    ["food"],
    [st.trickUntil ? "🍬 Дать конфету!" : "Кинуть мячик", () => (st.trickUntil ? feed("🍬") : throwThing("ball"))],
    ["Кинуть подушку", () => throwThing("pillow")],
    ...(home.on || game.kind ? [] : [["Прятки", () => seekGame()]]),
    ...(halloween() && !st.trickUntil ? [["🎃 Сладость или гадость?", () => sendEv("trick_ask")]] : []),
    ["—"],
    ["Помахать", () => gesture("wave", 2.5)],
    // праздничная шляпа: на моделях с ушами или пышной причёской сидит по-разному — пусть её можно снять
    ...(holiday() ? [[cfg.fun?.hat === false ? "🎩 Надеть праздничную шляпу" : "🎩 Снять шляпу", () => fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fun: { hat: cfg.fun?.hat === false } }) })]] : []),
    [cfg.behavior?.mute_until * 1000 > Date.now() ? "Можно говорить" : "Помолчи час", () => fetch("/api/chatter/mute", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ minutes: cfg.behavior?.mute_until * 1000 > Date.now() ? 0 : 60 }) })
      .then(() => say(line(cfg.behavior?.mute_until * 1000 > Date.now() ? "mute_off" : "mute_on"), 2500))],
    ["Поклониться", () => gesture("bow", 1.8)],
    [st.sleeping ? "Разбудить" : "Уложить спать", () => { if (st.sleeping) activity(); else if (home.on && home.room.spot("bed")) roomWalkTo({ name: "bed", sleep: true, ...home.room.spot("bed") });
      else st.lastActivity = performance.now() - SLEEP_AFTER - 1; }],
    ...(models.length > 1 ? [["—"], ...models.map((m) => [`${m.name === modelKey ? "✓ " : "  "}${m.title || m.name}`,
      () => fetch("/api/avatar/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: m.name }) })])] : []),
    ["—"],
    ["Открыть {name}", () => K.openMain?.()],
    ["Спрятать", () => fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ avatar: { enabled: false } }) })],
  ];
  menuEl.innerHTML = items.map(([t], i) => (t === "—" ? "<hr>" : t === "food"
    ? `<div class="food" title="Угостить">${Object.keys(FOOD_KIND).map((f) => `<span data-food="${f}">${f}</span>`).join("")}</div>`
    : `<div data-i="${i}">${t}</div>`)).join("");
  menuEl.style.left = Math.min(e.clientX, W - 190) + "px";
  menuEl.style.top = Math.min(e.clientY, H - menuEl.childElementCount * 30 - 10) + "px";
  menuEl.classList.add("show");
  K.avatarHit?.(true);
  menuEl.onclick = (ev) => {
    const food = ev.target.dataset.food;
    if (food) { hideMenu(); feed(food); return; }
    const i = ev.target.dataset.i;
    if (i === undefined) return;
    hideMenu();
    items[i][1]();
  };
});
function hideMenu() { if (menuEl.classList.contains("show")) { menuEl.classList.remove("show"); K.avatarHit?.(hit); } }

// ---------- физика на рабочем столе: земля, падение, окна ----------
// Земля — верхний край панели задач (рабочая область экрана): ноги стоят ровно на нём и не уходят под панель.
// Отпустили — падает на ближайший край окна под ногами или на панель задач. Окна приходят из ядра сверху вниз
// по z-порядку: край, закрытый другим окном, не считается. На окне сидит и следит за ним (переехало, растянули —
// едет следом; свернули или развернули на весь экран — падает). У верхнего края окна — хватается и висит.
// За окном — прячется и выглядывает сбоку (окно аватара встаёт в z-порядке под это окно).
const phys = { mode: "ground", vy: 0, x: 0, y: 0, hwnd: 0, rel: 0.5, wins: [], winsAt: 0, winsBusy: false,
  nextPoll: 0, until: 0, side: 1, peekWin: null };
const G = 2600, VMAX = 2400;
const _wp = new THREE.Vector3();
function scrPx(v) { camera.updateMatrixWorld(); return toScreen(v); }
const groundPx = () => scrPx(_wp.set(vrm.scene.position.x, 0, vrm.scene.position.z)).y;   // где в окне подошвы
function hipsPx() { B.hips.getWorldPosition(_wp); return scrPx(_wp).y; }
function handsPx() {
  const a = new THREE.Vector3(), b = new THREE.Vector3();
  B.leftHand?.getWorldPosition(a); B.rightHand?.getWorldPosition(b);
  return scrPx(a.add(b).multiplyScalar(0.5)).y;
}
const groundLine = () => (st.work ? st.work.y + st.work.height : screen.availHeight);
function refreshWins(force = false) {
  if (!K.deskWindows || phys.winsBusy || (!force && performance.now() - phys.winsAt < 350)) return phys.winsP;
  phys.winsBusy = true;
  return (phys.winsP = K.deskWindows().then((w) => { phys.wins = w || []; phys.winsAt = performance.now(); phys.winsBusy = false; return phys.wins; })
    .catch(() => { phys.winsBusy = false; return phys.wins; }));
}
// край окна виден в точке x (его не закрывает окно выше по z-порядку)
function edgeVisible(i, x, y) {
  for (let j = 0; j < i; j++) {
    const o = phys.wins[j];
    if (x >= o.x && x <= o.x + o.w && y >= o.y && y <= o.y + o.h) return false;
  }
  return true;
}
// ближайшая опора под ногами между y0 и y1 (край окна или панель задач)
function surfaceBetween(cx, y0, y1) {
  let best = null;
  phys.wins.forEach((w, i) => {
    if (w.maximized || w.y <= y0 || w.y > y1 || cx < w.x + 25 || cx > w.x + w.w - 25) return;
    if (!edgeVisible(i, cx, w.y - 3)) return;
    if (!best || w.y < best.y) best = { y: w.y, win: w };
  });
  const g = groundLine();
  if (!best && y1 >= g) best = { y: g, win: null };
  return best;
}
function centerX() { return st.win.x + W / 2; }
function setY(y) { phys.y = y; st.win.y = Math.round(y); K.placeAvatar?.(st.win.x, Math.round(y)); }

function startFall() {
  if (home.on) return;
  if (st.perched) { st.perched = false; frame(); }
  phys.mode = "fall"; phys.vy = 0; phys.y = st.win.y; phys.hwnd = 0;
  refreshWins(true);
}
// отпустили мышью: схватиться за край окна рядом с руками или падать
async function release() {
  if (home.on) return;
  // по горизонтали — не дальше края экрана (хотя бы полтела видно)
  const w = st.work;
  if (w) {
    const cx = clampN(centerX(), w.x + 60, w.x + w.width - 60);
    if (cx !== centerX()) { st.win.x = Math.round(cx - W / 2); K.placeAvatar?.(st.win.x, st.win.y); }
  }
  const wins = (await refreshWins(true)) || [];
  const cx = centerX(), hands = st.win.y + handsPx();
  const i = wins.findIndex((o, k) => !o.maximized && o.y > hands - 40 && o.y < hands + 110 && cx > o.x + 30 && cx < o.x + o.w - 30 && edgeVisible(k, cx, o.y - 3));
  if (i >= 0) return startHang(wins[i]);
  if (st.win.y + groundPx() > groundLine() + 2) { setY(groundLine() - groundPx()); landed(null); return; }   // под панелью — наверх
  startFall();
}
function landed(win) {
  phys.mode = "ground"; phys.vy = 0;
  if (win && win.w > 260) return perchOn(win, true);
  gesture("land", 0.45);
}

function startHang(win) {
  if (st.perched) { st.perched = false; }
  phys.mode = "hang"; phys.hwnd = win.hwnd; phys.rel = clampN((centerX() - win.x) / win.w, 0.08, 0.92);
  phys.until = clock.elapsedTime + rnd(5, 10); phys.nextPoll = 0;
  frame();
  setEmotion("surprised", 3, false);
  say(line("hang"), 2500);
  placeOn(win);
}
async function perchOn(win, fromFall = false) {
  phys.mode = "perch"; phys.hwnd = win.hwnd; phys.nextPoll = 0;
  phys.rel = clampN((centerX() - win.x) / win.w, 0.12, 0.88);
  st.perched = true;
  frame();
  placeOn(win);
  if (fromFall) { setEmotion("happy", 2, false); }
}
// поставить на окно: сидя — таз на верхнем крае, вися — руки чуть ниже края
function placeOn(win) {
  const x = Math.round(win.x + phys.rel * win.w - W / 2);
  const y = phys.mode === "hang" ? win.y + 6 - handsPx() : win.y + 6 - hipsPx();
  st.win.x = x;
  setY(y);
}
async function followWindow(t) {
  if (t < phys.nextPoll || !K.deskWindow || phys.polling) return;
  phys.nextPoll = t + 0.1;
  phys.polling = true;
  // ядро может задуматься (загрузка моделей) — ждём ответа не дольше 2 с, иначе считаем, что окна нет
  const w = await Promise.race([K.deskWindow(phys.hwnd), new Promise((r) => setTimeout(() => r(null), 2000))]).catch(() => null);
  phys.polling = false;
  if (phys.mode !== "perch" && phys.mode !== "hang") return;
  const gone = !w || w.gone || !w.visible || w.minimized || w.maximized || w.x < -10000 || w.y < -10000;
  const fall = (covered) => {                   // окно свернули, закрыли — или его заслонило другое окно
    say(line(covered ? "window_covered" : "window_gone"), 2200);
    setEmotion("surprised", 2, false);
    startFall();
  };
  if (gone) return fall(false);
  rideSample(w);
  placeOn(w);
  // заслонило ли место другим окном — в фоне: перебор всех окон идёт до секунды, и раньше слежение за окном на это
  // время замирало (окно тащат — она стоит на месте, потом прыгает)
  if (!phys.coverBusy) {
    phys.coverBusy = true;
    seatCovered(t).then((c) => { phys.coverBusy = false; if (c && (phys.mode === "perch" || phys.mode === "hang") && phys.hwnd === w.hwnd) fall(true); },
      () => { phys.coverBusy = false; });
  }
}
// ---------- едет на окне ----------
// Окно, на котором она сидит, тащат: корпус по инерции отклоняется против движения и раскачивается (пружина
// с недодемпфированием — покачивание после остановки), руки хватаются за край окна, при рывках — «не так резко»,
// при тряске (частая смена направления) — сердится. Скорость окна — по опросу followWindow (раз в 0,1 с).
const ride = { vx: 0, vy: 0, last: null, lean: { x: 0, v: 0 }, pitch: { x: 0, v: 0 }, grip: 0, flips: [], dir: 0, fast: 0, moved: 0, nextSay: 0 };
function rideSample(w) {
  const now = performance.now() / 1000;
  if (ride.last && ride.last.hwnd === w.hwnd) {
    const dtw = Math.max(0.05, now - ride.last.t);
    const vx = (w.x - ride.last.x) / dtw, vy = (w.y - ride.last.y) / dtw;
    ride.vx = ride.vx * 0.4 + vx * 0.6;
    ride.vy = ride.vy * 0.4 + vy * 0.6;
    const d = Math.abs(vx) > 250 ? Math.sign(vx) : 0;      // смена направления на скорости — признак тряски
    if (d && ride.dir && d !== ride.dir) ride.flips.push(now);
    if (d) ride.dir = d;
  } else { ride.vx = ride.vy = 0; ride.dir = 0; }
  ride.last = { hwnd: w.hwnd, x: w.x, y: w.y, t: now };
}
function wobble(s, target, w, z, dt) {
  s.v += (w * w * (target - s.x) - 2 * z * w * s.v) * dt;
  s.x += s.v * dt;
  return s.x;
}
function rideTick(dt, t, v0) {
  const on = phys.mode === "perch" && st.perched;
  if (!on) { ride.vx = damp(ride.vx, 0, 6, dt); ride.vy = damp(ride.vy, 0, 6, dt); ride.last = null; }
  else if (ride.last && performance.now() / 1000 - ride.last.t > 0.35) { ride.vx = damp(ride.vx, 0, 8, dt); ride.vy = damp(ride.vy, 0, 8, dt); }
  const sp = Math.hypot(ride.vx, ride.vy);
  const lean = wobble(ride.lean, on ? clampN(-ride.vx / 1400, -1, 1) : 0, 7, 0.22, dt);
  const pitch = wobble(ride.pitch, on ? clampN(ride.vy / 1400, -1, 1) : 0, 7, 0.25, dt);
  ride.grip = damp(ride.grip, on && sp > 280 ? 1 : 0, sp > 280 ? 9 : 2.5, dt);
  if (Math.abs(lean) + Math.abs(pitch) > 0.002) {
    addRot(B.spine, pitch * 0.18 * v0, 0, lean * 0.22 * v0);
    addRot(B.chest || B.spine, pitch * 0.1 * v0, 0, lean * 0.14 * v0);
    addRot(B.neck, 0, 0, -lean * 0.12 * v0);                // голова удерживает горизонт
  }
  if (ride.grip > 0.01) {                                    // руки — на край окна по бокам от бёдер
    for (const sd of ["left", "right"]) {
      const m = sd === "left" ? 1 : -1;
      B[sd + "UpperArm"]?.quaternion.slerp(qt.setFromEuler(e3.set(0.35 * v0, 0, -m * 1.2 * v0)), ride.grip);
      B[sd + "LowerArm"]?.quaternion.slerp(qt.setFromEuler(e3.set(0, m * 0.35, 0)), ride.grip);
    }
  }
  if (!on) return;
  const now = performance.now() / 1000;
  ride.flips = ride.flips.filter((x) => now - x < 1.2);
  ride.fast = sp > 1600 ? ride.fast + dt : 0;
  ride.moved = sp > 200 ? ride.moved + dt : sp < 40 ? (ride.moved > 1.5 ? -1 : 0) : ride.moved;   // -1 — ехали дольше 1,5 с и встали
  if (t < ride.nextSay) return;
  if (ride.flips.length >= 3) {
    ride.flips = []; ride.nextSay = t + 7;
    setEmotion("angry", 2.5, false);
    say(line("ride_shake"), 2400);
  } else if (ride.fast > 0.25) {
    ride.fast = 0; ride.nextSay = t + 6;
    setEmotion("surprised", 2, false);
    say(line("ride_fast"), 2200);
  } else if (ride.moved < 0) {                               // долго ехали и встали
    ride.moved = 0;
    if (Math.random() < 0.5) { ride.nextSay = t + 8; say(line("ride_stop"), 2200); }
  }
}
// Место, где она сидит, закрыто окном выше по z-порядку? Она всегда поверх всех, и без этой проверки
// оставалась «сидеть в воздухе» над окном, которое вывели вперёд.
async function seatCovered(t) {
  if (t < (phys.coverCheck || 0)) return false;
  phys.coverCheck = t + 0.5;
  const wins = (await refreshWins()) || [];
  const i = wins.findIndex((o) => o.hwnd === phys.hwnd);
  if (i < 0) return false;                        // в списке нет (например, окно-инструмент) — не спорим
  const o = wins[i], x = clampN(centerX(), o.x + 2, o.x + o.w - 2);
  return !edgeVisible(i, x, o.y + 2);
}

// ---------- прятки за окном ----------
// Подходит к краю окна, встаёт за ним (окно аватара — под этим окном) и выглядывает в сторону хозяина.
async function startPeek(opt = {}) {
  if (home.on || st.camMode || !K.behind || phys.mode !== "ground" || !st.work) return false;
  const wins = (await refreshWins(true)) || [];
  const feet = groundLine(), bodyPx = feet - (st.win.y + hipsPx());
  const torso = [feet - bodyPx * 1.7, feet - bodyPx * 0.6];          // от груди до бёдер — должно быть за окном
  const cx0 = centerX(), cand = [];
  wins.forEach((o, i) => {
    if (o.maximized || o.y > torso[0] || o.y + o.h < torso[1]) return;
    for (const [edge, side] of [[o.x, -1], [o.x + o.w, 1]]) {
      if (edge < st.work.x + W * 0.4 || edge > st.work.x + st.work.width - W * 0.4) continue;
      if (!edgeVisible(i, edge - side * 20, (torso[0] + torso[1]) / 2)) continue;
      cand.push({ win: o, edge, side, d: Math.abs(edge - cx0) });
    }
  });
  if (!cand.length) return false;
  cand.sort((a, b) => a.d - b.d);
  const c = opt.random ? pick(cand) : cand[0], bodyW = Math.max(60, bodyPx * 0.55);
  const targetX = Math.round(c.edge - c.side * bodyW * 0.45 - W / 2);      // большая часть тела — за окном
  phys.peekWin = c.win; phys.side = c.side;
  const hide = async () => {
    phys.mode = "peek"; phys.until = clock.elapsedTime + rnd(9, 18); phys.nextPoll = 0;
    await K.behind(c.win.hwnd);
    setEmotion("shy", 3, false);
  };
  if (!opt.instant && Math.abs(targetX - st.win.x) < 700 && clips.walk?.length && startWalk(targetX)) life.onArrive = hide;
  else if (opt.instant) { st.win.x = targetX; K.placeAvatar?.(st.win.x, st.win.y); await hide(); }
  else { poof(); st.win.x = targetX; K.placeAvatar?.(st.win.x, st.win.y); setTimeout(hide, 200); }
  return true;
}
async function endPeek(found = false, quiet = false) {
  if (phys.mode !== "peek") return;
  phys.mode = "ground";
  await K.behind?.(0);
  if (found && !quiet) { setEmotion("surprised", 2, false); say(line("peek_found"), 2500); }
  if (clips.walk?.length) startWalk(st.win.x - phys.side * rnd(160, 260));
}
async function peekTick(t) {
  const seek = game.kind === "seek";               // в игре «прятки» итог считает плагин, а не просто «нашли»
  if (!phys.peekWin || t > phys.until) return seek ? seekLost() : endPeek(false);
  // хозяин навёл мышь на видимую часть — нашёл
  if (hit && performance.now() - (st.cursorMovedAt || 0) < 300) return seek ? seekFound() : endPeek(true);
  if (t < phys.nextPoll) return;
  phys.nextPoll = t + 0.5;
  const w = await K.deskWindow(phys.peekWin.hwnd);
  if (!w || w.gone || w.minimized || !w.visible || Math.abs(w.x - phys.peekWin.x) > 40 || Math.abs(w.w - phys.peekWin.w) > 40) {
    return seek ? seekFound() : endPeek(false);     // окно убрали — укрытия нет, значит нашли
  }
}

function physTick(dt, t) {
  if (home.on || !vrm || st.dragging || drag) return;
  if (phys.mode === "fall") {
    phys.vy = Math.min(VMAX, phys.vy + G * dt);
    const g0 = phys.y + groundPx(), dy = phys.vy * dt;
    if (performance.now() - phys.winsAt > 350) refreshWins();
    const s = surfaceBetween(centerX(), g0, g0 + dy);
    if (s) { setY(s.y - groundPx()); return landed(s.win); }
    setY(phys.y + dy);
    return;
  }
  if (phys.mode === "perch" || phys.mode === "hang") {
    followWindow(t);
    if (phys.mode === "hang" && t > phys.until) {
      if (Math.random() < 0.55) {                         // подтянулась и села на край
        say(line("climb"), 2500);
        K.deskWindow?.(phys.hwnd).then((w) => { if (w && !w.gone && phys.mode === "hang") perchOn(w); });
      } else {
        say(line("slip"), 2200);
        startFall();
      }
      phys.until = t + 99;
    }
    return;
  }
  if (phys.mode === "peek") return peekTick(t);
  // на земле: ноги — на панели задач (после смены экрана, при запуске, если окно сдвинули)
  if (life.mode === "idle" && st.work && t > (phys.nextPoll || 0)) {
    phys.nextPoll = t + 0.5;
    const feet = st.win.y + groundPx(), g = groundLine();
    if (feet < g - 6) startFall();
    else if (feet > g + 3) setY(g - groundPx());
  }
}

// ---------- сесть на окно (меню, голос) ----------
async function setPerch(on) {
  if (camBusy()) return;
  if (!on) return startFall();
  const wins = (await refreshWins(true)) || [];
  const cx = centerX();
  const onScreen = (o) => !o.maximized && o.w > 300 && st.work && o.x < st.work.x + st.work.width && o.x + o.w > st.work.x;
  // окно прямо под ней, иначе самое верхнее на этом экране
  const win = wins.find((o, i) => onScreen(o) && cx > o.x + 30 && cx < o.x + o.w - 30 && edgeVisible(i, cx, o.y - 3)) ||
    wins.find(onScreen);
  if (!win) { say("Не на чем сидеть — все окна развёрнуты.", 3000); return; }
  poof();
  perchOn(win);
}

K.onCursor?.((d) => {
  if (!st.sleeping && Math.abs(d.x - st.cursor.x) + Math.abs(d.y - st.cursor.y) > 2 &&
      d.x > d.win.x - 150 && d.x < d.win.x + d.win.width + 150 && d.y > d.win.y - 150 && d.y < d.win.y + d.win.height + 150) activity();
  if (Math.abs(d.x - st.cursor.x) + Math.abs(d.y - st.cursor.y) > 2) st.cursorMovedAt = performance.now();
  st.cursor = d; st.win = d.win; st.work = d.work;
});
addEventListener("pointermove", (e) => { if (!K.onCursor) st.cursor = { x: e.screenX, y: e.screenY }; });

// танца «под любую музыку» нет: она пускалась в пляс под каждое видео на YouTube. Танец — только по просьбе.

// ---------- смотрит видео вместе с вами ----------
// Играет видео в окне YouTube/Twitch/…: окно не развёрнуто — садится на его верхний край и смотрит вниз, в экран;
// развёрнуто — встаёт внизу экрана вполоборота спиной к вам и смотрит вверх, как перед телевизором. Пауза —
// оборачивается к вам; видео кончилось (минуту не играет) — потягивается и уходит. Танцев под видео нет.
const VIDEO_RE = /youtube|twitch|vk видео|vk video|кинопоиск|netflix|rutube|crunchyroll|vimeo|anime|аниме/i;
const media = () => fetch("/api/media/now").then((r) => r.json()).catch(() => null);
async function watchTick(t) {
  if (cfg.avatar?.watch === false || !vrm || st.camMode || t < (st.nextWatch ?? 45) || st.watchBusy) return;
  st.nextWatch = t + 15;
  st.watchBusy = true;
  try {
    const w = st.watching;
    if (w) {
      const m = await media();
      const playing = !!m?.playing;
      const gone = w.mode === "perch" ? (!st.perched || phys.hwnd !== w.hwnd) : (phys.mode !== "ground" || st.dragging);
      if (playing) w.pausedAt = 0; else w.pausedAt ||= t;
      st.watchPaused = !playing;
      if (gone || (w.pausedAt && t - w.pausedAt > 60) || t - w.since > 20 * 60) stopWatching(!gone);
      return;
    }
    const asked = st.watchAsked; st.watchAsked = false;
    if (st.perched || phys.mode !== "ground" || st.pastime || ptBusy() || st.sleeping || (!asked && Math.random() > 0.4)) return;
    const m = await media();
    if (!m?.playing) return;
    const wins = (await refreshWins(true)) || [];
    const v = wins.find((o) => VIDEO_RE.test(o.title || "") && o.w > 480 && o.h > 300);
    if (!v) return;
    if (!v.maximized && st.work && v.y > st.work.y + 40) {
      poof(); perchOn(v);
      st.watching = { mode: "perch", hwnd: v.hwnd, since: t, pausedAt: 0 };
    } else {
      st.watching = { mode: "tv", since: t, pausedAt: 0 };
    }
    setEmotion("happy", 2, false);
  } finally { st.watchBusy = false; }
}
function stopWatching(stretch = true) {
  st.watching = null; st.watchPaused = false; st.watchYaw = 0;
  if (stretch) gesture("stretch", 1.8);
}
function watchLook() {
  const w = st.watching;
  if (!w || st.watchPaused) return null;                    // пауза — смотрит на вас (обычный взгляд за курсором)
  return w.mode === "perch" ? vec(0, -modelH * 0.4, 0.9)     // с края окна — вниз, в видео
    : vec(0, camera.position.y + modelH * 0.9, -1.5);        // перед «телевизором» — вверх, в экран за собой
}

// ---------- показ всех движений по очереди (кнопка в «Гардеробе», /api/avatar/demo) ----------
let demoRunning = false;
async function demo() {
  if (demoRunning || !vrm) return;
  demoRunning = true;
  activity();
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const clipLen = (kind) => Math.max(...(clips[kind] || []).map((c) => c.duration), 2);
  const steps = [
    ["Помахать", () => gesture("wave", 3), () => clipLen("wave")],
    ["Поклон", () => gesture("bow", 3), () => clipLen("bow")],
    ["«Прошу сюда»", () => gesture("guide", 3), () => clipLen("guide")],
    ["Кивок", () => gesture("nod", 2), () => clipLen("nod")],
    ["Смущение", () => { setEmotion("shy", 4, false); gesture("shy", 3); }, () => clipLen("shy")],
    ["Сердится: «кыш-кыш»", () => { setEmotion("angry", 4, false); gesture("angry", 3); }, () => clipLen("angry")],
    ["Грусть", () => { setEmotion("sad", 4, false); gesture("sad", 3); }, () => clipLen("sad")],
    ["Радость", () => { setEmotion("happy", 4, false); gesture("happy", 3); }, () => clipLen("happy")],
    ["Приветствие двумя руками", () => gesture("greet", 3), () => clipLen("greet")],
    ["Подзывает", () => gesture("call", 3), () => clipLen("call")],
    ["Гладят по голове", () => { st.patting = true; pat.lastMove = performance.now() + 4000; }, () => 4],
    ["Голова кружится", () => { st.patting = false; dz.acc = 100; dz.cool = 0; }, () => 4],
    ["Чих", () => sneeze("leaf"), () => 4],
    ["Прогулка", () => startWalk(st.win.x + (st.win.x > (st.work?.x || 0) + (st.work?.width || 1920) / 2 ? -350 : 350)), () => 3.5],
    ["Обратно", () => startWalk(st.win.x + (life.dir > 0 ? -350 : 350)), () => 3.5],
    ["Танец (8 секунд)", () => gesture("dance", 8), () => 8],
    ["Переодевание: «пуф!»", () => { endOneShot(); poof(); }, () => 3],
  ];
  for (const [title, run, len] of steps) {
    st.lastClipAt = -99;
    showState(title);
    run();
    await wait(Math.min(12, len() + 0.8) * 1000);
    if (st.oneShot && !/dance/.test(st.oneShot.getClip().name)) endOneShot();
    await wait(700);
  }
  if (st.oneShot) endOneShot();
  setEmotion("happy", 3, false);
  showState("");
  say("Это всё, что я умею. Пока что.", 4000);
  demoRunning = false;
}

// ---------- фокус (плагин «Фокус», помодоро): таймер у её ног, во время работы — тише ---------
const focus = { phase: null, until: 0, timer: 0 };
function confetti() {
  const p = headPos(0.15);
  for (let i = 0; i < 46; i++) {
    const a = Math.random() * Math.PI * 2, sp = rnd(0.4, 1.0);
    particle({ tex: pick(["star", "petal", "heart", "spark"]), pos: p.clone(), vel: vec(Math.cos(a) * sp * 0.6, rnd(0.6, 1.3), Math.sin(a) * sp * 0.3),
      gravity: 1.4, drag: 1.2, spin: rnd(-4, 4), life: rnd(1.4, 2.4), size: rnd(0.03, 0.06), fade: 0.4 });
  }
}
function focusState(m) {
  if (focus.phase === "work" && m.phase === "rest") { confetti(); setEmotion("happy", 5, false); }
  focus.phase = m.phase; focus.until = Date.now() + (m.left || 0) * 1000;
  clearInterval(focus.timer);
  if (!m.phase) { showState(""); return; }
  const tick = () => {
    const left = Math.max(0, Math.round((focus.until - Date.now()) / 1000));
    if (!st.thinking && !st.sleeping) showState(`${focus.phase === "work" ? "🍅 фокус" : "☕ перерыв"} ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`);
  };
  tick();
  focus.timer = setInterval(tick, 1000);
}

// ---------- комната: режим «Домой» ----------
// Окно шире, камера сверху-спереди на диораму 4×4 м (мебель Kenney, CC0). {name} разувается, ходит между
// местами (стол, кровать, полка, растение, окно), садится, рассматривает; новые вещи замечает и идёт смотреть.
// Мебель тянется мышью и сохраняется в настройках (avatar.room_layout); каталог — в «Гардеробе».
const ROOM_W = 900, ROOM_H = 760;
const SPOTS = ["chair", "bed", "shelf", "plant", "window", "desk"];
const SPOT_LINES = {
  chair: ["Посижу немного.", "Так, что у нас в ноутбуке…", "Пишу список дел. Вам. Не мне.", "Кресло тёплое. Вы сидели?"],
  bed: ["Мягко…", "Только на минутку присяду.", "Подушку бы взбить.", "Не смотрите так. Я просто отдыхаю."],
  shelf: ["Надо бы книги разобрать.", "Эту я ещё не дочитала.", "Кто ставит книги вверх ногами? А, это я.", "Пыль. Опять пыль."],
  plant: ["Растёт! Хорошая девочка.", "Полить бы тебя.", "Ещё один листик. Я считаю.", "Не вянь. Я запрещаю."],
  window: ["Что там за окном…", "Красиво сегодня.", "Хм, птица. Нет, пакет.", "Отсюда видно весь ваш рабочий стол."],
  desk: ["Беспорядок. Опять.", "Кто тут крошки оставил?", "Ваш ноутбук скучает.", "Протру стол. Не мешайте."],
};
// по времени суток — чтобы реплики не повторялись по кругу
const TIME_LINES = {
  morning: ["Утро… Чай будет?", "Окно открыть? Свежо."],
  evening: ["Вечер. Лампу включу.", "Скоро ужин. Вы помните про ужин?"],
  night: ["Тихо как… Все спят, кроме нас.", "Звёзды видно. Почти."],
};
let lastLine = "";
function roomLine(spot) {
  const h = new Date().getHours(), tod = h >= 6 && h < 11 ? "morning" : h >= 18 && h < 23 ? "evening" : h >= 23 || h < 6 ? "night" : null;
  const pool = [...(SPOT_LINES[spot] || []), ...(tod && Math.random() < 0.3 ? TIME_LINES[tod] : [])].filter((l) => l !== lastLine);
  return (lastLine = pick(pool.length ? pool : ["…"]));
}
// куда в комнате показывает курсор (точка на высоте ~0,7 м) — она провожает его взглядом, пока мышь двигается
const roomRay = new THREE.Raycaster(), lookPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -0.7), lookHit = new THREE.Vector3();
function roomCursorLook() {
  if (performance.now() - (st.cursorMovedAt || 0) > 4000) return null;
  const x = st.cursor.x - st.win.x, y = st.cursor.y - st.win.y;
  if (x < 0 || y < 0 || x > W || y > H) return null;
  roomRay.setFromCamera(new THREE.Vector2(x / W * 2 - 1, 1 - y / H * 2), camera);
  if (!roomRay.ray.intersectPlane(lookPlane, lookHit) || Math.abs(lookHit.x) > 2.5 || Math.abs(lookHit.z) > 2.5) return null;
  return lookHit;
}
const ndcOf = (e) => new THREE.Vector2(e.clientX / W * 2 - 1, 1 - e.clientY / H * 2);
function roomLayout() { return cfg.avatar?.room_layout?.length ? cfg.avatar.room_layout : DEFAULT_LAYOUT; }
function roomCamera() {
  camera.fov = 30;
  // диорама целиком: дальше при узком окне
  // колёсиком — ближе к {name}: точка взгляда плавно переезжает с центра комнаты на неё
  const z = home.zoom ?? 0, P = vrm?.scene.position ?? new THREE.Vector3();
  const dist = (9 - z * 5.5) / Math.min(1, camera.aspect / 1.15);
  const at = new THREE.Vector3(0, 0.75, -0.1).lerp(new THREE.Vector3(P.x, 0.75, P.z), z);
  const dir = new THREE.Vector3(0.62, 0.52, 0.86).normalize();
  camera.position.copy(at).addScaledVector(dir, dist);
  camera.lookAt(at);
  camera.updateProjectionMatrix();
}
async function syncRoom() {
  const layout = roomLayout(), key = JSON.stringify(layout);
  if (key !== home.layoutKey) { home.layoutKey = key; await home.room.build(layout); }
  // новое, чего она ещё не видела, — пойдёт смотреть
  const ids = home.room.layout.map((d) => d.id);
  if (home.seen == null) home.seen = cfg.avatar?.room_seen ?? (cfg.avatar?.room_layout?.length ? DEFAULT_LAYOUT.map((d) => d.id) : ids);
  for (const id of ids) if (!home.seen.includes(id) && !home.wow.includes(id)) home.wow.push(id);
}
function saveRoom(extra = {}) {
  fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ avatar: { room_layout: home.room.layout, room_seen: home.seen, ...extra } }) }).catch(() => {});
}
async function setHome(on) {
  if (on && !cfg.features?.room) return;         // комната — бета, в релизе выключена
  if (!vrm || on === home.on) return;
  activity();
  if (life.mode !== "idle") { stopWalk(); life.mode = "idle"; life.yaw = 0; }
  if (st.perched) await setPerch(false);
  if (on) {
    home.room ??= createRoom(scene);
    poof();
    home.saved = { w: W, h: H };
    K.resizeAvatar?.(ROOM_W, ROOM_H);
    home.on = true;
    document.body.classList.add("room");                 // у комнаты свои стены — края окна не растворяем
    home.layoutKey = "";
    home.seen = null;
    await syncRoom();
    home.room.root.visible = true;
    home.room.setTimeOfDay(new Date().getHours());
    vrm.scene.position.set(0.35, 0, 0.9);
    vrm.scene.rotation.y = st.baseYaw;
    home.mode = "idle"; home.next = clock.elapsedTime + 4; home.sit = false; home.look = null;
    roomCamera();
    say(home.wow.length ? "Дома! Ой… а это что такое?" : pick(["Дома! Сейчас, только разуюсь.", "Наконец-то дома."]), 3500);
  } else {
    if (life.mode === "walk") endOneShot();
    life.mode = "idle";
    home.on = false; home.sit = false; home.look = null; home.lie = false; home.lieW = 0;
    document.body.classList.remove("room");
    vrm.scene.quaternion.identity();
    home.room.root.visible = false;
    vrm.scene.position.set(0, 0, 0);
    vrm.scene.rotation.y = st.baseYaw;
    camera.fov = 24;
    camera.updateProjectionMatrix();
    poof();
    K.resizeAvatar?.(home.saved?.w || 620, home.saved?.h || 760);
    frame();
    say("Ладно, побуду с вами.", 2500);
  }
}
function spotFor(name) {
  if (name === "window") return home.room.windowSpot();
  return home.room.spot(name);
}
// следующая цель: сначала новые вещи, ночью — кровать, иначе случайное место
function pickRoomTarget() {
  if (home.wow.length) {
    const it = home.room.items.get(home.wow[0]);
    if (it) {
      // встаёт сбоку от вещи (поперёк взгляда камеры) — чтобы не заслонить её собой
      const p = it.obj.position, r = Math.max(it.size.x, it.size.z) * 0.6 + 0.45, m = 1.75;
      let pos = p.clone().addScaledVector(new THREE.Vector3(-0.81, 0, 0.58), r);
      if (Math.abs(pos.x) > m || Math.abs(pos.z) > m) pos = p.clone().addScaledVector(new THREE.Vector3(0.81, 0, -0.58), r);
      pos.x = clampN(pos.x, -m, m); pos.z = clampN(pos.z, -m, m);
      return { name: "wow", id: it.def.id, pos, face: Math.atan2(p.x - pos.x, p.z - pos.z), lookAt: p.clone().setY(0.6) };
    }
    home.wow.shift();
  }
  const hour = new Date().getHours();
  const name = (hour >= 23 || hour < 6) && Math.random() < 0.7 ? "bed" : pick(SPOTS.filter((s) => s !== home.target?.name));
  const s = spotFor(name);
  return s ? { name, ...s } : null;
}
// обход мебели: если прямая к цели задевает предмет (его след на полу с запасом), идём через промежуточную
// точку — ту, с которой оба отрезка свободны и путь короче. Без настоящего поиска пути: комната маленькая.
function footprints(skipId) {
  const out = [];
  for (const it of home.room.items.values()) {
    if (it.def.onTop || /^rug/.test(it.def.model) || it.def.id === skipId) continue;
    const p = it.obj.position, r = it.obj.rotation.y, c = Math.abs(Math.cos(r)), sn = Math.abs(Math.sin(r));
    const hx = (it.size.x * c + it.size.z * sn) / 2 + 0.22, hz = (it.size.x * sn + it.size.z * c) / 2 + 0.22;
    out.push([p.x - hx, p.x + hx, p.z - hz, p.z + hz]);
  }
  return out;
}
function segFree(a, b, boxes) {
  const L = Math.hypot(b.x - a.x, b.z - a.z);
  for (let i = 1; i < 20; i++) {
    if (Math.min(L * i / 20, L * (20 - i) / 20) < 0.35) continue;    // у начала и конца можно стоять вплотную к мебели
    const x = a.x + (b.x - a.x) * i / 20, z = a.z + (b.z - a.z) * i / 20;
    if (boxes.some(([x0, x1, z0, z1]) => x > x0 && x < x1 && z > z0 && z < z1)) return false;
  }
  return true;
}
function planPath(from, to, skipId) {
  const boxes = footprints(skipId);
  if (segFree(from, to, boxes)) return [to];
  let best = null;
  for (let gx = -1.6; gx <= 1.61; gx += 0.4) for (let gz = -1.6; gz <= 1.61; gz += 0.4) {
    const w = new THREE.Vector3(gx, 0, gz);
    if (!segFree(from, w, boxes) || !segFree(w, to, boxes)) continue;
    const len = from.distanceTo(w) + w.distanceTo(to);
    if (!best || len < best.len) best = { w, len };
  }
  return best ? [best.w, to] : [to];
}
function roomWalkTo(tg) {
  if (!home.on) return;
  const skip = tg.id || [...home.room.items.values()].find((i) => i.def.spot === tg.name)?.def.id;
  tg.path = planPath(vrm.scene.position.clone().setY(0), tg.pos.clone().setY(0), skip);
  const list = clips.walk || [];
  if (!list.length) { vrm.scene.position.set(tg.pos.x, 0, tg.pos.z); return arrive(tg); }
  home.target = tg; home.sit = false; home.look = null;
  life.clip = list.find((c) => c.name.includes(new Date().getHours() >= 23 ? "tired" : "feminine")) || list[0];
  const a = mixer.clipAction(life.clip);
  const from = st.oneShot || st.idleAction;
  a.reset().setLoop(THREE.LoopRepeat, Infinity).setEffectiveWeight(1).play();
  if (from && from !== a) from.crossFadeTo(a, 0.35, false); else a.fadeIn(0.35);
  if (st.legsAction && !st.legsOff) { st.legsAction.fadeOut(0.35); st.legsOff = true; }
  st.oneShot = a;
  life.mode = "walk";
  home.mode = "walk";
}
function arrive(tg) {
  if (life.mode === "walk") { endOneShot(); life.mode = "idle"; }
  home.mode = "turn";
  home.faceTo = tg.face ?? 0;
  const t = clock.elapsedTime;
  home.next = t + rnd(30, 70);
  if (tg.name === "wow") {
    home.look = tg.lookAt;
    setEmotion("surprised", 2, false);
    say(pick(["Ого! Это мне?", "Новое! Когда вы успели?", "Ой, а это откуда?"]), 3000);
    setTimeout(() => { setEmotion("happy", 4); say(pick(["Мне нравится. Не то чтобы я просила…", "Ладно, пусть стоит. Красиво."]), 4000); }, 2200);
    home.wow.shift();
    home.seen.push(tg.id);
    saveRoom();
    home.next = t + 9;
    return;
  }
  if ((tg.name === "chair" || tg.name === "bed") && tg.seatY) {
    const hh = Math.abs(vrm.humanoid.normalizedRestPose.hips.position[1]) || 0.85;
    home.seatY = tg.seatY + 0.06 - hh;
    setTimeout(() => { if (home.target === tg) home.sit = true; }, 700);
    const hour = new Date().getHours();
    // ночью: посидела — легла головой на подушку и уснула; проснётся — встанет туда же, где сидела
    if (tg.name === "bed" && (tg.sleep || hour >= 23 || hour < 6)) setTimeout(() => { if (home.target === tg) { home.lie = true; home.slept = false; st.lastActivity = performance.now() - SLEEP_AFTER - 1; } }, 5000);
  } else if (tg.name === "window") home.look = new THREE.Vector3(-0.1, 1.5, -2);
  if (Math.random() < 0.45) say(roomLine(tg.name), 3000);
  if (tg.name === "plant" || tg.name === "shelf") setTimeout(() => gesture("think", 3), 900);
}
const _qLie = new THREE.Quaternion(), _qStand = new THREE.Quaternion(), _qx = new THREE.Quaternion(), _vLie = new THREE.Vector3();
// лёжа на спине: поворот −90° вокруг X поверх «лицом к зрителю» — голова к изголовью (−z), лицо вверх
function lieTick(dt) {
  if (st.sleeping) home.slept = true;
  if (home.lie && home.slept && !st.sleeping) { home.lie = false; home.sit = true; home.next = clock.elapsedTime + rnd(4, 8); }   // проснулась
  home.lieW = damp(home.lieW || 0, home.lie ? 1 : 0, 2.2, dt);
  if (home.lieW < 0.002) { home.lieW = 0; return false; }
  const bed = [...home.room.items.values()].find((i) => i.def.spot === "bed");
  if (!bed) { home.lie = false; return false; }
  const bp = bed.obj.position, len = Math.max(bed.size.x, bed.size.z);
  _vLie.set(bp.x, bed.size.y * 0.55 + 0.1, bp.z + len / 2 - 0.12);         // стопы у изножья, спина на матрасе
  const w = home.lieW * home.lieW * (3 - 2 * home.lieW);
  const from = home.target?.pos || bp;
  const sitY = home.seatY * st.sitting;
  vrm.scene.position.set(from.x + (_vLie.x - from.x) * w, sitY + (_vLie.y - sitY) * w, from.z + (_vLie.z - from.z) * w);
  _qStand.setFromAxisAngle(_vLie.set(0, 1, 0), st.baseYaw + (home.faceTo ?? 0));
  _qLie.setFromAxisAngle(_vLie.set(0, 1, 0), st.baseYaw).premultiply(_qx.setFromAxisAngle(_vLie.set(1, 0, 0), -Math.PI / 2));
  vrm.scene.quaternion.copy(_qStand).slerp(_qLie, w);
  if (home.lie) home.sit = false;
  return true;
}
function roomTick(dt, t) {
  if (lieTick(dt)) { if (home.zoom > 0.01) roomCamera(); return; }
  const busy = st.dragging || st.sleeping || st.speechOn || st.thinking || st.listening || st.patting || demoRunning ||
    game.kind || menuEl.classList.contains("show") || home.dragIt;
  const P = vrm.scene.position;
  if (home.mode === "walk") {
    const tg = home.target, wp = tg.path?.[0] || tg.pos, dx = wp.x - P.x, dz = wp.z - P.z, d = Math.hypot(dx, dz);
    if (d < 0.08 && tg.path?.length > 1) { tg.path.shift(); return; }       // промежуточная точка пройдена
    // разворот на ходу — на пружине, без рывка
    const want = st.baseYaw + Math.atan2(dx, dz);
    let dy = want - vrm.scene.rotation.y;
    dy = Math.atan2(Math.sin(dy), Math.cos(dy));
    vrm.scene.rotation.y += dy * Math.min(1, dt * 6);
    const hh = Math.abs(vrm.humanoid.normalizedRestPose.hips.position[1]) || 0.9;
    const v = (life.clip.userData.speed || 1) * hh * (st.oneShot?.getEffectiveWeight() ?? 1) * (Math.abs(dy) > 0.8 ? 0.3 : 1);
    if (busy || d < 0.06) { if (!busy) arrive(tg); else { endOneShot(); life.mode = "idle"; home.mode = "idle"; home.next = t + 8; } return; }
    const s = Math.min(d, v * dt);
    P.x += dx / d * s; P.z += dz / d * s;
    if (home.zoom > 0.01) roomCamera();
    return;
  }
  if (home.mode === "turn") {
    let dy = st.baseYaw + home.faceTo - vrm.scene.rotation.y;
    dy = Math.atan2(Math.sin(dy), Math.cos(dy));
    vrm.scene.rotation.y += dy * Math.min(1, dt * 4);
    if (Math.abs(dy) < 0.02) home.mode = "idle";
  }
  if (t % 300 < dt) home.room.setTimeOfDay(new Date().getHours());
  if (home.zoom > 0.01) roomCamera();
  if (busy || st.oneShot || t < home.next || cfg.avatar?.wander === false && !home.wow.length) return;
  const tg = pickRoomTarget();
  if (!tg) { home.next = t + 30; return; }
  home.sit = false;
  setTimeout(() => roomWalkTo(tg), st.sitting > 0.3 ? 700 : 0);       // сначала встаёт, потом идёт
  home.next = t + 1e9;
}
addEventListener("wheel", (e) => {
  if (!home.on) return;
  home.zoom = clampN((home.zoom ?? 0) - Math.sign(e.deltaY) * 0.12, 0, 0.85);
});
// мебель: схватить, двигать по полу, отпустить — сохраняется
function roomPointerDown(e) {
  const ndc = ndcOf(e);
  raycaster.setFromCamera(ndc, camera);
  if (raycaster.intersectObject(vrm.scene, true).length) return false;      // по ней — обычные тычки
  const it = home.room.pickItem(ndc, camera);
  if (!it) return false;
  home.dragIt = it;
  document.body.style.cursor = "grabbing";
  return true;
}
function roomDrop() {
  const it = home.dragIt;
  home.dragIt = null;
  document.body.style.cursor = "default";
  home.layoutKey = JSON.stringify(home.room.layout);
  saveRoom();
  if (Math.random() < 0.5) say(pick(["Так лучше? Ну, может быть.", "Опять переставляете…", "Хм. А ничего."]), 2500);
  home.look = it.obj.position.clone().setY(0.5);
  setTimeout(() => { home.look = null; }, 2500);
}

// ---------- игры на рабочем столе (плагин «Мини-игры» → вкладка «На рабочем столе») ----------
const game = { kind: null, count: 0 };
const deskResult = (g, value) => fetch("/api/plugins/games/desk/result", { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ game: g, value }) }).catch(() => {});
async function deskGame(m) {
  if (game.kind || !vrm) return;
  activity();
  if (life.mode !== "idle") stopWalk();
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  if (m.kind === "pokes") {
    game.kind = "pokes"; game.count = 0;
    say("Тыкайте! Десять секунд… пошли!", 0);
    await wait(10000);
    game.kind = null;
    say(`Время! ${game.count} тычков.`, 5000);
    deskResult("pokes", game.count);
  } else if (m.kind === "hide" && K.placeAvatar && st.work) {
    if (camBusy()) return;
    await toGround();                                // с края окна или из укрытия — иначе слежение за окном тянет назад
    game.kind = "hide"; game.count = 0; game.caught = false;
    const home = { x: st.win.x, y: st.win.y };
    say("Попробуйте меня поймать!", 2500);
    await wait(1500);
    for (let round = 0; round < 6; round++) {
      poof();
      await wait(300);
      const w = st.work;
      K.placeAvatar(w.x + Math.random() * Math.max(1, w.width - W), w.y + w.height - H);
      game.caught = false;
      await wait(2600);
      if (game.caught) { game.count++; setEmotion("surprised", 1.5, false); }
    }
    poof();
    await wait(300);
    K.placeAvatar(home.x, home.y);
    game.kind = null;
    say(`Поймали ${game.count} из 6.`, 5000);
    deskResult("hide", game.count);
  } else if (m.kind === "reflex") {
    // «Реакция»: в случайный момент вскидывает руку — «Сейчас!»; клик раньше — фальстарт
    game.kind = "reflex"; game.armedAt = 0; game.done = false;
    say("Приготовьтесь… Кликайте по мне, когда скажу «сейчас»!", 0);
    await wait(1500 + Math.random() * 3500);
    if (game.done) return;
    game.armedAt = performance.now();
    gesture("wave", 1.2); setEmotion("surprised", 1.5, false);
    say("СЕЙЧАС!", 0);
    await wait(3000);
    if (!game.done) { game.done = true; game.kind = null; say("Уснули?", 3000); deskResult("reflex", 3000); }
  } else if (m.kind === "emotion") {
    // показывает эмоцию лицом, ушами, хвостом и телом — без слов
    say("Угадайте, что я чувствую?", 0);
    setEmotion(m.emo, 8, false);
    gesture({ happy: "happy", shy: "shy", angry: "angry", sad: "sad", surprised: "nod" }[m.emo] || "nod", 4);
    setTimeout(hideBubble, 8000);
  }
}

// ---------- переход на другой экран ----------
async function goScreen(index) {
  if (!K.toScreen || !K.screens || camBusy()) return;
  const list = await K.screens();
  const allowed = cfg.avatar?.screens || [];
  const d = list[index - 1];
  if (!d) { say(`Экрана ${index} нет — их у вас ${list.length}.`, 4000); return; }
  if (allowed.length && !allowed.includes(d.id)) { say("Туда мне нельзя — вы сами запретили этот экран в настройках.", 4500); return; }
  if (life.mode !== "idle") stopWalk();
  poof();
  await new Promise((r) => setTimeout(r, 450));
  await K.toScreen(index);
  setTimeout(() => { poof(); setEmotion("happy", 3, false); say(line("screen_moved"), 3500); }, 120);
}

// ---------- забавы: угощения, мячик и подушка, Хэллоуин, прятки за окнами ----------
// Голос и наклейки — в ядре (plugins/fun): окно шлёт avatar.event, ядро решает, что сказать.
function emojiTex(ch) {
  const k = "e:" + ch;
  TEX_DRAW[k] ??= (g) => { g.font = "50px 'Segoe UI Emoji'"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(ch, 32, 36); };
  return k;
}
TEX_DRAW.pillow = (g) => {
  g.fillStyle = "#f4ecff"; g.strokeStyle = "#b9a3e0"; g.lineWidth = 3;
  g.beginPath(); g.roundRect(6, 16, 52, 32, 14); g.fill(); g.stroke();
  g.strokeStyle = "rgba(185,163,224,.6)"; g.beginPath(); g.moveTo(14, 32); g.lineTo(50, 32); g.stroke();
};
// love — ест с удовольствием, like — ест, hate — выбрасывает
const FOOD_KIND = { "🍙": "love", "🍡": "love", "🍵": "like", "🍪": "like", "🍬": "like", "🍎": "like", "🥦": "hate" };
const sendEv = (kind, extra = {}) => send({ type: "avatar.event", kind, ...extra });

function halloween() {
  const v = cfg.fun?.halloween;
  if (v === true || v === false) return v;
  const d = new Date(), m = d.getMonth() + 1, day = d.getDate();
  return (m === 10 && day >= 24) || (m === 11 && day === 1);
}

// шляпа ведьмы на время Хэллоуина — между ушами, чуть набекрень
// Верх головы — по факту: луч сверху вниз по центру головы до причёски. Раньше шляпа ставилась на 11 % роста над
// костью головы — у моделей с большой головой или пышной причёской проваливалась в волосы, у маленьких висела.
function headTop(fallbackDy) {
  const fb = headPos(fallbackDy);
  if (!vrm) return fb;
  vrm.scene.updateMatrixWorld(true);
  const hb = new THREE.Vector3();
  vrm.humanoid.getRawBoneNode("head").getWorldPosition(hb);
  const rc = new THREE.Raycaster(new THREE.Vector3(hb.x, hb.y + modelH * 0.6, hb.z), new THREE.Vector3(0, -1, 0), 0, modelH * 0.7);
  const meshes = [];
  vrm.scene.traverse((o) => { if (o.isMesh && o.visible) meshes.push(o); });
  const hit = rc.intersectObjects(meshes, false).find((x) => x.point.y > hb.y && x.point.y < hb.y + modelH * 0.3);
  return hit ? new THREE.Vector3(hit.point.x, hit.point.y - modelH * 0.012, fb.z) : fb;   // чуть утопить в волосы
}
function makeHat(kind = "witch") {
  if (st.hat) { st.hat.parent?.remove(st.hat); st.hat = null; }
  st.hatVrm = vrm; st.hatKind = kind;
  const h = modelH, g = new THREE.Group();
  if (kind !== "witch") {                     // колпак Санты или праздничный — конус с помпоном
    const santa = kind === "santa";
    const cone = new THREE.Mesh(new THREE.ConeGeometry(h * 0.045, h * 0.11, 24, 4),
      new THREE.MeshStandardMaterial({ color: santa ? 0xd8262f : 0xff7eb6, roughness: 0.8 }));
    cone.position.y = h * 0.055; cone.rotation.z = santa ? -0.25 : 0;
    const pom = new THREE.Mesh(new THREE.SphereGeometry(h * 0.012, 16, 12),
      new THREE.MeshStandardMaterial({ color: santa ? 0xffffff : 0xffe14d, roughness: 1 }));
    pom.position.set(santa ? h * 0.026 : 0, h * 0.11, 0);
    g.add(cone, pom);
    if (santa) {
      const trim = new THREE.Mesh(new THREE.TorusGeometry(h * 0.045, h * 0.009, 10, 28), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1 }));
      trim.rotation.x = Math.PI / 2; g.add(trim);
    } else {
      for (let i = 0; i < 3; i++) {             // полоски на колпаке
        const band = new THREE.Mesh(new THREE.CylinderGeometry(h * (0.036 - i * 0.012), h * (0.04 - i * 0.012), h * 0.008, 20, 1, true),
          new THREE.MeshStandardMaterial({ color: [0x7ad7ff, 0xffe14d, 0x9cff8a][i], roughness: 0.7, side: THREE.DoubleSide }));
        band.position.y = h * (0.018 + i * 0.03); g.add(band);
      }
    }
    const head = vrm.humanoid.getRawBoneNode("head"), top = headTop(0.1);
    attach(g, head, vec(top.x, top.y, top.z - h * 0.04), new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.25, 0, santa ? 0.18 : -0.12)));
    st.hat = g;
    return;
  }
  const black = new THREE.MeshStandardMaterial({ color: 0x2a1d3d, roughness: 0.85 });
  const band = new THREE.MeshStandardMaterial({ color: 0xf08a24, roughness: 0.6 });
  const brim = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.085, h * 0.088, h * 0.005, 32), black);
  const cone = new THREE.Mesh(new THREE.ConeGeometry(h * 0.04, h * 0.12, 24, 4), black);
  cone.position.y = h * 0.06;
  const tip = new THREE.Mesh(new THREE.ConeGeometry(h * 0.014, h * 0.05, 12), black);
  tip.position.set(h * 0.016, h * 0.125, 0); tip.rotation.z = -0.9;
  const ring = new THREE.Mesh(new THREE.CylinderGeometry(h * 0.0405, h * 0.042, h * 0.014, 24, 1, true), band);
  ring.position.y = h * 0.011;
  g.add(brim, cone, tip, ring);
  const head = vrm.humanoid.getRawBoneNode("head");
  const top = headTop(0.11);
  attach(g, head, vec(top.x, top.y, top.z - h * 0.045), new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.3, 0, 0.16)));
  st.hat = g;
}
function funTick(t) {
  if (!vrm) return;
  const kind = cfg.fun?.hat === false ? null : { halloween: "witch", newyear: "santa", birthday: "party" }[holiday()] || null;
  if (kind && (st.hatVrm !== vrm || st.hatKind !== kind)) makeHat(kind);
  if (st.hat) st.hat.visible = !!kind && st.hatVrm === vrm && st.hatKind === kind;
  if (st.trickUntil && t > st.trickUntil) trickPrank();
}
// летучие мыши и тыквы вместо обычной осени
function halloweenAmbient(top, halfW, z, fall) {
  const r = Math.random();
  if (r < 0.45) {
    const vy = rnd(0.09, 0.14);
    particle({ tex: "leaf" + Math.floor(Math.random() * 4), pos: vec(rnd(-0.85, 0.85) * halfW, top, z), vel: vec(rnd(-0.02, 0.04), -vy),
      sway: 0.28, spin: rnd(-1.6, 1.6), life: fall(vy), size: rnd(0.03, 0.045), fade: 0.1, ambient: true });
  } else if (r < 0.85) {
    const dir = Math.random() < 0.5 ? 1 : -1, y0 = modelH * rnd(0.55, 1.05), x0 = -dir * halfW * 1.1, ph = Math.random() * 6;
    particle({ tex: emojiTex("🦇"), pos: vec(x0, y0, z), life: rnd(3.5, 5), size: rnd(0.035, 0.05), fade: 0.1, ambient: true,
      upd: (f) => {
        f.obj.position.set(x0 + dir * f.life * halfW * 0.55, y0 + Math.sin(f.life * 3 + ph) * modelH * 0.05, z);
        const flap = 0.75 + 0.25 * Math.abs(Math.sin(f.life * 14)), sz = 0.045 * modelH;
        f.obj.scale.set(sz * flap * dir, sz, 1);
      } });
  } else {
    const vy = rnd(0.06, 0.08);
    particle({ tex: emojiTex("🎃"), pos: vec(rnd(-0.8, 0.8) * halfW, top, z), vel: vec(0, -vy), sway: 0.1, spin: rnd(-0.8, 0.8),
      life: fall(vy), size: rnd(0.04, 0.055), fade: 0.1, ambient: true });
  }
}

// ---- угощения ----
function crumbs(at, color) {
  for (let i = 0; i < 7; i++)
    particle({ tex: "spark", pos: at.clone(), vel: vec(rnd(-0.15, 0.15), rnd(0.05, 0.2)), gravity: 0.9, life: 0.7, size: 0.012, color, fade: 0.4 });
}
function feed(food) {
  if (!vrm || st.eating) return;
  activity();
  st.eating = true;
  if (st.trickUntil) { st.trickUntil = 0; sendEv("trick_treat", { food }); } else sendEv("fed", { food });
  const kind = FOOD_KIND[food] || "like";
  const mouth = headPos(-0.05), hands = headPos(-0.24);
  const start = vec(mouth.x + rnd(-0.35, 0.35) * modelH, mouth.y + modelH * 0.45, mouth.z + 0.1);
  const away = Math.random() < 0.5 ? -1 : 1;
  if (kind === "hate") setTimeout(() => { setEmotion("angry", 3); gesture("angry", 2); }, 800);
  else setTimeout(() => { setEmotion(kind === "love" ? "happy" : "neutral", 4); gesture(kind === "love" ? "happy" : "nod", 2.5); }, 700);
  particle({ tex: emojiTex(food), pos: start, life: kind === "hate" ? 2.4 : 2.6, size: 0.07, fade: 0.08, upd: (f, dt) => {
    const sz = 0.07 * modelH;
    if (f.life < 0.75) {                       // прилетает в руки
      const e = 1 - (1 - f.life / 0.75) ** 3;
      f.obj.position.lerpVectors(start, hands, e);
    } else if (kind === "hate") {              // брокколи — прочь, по дуге
      if (!f.thrown) { f.thrown = true; f.v.set(away * 0.9 * modelH, 0.7 * modelH, 0); }
      if (f.life > 1.1) { f.v.y -= 2.2 * modelH * dt; f.obj.position.addScaledVector(f.v, dt); f.obj.material.rotation += dt * 10; }
    } else {                                   // подносит ко рту и откусывает: три укуса, крошки
      const k = Math.min(1, (f.life - 0.75) / 0.4);
      f.obj.position.lerpVectors(hands, mouth, k);
      if (f.life > 1.1) {
        const left = Math.max(0, 1 - Math.floor((f.life - 1.1) / 0.45 + 1) * 0.34);
        if (f.bites === undefined || left < f.bites) { f.bites = left; crumbs(mouth, food === "🍵" ? 0x9ccf6a : 0xffe0b0); }
        f.obj.scale.set(sz * left, sz * left, 1);
      }
    }
  } });
  setTimeout(() => { st.eating = false; }, 2700);
}

// ---- мячик и подушка ----
function throwThing(kind = "ball") {
  if (!vrm || st.throwing || game.kind) return;
  activity();
  st.throwing = true;
  const head = headPos(0.0), hands = headPos(-0.24), side = Math.random() < 0.5 ? -1 : 1;
  const start = vec(head.x + side * modelH * 0.7, head.y - modelH * 0.2, head.z + 0.15);
  const r = Math.random();
  const out = kind === "pillow" ? (r < 0.5 ? "catch" : "bonk") : r < 0.45 ? "catch" : r < 0.75 ? "dodge" : "bonk";
  const tex = kind === "pillow" ? "pillow" : emojiTex("🏐"), dur = 0.55;
  const end = out === "catch" ? hands : out === "bonk" ? head : vec(head.x - side * modelH * 0.8, head.y - modelH * 0.1, head.z + 0.15);
  if (out === "dodge") setTimeout(() => { st.flinchV -= 6; setEmotion("happy", 2.5, false); }, dur * 400);
  particle({ tex, pos: start, life: out === "catch" ? 2.6 : 2.2, size: kind === "pillow" ? 0.11 : 0.06, fade: 0.1, upd: (f, dt) => {
    if (!f.stage) {
      const k = Math.min(1, f.life / (out === "dodge" ? dur * 1.6 : dur));
      f.obj.position.lerpVectors(start, end, k);
      f.obj.position.y += Math.sin(Math.PI * k) * modelH * 0.12;
      f.obj.material.rotation += dt * 8;
      if (k >= 1) {
        f.stage = out;
        f.t0 = f.life;
        sendEv(`throw_${kind}_${out}`);
        if (out === "bonk") {
          for (let i = 0; i < 6; i++) particle({ tex: "star", pos: headPos(0.08), vel: vec(rnd(-0.2, 0.2), rnd(0.1, 0.3)), gravity: 0.6, life: 1, size: 0.025, spin: 3 });
          if (kind === "pillow") for (let i = 0; i < 8; i++) particle({ tex: "puff", pos: head.clone(), vel: vec(rnd(-0.3, 0.3), rnd(0, 0.3)), drag: 2, life: 0.9, size: 0.04 });
          st.flinchV -= 7; setEmotion("angry", 3); gesture("angry", 2);
          f.v.set(side * 0.5 * modelH, 0.5 * modelH, 0);
        } else if (out === "dodge") f.v.set(-side * 1.2 * modelH, 0, 0);
        else { setEmotion("happy", 3); gesture("happy", 2); }
      }
      return;
    }
    const s = f.life - f.t0;
    if (f.stage === "bonk" || f.stage === "dodge") {
      f.v.y -= 2 * modelH * dt; f.obj.position.addScaledVector(f.v, dt); f.obj.material.rotation += dt * 6;
    } else if (s > 1.0) {                       // поймала — бросает обратно: мячик в сторону, подушку — в вас
      if (kind === "pillow") { const sc = f.obj.scale.x * (1 + dt * 3.5); f.obj.scale.set(sc, sc, 1); f.obj.position.z += dt * modelH; }
      else { if (!f.back) { f.back = true; f.v.set(side * 1.3 * modelH, 0.6 * modelH, 0); } f.v.y -= 2 * modelH * dt; f.obj.position.addScaledVector(f.v, dt); }
    }
  } });
  setTimeout(() => { st.throwing = false; }, 2700);
}

// ---- прятки за окнами: прячется за случайное окно, ищите выглядывающий край ----
async function seekGame() {
  if (game.kind || !vrm || camBusy()) return;
  if (home.on || phys.mode !== "ground") { say("Сначала выпустите меня на рабочий стол.", 3000); return; }
  game.kind = "seek";
  activity();
  say("Закройте глаза и считайте до пяти! Не подглядывать!", 2600);
  await new Promise((r) => setTimeout(r, 2600));
  poof();
  renderer.domElement.style.opacity = 0;
  let ok = false;
  try { ok = await startPeek({ random: true, instant: true }); } catch {}
  await new Promise((r) => setTimeout(r, 350));
  renderer.domElement.style.opacity = 1;
  if (!ok) { game.kind = null; say("Тут негде спрятаться — откройте пару окон.", 4000); return; }
  hideBubble();
  phys.until = clock.elapsedTime + 90;
  game.t0 = performance.now();
  const round = game.t0;
  setTimeout(() => { if (game.kind === "seek" && game.t0 === round) { say("Хи-хи…", 1500); } }, 30000);
  setTimeout(() => { if (game.kind === "seek" && game.t0 === round) sneeze(); }, 55000);
  setTimeout(() => { if (game.kind === "seek" && game.t0 === round) seekLost(); }, 85000);
}
function seekLost() {
  game.kind = null;
  endPeek(false);
  sendEv("seek_lost");
}
function seekFound() {
  const sec = Math.round((performance.now() - game.t0) / 1000);
  game.kind = null;
  endPeek(true, true);                              // реплику скажет плагин (с секундами)
  setEmotion("surprised", 2, false);
  sendEv("seek_found", { sec });
}

// ---- «сладость или гадость»: не угостили вовремя — проказа ----
function trickStart(sec = 40) {
  st.trickUntil = clock.elapsedTime + sec;
  setEmotion("happy", 3, false); gesture("call", 2);
}
function trickPrank() {
  st.trickUntil = 0;
  for (let i = 0; i < 10; i++) {
    const dir = Math.random() < 0.5 ? 1 : -1, y0 = modelH * rnd(0.3, 1.0), ph = Math.random() * 6, sp = rnd(0.5, 0.9);
    particle({ tex: emojiTex("🦇"), pos: vec(0, y0, 0.2), life: 1.8, size: 0.05, fade: 0.3, upd: (f) => {
      f.obj.position.set(dir * f.life * modelH * sp, y0 + Math.sin(f.life * 5 + ph) * modelH * 0.08, 0.2);
    } });
  }
  sendEv("trick_prank");
  setTimeout(() => startPeek().then((ok) => { if (!ok) setEmotion("angry", 4); }), 1500);
}

// отладка в консоли: __gesture("bow")
window.__gesture = (n, s = 4) => gesture(n, s);
window.__st = st;
window.__k = { seat, applyTints, setEmotion, setGameMode, toGround, ride, perchOn, clock, startle, phys, frame, home, setHome, roomTick, roomWalkTo, planPath, get vrm() { return vrm; }, get mixer() { return mixer; }, get cfg() { return cfg; }, B, clips, st, THREE, camera, renderer, scene, lights: { keyLight, fillLight, backLight },
  fx: { sneeze, poof, particle, headPos, dz, pat }, demo, life, startWalk,
  fun: { feed, throwThing, seekGame, trickStart, halloween, makeHat }, pastime: startPastime, endPastime };

connect();
loadModel().catch((e) => say("Не загрузилась модель: " + e.message, 10000));
animate();
