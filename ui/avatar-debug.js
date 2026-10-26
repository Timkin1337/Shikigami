// Проверка плавности анимации (подключается только из консоли/тестов: import("/ui/avatar-debug.js")).
//   await __k.probe(5)   — 5 секунд пишет повороты костей каждый кадр; рывок = большой поворот за один кадр
//   await __k.sheet(12)  — лист из 12 кадров поверх окна (для снимка экрана), __k.sheetOff() убирает
const k = window.__k;
const NAMES = ["hips", "spine", "chest", "neck", "head", "leftUpperArm", "rightUpperArm", "leftLowerArm",
  "rightLowerArm", "leftUpperLeg", "rightUpperLeg"];

k.probe = (sec = 5) => new Promise((done) => {
  // пишем прямо из цикла рисования: каждый вызов — ровно один отрисованный кадр
  const { THREE } = k;
  const prev = {}, stats = {}, qw = new THREE.Quaternion();
  let frames = 0, time = 0, worstDt = 0;
  k.onFrame = (dt) => {
    time += dt; frames++;
    worstDt = Math.max(worstDt, dt);
    const h = k.vrm?.humanoid;
    for (const n of NAMES) {
      const node = h?.getRawBoneNode(n);
      if (!node) continue;
      node.getWorldQuaternion(qw);
      if (prev[n]) {
        const deg = prev[n].angleTo(qw) * 180 / Math.PI;      // поворот за этот кадр, градусы
        const s = stats[n] ??= { max: 0, at: 0, sum: 0, spikes: 0, lastDeg: 0 };
        if (deg > s.max) { s.max = deg; s.at = +time.toFixed(2); }
        if (deg > 1.5 && deg > s.lastDeg * 3 + 0.5) s.spikes++;   // рывок: скорость скачком выросла
        s.lastDeg = deg; s.sum += deg;
      }
      (prev[n] ??= new THREE.Quaternion()).copy(qw);
    }
    if (time >= sec) {
      k.onFrame = null;
      const out = {};
      for (const [n, s] of Object.entries(stats)) out[n] = { max: +s.max.toFixed(2), at: s.at, avg: +(s.sum / frames).toFixed(3), spikes: s.spikes };
      done({ frames, fps: +(frames / time).toFixed(1), worstDtMs: +(worstDt * 1000).toFixed(1), bones: out });
    }
  };
});

k.sheet = async (n = 12, every = 150, scale = 0.33, cols = 6) => {
  const W = innerWidth, H = innerHeight;
  const w = Math.round(W * scale), h = Math.round(H * scale);
  const c = document.createElement("canvas");
  c.width = w * cols; c.height = h * Math.ceil(n / cols);
  const g = c.getContext("2d");
  g.fillStyle = "#3a3f4a"; g.fillRect(0, 0, c.width, c.height);
  g.font = "12px sans-serif";
  for (let i = 0; i < n; i++) {
    await new Promise((r) => setTimeout(r, every));
    const x = (i % cols) * w, y = Math.floor(i / cols) * h;
    for (const cv of document.querySelectorAll("canvas")) g.drawImage(cv, x, y, w, h);   // модель и эффекты
    g.fillStyle = "#fff"; g.fillText(String(i), x + 4, y + 14);
  }
  k.sheetOff();
  const img = new Image();
  img.src = c.toDataURL();
  img.id = "__sheet";
  img.style.cssText = "position:fixed;left:0;top:0;z-index:999;max-width:100vw;max-height:100vh;object-fit:contain;background:#3a3f4a";
  document.body.appendChild(img);
  return `${c.width}x${c.height}`;
};
k.sheetOff = () => document.getElementById("__sheet")?.remove();

// Видео: модель + эффекты + подпись номера + облачко → WebM на сервер (data/recordings/)
k.record = async (sec = 30, name = "avatar.webm", fps = 30, wide = false) => {
  // wide: широкий кадр, окно «едет» внутри него (сдвиг из заглушки window.__moved) — видно прогулку
  const W = innerWidth, H = innerHeight, sc = wide ? 0.7 : 1;
  const CW = wide ? 1100 : W, CH = Math.round(H * sc);
  const c = document.createElement("canvas");
  c.width = CW; c.height = CH;
  const g = c.getContext("2d");
  const rec = new MediaRecorder(c.captureStream(fps), { mimeType: "video/webm;codecs=vp9", videoBitsPerSecond: 2_500_000 });
  const chunks = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  let on = true;
  const wrap = (text, maxW) => {
    const words = text.split(/\s+/), lines = [];
    let line = "";
    for (const w of words) { const t = line ? line + " " + w : w; if (g.measureText(t).width > maxW && line) { lines.push(line); line = w; } else line = t; }
    if (line) lines.push(line);
    return lines.slice(0, 4);
  };
  const draw = () => {
    if (!on) return;
    const grd = g.createLinearGradient(0, 0, 0, CH);
    grd.addColorStop(0, "#36404f"); grd.addColorStop(1, "#1d222b");
    g.fillStyle = grd; g.fillRect(0, 0, CW, CH);
    if (wide) { g.fillStyle = "#151a22"; g.fillRect(0, CH - 6, CW, 6); }    // «панель задач», по которой идёт
    const ox = wide ? CW / 2 - W * sc / 2 + (window.__moved || 0) * sc : 0;
    for (const cv of document.querySelectorAll("canvas")) g.drawImage(cv, ox, 0, W * sc, H * sc);
    const bubble = document.getElementById("bubble");
    if (bubble.classList.contains("show") && bubble.textContent.trim()) {
      g.font = "15px Segoe UI, sans-serif";
      const lines = wrap(bubble.textContent.trim(), W - 52);
      g.fillStyle = "rgba(23,17,13,.88)";
      g.beginPath(); g.roundRect(12, 8, Math.min(CW, W) - 24, 18 + lines.length * 21, 14); g.fill();
      g.fillStyle = "#f3e9df";
      lines.forEach((l, i) => g.fillText(l, 26, 31 + i * 21));
    }
    const state = document.getElementById("state").textContent;
    if (state) {
      g.font = "bold 20px Segoe UI, sans-serif";
      g.fillStyle = "rgba(0,0,0,.55)"; g.fillText(state, 15, CH - 15);
      g.fillStyle = "#f0a03c"; g.fillText(state, 14, CH - 16);
    }
    requestAnimationFrame(draw);
  };
  draw();
  rec.start(1000);
  await new Promise((r) => setTimeout(r, sec * 1000));
  on = false;
  const stopped = new Promise((r) => { rec.onstop = r; });
  rec.stop();
  await stopped;
  const blob = new Blob(chunks, { type: "video/webm" });
  const r = await fetch("/api/debug/recording?name=" + encodeURIComponent(name), { method: "POST", body: blob });
  return r.json();
};

// Клип для промо-ролика: чёткий (рендер ×scale), на тёплом фоне ролика, ровно fps кадров в секунду
// из самой анимации, без облачка и подписей. Сохраняется в data/recordings/<name>.
k.recordClip = async (sec = 3, name = "clip.webm", scale = 2, fps = 30) => {
  const r0 = k.renderer.getPixelRatio();
  k.renderer.setPixelRatio(scale);
  k.renderer.setSize(innerWidth, innerHeight);
  const src = k.renderer.domElement, c = document.createElement("canvas");
  c.width = src.width; c.height = src.height;
  const g = c.getContext("2d");
  // H.264 кодирует видеокарта; VP9 на процессоре не успевал и ронял кадры (14 к/с вместо 30)
  const mime = ["video/webm;codecs=h264", "video/webm;codecs=vp8", "video/webm;codecs=vp9"].find((m) => MediaRecorder.isTypeSupported(m));
  const stream = c.captureStream(0), track = stream.getVideoTracks()[0];
  const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 16_000_000 });
  const chunks = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  let on = true;
  const draw = () => {
    if (!on) return;
    const grd = g.createRadialGradient(c.width * 0.5, c.height * 0.55, 10, c.width * 0.5, c.height * 0.55, c.height * 0.8);
    grd.addColorStop(0, "#4a2414"); grd.addColorStop(0.55, "#22120b"); grd.addColorStop(1, "#0e0b09");
    g.fillStyle = grd; g.fillRect(0, 0, c.width, c.height);
    g.drawImage(src, 0, 0);
    track.requestFrame();               // каждый нарисованный кадр — в запись
    requestAnimationFrame(draw);
  };
  draw();
  rec.start(500);
  await new Promise((r) => setTimeout(r, sec * 1000));
  on = false;
  const stopped = new Promise((r) => { rec.onstop = r; });
  rec.stop();
  await stopped;
  k.renderer.setPixelRatio(r0);
  k.renderer.setSize(innerWidth, innerHeight);
  const blob = new Blob(chunks, { type: "video/webm" });
  const res = await fetch("/api/debug/recording?name=" + encodeURIComponent(name), { method: "POST", body: blob });
  return res.json();
};
