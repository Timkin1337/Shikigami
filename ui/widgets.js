// Виджеты главной: какие показывать, в какой колонке и в каком порядке (config ui.widgets), плюс новые виджеты.
// Каждый виджет можно спрятать «×», перетащить в другую колонку и вернуть кнопкой «+ Виджет».
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = (p, o = {}) => fetch(p, { headers: { "Content-Type": "application/json" }, ...o,
  body: o.body && JSON.stringify(o.body) }).then((r) => r.json());

const CATALOG = {
  "w-clock": { title: "Часы", icon: "🕒" },
  "w-weather": { title: "Погода", icon: "⛅" },
  "w-currency": { title: "Курсы валют", icon: "💱" },
  "w-sys": { title: "Состояние ПК", icon: "🖥" },
  "w-upcoming": { title: "Ближайшие дела", icon: "📅" },
  "w-focus": { title: "Фокус (помодоро)", icon: "🍅", make: focusW },
  "w-clip": { title: "Буфер обмена", icon: "📋", make: clipW },
  "w-mc": { title: "Майнкрафт", icon: "⛏", make: mcW },
  "w-chatter": { title: "{name}: болтливость", icon: "🦊", make: chatterW },
  "w-games": { title: "Рекорды мини-игр", icon: "🏆", make: gamesW },
};
const DEFAULT = { left: ["w-clock", "w-weather", "w-currency"], right: ["w-sys", "w-upcoming"] };
let layout = DEFAULT;
const timers = {};

export async function initWidgets() {
  const c = await api("/api/config").catch(() => ({}));
  layout = c.ui?.widgets?.left ? c.ui.widgets : DEFAULT;
  apply();
  const add = document.createElement("button");
  add.className = "btn widget-add"; add.textContent = "+ Виджет"; add.id = "widget-add";
  $(".home-left").after(add);
  add.onclick = picker;
  for (const col of [".home-left", ".home-right"]) {
    const el = $(col);
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop"); });
    el.addEventListener("dragleave", () => el.classList.remove("drop"));
    el.addEventListener("drop", (e) => {
      e.preventDefault(); el.classList.remove("drop");
      const w = document.getElementById(e.dataTransfer.getData("text/widget"));
      if (!w) return;
      const before = [...el.children].find((ch) => ch !== w && e.clientY < ch.getBoundingClientRect().top + ch.offsetHeight / 2);
      el.insertBefore(w, before || null);
      save();
    });
  }
}

function el(id) {
  let w = document.getElementById(id);
  if (!w && CATALOG[id]?.make) w = CATALOG[id].make(id);
  if (w && !w.dataset.decorated) {
    w.dataset.decorated = "1";
    w.draggable = true;
    w.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/widget", id));
    const x = document.createElement("button");
    x.className = "widget-x"; x.title = "Спрятать виджет"; x.textContent = "×";
    x.onclick = (e) => { e.stopPropagation(); hide(id); };
    w.appendChild(x);
  }
  return w;
}
function apply() {
  const shown = new Set([...layout.left, ...layout.right]);
  for (const id of Object.keys(CATALOG)) {
    const w = document.getElementById(id);
    if (w && !shown.has(id)) { w.remove(); stop(id); }
  }
  for (const [col, ids] of [[".home-left", layout.left], [".home-right", layout.right]]) {
    for (const id of ids) { const w = el(id); if (w) { $(col).appendChild(w); start(id); } }
  }
}
function save() {
  layout = { left: [...$(".home-left").children].map((c) => c.id).filter((i) => CATALOG[i]),
    right: [...$(".home-right").children].map((c) => c.id).filter((i) => CATALOG[i]) };
  api("/api/config", { method: "POST", body: { ui: { widgets: layout } } });
}
function hide(id) {
  layout.left = layout.left.filter((i) => i !== id);
  layout.right = layout.right.filter((i) => i !== id);
  document.getElementById(id)?.remove(); stop(id);
  api("/api/config", { method: "POST", body: { ui: { widgets: layout } } });
}
function picker() {
  const shown = new Set([...layout.left, ...layout.right]);
  const free = Object.entries(CATALOG).filter(([id]) => !shown.has(id));
  const box = document.createElement("div");
  box.className = "widget-picker glass";
  box.innerHTML = free.length ? free.map(([id, w]) => `<button data-id="${id}">${w.icon} ${esc(w.title)}</button>`).join("")
    : '<span class="muted">Все виджеты уже на экране</span>';
  document.body.appendChild(box);
  const r = $("#widget-add").getBoundingClientRect();
  Object.assign(box.style, { left: r.left + "px", top: Math.max(70, r.top - box.offsetHeight - 8) + "px" });
  const close = (e) => { if (!box.contains(e.target)) { box.remove(); removeEventListener("mousedown", close, true); } };
  setTimeout(() => addEventListener("mousedown", close, true));
  box.onclick = (e) => {
    const id = e.target.closest("[data-id]")?.dataset.id; if (!id) return;
    (layout.left.length <= layout.right.length ? layout.left : layout.right).push(id);
    apply(); save(); box.remove();
  };
}

// ---------------- новые виджеты ----------------
function shell(id, html) {
  const w = document.createElement("div");
  w.className = "widget glass mini-w"; w.id = id; w.innerHTML = html;
  return w;
}
function start(id) {
  const f = CATALOG[id]?.tick;
  if (f && !timers[id]) { f(); timers[id] = setInterval(f, CATALOG[id].every || 3000); }
}
function stop(id) { clearInterval(timers[id]); delete timers[id]; }

function focusW(id) {
  const w = shell(id, `<div class="w-title">🍅 Фокус</div><div class="mini-big" data-k="t">25:00</div><div class="muted" data-k="s">готовы?</div>
    <div class="mini-row"><button class="btn" data-a="start">▶ 25 мин</button><button class="btn" data-a="stop">■</button></div>`);
  let st = null, at = 0;
  CATALOG[id].every = 1000;
  CATALOG[id].tick = async () => {
    if (!st || Date.now() - at > 5000) { st = await api("/api/plugins/focus/state").catch(() => null); at = Date.now(); }
    if (!st) return;
    const left = st.phase ? Math.max(0, st.left - (Date.now() - at) / 1000) : st.work * 60;
    $("[data-k=t]", w).textContent = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}`;
    $("[data-k=s]", w).textContent = st.phase === "work" ? (st.task || "работаем") : st.phase === "rest" ? "перерыв ☕" : `сегодня: ${st.done_today} 🍅`;
  };
  w.onclick = async (e) => {
    const a = e.target.dataset.a; if (!a) return;
    st = await api(`/api/plugins/focus/${a}`, { method: "POST", body: { minutes: 25 } }); at = Date.now();
  };
  return w;
}
function clipW(id) {
  const w = shell(id, `<div class="w-title">📋 Буфер</div><ul class="mini-list"></ul>`);
  CATALOG[id].every = 4000;
  CATALOG[id].tick = async () => {
    const items = (await api("/api/clip").catch(() => [])).slice(0, 3);
    $("ul", w).innerHTML = items.map((it, i) => `<li data-i="${i}" title="Клик — скопировать снова">${esc(it.text.slice(0, 70))}${it.text.length > 70 ? "…" : ""}</li>`).join("")
      || '<li class="muted">Пусто</li>';
  };
  w.onclick = (e) => { const li = e.target.closest("[data-i]"); if (li) api("/api/clip/copy", { method: "POST", body: { i: +li.dataset.i } }); };
  return w;
}
function mcW(id) {
  const w = shell(id, `<div class="w-title">⛏ Майнкрафт</div><div data-k="body" class="muted">Игра не запущена</div>`);
  CATALOG[id].every = 2000;
  CATALOG[id].tick = async () => {
    const s = await api("/api/plugins/minecraft/eyes/state").catch(() => ({}));
    $("[data-k=body]", w).innerHTML = !s.fresh ? '<span class="muted">Игра не запущена (или нет мода «Глаза {name}»)</span>'
      : `❤ ${s.hp}/${s.hp_max} · 🍗 ${s.food}/20<br>${esc(s.dim)}, ${esc(s.biome)} · ${s.time}<br>` +
        (s.hostile ? `<span class="warn">⚠ враги рядом: ${s.hostile} (${esc(s.nearest)})</span>` : '<span class="muted">врагов рядом нет</span>') +
        (s.hand ? `<br><span class="muted">в руке: ${esc(s.hand)}</span>` : "");
  };
  return w;
}
function chatterW(id) {
  const NAMES = { silent: "молчит", minimal: "минимум", normal: "обычно", chatty: "болтушка" };
  const w = shell(id, `<div class="w-title">🦊 {name}</div><div data-k="s" class="muted"></div>
    <div class="mini-row"><button class="btn" data-m="60">Помолчи час</button><button class="btn" data-m="0">Можно говорить</button></div>`);
  CATALOG[id].every = 10000;
  CATALOG[id].tick = async () => {
    const s = await api("/api/chatter").catch(() => null); if (!s) return;
    $("[data-k=s]", w).textContent = `Режим: ${NAMES[s.mode] || s.mode} · сама сказала за час: ${s.said_last_hour}/${s.budget}` +
      (s.muted_for_min ? ` · молчит ещё ${s.muted_for_min} мин` : "");
  };
  w.onclick = async (e) => {
    const m = e.target.dataset.m; if (m === undefined) return;
    await api("/api/chatter/mute", { method: "POST", body: { minutes: +m } }); CATALOG[id].tick();
  };
  return w;
}
function gamesW(id) {
  const w = shell(id, `<div class="w-title">🏆 Рекорды</div><div data-k="b" class="muted"></div><div class="mini-row"><button class="btn" data-a="open">Играть</button></div>`);
  CATALOG[id].every = 15000;
  CATALOG[id].tick = async () => {
    const s = await api("/api/plugins/games/state").catch(() => null); if (!s) return;
    const d = s.desk || {}, m = s.more || {};
    $("[data-k=b]", w).innerHTML = `Крестики: ${s.ttt.score.me}:${s.ttt.score.her} · КНБ: ${s.rps.score.me}:${s.rps.score.her}<br>` +
      `Тычки: ${d.pokes?.best ?? 0} · реакция: ${d.reflex?.best ? d.reflex.best + " мс" : "—"}<br>` +
      `Число: ${m.guess?.best || "—"} поп. · викторина: ${m.quiz?.right ?? 0}✓`;
  };
  w.onclick = (e) => {
    if (e.target.dataset.a !== "open") return;
    $("#plugin-frame").src = "/plugins/games/panel.html"; $("#plugin-frame-title").textContent = "Мини-игры";
    $("#plugin-frame-wrap").classList.remove("hidden");
  };
  return w;
}
