// Мост между страницами (их отдаёт ядро) и оболочкой Electron.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("shikigami", {
  notify: (title, text) => ipcRenderer.send("notify", { title, text }),
  attention: () => ipcRenderer.send("attention"),
  reloadHotkeys: () => ipcRenderer.send("hotkeys:reload"),
  // аватар
  avatarHit: (hit) => ipcRenderer.send("avatar:hit", hit),
  moveAvatar: (dx, dy) => ipcRenderer.send("avatar:move", { dx, dy }),
  placeAvatar: (x, y) => ipcRenderer.send("avatar:place", { x, y }),
  resizeAvatar: (w, h) => ipcRenderer.send("avatar:resize", { w, h }),
  // окна рабочего стола (в единицах Electron): сверху вниз по z-порядку; одно окно по hwnd; спрятаться за окно
  deskWindows: () => ipcRenderer.invoke("desk:windows"),
  deskWindow: (hwnd) => ipcRenderer.invoke("desk:window", hwnd),
  behind: (hwnd) => ipcRenderer.invoke("avatar:behind", hwnd),
  // текст с экрана: рамка выделения
  ocr: () => ipcRenderer.send("ocr:start"),
  ocrDone: (r) => ipcRenderer.send("ocr:done", r),
  ocrCancel: () => ipcRenderer.send("ocr:cancel"),
  openMain: () => ipcRenderer.send("avatar:open-main"),
  gameMode: (on) => ipcRenderer.send("avatar:game-mode", on),
  onCursor: (cb) => ipcRenderer.on("cursor", (_e, d) => cb(d)),
  // экраны: список (слева направо) и перейти на экран N
  screens: () => ipcRenderer.invoke("screens:list"),
  toScreen: (index) => ipcRenderer.invoke("avatar:to-screen", index),
});
