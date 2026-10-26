// Оболочка Shikigami: поднимает Python-ядро, держит главное окно, прозрачное окно аватара,
// значок в трее и глобальные горячие клавиши. Вся логика — в ядре, здесь только окна.
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, screen, Notification, nativeImage, shell, dialog } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, execFileSync } = require("child_process");

// Папка программы. Из исходников оболочка лежит в shell/, в сборке — в resources/app рядом с Shikigami.exe
const ROOT = fs.existsSync(path.join(__dirname, "..", "shikigami.py")) ? path.resolve(__dirname, "..") : path.resolve(process.resourcesPath, "..");
// Python: в сборке — свой в runtime/python, у разработчика — .venv
const PY = [path.join(ROOT, "runtime", "python", "python.exe"), path.join(ROOT, ".venv", "Scripts", "python.exe")].find((p) => fs.existsSync(p));
const PORT = 7853;
const BASE = `http://127.0.0.1:${PORT}`;
const ICON = path.join(__dirname, "assets", "icon256.png");

let core = null, mainWin = null, avatarWin = null, tray = null, quitting = false, starting = false;
const LOG = path.join(ROOT, "data", "core.log");
let avatarClickThrough = true;

if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => showMain());
app.setAppUserModelId("Shikigami");

// ---------- ядро ----------
async function coreAlive() {
  try { return (await fetch(`${BASE}/api/config`, { signal: AbortSignal.timeout(1500) })).ok; } catch { return false; }
}
async function startCore() {
  if (starting || core || await coreAlive()) return;
  starting = true;
  try {
    // Лог ядра — в data/core.log (при каждом запуске заново), чтобы ошибки было где искать
    const log = fs.createWriteStream(LOG, { flags: "w" });
    if (!PY) {                    // раньше spawn без Python падал окном «JavaScript error in the main process»
      quitting = true;
      dialog.showErrorBox("Shikigami", `Не найдено ядро программы (Python) в папке:
${ROOT}

Переустановите Shikigami.`);
      app.quit();
      return;
    }
    const proc = spawn(PY, ["-X", "faulthandler", path.join(ROOT, "shikigami.py")], { cwd: ROOT, windowsHide: true,
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" } });
    core = proc;
    proc.stdout.on("data", (d) => { log.write(d); process.stdout.write(`[ядро] ${d}`); });
    proc.stderr.on("data", (d) => { log.write(d); process.stderr.write(`[ядро] ${d}`); });
    // событие от прежнего процесса может прийти, когда уже запущен новый — не путаем их
    proc.on("exit", (code) => { log.end(`
[ядро завершилось, код ${code}]
`); if (core === proc) core = null; });
    for (let i = 0; i < 120 && core === proc; i++) { if (await coreAlive()) return; await new Promise((r) => setTimeout(r, 500)); }
  } finally { starting = false; }
}
// Ядро могли закрыть (или запустить отдельно и закрыть) — поднимаем заново
setInterval(async () => { if (!quitting && !core && !(await coreAlive())) startCore(); }, 4000);
const getJSON = async (p) => (await fetch(BASE + p)).json();
const postJSON = (p, body) => fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---------- окна ----------
function createMain() {
  mainWin = new BrowserWindow({
    width: 1280, height: 820, minWidth: 980, minHeight: 640, show: false, icon: ICON,
    backgroundColor: "#0e0b09", titleBarStyle: "hidden",
    titleBarOverlay: { color: "#00000000", symbolColor: "#f3e9df", height: 56 },
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  mainWin.loadURL(BASE + "/");
  mainWin.once("ready-to-show", () => mainWin.show());
  mainWin.on("close", (e) => { if (!quitting) { e.preventDefault(); mainWin.hide(); } });
  lockNavigation(mainWin);
}
// Ссылки из чата (их пишет модель, а она могла начитаться чужих страниц) открываем только как сайты:
// shell.openExternal("file:///…/x.exe") запустил бы программу. И окна не уходят со своего адреса —
// чужая страница в окне получила бы window.shikigami.
function lockNavigation(win) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(BASE + "/")) e.preventDefault(); });
}
function showMain() {
  if (!mainWin) return;
  if (mainWin.isMinimized()) mainWin.restore();
  mainWin.show(); mainWin.focus();
}

// Размер окна аватара (в единицах Electron) помним сами. Брать его из getBounds() нельзя: на экранах с разным
// масштабом (ноутбук 125 %, монитор 100 %) каждое setBounds с прочитанным размером округляется в большую
// сторону, и окно подрастало на 3–5 px при каждом переходе — после «вебки», смены экрана, прогулки через край.
let avatarSize = { width: 620, height: 760 };      // шире модели: в танце руки уходят в стороны — не обрезать краем
function avatarAt(x, y) { avatarWin.setBounds({ x: Math.round(x), y: Math.round(y), ...avatarSize }); }

function createAvatar() {
  const wa = screen.getPrimaryDisplay().workArea;
  const w = avatarSize.width, h = avatarSize.height;
  avatarWin = new BrowserWindow({
    width: w, height: h, x: wa.x + wa.width - w - 10, y: wa.y + wa.height - h,
    transparent: true, frame: false, resizable: false, hasShadow: false, skipTaskbar: true,
    alwaysOnTop: true, focusable: false, show: false, backgroundColor: "#00000000",
    webPreferences: { preload: path.join(__dirname, "preload.js"), backgroundThrottling: false },
  });
  avatarWin.setAlwaysOnTop(true, "screen-saver");
  avatarWin.setIgnoreMouseEvents(true, { forward: true });
  avatarWin.loadURL(BASE + "/avatar");
  lockNavigation(avatarWin);
  avatarWin.once("ready-to-show", () => avatarWin.showInactive());
}
// ---------- экраны ----------
// Нумерация слева направо. В настройках можно разрешить ей только часть экранов (например, без экрана ноутбука).
const displaysSorted = () => screen.getAllDisplays().sort((a, b) => a.bounds.x - b.bounds.x || a.bounds.y - b.bounds.y);
function screensInfo() {
  const prim = screen.getPrimaryDisplay().id;
  return displaysSorted().map((d, i) => ({ index: i + 1, id: d.id, width: d.size.width, height: d.size.height,
    primary: d.id === prim, label: d.label || "" }));
}
function moveToScreen(index) {
  if (camMode) return false;                 // «вебка» поверх игры стоит в своём углу
  const d = displaysSorted()[index - 1];
  if (!d || !avatarWin) return false;
  const wa = d.workArea;
  avatarAt(wa.x + wa.width - avatarSize.width - 10, wa.y + wa.height - avatarSize.height);
  return true;
}
ipcMain.handle("screens:list", () => screensInfo());
ipcMain.handle("avatar:to-screen", (_e, index) => moveToScreen(index));
function keepAllowedScreen(cfg) {
  const allowed = cfg?.avatar?.screens || [];          // id разрешённых экранов; пусто — все
  if (!avatarWin || !allowed.length) return;
  const cur = screen.getDisplayMatching(avatarWin.getBounds());
  if (allowed.includes(cur.id)) return;
  const i = displaysSorted().findIndex((d) => allowed.includes(d.id));
  if (i >= 0) moveToScreen(i + 1);
}

async function syncAvatar() {
  const cfg = await getJSON("/api/config").catch(() => null);
  const on = cfg?.avatar?.enabled !== false && cfg?.setup_done !== false;   // до мастера первого запуска — без аватара
  keepAllowedScreen(cfg);
  if (on && !avatarWin) createAvatar();
  if (!on && avatarWin) { avatarWin.destroy(); avatarWin = null; }
  buildTray(on, cfg?.assistant_name || "Shikigami");
}

// Курсор и окно аватара → в рендерер: взгляд следит за мышью, модель понимает, где она на экране
setInterval(() => {
  if (!avatarWin || avatarWin.isDestroyed()) return;
  const p = screen.getCursorScreenPoint();
  const b = avatarWin.getBounds(), d = screen.getDisplayMatching(b);
  // work — экран, где стоит сама {name} (а не где курсор): по нему она и гуляет
  avatarWin.webContents.send("cursor", { x: p.x, y: p.y, win: b, work: d.workArea,
    screen: displaysSorted().findIndex((x) => x.id === d.id) + 1 });
}, 33);

ipcMain.on("avatar:hit", (_e, hit) => {
  // Прозрачные места окна пропускают клики насквозь, а саму модель можно схватить мышью
  if (!avatarWin || camMode || hit === !avatarClickThrough) return;
  avatarClickThrough = !hit;
  avatarWin.setIgnoreMouseEvents(!hit, { forward: true });
});
ipcMain.on("avatar:move", (_e, { dx, dy }) => {
  if (!avatarWin || camMode) return;
  const b = avatarWin.getBounds();
  avatarAt(b.x + dx, b.y + dy);
});
ipcMain.on("avatar:place", (_e, { x, y }) => {
  if (!avatarWin || camMode) return;
  avatarAt(x, y);
});
ipcMain.on("avatar:open-main", () => showMain());
// игровой режим: поверх полноэкранной игры прозрачное окно «всегда сверху» мешает Windows отдать игре экран
// напрямую (лишняя композиция, кадры) — прячем окно целиком, пока игра на весь экран.
// «Вебка» (у кого один экран): вместо пряток окно сжимается в квадрат в углу экрана с игрой, как камера стримера;
// мышь проходит насквозь, двигать и растягивать окно аватар не может, после игры всё возвращается как было.
let camMode = null;                          // { bounds } — где окно стояло до игры
ipcMain.on("avatar:game-mode", (_e, arg) => {
  if (!avatarWin || avatarWin.isDestroyed()) return;
  const { on, cam } = typeof arg === "object" && arg ? arg : { on: !!arg };
  if (on && cam) {
    if (!camMode) camMode = { bounds: avatarWin.getBounds() };
    const d = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).bounds;   // курсор в игре — значит, её экран
    const size = Math.round(Math.max(120, Math.min(cam.size || 260, d.height * 0.6))), m = 16;
    const corner = String(cam.corner || "br");
    avatarWin.setBounds({ x: corner.includes("l") ? d.x + m : d.x + d.width - size - m,
      y: corner.includes("t") ? d.y + m : d.y + d.height - size - m, width: size, height: size });
    avatarWin.setIgnoreMouseEvents(true);
    avatarWin.setOpacity(Math.max(0.3, Math.min(1, cam.opacity ?? 1)));
    avatarWin.showInactive();
    avatarWin.moveTop();
  } else if (on) {
    avatarWin.hide();
  } else {
    if (camMode) {
      avatarAt(camMode.bounds.x, camMode.bounds.y);
      avatarWin.setOpacity(1);
      avatarWin.setIgnoreMouseEvents(true, { forward: true });
      avatarClickThrough = true;
      camMode = null;
    }
    avatarWin.showInactive();
  }
});
// ---------- текст с экрана: прозрачная рамка выделения поверх каждого экрана ----------
let ocrWins = [];
function closeOcr() { for (const w of ocrWins) if (!w.isDestroyed()) w.destroy(); ocrWins = []; }
function startOcr() {
  if (ocrWins.length) return;
  for (const d of screen.getAllDisplays()) {
    const w = new BrowserWindow({ ...d.bounds, frame: false, transparent: true, alwaysOnTop: true, skipTaskbar: true, resizable: false,
      movable: false, fullscreenable: false, hasShadow: false, backgroundColor: "#00000000",
      webPreferences: { preload: path.join(__dirname, "preload.js") } });
    w.setAlwaysOnTop(true, "screen-saver");
    w.setBounds(d.bounds);
    w.loadURL(BASE + "/ui/ocr.html");
    w.displayId = d.id;
    ocrWins.push(w);
  }
  ocrWins[0]?.focus();
}
ipcMain.on("ocr:cancel", () => closeOcr());
ipcMain.on("ocr:done", async (e, r) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  const b = w.getBounds();
  const dip = { x: b.x + r.x, y: b.y + r.y, width: r.w, height: r.h };
  closeOcr();
  const p = screen.dipToScreenRect(null, dip);          // в физические пиксели (экран ноутбука — 125 %)
  await new Promise((res) => setTimeout(res, 120));      // рамка успела исчезнуть с экрана
  postJSON("/api/ocr", { x: p.x, y: p.y, w: p.width, h: p.height }).catch(() => {});
});
ipcMain.on("ocr:start", () => startOcr());
// ---------- окна рабочего стола: края, на которых можно сидеть и висеть, и за которыми прятаться ----------
// Ядро отдаёт физические пиксели; здесь — в единицы Electron (на экране ноутбука 125 % они различаются)
const toDip = (w) => { const r = screen.screenToDipRect(null, { x: w.x, y: w.y, width: w.w, height: w.h });
  return { ...w, x: r.x, y: r.y, w: r.width, h: r.height }; };
ipcMain.handle("desk:windows", async () => (await getJSON("/api/desktop/windows").catch(() => [])).map(toDip));
ipcMain.handle("desk:window", async (_e, hwnd) => {
  const w = await getJSON(`/api/desktop/window/${hwnd}`).catch(() => ({ gone: true }));
  return w.gone ? w : toDip(w);
});
// Спрятаться за окно: снять «поверх всех» и встать в z-порядке сразу под ним; on=true — вернуть наверх
ipcMain.handle("avatar:behind", async (_e, hwnd) => {
  if (!avatarWin || camMode) return false;
  if (!hwnd) { avatarWin.setAlwaysOnTop(true, "screen-saver"); return true; }
  avatarWin.setAlwaysOnTop(false);
  await postJSON("/api/desktop/behind", { hwnd }).catch(() => {});
  return true;
});
// «Домой»: окно шире под комнату; угол у панели задач остаётся на месте, окно не вылезает за экран
ipcMain.on("avatar:resize", (_e, { w, h }) => {
  if (!avatarWin || camMode) return;
  const b = avatarWin.getBounds(), wa = screen.getDisplayMatching(b).workArea;
  const x = Math.max(wa.x, Math.min(b.x + b.width - w, wa.x + wa.width - w));
  const y = Math.max(wa.y, Math.min(b.y + b.height - h, wa.y + wa.height - h));
  avatarSize = { width: Math.round(w), height: Math.round(h) };
  avatarWin.setBounds({ x, y, ...avatarSize });
});
ipcMain.on("notify", (_e, { title, text }) => {
  const n = new Notification({ title, body: text, icon: ICON });
  n.on("click", showMain);
  n.show();
});
ipcMain.on("attention", () => { if (mainWin && !mainWin.isFocused()) { showMain(); mainWin.flashFrame(true); } });
ipcMain.on("hotkeys:reload", () => registerHotkeys());

// ---------- трей ----------
async function buildTray(avatarOn, name = "Shikigami") {
  const models = await getJSON("/api/models").catch(() => []);
  if (!tray) {
    tray = new Tray(nativeImage.createFromPath(path.join(__dirname, "assets", "icon32.png")));
    tray.on("click", showMain);
  }
  tray.setToolTip(name);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Открыть", click: showMain },
    { label: avatarOn ? `Спрятать ${name}` : `Показать ${name}`, click: async () => {
      await postJSON("/api/config", { avatar: { enabled: !avatarOn } }); syncAvatar(); } },
    { label: "Наряд", submenu: models.length ? models.map((m) => ({ label: m.title || m.name, type: "radio", checked: !!m.current,
        click: () => postJSON("/api/avatar/model", { name: m.name }) })) : [{ label: "нет моделей", enabled: false }] },
    { label: "Слушать (Ctrl+Alt+Space)", click: () => postJSON("/api/voice/listen", {}) },
    { label: "Текст с экрана (Ctrl+Alt+O)", click: () => startOcr() },
    { type: "separator" },
    { label: "Выход", click: () => { quitting = true; app.quit(); } },
  ]));
}

// ---------- горячие клавиши ----------
async function registerHotkeys() {
  globalShortcut.unregisterAll();
  globalShortcut.register("CommandOrControl+Alt+K", () => (mainWin.isVisible() && mainWin.isFocused() ? mainWin.hide() : showMain()));
  globalShortcut.register("CommandOrControl+Alt+Space", () => postJSON("/api/voice/listen", {}));
  globalShortcut.register("CommandOrControl+Alt+O", () => startOcr());
  globalShortcut.register("CommandOrControl+Alt+T", () => postJSON("/api/read-selection", {}));   // прочитать выделенное вслух
  globalShortcut.register("CommandOrControl+Alt+R", () => {   // перезагрузить окна после правок интерфейса
    for (const w of [mainWin, avatarWin]) if (w && !w.isDestroyed()) w.webContents.reloadIgnoringCache();
  });
  const list = await getJSON("/api/commands/hotkeys").catch(() => []);
  for (const h of list) {
    try {
      const ok = globalShortcut.register(h.accelerator, () => postJSON("/api/hotkey", { accelerator: h.accelerator }));
      if (!ok) console.warn(`горячая клавиша занята: ${h.accelerator} (${h.name})`);
    } catch (e) { console.warn(`неверная горячая клавиша ${h.accelerator}: ${e.message}`); }
  }
}

// ---------- старт ----------
app.whenReady().then(async () => {
  await startCore();
  createMain();
  await syncAvatar();
  await registerHotkeys();
  setInterval(syncAvatar, 5000);
  setInterval(registerHotkeys, 15000);  // команды могли поменяться голосом
});
app.on("window-all-closed", (e) => e.preventDefault());
app.on("before-quit", () => {
  quitting = true;
  globalShortcut.unregisterAll();
  // python.exe из .venv — лишь запускалка настоящего интерпретатора: гасим всё дерево процессов
  if (core) try { execFileSync("taskkill", ["/pid", String(core.pid), "/T", "/F"], { windowsHide: true }); } catch {}
});
