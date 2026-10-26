// Комната {name}: маленькая уютная диорама прямо на рабочем столе (фон прозрачный).
// Мебель — Kenney Furniture Kit (CC0). Предметы у Kenney в своём масштабе и с началом координат в углу,
// поэтому у каждого — свой масштаб; положение задаём центром предмета на полу.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export const SIZE = 4;                 // комната 4×4 м, стены сзади (z = −2) и слева (x = −2)
const HALF = SIZE / 2;
const BASE = "/room/kenney-furniture/glb/";

// масштаб предметов Kenney в метры (подобран на глаз под рост персонажа ~1,5 м)
export const SCALE = { default: 1.7, bear: 0.9, bedSingle: 1.65, bedDouble: 1.65, desk: 1.95, laptop: 1.32, pillowBlue: 1.9, wall: 2, wallWindow: 2, wallDoorway: 2,
  rugRound: 2.2, rugRectangle: 2, rugSquare: 2, rugRounded: 2, pottedPlant: 1.37, plantSmall1: 1.6, plantSmall2: 1.6,
  plantSmall3: 1.6, bookcaseOpen: 1.75, bookcaseClosed: 1.75, lampRoundFloor: 1.8, lampSquareFloor: 1.8 };

// Места, куда {name} ходит: точка, куда встать, и куда смотреть/садиться
export const DEFAULT_LAYOUT = [
  { id: "rug", model: "rugRound", x: 0.3, z: 0.2, rot: 0 },
  { id: "bed", model: "bedSingle", x: -1.45, z: -0.95, rot: 0, spot: "bed" },
  { id: "desk", model: "desk", x: 1.15, z: -1.55, rot: 0, spot: "desk" },
  { id: "chair", model: "chairDesk", x: 1.15, z: -0.95, rot: Math.PI, spot: "chair" },
  { id: "screen", model: "laptop", x: 1.15, z: -1.65, rot: 0, onTop: "desk" },
  { id: "shelf", model: "bookcaseOpen", x: -1.6, z: 0.9, rot: Math.PI / 2, spot: "shelf" },
  { id: "plant", model: "pottedPlant", x: 1.65, z: 0.7, rot: 0, spot: "plant" },
  { id: "lamp", model: "lampRoundFloor", x: -0.75, z: -1.8, rot: 0 },
  { id: "pillow", model: "pillowBlue", x: -1.45, z: -1.7, rot: 0, onTop: "bed" },
];

const loader = new GLTFLoader();
const cache = new Map();
function loadModel(name) {
  if (!cache.has(name)) cache.set(name, loader.loadAsync(BASE + name + ".glb").then((g) => g.scene));
  return cache.get(name).then((s) => s.clone(true));
}

export function createRoom(scene) {
  const root = new THREE.Group();
  root.name = "room";
  root.visible = false;
  scene.add(root);
  const items = new Map();       // id → { obj, def, box }
  const furniture = new THREE.Group();
  root.add(furniture);

  // пол, плинтус, стены
  const wood = new THREE.MeshStandardMaterial({ color: 0xc89f78, roughness: 0.85 });
  const floor = new THREE.Mesh(new THREE.BoxGeometry(SIZE, 0.08, SIZE), wood);
  floor.position.y = -0.04;
  floor.receiveShadow = true;
  floor.name = "floor";
  root.add(floor);
  const wallMat = new THREE.MeshStandardMaterial({ color: 0xf3e3d3, roughness: 0.95 });
  const wallH = 2.4;
  const back = new THREE.Mesh(new THREE.BoxGeometry(SIZE, wallH, 0.08), wallMat);
  back.position.set(0, wallH / 2, -HALF - 0.04);
  const left = new THREE.Mesh(new THREE.BoxGeometry(0.08, wallH, SIZE), wallMat);
  left.position.set(-HALF - 0.04, wallH / 2, 0);
  root.add(back, left);
  // окно в задней стене: рама и «небо», цвет которого меняется по времени суток
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 0.9), new THREE.MeshBasicMaterial({ color: 0x9fd0ff }));
  sky.position.set(-0.1, 1.45, -HALF + 0.005);
  const frameMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.6 });
  const frameBits = [[1.22, 0.07, 0, 0.48], [1.22, 0.07, 0, -0.48], [0.07, 1.0, -0.58, 0], [0.07, 1.0, 0.58, 0], [0.05, 0.9, 0, 0], [1.1, 0.05, 0, 0]];
  for (const [w, h, x, y] of frameBits) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.05), frameMat);
    m.position.set(-0.1 + x, 1.45 + y, -HALF + 0.02);
    root.add(m);
  }
  root.add(sky);
  // свет комнаты: мягкий общий + тёплая лампа
  const hemi = new THREE.HemisphereLight(0xfff4e6, 0x8a6a50, 0.9);
  const lampLight = new THREE.PointLight(0xffc98a, 6, 5, 1.6);
  lampLight.position.set(-1.6, 1.6, -1.6);
  root.add(hemi, lampLight);

  async function place(def) {
    const obj = await loadModel(def.model);
    const s = SCALE[def.model] ?? SCALE.default;
    obj.scale.setScalar(s);
    obj.traverse((o) => { if (o.isMesh) { o.castShadow = o.receiveShadow = true; o.userData.item = def.id; } });
    // центрируем по полу: у Kenney начало координат в углу предмета
    const box = new THREE.Box3().setFromObject(obj);
    const c = box.getCenter(new THREE.Vector3());
    const pivot = new THREE.Group();
    obj.position.set(-c.x, -box.min.y, -c.z);
    pivot.add(obj);
    pivot.rotation.y = def.rot || 0;
    pivot.position.set(def.x, 0, def.z);
    pivot.userData.item = def.id;
    furniture.add(pivot);
    const size = box.getSize(new THREE.Vector3());
    items.set(def.id, { obj: pivot, def, size });
    return pivot;
  }
  function stackOnTop() {   // ноутбук на стол, подушка на кровать
    for (const { obj, def } of items.values()) {
      if (!def.onTop) continue;
      const base = items.get(def.onTop);
      if (base) obj.position.y = /^bed/.test(base.def.model) ? base.size.y * 0.62 : base.size.y - 0.02;   // на кровати — на матрас, не на спинку
    }
  }

  let layout = [];
  async function build(defs) {
    layout = defs.map((d) => ({ ...d }));
    furniture.clear();
    items.clear();
    await Promise.all(layout.map(place));
    stackOnTop();
    const lamp = [...items.values()].find((i) => /^lamp/.test(i.def.model));      // свет — от торшера, где бы он ни стоял
    if (lamp) lampLight.position.set(lamp.obj.position.x, lamp.size.y * 0.9, lamp.obj.position.z);
  }

  function setTimeOfDay(hour) {
    const day = [0x9fd0ff, 0xffc58f, 0x2b3557];
    const c = hour >= 7 && hour < 18 ? day[0] : hour >= 18 && hour < 21 ? day[1] : day[2];
    sky.material.color.setHex(c);
    const night = hour >= 21 || hour < 7;
    lampLight.intensity = night ? 4.5 : 1.5;
    hemi.intensity = night ? 0.45 : 0.9;
  }

  // точки для жизни в комнате: где встать и куда смотреть
  function spot(name) {
    const it = [...items.values()].find((i) => i.def.spot === name);
    if (!it) return null;
    const p = it.obj.position, r = it.obj.rotation.y, d = it.size;
    const front = new THREE.Vector3(Math.sin(r), 0, Math.cos(r));     // «перед» предмета
    switch (name) {
      case "chair": return { pos: p.clone(), face: r, seatY: Math.min(0.48, d.y * 0.45) };
      case "bed": return { pos: p.clone().add(new THREE.Vector3(d.x * 0.5 - 0.05, 0, 0.2)), face: Math.PI / 2,
        seatY: Math.min(0.5, d.y * 0.75), lie: { pos: p.clone().add(new THREE.Vector3(0, d.y * 0.8, 0.15)), head: -1 } };
      case "shelf": return { pos: p.clone().addScaledVector(front, 0.75), face: r + Math.PI };
      case "plant": return { pos: p.clone().add(new THREE.Vector3(-0.7, 0, 0.2)), face: Math.atan2(0.7, -0.2) };
      case "desk": return { pos: p.clone().addScaledVector(front, 0.9), face: r + Math.PI };
      default: return { pos: p.clone().addScaledVector(front, 0.8), face: r + Math.PI };
    }
  }
  const windowSpot = () => ({ pos: new THREE.Vector3(-0.1, 0, -HALF + 0.7), face: Math.PI });

  // перетаскивание мебели мышью по полу
  const ray = new THREE.Raycaster(), plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), hitP = new THREE.Vector3();
  function pickItem(ndc, camera) {
    ray.setFromCamera(ndc, camera);
    const h = ray.intersectObjects(furniture.children, true)[0];
    if (!h) return null;
    let o = h.object;
    while (o && !o.userData.item) o = o.parent;
    const id = o?.userData.item;
    const it = items.get(id);
    return it && !it.def.onTop ? it : null;
  }
  function dragTo(it, ndc, camera) {
    ray.setFromCamera(ndc, camera);
    if (!ray.ray.intersectPlane(plane, hitP)) return;
    const m = HALF - 0.25;
    it.obj.position.x = it.def.x = THREE.MathUtils.clamp(hitP.x, -m, m);
    it.obj.position.z = it.def.z = THREE.MathUtils.clamp(hitP.z, -m, m);
    for (const o of items.values()) if (o.def.onTop === it.def.id) { o.obj.position.x = o.def.x = it.def.x + (o.def.dx ?? 0); o.obj.position.z = o.def.z = it.def.z + (o.def.dz ?? -0.1); }
  }

  // уведомление пришло — лампа мигает три раза (как свет в комнате, когда кто-то пришёл)
  function blink() {
    const base = lampLight.intensity;
    let n = 0;
    const id = setInterval(() => {
      lampLight.intensity = n % 2 ? base : base * 3.2;
      if (++n >= 6) { clearInterval(id); lampLight.intensity = base; }
    }, 180);
  }

  return {
    root, build, setTimeOfDay, blink, spot, windowSpot, pickItem, dragTo, items,
    get layout() { return layout; },
    floorY: 0,
  };
}
