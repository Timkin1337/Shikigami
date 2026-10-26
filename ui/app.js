// Главное окно {name}: связь с ядром по WebSocket, чат, виджеты, дела, плагины, настройки.
import { initCommands, onCommandEvent } from "/ui/commands.js";
import { initWidgets } from "/ui/widgets.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  return r.json();
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

const TOOL_NAMES = {
  avatar_home: "Домой", focus_start: "Фокус-таймер", focus_stop: "Стоп фокуса",   open_app: "Запуск программы", open_url: "Открываю сайт", youtube: "YouTube", list_windows: "Окна",
  window_action: "Окно", kill_process: "Завершить процесс", search_files: "Поиск файлов", list_dir: "Папка",
  read_file: "Чтение файла", open_path: "Открыть", write_file: "Запись файла", move_path: "Перенос",
  make_dir: "Новая папка", recycle: "В корзину", web_search: "Поиск в интернете", fetch_page: "Чтение страницы",
  press_keys: "Клавиши", type_text: "Ввод текста", volume: "Громкость", media: "Плеер", run_powershell: "PowerShell",
  clipboard: "Буфер обмена", lock_pc: "Блокировка", power: "Питание", system_info: "Состояние ПК",
  add_reminder: "Напоминание", list_reminders: "Напоминания", cancel_reminder: "Отмена напоминания",
  add_note: "Заметка", find_notes: "Заметки", delete_note: "Удаление заметки", add_task: "Задача",
  list_tasks: "Задачи", complete_task: "Задача выполнена", add_event: "Событие", list_events: "Календарь",
  weather: "Погода", currency: "Курсы валют", remember: "Запоминаю", forget: "Забываю", recall: "Вспоминаю",
  create_command: "Новая команда", list_commands: "Команды", run_command: "Запуск команды",
  screenshot: "Снимок экрана", send_to_phone: "На телефон", play_game: "Мини-игра", avatar_screen: "На другой экран", list_avatars: "Наряды", switch_avatar: "Переодеваюсь", breathe_with_me: "Дышим",
  avatar_gesture: "Жест", chess_game: "Шахматы", search_in_browser: "Поиск в браузере", read_screen: "Текст с экрана",
  avatar_activity: "Занятие", avatar_hide: "Прячусь", avatar_photo: "Фото", avatar_recolor: "Перекраска",
  chatter_mode: "Болтливость", set_theme: "Тема", watch_video_together: "Смотрим видео", read_selection_aloud: "Читаю вслух",
  monitor_off: "Гашу экран", look_at_screen: "Смотрю на экран", modules_list: "Модули", module_install: "Установка модуля",
  ai_model_status: "Модель ИИ", ai_model_pick: "Подбор модели", friendship_status: "Дружба", fun: "Развлечения", alarm: "Будильник",
  minecraft_chat: "Майнкрафт: чат", minecraft_recent: "Майнкрафт: события", minecraft_view: "Майнкрафт: что видно",
  mc_join: "Майнкрафт: захожу", mc_leave: "Майнкрафт: выхожу", mc_status: "Майнкрафт: как я", mc_inventory: "Майнкрафт: инвентарь",
  mc_follow: "Майнкрафт: иду следом", mc_come: "Майнкрафт: подхожу", mc_stop: "Майнкрафт: стоп", mc_collect: "Майнкрафт: добываю",
  mc_craft: "Майнкрафт: крафт", mc_give: "Майнкрафт: отдаю", mc_attack: "Майнкрафт: атака", mc_guard: "Майнкрафт: охрана",
  mc_goto: "Майнкрафт: иду", mc_sleep: "Майнкрафт: сплю", mc_say: "Майнкрафт: пишу в чат",
};

const CLIP_EMPTY = "Пусто. Скопируйте что-нибудь — появится здесь. Пароли из менеджеров паролей не сохраняются.";
// ---------- связь с ядром ----------
let ws, wsReady = false;
const handlers = [];
function send(msg) { if (wsReady) ws.send(JSON.stringify(msg)); }
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws?role=main`);
  ws.onopen = () => { wsReady = true; setStatus("ok", "на связи"); send({ type: "ui.chat", chat_id: state.chat }); };
  ws.onclose = () => { wsReady = false; setStatus("off", "нет связи с ядром"); setTimeout(connect, 1500); };
  ws.onmessage = (e) => { const m = JSON.parse(e.data); handlers.forEach((h) => h(m)); };
}
function setStatus(kind, text) {
  const s = $("#status");
  s.className = `status-pill ${kind}`;
  $(".status-text", s).textContent = text;
}
window.shikigami?.onHotkeyFocus?.(() => goto("chat"));

// ---------- навигация ----------
const state = { chat: null, streaming: null, page: "home", listSub: "reminders", busy: false };
function goto(page, sub) {
  state.page = page;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
  if (page === "chat") { loadChats(); $("#input").focus(); }
  if (page === "lists") showList(sub || state.listSub);
  if (page === "plugins") loadPlugins();
  if (page === "settings") loadSettings();
  if (page === "wardrobe") { loadWardrobe(); loadTints(); }
  if (page === "commands") initCommands();
  if (page === "modules") loadModules();
}
$$(".tab").forEach((t) => (t.onclick = () => goto(t.dataset.page)));
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-goto]");
  if (g) goto(g.dataset.goto, g.dataset.sub);
});

// ---------- чат ----------
function renderText(text) {
  let h = esc(text);
  h = h.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  // ремарки в звёздочках и скрытую метку эмоции [shy] в чате не показываем
  h = h.replace(/\*[^*\n]{1,160}\*/g, "").replace(/\s*\[(happy|shy|angry|sad|surprised|relaxed|neutral)\]\s*$/i, "");
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  h = h.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank">$1</a>');
  return h;
}
function scrollDown() { const m = $("#messages"); m.scrollTop = m.scrollHeight; }
function clearHint() { $(".empty-hint", $("#messages"))?.remove(); }

function addMsg(role, text, meta) {
  clearHint();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `<div class="bubble">${role === "user" ? esc(text) : renderText(text)}</div>${meta ? `<div class="meta">${esc(meta)}</div>` : ""}`;
  $("#messages").appendChild(el);
  scrollDown();
  return el;
}
function addNotice(text) {
  clearHint();
  const el = document.createElement("div");
  el.className = "notice";
  el.textContent = text;
  $("#messages").appendChild(el);
  scrollDown();
}
function toolCard(id, name, args, status = "run", result) {
  clearHint();
  let el = $(`.tool-card[data-id="${CSS.escape(id)}"]`);
  if (!el) {
    el = document.createElement("div");
    el.className = "tool-card";
    el.dataset.id = id;
    $("#messages").appendChild(el);
  }
  el.className = `tool-card ${status}`;
  const argText = args ? Object.entries(args).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ") : "";
  if (args !== undefined) el.dataset.args = argText;
  // карточка лежит в #messages, куда автоперевод не заходит (там текст людей и модели) — подписи переводим сами
  const tr = (t) => window.i18n?.t?.(t) ?? t;
  const label = { run: "…", ok: "✓", error: "✕", denied: tr("запрещено") }[status] || status;
  el.innerHTML = `<b>${esc(tr(TOOL_NAMES[name] || name))}</b> <span class="args">${esc(el.dataset.args || "")}</span> <span class="st">${label}</span>` +
    (result !== undefined ? `<details><summary>${esc(tr("результат"))}</summary><pre>${esc(JSON.stringify(result, null, 1))}</pre></details>` : "");
  scrollDown();
}
function typing(on) {
  $(".typing", $("#messages"))?.remove();
  if (on) {
    const t = document.createElement("div");
    t.className = "msg assistant typing";
    t.innerHTML = '<div class="bubble"><span></span><span></span><span></span></div>';
    $("#messages").appendChild(t);
    scrollDown();
  }
}
function setBusy(b) {
  state.busy = b;
  $("#send").classList.toggle("hidden", b);
  $("#stop").classList.toggle("hidden", !b);
}

async function loadChats() {
  const chats = await api("/api/chats");
  $("#chats").innerHTML = chats.map((c) => `<li data-id="${c.id}" class="${c.id === state.chat ? "active" : ""}">
    <span class="title">${esc(c.title)}</span><button class="del" title="Удалить">×</button></li>`).join("");
}
$("#chats").onclick = async (e) => {
  const li = e.target.closest("li");
  if (!li) return;
  const id = +li.dataset.id;
  if (e.target.classList.contains("del")) {
    await api(`/api/chats/${id}`, { method: "DELETE" });
    if (state.chat === id) newChat();
    return loadChats();
  }
  openChat(id);
};
async function openChat(id) {
  state.chat = id;
  send({ type: "ui.chat", chat_id: id });
  const msgs = await api(`/api/chats/${id}`);
  $("#messages").innerHTML = "";
  let n = 0;
  for (const m of msgs) {
    if (m.role === "user") addMsg("user", m.content);
    else if (m.role === "assistant" && m.content) addMsg("assistant", m.content);
    else if (m.role === "call") { let a = {}; try { a = JSON.parse(m.args || "{}"); } catch {} toolCard(`h${n++}`, m.name, a, "ok"); }
  }
  if (!msgs.length) $("#messages").innerHTML = `<div class="empty-hint">${esc(window.i18n?.t?.("Пусто. Напишите что-нибудь.") ?? "Пусто. Напишите что-нибудь.")}</div>`;
  loadChats();
}
function newChat() {
  state.chat = null;
  send({ type: "ui.chat", chat_id: null });
  $("#messages").innerHTML = '<div class="empty-hint">Новый разговор. Скажите «{name}» или напишите.</div>';
  loadChats();
}
$("#new-chat").onclick = newChat;

// ---- картинки к реплике: 📎, Ctrl+V, перетаскивание в чат; ядро сохраняет их у себя, в облако — только при отправке ----
const attached = [];                                   // [{id, url}]
function drawAttached() {
  $("#attach-row").classList.toggle("hidden", !attached.length);
  $("#attach-row").innerHTML = attached.map((a, i) => `<div class="att"><img src="${a.url}" alt=""><button type="button" class="x" data-i="${i}" title="Убрать">×</button></div>`).join("");
}
async function attachFiles(files) {
  for (const f of [...files].filter((f) => f.type.startsWith("image/")).slice(0, 4 - attached.length)) {
    const data = await new Promise((r) => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
    const res = await api("/api/chat/image", { method: "POST", body: { data } }).catch(() => ({ error: "не загрузилась" }));
    if (res.id) attached.push(res); else toast("Картинка", res.error || "не загрузилась");
  }
  drawAttached();
}
$("#attach-file").onchange = (e) => { attachFiles(e.target.files); e.target.value = ""; };
$("#attach-row").onclick = (e) => { const b = e.target.closest(".x"); if (b) { attached.splice(+b.dataset.i, 1); drawAttached(); } };
$("#input").addEventListener("paste", (e) => {
  const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (imgs.length) { e.preventDefault(); attachFiles(imgs); }
});

$("#composer").onsubmit = (e) => {
  e.preventDefault();
  const text = $("#input").value.trim();
  if (!text && !attached.length) return;
  $("#input").value = "";
  autoGrow();
  const images = attached.splice(0).map((a) => a.id), urls = images.map((id) => `/uploads/${id}`);
  drawAttached();
  state.pendingUser = text;
  const el = addMsg("user", text || "🖼");
  if (urls.length) $(".bubble", el).insertAdjacentHTML("afterbegin", `<div class="att-imgs">${urls.map((u) => `<img class="att-img" src="${u}" alt="">`).join("")}</div>`);
  send({ type: "chat", text, chat_id: state.chat, source: "ui", ...(images.length ? { images } : {}) });
};
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
});
function autoGrow() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; }
$("#input").addEventListener("input", autoGrow);
$("#stop").onclick = () => send({ type: "stop", chat_id: state.chat });

// Кнопка микрофона: удерживать — говорить
for (const id of ["#mic", "#hero-mic"]) {
  const b = $(id);
  const down = (e) => { e.preventDefault(); b.classList.add("recording"); send({ type: "voice.ptt", down: true }); };
  const up = () => { if (!b.classList.contains("recording")) return; b.classList.remove("recording"); send({ type: "voice.ptt", down: false }); };
  b.addEventListener("mousedown", down);
  b.addEventListener("mouseup", up);
  b.addEventListener("mouseleave", up);
}

handlers.push((m) => {
  const mine = (cid) => cid != null && cid === state.chat;
  switch (m.type) {
    case "hello": $$("[data-bind=name]").forEach((e) => (e.textContent = m.name)); window.applyName?.(m.name); break;
    case "chat.created":
      if (m.source === "ui" || state.chat == null) { state.chat = m.chat_id; loadChats(); }
      break;
    case "chat.user":
      if (mine(m.chat_id) && m.source !== "ui") addMsg("user", m.text, (m.source === "voice" ? "голосом" : m.source) + (m.queued ? " · ждёт очереди" : ""));
      if (mine(m.chat_id)) { setBusy(true); typing(true); }
      setStatus("busy", "думает…");
      break;
    case "chat.tool":
      if (mine(m.chat_id)) { typing(false); toolCard(m.id, m.name, m.args, m.status, m.result); typing(m.status !== "run"); }
      break;
    case "chat.switch":
      if (mine(m.chat_id) && state.streaming) { state.streaming.remove(); state.streaming = null; }
      setStatus("busy", `переключилась на ${m.provider}`);
      break;
    case "chat.delta":
      if (!mine(m.chat_id)) break;
      typing(false);
      if (!state.streaming) { state.streaming = addMsg("assistant", ""); state.streamText = ""; }
      state.streamText += m.text;
      $(".bubble", state.streaming).innerHTML = renderText(state.streamText);
      scrollDown();
      break;
    case "chat.meta":
      state.lastMeta = `${m.provider} · ${m.seconds} ${window.i18n?.t?.("с") ?? "с"}`;
      break;
    case "friend.state": showFriend(m); break;
    case "friend.levelup": toast("Дружба", `Новый уровень: ${m.name}`); break;
    case "chat.done":
      setStatus("ok", "на связи");
      if (!mine(m.chat_id)) { if (m.source !== "ui") toast("{name}", m.text.replace(/\*[^*]+\*/g, "").trim().slice(0, 160)); break; }
      typing(false);
      setBusy(false);
      if (state.streaming) {
        $(".bubble", state.streaming).innerHTML = renderText(m.text || state.streamText);
        if (state.lastMeta) state.streaming.insertAdjacentHTML("beforeend", `<div class="meta">${esc(state.lastMeta)}</div>`);
      } else if (m.text) addMsg("assistant", m.text, m.error && m.detail ? `техническая причина: ${m.detail}` : state.lastMeta);
      state.streaming = null;
      loadChats();
      break;
    case "chat.notice":
      if (m.user_text && state.pendingUser !== m.user_text && state.page === "chat") addMsg("user", m.user_text);
      state.pendingUser = null;
      if (state.page === "chat") addNotice(m.text);
      else toast("Команда", m.text);
      break;
    case "confirm.request": showConfirm(m); break;
    case "confirm.expired": if (state.confirmId === m.id) hideConfirm(); break;
    case "notify": toast(m.title, m.text, m.kind); break;
    case "module.progress": moduleProgress(m); break;
    case "voice.design":
      $("#es-progress").textContent = m.text || "";
      if (m.done) { $("#es-make").disabled = false; loadVoices().then(() => { if (m.id) { $("#es-voice").value = m.id; esDesc(); } }); }
      break;
    case "llm.search": setStatus("busy", m.text); break;
    case "lines.progress":                                   // банк фраз под характер пишется в фоне
      $("#lines-out").textContent = m.text || "";
      if (m.finished) { $("#lines-regen").disabled = false; toast(m.error ? "Реплики" : "Реплики готовы", m.text); }
      break;           // ищет рабочую модель — не «зависла»
    case "avatar.tint.parts": {                                // показываем только те части, что нашлись у модели
      if (!m.parts) break;                                    // это эхо нашего же вопроса
      const parts = m.parts || [];
      $$("#tint-row [data-part]").forEach((l) => l.classList.toggle("hidden", !parts.includes(l.dataset.part)));
      $("#tint-note").textContent = parts.length ? "" : "У этой модели не нашлось частей для перекраски (материалы названы не по-VRoid).";
      break;
    }
    case "data.changed": refreshLists(m.what); break;
    case "clip.changed": if (state.page === "lists" && state.listSub === "clip") renderClip(); break;
    case "config.changed":
      if (state.page === "wardrobe") loadWardrobe();
      api("/api/config").then((c) => {                        // сменили имя в настройках — сразу везде, без перезагрузки
        applyTheme(c.ui?.theme);
        $$("[data-bind=name]").forEach((e) => (e.textContent = c.assistant_name || "Shikigami"));
        window.applyName?.(c.assistant_name);
      });
      break;
    case "voice.state": setVoiceState(m); break;
    case "voice.heard": if (state.page === "chat" && state.chat == null) addMsg("user", m.text, "голосом"); break;
    case "speech": playSpeech(m); break;
    case "ui.open_plugin":
      goto("plugins");
      $("#plugin-frame").src = `/plugins/${m.name}/panel.html${m.tab ? "?tab=" + encodeURIComponent(m.tab) : ""}`;
      $("#plugin-frame-title").textContent = m.title || m.name;
      $("#plugin-frame-wrap").classList.remove("hidden");
      window.shikigami?.attention?.();
      break;
    case "error": toast("Ошибка", m.text); break;
  }
  if (m.type.startsWith("command")) onCommandEvent(m);
});

function setVoiceState(m) {
  const map = { listening: ["busy", "слушаю…"], recognizing: ["busy", "распознаю…"], speaking: ["ok", "говорит"], idle: ["ok", "на связи"],
    wake: ["busy", "слушаю команду…"], error: ["off", m.text || "ошибка голоса"], mic: ["ok", `микрофон: ${m.name || "?"}`] };
  const [k, t] = map[m.state] || ["ok", "на связи"];
  setStatus(k, t);
  $$("#mic, #hero-mic").forEach((b) => b.classList.toggle("recording", m.state === "listening" || m.state === "wake"));
}

// ---------- речь (если аватар скрыт, говорит главное окно) ----------
function playSpeech(m) {
  if (m.player !== "main" || !m.url) return;
  const a = new Audio(m.url);
  const done = () => send({ type: "speech.ended", id: m.id });
  a.onended = done; a.onerror = done;
  a.play().catch(done);
}

// ---------- подтверждение ----------
function showConfirm(m) {
  state.confirmId = m.id;
  $("#confirm-text").textContent = m.text;
  $("#confirm").classList.remove("hidden");
  window.shikigami?.attention?.();
}
function hideConfirm() { $("#confirm").classList.add("hidden"); state.confirmId = null; }
$("#confirm-yes").onclick = () => { send({ type: "confirm", id: state.confirmId, ok: true }); hideConfirm(); };
$("#confirm-no").onclick = () => { send({ type: "confirm", id: state.confirmId, ok: false }); hideConfirm(); };

// ---------- уведомления ----------
function toast(title, text, kind = "") {
  const t = document.createElement("div");
  t.className = `toast glass ${kind}`;
  t.innerHTML = `<b>${esc(title)}</b><div>${esc(text)}</div>`;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "reminder" ? 20000 : 6000);
  t.onclick = () => t.remove();
  if (document.hidden || kind === "reminder") window.shikigami?.notify?.(title, text);
}

// ---------- главная: виджеты ----------
function tickClock() {
  const d = new Date();
  const loc = document.documentElement.lang || "ru";      // язык выставляет i18n.js
  $(".clock-time").textContent = d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
  $(".clock-date").textContent = d.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long" });
}
setInterval(tickClock, 1000); tickClock();

const WEATHER_ICON = (c) => c === 0 ? "☀" : c <= 2 ? "🌤" : c === 3 ? "☁" : c <= 48 ? "🌫" : c <= 67 || (c >= 80 && c <= 82) ? "🌧" : c <= 86 ? "❄" : "⛈";
async function loadWeather() {
  const w = await api("/api/weather").catch(() => ({ error: "нет сети" }));
  const box = $("#w-weather");
  if (w.error) { $(".w-big", box).textContent = "—"; $(".w-sub", box).textContent = w.error.includes("город") ? "укажите город в настройках" : w.error; return; }
  $(".w-icon", box).textContent = WEATHER_ICON(w.now.code);
  $(".w-big", box).textContent = `${w.now.temp > 0 ? "+" : ""}${w.now.temp}°`;
  $(".w-sub", box).textContent = `${w.city}, ${w.now.sky}`;
}
async function loadCurrency() {
  const c = await api("/api/currency").catch(() => ({}));
  if (!c.rates) return;
  // символ своей валюты берём у браузера (Br, ₴, ₽, ₸, $…); дешёвые валюты — за 100 единиц, как в обменниках
  const sym = (() => { try { return (0).toLocaleString("ru", { style: "currency", currency: c.base }).replace(/[\d\s,.]/g, "") || c.base; } catch { return c.base; } })();
  $(".w-icon", $("#w-currency")).textContent = sym.length > 3 ? c.base : sym;
  $(".w-rows", $("#w-currency")).innerHTML = Object.entries(c.rates).map(([k, v]) => {
    const per = v.rate < 0.1 ? 100 : 1;
    return `<div>${per > 1 ? per + " " : ""}${esc(k)} <b>${(v.rate * per).toFixed(v.rate * per < 10 ? 3 : 2)}</b> <span class="${v.change_pct >= 0 ? "up" : "down"}">${v.change_pct >= 0 ? "▲" : "▼"}${Math.abs(v.change_pct)}%</span></div>`;
  }).join("");
}
function showFriend(f) {
  const box = $("#w-friend");
  if (!box || !f) return;
  box.classList.toggle("hidden", f.enabled === false);
  const t = (s) => window.i18n?.t(s) ?? s;                    // названия уровней приходят из ядра по-русски
  $(".f-name", box).textContent = t(f.name);
  const pct = f.next ? Math.round(((f.points - f.floor) / (f.next - f.floor)) * 100) : 100;
  $(".f-bar i", box).style.width = pct + "%";
  $(".f-sub", box).textContent = f.next ? `дружба · ${f.points} из ${f.next} до «${t(f.next_name)}»` : `дружба · ${f.points} — выше некуда`;
}
$("#w-friend").onclick = () => {                       // что это вообще такое — раздел в «Помощи»
  goto("help");
  const d = $("#help-friend");
  d.open = true;
  d.scrollIntoView({ behavior: "smooth", block: "start" });
};
async function loadFriend() { showFriend(await api("/api/friendship").catch(() => null)); }
function meter(k, pct, text) {
  const m = $(`.meter[data-k=${k}]`);
  if (!m) return;
  $(".m-val", m).textContent = text;
  $(".bar-fill", m).style.width = `${Math.min(100, pct || 0)}%`;
  m.classList.toggle("hot", pct > 85);
}
async function loadSys() {
  if (state.page !== "home" || document.hidden) return;
  const w = await api("/api/widgets").catch(() => null);
  if (!w) return;
  const s = w.sys;
  meter("cpu", s.cpu, `${Math.round(s.cpu)}%`);
  meter("ram", s.ram, `${Math.round(s.ram)}% · ${s.ram_used_gb} ГБ`);
  if (s.gpu !== undefined) {
    meter("gpu", s.gpu, `${s.gpu}% · ${s.gpu_temp}°`);
    meter("vram", (s.vram_used_gb / s.vram_total_gb) * 100, `${s.vram_used_gb}/${s.vram_total_gb} ГБ`);
  }
  const p = w.media, box = $("#w-player");
  if (p && p.title) {
    $(".player-title", box).textContent = p.title;
    $(".player-artist", box).textContent = p.artist || "";
    $(".player-pos", box).textContent = fmtTime(p.position);
    $(".player-dur", box).textContent = fmtTime(p.duration);
    $(".bar-fill", box).style.width = p.duration ? `${(p.position / p.duration) * 100}%` : "0";
    box.classList.toggle("playing", !!p.playing);
  } else {
    $(".player-title", box).textContent = "Ничего не играет";
    $(".player-artist", box).textContent = "";
  }
}
setInterval(loadSys, 2000);
$$("[data-media]").forEach((b) => (b.onclick = () => fetch("/api/media/" + b.dataset.media, { method: "POST" }).then(loadSys)));

async function loadUpcoming() {
  const l = await api("/api/lists");
  const items = [
    ...l.reminders.map((r) => ({ t: r.due, text: `🔔 ${r.text}` })),
    ...l.events.map((e) => ({ t: e.start, text: `📅 ${e.title}` })),
  ].slice(0, 6);
  const tasks = l.tasks.filter((t) => !t.done);
  $("#w-upcoming .mini-list").innerHTML = items.length
    ? items.map((i) => `<li><span class="muted">${esc(i.t || "")}</span> ${esc(i.text)}</li>`).join("")
    : '<li class="muted">Ничего не запланировано</li>';
  $("#badge-tasks").textContent = tasks.length || "";
  $("#badge-rem").textContent = l.reminders.length || "";
  state.lists = l;
}

// ---------- дела ----------
const LIST_FORMS = {
  reminders: { fields: [["text", "О чём напомнить"], ["when", "Когда", "datetime-local"]], add: (f) => ({ text: f.text, when: f.when }) },
  tasks: { fields: [["text", "Задача"]], add: (f) => ({ text: f.text }) },
  events: { fields: [["title", "Событие"], ["start", "Начало", "datetime-local"], ["place", "Где"]], add: (f) => f },
  notes: { fields: [["text", "Текст заметки"]], add: (f) => ({ text: f.text }) },
  facts: { fields: [["fact", "Что запомнить о вас"]], add: (f) => ({ fact: f.fact }) },
};
function showList(sub) {
  state.listSub = sub;
  $$(".subtab").forEach((t) => t.classList.toggle("active", t.dataset.sub === sub));
  if (sub === "clip") return renderClip();
  const form = LIST_FORMS[sub];
  $("#list-add").innerHTML = form.fields.map(([n, ph, type]) => `<input name="${n}" placeholder="${ph}" type="${type || "text"}" ${n !== "place" ? "required" : ""}>`).join("") +
    '<button class="btn primary">Добавить</button>';
  renderList();
}
$$(".subtab").forEach((t) => (t.onclick = () => showList(t.dataset.sub)));
$("#list-add").onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  await api(`/api/lists/${state.listSub}/add`, { method: "POST", body: LIST_FORMS[state.listSub].add(f) });
  e.target.reset();
};
// ---- история буфера обмена (ядро: core/clip.py) и текст с экрана ----
async function renderClip(items) {
  $("#list-add").innerHTML = `<input id="clip-q" placeholder="Поиск по истории…"><button class="btn" type="button" id="clip-ocr" title="Выделите область экрана — текст окажется в буфере">🔍 Текст с экрана (Ctrl+Alt+O)</button>
    <button class="btn" type="button" id="clip-clear">Очистить (кроме закреплённых)</button>`;
  $("#list-add").onsubmit = (e) => e.preventDefault();
  items = items || await api("/api/clip");
  const draw = () => {
    const q = ($("#clip-q")?.value || "").toLowerCase();
    $("#list-items").innerHTML = items.map((it, i) => ({ it, i })).filter(({ it }) => !q || it.text.toLowerCase().includes(q))
      .map(({ it, i }) => `<li class="clip-item${it.pinned ? " pinned" : ""}" data-i="${i}"><span class="text" title="Клик — скопировать">${esc(it.text.length > 400 ? it.text.slice(0, 400) + "…" : it.text)}</span>
        <span class="muted">${new Date(it.at * 1000).toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
        <button class="pin" title="${it.pinned ? "Открепить" : "Закрепить — не удалится"}">${it.pinned ? "📌" : "📍"}</button><button class="del" title="Удалить">×</button></li>`).join("")
      || `<li class="muted">${esc(window.i18n?.t?.(CLIP_EMPTY) ?? CLIP_EMPTY)}</li>`;
  };
  draw();
  $("#clip-q").oninput = draw;
  $("#clip-ocr").onclick = () => window.shikigami?.ocr?.();
  $("#clip-clear").onclick = async () => { items = await api("/api/clip/clear", { method: "POST", body: {} }); draw(); };
  state.clipClick = async (e) => {                 // свой onclick списка не ставим: он ломал удаление в остальных списках
    const li = e.target.closest(".clip-item"); if (!li) return;
    const i = +li.dataset.i;
    if (e.target.classList.contains("pin")) items = await api("/api/clip/pin", { method: "POST", body: { i } });
    else if (e.target.classList.contains("del")) items = await api("/api/clip/delete", { method: "POST", body: { i } });
    else { await api("/api/clip/copy", { method: "POST", body: { i } }); toast("Буфер", "Скопировано"); return; }
    draw();
  };
}
function renderList() {
  const l = state.lists;
  if (!l || state.listSub === "clip") return;
  const sub = state.listSub;
  const rows = {
    reminders: l.reminders.map((r) => [r.id, r.text, r.due + (r.repeat ? ` · ${r.repeat}` : ""), false]),
    tasks: l.tasks.map((t) => [t.id, t.text, t.due || "", t.done]),
    events: l.events.map((e) => [e.id, e.title, `${e.start}${e.place ? " · " + e.place : ""}`, false]),
    notes: l.notes.map((n) => [n.id, n.text, n.date, false]),
    facts: l.facts.map((f) => [f.id, f.text, "", false]),
  }[sub];
  $("#list-items").innerHTML = rows.length ? rows.map(([id, text, date, done]) => `<li data-id="${id}" class="${done ? "done" : ""}">
    <span class="text">${esc(text)}</span><span class="muted">${esc(date || "")}</span>
    ${sub === "tasks" && !done ? '<button class="done-btn" title="Готово">✓</button>' : ""}<button class="del" title="Удалить">×</button></li>`).join("")
    : `<li class="muted">${esc(window.i18n?.t?.("Пусто") ?? "Пусто")}</li>`;      // список — вне перевода (данные), подпись — переводим
}
$("#list-items").onclick = async (e) => {
  if (state.listSub === "clip") return state.clipClick?.(e);
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  const id = +li.dataset.id;
  if (e.target.classList.contains("del")) await api(`/api/lists/${state.listSub}/delete`, { method: "POST", body: { id } });
  if (e.target.classList.contains("done-btn")) await api(`/api/lists/tasks/done`, { method: "POST", body: { id } });
};
async function refreshLists(what) {
  if (what === "chats") return loadChats();
  if (what === "tools") return;
  await loadUpcoming();
  if (state.page === "lists") renderList();
}

// ---------- плагины ----------
async function loadPlugins() {
  const ps = await api("/api/plugins");
  $("#plugin-grid").innerHTML = ps.length ? ps.map((p) => `<div class="plugin-card glass"><h3>${esc(p.title)}</h3>
    <p class="muted">${esc(p.description)}</p>${p.panel ? `<button class="btn" data-plugin="${p.name}" data-title="${esc(p.title)}">Открыть</button>` : ""}</div>`).join("")
    : '<div class="muted">Плагинов нет. Положите папку с plugin.py в plugins/.</div>';
}
$("#plugin-grid").onclick = (e) => {
  const b = e.target.closest("[data-plugin]");
  if (!b) return;
  $("#plugin-frame").src = `/plugins/${b.dataset.plugin}/panel.html`;
  $("#plugin-frame-title").textContent = b.dataset.title;
  $("#plugin-frame-wrap").classList.remove("hidden");
};
const closePlugin = () => { $("#plugin-frame-wrap").classList.add("hidden"); $("#plugin-frame").src = "about:blank"; };
$("#plugin-frame-close").onclick = closePlugin;
$("#plugin-frame-close").title = "Закрыть (Esc)";
addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#plugin-frame-wrap").classList.contains("hidden")) closePlugin(); });
// Esc внутри самого плагина (фокус в iframe) — тоже закрывает
$("#plugin-frame").addEventListener("load", () => {
  try { $("#plugin-frame").contentWindow.addEventListener("keydown", (e) => { if (e.key === "Escape") closePlugin(); }); } catch {}
});

// ---------- настройки ----------
const getPath = (o, p) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, p, v) { const ks = p.split("."); let c = o; ks.slice(0, -1).forEach((k) => (c = c[k] ??= {})); c[ks.at(-1)] = v; }
// Экраны: на каких мониторах ей можно быть (список даёт оболочка Electron)
async function loadScreens(cfg) {
  const box = $("#screens-box");
  if (!box || !window.shikigami?.screens) return;
  const list = await window.shikigami.screens();
  const allowed = cfg.avatar?.screens || [];
  box.innerHTML = `<div class="muted">Экраны, где ей можно быть (слева направо):</div>` + list.map((d) =>
    `<label class="check"><input type="checkbox" data-screen="${d.id}" ${!allowed.length || allowed.includes(d.id) ? "checked" : ""}>
      Экран ${d.index} — ${d.width}×${d.height}${d.primary ? ` (${window.i18n?.t("основной") ?? "основной"})` : ""}</label>`).join("");
}
function screensPatch() {
  const boxes = $$("#screens-box [data-screen]");
  if (!boxes.length) return null;
  const on = boxes.filter((b) => b.checked).map((b) => Number(b.dataset.screen));
  return on.length === boxes.length || !on.length ? [] : on;      // все или ни одного — без ограничений
}
async function loadSettings() {
  const cfg = await api("/api/config");
  loadScreens(cfg);
  for (const el of $$("#settings [name]")) {
    if (el.name.startsWith("_secrets.")) { el.value = ""; el.placeholder = cfg._secrets[el.name.slice(9)] ? "задан (оставьте пустым)" : "не задан"; continue; }
    const v = getPath(cfg, el.name);
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.name === "mcp.servers") el.value = JSON.stringify(v || {}, null, 2);
    else if (el.name === "telegram.allowed_ids") el.value = (v || []).join(", ");
    else if (el.type === "number" && el.min !== "" && (v == null || Number(v) < Number(el.min))) el.value = el.min;   // 0 из пустого поля блокировал «Сохранить»
    else el.value = v ?? "";
  }
}
$("#voice-test").onclick = () => {
  const v = (n) => $(`#settings [name="voice.${n}"]`).value;
  api("/api/voice/test", { method: "POST", body: { tts_speaker: v("tts_speaker"), tts_pitch: v("tts_pitch"), tts_rate: v("tts_rate"),
    tts_engine: v("tts_engine"), edge_voice: v("edge_voice"),
    espeech_voice: v("espeech_voice"), espeech_strength: v("espeech_strength"), espeech_steps: v("espeech_steps") } });
};

// голос с эмоциями: тембры, их описание, создание нового по описанию
let esVoices = [];
async function loadVoices() {
  const r = await api("/api/voices").catch(() => null);
  if (!r) return;
  esVoices = r.voices;
  const sel = $("#es-voice"), keep = sel.value || r.current;
  sel.innerHTML = r.voices.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}${x.mild ? "" : " (только яркие эмоции)"}</option>`).join("");
  sel.value = r.voices.some((x) => x.id === keep) ? keep : r.current;
  esDesc();
  const can = r.designer === "installed";
  $("#es-make").disabled = !can || r.busy;
  $("#es-design-note").textContent = can ? "Опишите голос словами — получится новый тембр со всеми эмоциями (1–3 минуты на видеокарте)."
    : "Нужен модуль «Создание тембров по описанию» (вкладка «Модули»).";
}
function esDesc() {
  const x = esVoices.find((v) => v.id === $("#es-voice").value);
  $("#es-desc").textContent = x?.description || "";
}
function esShow() {                    // показываем только поля выбранного движка голоса — остальные лишь путали
  const eng = $('#settings [name="voice.tts_engine"]').value;
  $("#espeech-panel").classList.toggle("hidden", eng !== "espeech");
  $$("#settings .only-silero").forEach((e) => e.classList.toggle("hidden", eng !== "silero"));
  $$("#settings .only-edge").forEach((e) => e.classList.toggle("hidden", eng !== "edge"));
}
$("#es-voice").onchange = esDesc;
$('#settings [name="voice.tts_engine"]').addEventListener("change", esShow);
$("#es-make").onclick = async () => {
  const r = await api("/api/voices/design", { method: "POST", body: { name: $("#es-name").value, description: $("#es-text").value } });
  if (!r.ok) { $("#es-progress").textContent = r.error || "не вышло"; return; }
  $("#es-make").disabled = true;
};
$$('.tab[data-page="settings"]').forEach((t) => t.addEventListener("click", () => setTimeout(() => { loadVoices(); esShow(); }, 300)));
$("#settings").onsubmit = async (e) => {
  e.preventDefault();
  const patch = {};
  try {
    for (const el of $$("#settings [name]")) {
      let v = el.type === "checkbox" ? el.checked : el.value;
      if (el.name.startsWith("_secrets.") && !v) continue;
      if (el.type === "number") { if (v === "") continue; v = Number(v); }   // пустое число не превращаем в 0
      if (el.name === "mcp.servers") v = JSON.parse(v || "{}");
      if (el.name === "telegram.allowed_ids") v = v.split(",").map((x) => x.trim()).filter(Boolean).map(Number);
      setPath(patch, el.name, v);
    }
  } catch (err) { $("#settings-msg").textContent = "Ошибка в JSON MCP: " + err.message; return; }
  const scr = screensPatch();
  if (scr) setPath(patch, "avatar.screens", scr);
  await api("/api/config", { method: "POST", body: patch });
  $("#settings-msg").textContent = "Сохранено. Микрофон и распознавание применятся после перезапуска.";
  greet();                                                   // вписали ключ — подсказка «подключите мозг» уходит
  setTimeout(() => ($("#settings-msg").textContent = ""), 5000);
  loadWeather();
};
// ---------- гардероб ----------
async function loadWardrobe() {
  const models = await api("/api/models");
  $("#model-grid").innerHTML = models.map((m) => {
    const st = m.settings || {};
    return `<div class="model-card glass ${m.current ? "current" : ""}" data-name="${esc(m.name)}">
      <div class="model-thumb">${m.thumb ? `<img src="${m.thumb}" alt="">` : "👤"}</div>
      <div class="model-info">
        <b>${esc(m.title || m.name)}</b>
        <div class="muted">${esc(m.author || "автор не указан")} · VRM ${esc(m.spec || "?")} · ${m.size_mb ?? "?"} МБ</div>
        <div class="muted">${esc(m.avatar || "")}${m.credit ? " · нужно указывать автора" : ""}</div>
        <label>Размер <input type="range" min="0.6" max="1.6" step="0.05" value="${st.scale || 1}" data-act="scale"></label>
        <label title="Если кисти прячутся за пышной юбкой — подвиньте вперёд">Руки вперёд <input type="range" min="0" max="0.6" step="0.02" value="${st.arm_forward ?? 0.28}" data-act="arm"></label>
        <div class="model-actions">
          ${m.current ? '<span class="badge-on">надета</span>' : '<button class="btn primary" data-act="wear">Надеть</button>'}
          ${m.current ? "" : '<button class="btn danger" data-act="delete">Удалить</button>'}
        </div>
      </div></div>`;
  }).join("") || '<div class="muted">Моделей нет — перетащите сюда .vrm</div>';
  const anims = await api("/api/animations");
  const checkText = (c) => Object.entries(c).flatMap(([bone, kinds]) => Object.entries(kinds).map(([k, v]) =>
    `${bone}: ${k} ${v.худший}° (предел ${v.предел}), ${Math.round(v.доля * 100)}% кадров`)).join("\n");
  $("#anim-list").innerHTML = anims.length ? anims.map((a) => {
    const bad = a.check && Object.keys(a.check).length;
    const mark = a.check == null ? "" : bad ? `<span class="bad" title="${esc(checkText(a.check))}">✗ суставы</span>` : '<span class="ok" title="Суставы в пределах человеческих">✓</span>';
    return `<li><span class="text">${esc(a.name)}</span><span class="muted">${esc(a.kind)}</span>${mark}</li>`;
  }).join("")
    : '<li class="muted">Анимаций нет</li>';
  loadRoom();
}
$("#model-grid").addEventListener("click", async (e) => {
  const card = e.target.closest(".model-card"), act = e.target.dataset.act;
  if (!card || !act) return;
  const name = card.dataset.name;
  if (act === "wear") await api("/api/avatar/model", { method: "POST", body: { name } });
  if (act === "delete" && confirm(`Удалить модель ${name}?`)) {
    const r = await api(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (r.error) toast("Гардероб", r.error);
  }
  loadWardrobe();
});
// ---- комната ----
const FURN = { bedSingle: "Кровать", bedDouble: "Двуспальная кровать", bedBunk: "Двухъярусная", desk: "Стол", deskCorner: "Угловой стол",
  chairDesk: "Офисный стул", chair: "Стул", chairCushion: "Стул с подушкой", chairRounded: "Круглый стул", laptop: "Ноутбук",
  computerScreen: "Монитор", bookcaseOpen: "Открытая полка", bookcaseClosed: "Шкаф", books: "Книги", pottedPlant: "Растение в горшке",
  plantSmall1: "Цветочек", plantSmall2: "Кактус", plantSmall3: "Росток", lampRoundFloor: "Торшер", lampSquareFloor: "Торшер квадратный",
  lampRoundTable: "Настольная лампа", rugRound: "Круглый ковёр", rugRectangle: "Ковёр", rugSquare: "Квадратный ковёр", pillow: "Подушка",
  pillowBlue: "Синяя подушка", bear: "Плюшевый мишка", loungeSofa: "Диван", loungeChair: "Кресло", loungeChairRelax: "Кресло-шезлонг",
  tableCoffee: "Журнальный столик", tableRound: "Круглый стол", televisionModern: "Телевизор", televisionVintage: "Старый телевизор",
  radio: "Радио", speaker: "Колонка", trashcan: "Мусорка", coatRackStanding: "Вешалка", sideTable: "Тумбочка", kitchenFridgeSmall: "Мини-холодильник",
  kitchenCoffeeMachine: "Кофемашина", cardboardBoxOpen: "Коробка", ceilingFan: "Вентилятор", bench: "Скамейка", stoolBar: "Табурет" };
const furnName = (m) => FURN[m] || m.replace(/([A-Z])/g, " $1").toLowerCase();
async function loadRoom() {
  const cfg = await api("/api/config");
  const layout = cfg.avatar?.room_layout || [];
  $("#room-items").innerHTML = layout.length ? layout.map((d) => `<span class="chip">${esc(furnName(d.model))}
    <button class="x" data-rm="${esc(d.id)}" title="Убрать из комнаты">✕</button></span>`).join("")
    : '<span class="muted">Стандартная расстановка. Добавьте что-нибудь из каталога.</span>';
  const grid = $("#room-grid");
  if (!grid.childElementCount) {
    const cat = await api("/api/room/catalog");
    const order = (m) => (FURN[m.model] ? 0 : 1);
    grid.innerHTML = cat.sort((a, b) => order(a) - order(b)).map((c) => `<button class="room-cell" data-add="${esc(c.model)}" title="${esc(c.model)}">
      ${c.preview ? `<img src="${c.preview}" alt="" loading="lazy">` : "🪑"}<span>${esc(furnName(c.model))}</span></button>`).join("");
  }
}
$("#room-grid").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-add]");
  if (!b) return;
  await api("/api/room/add", { method: "POST", body: { model: b.dataset.add } });
  toast("Комната", `${furnName(b.dataset.add)} — в комнате`);
  loadRoom();
});
$("#room-items").addEventListener("click", async (e) => {
  const id = e.target.dataset.rm;
  if (!id) return;
  await api("/api/room/remove", { method: "POST", body: { id } });
  loadRoom();
});
$("#room-home").onclick = () => api("/api/avatar/home", { method: "POST", body: { on: true } });
$("#room-desk").onclick = () => api("/api/avatar/home", { method: "POST", body: { on: false } });
$("#room-reset").onclick = async () => {
  if (!confirm("Вернуть стандартную расстановку мебели?")) return;
  await api("/api/config", { method: "POST", body: { avatar: { room_layout: [] } } });
  loadRoom();
};
initWidgets();
// ---- темы оформления ----
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  document.querySelectorAll("#theme-pick [data-theme]").forEach((b) => b.classList.toggle("on", (b.dataset.theme || "") === (t || "")));
}
$("#theme-pick").onclick = async (e) => {
  const b = e.target.closest("[data-theme]"); if (!b) return;
  applyTheme(b.dataset.theme);
  await api("/api/config", { method: "POST", body: { ui: { theme: b.dataset.theme } } });
};
api("/api/config").then((c) => applyTheme(c.ui?.theme));
// ---- кнопка голоса ----
const VK = { 8: "Backspace", 9: "Tab", 13: "Enter", 16: "Shift", 17: "Ctrl", 18: "Alt", 19: "Pause", 20: "CapsLock", 32: "Пробел",
  33: "PageUp", 34: "PageDown", 35: "End", 36: "Home", 45: "Insert", 46: "Delete", 91: "Win", 144: "NumLock", 145: "ScrollLock",
  160: "Левый Shift", 161: "Правый Shift", 162: "Левый Ctrl", 163: "Правый Ctrl", 164: "Левый Alt", 165: "Правый Alt", 192: "Ё/~" };
function pttName(k) {
  if (!k) return "не задана";
  if (k === "mouse3") return "Средняя кнопка мыши";
  if (k === "mouse4") return "Мышь: боковая «назад»";
  if (k === "mouse5") return "Мышь: боковая «вперёд»";
  const vk = +k.split(":")[1];
  if (vk >= 112 && vk <= 135) return "F" + (vk - 111);
  if (vk >= 96 && vk <= 105) return "Num " + (vk - 96);
  if ((vk >= 48 && vk <= 57) || (vk >= 65 && vk <= 90)) return String.fromCharCode(vk);
  return VK[vk] || "клавиша " + vk;
}
async function showPtt() { const c = await api("/api/config"); $("#ptt-key").textContent = pttName(c.voice?.ptt_key); }
$("#ptt-set").onclick = async () => {
  $("#ptt-key").textContent = "нажмите клавишу или кнопку мыши… (Esc — отмена)";
  $("#ptt-set").disabled = true;
  const r = await api("/api/voice/capture-key", { method: "POST", body: {} });
  $("#ptt-set").disabled = false;
  $("#ptt-key").textContent = pttName(r.key) + (r.key ? " ✓" : "");
  if (!r.key) showPtt();
};
$("#ptt-clear").onclick = async () => { await api("/api/config", { method: "POST", body: { voice: { ptt_key: "" } } }); showPtt(); };
showPtt();
$("#anim-demo").onclick = () => api("/api/avatar/demo", { method: "POST", body: {} });

// перекраска и фото (вкладка «Гардероб»)
let tintTimer = 0;
// надетая модель — по списку ядра: в настройках может быть пусто (первый запуск), а надета первая по алфавиту
async function tintModel() { const m = (await api("/api/models")).find((x) => x.current); return { name: m?.name, tint: m?.settings?.tint || {} }; }
async function loadTints() {
  const { name, tint } = await tintModel();
  $("#tint-model").textContent = name ? `· ${name.replace(/\.vrm$/i, "")}` : "";
  $$("[data-tint]").forEach((i) => { if (tint[i.dataset.tint]) i.value = tint[i.dataset.tint]; });
  $("#tint-note").textContent = "";
  send({ type: "avatar.tint.parts" });
}
$("#tint-row").oninput = (e) => {
  const inp = e.target.closest("[data-tint]");
  if (!inp) return;
  clearTimeout(tintTimer);
  tintTimer = setTimeout(async () => {                        // ползунок цвета шлёт десятки событий — красим по паузе
    const { name } = await tintModel();
    if (name) api("/api/avatar/settings", { method: "POST", body: { name, tint: { [inp.dataset.tint]: inp.value } } });
  }, 350);
};
$("#tint-reset").onclick = async () => {
  const { name } = await tintModel();
  if (name) api("/api/avatar/settings", { method: "POST", body: { name, tint: { hair: "", eyes: "", clothes: "" } } });
};
$("#photo-take").onclick = () => api("/api/avatar/photo", { method: "POST", body: { pose: $("#photo-pose").value, emotion: $("#photo-emo").value } });
$("#model-grid").addEventListener("change", async (e) => {
  const card = e.target.closest(".model-card"), act = e.target.dataset.act;
  if (!card) return;
  const body = { name: card.dataset.name };
  if (act === "scale") body.scale = Number(e.target.value);
  if (act === "arm") body.arm_forward = Number(e.target.value);
  await api("/api/avatar/settings", { method: "POST", body });
});
async function uploadFiles(files) {
  const vrm = [...files].filter((f) => /\.vrm$/i.test(f.name)), anim = [...files].filter((f) => /\.(vrma|fbx)$/i.test(f.name));
  for (const [list, url] of [[vrm, "/api/models"], [anim, "/api/animations"]]) {
    if (!list.length) continue;
    const fd = new FormData();
    list.forEach((f) => fd.append("file", f));
    toast("Гардероб", `Загружаю: ${list.map((f) => f.name).join(", ")}`);
    const r = await fetch(url, { method: "POST", body: fd }).then((r) => r.json());
    toast("Гардероб", r.ok ? (url === "/api/models" ? `Надела «${r.name}»` : "Анимации добавлены") : r.error || "не получилось");
  }
  if (!vrm.length && !anim.length) toast("Гардероб", "Нужны файлы .vrm, .vrma или .fbx");
  loadWardrobe();
}
$("#wardrobe-upload").onchange = (e) => uploadFiles(e.target.files);
let dragDepth = 0;
addEventListener("dragenter", (e) => { if (e.dataTransfer?.types.includes("Files")) { dragDepth++; $("#drop-overlay").classList.remove("hidden"); } });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").classList.add("hidden"); } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop-overlay").classList.add("hidden");
  if (!e.dataTransfer.files.length) return;
  const files = [...e.dataTransfer.files];             // картинки в чат — к реплике; модели и анимации — в «Гардероб»
  if (state.page === "chat" && files.every((f) => f.type.startsWith("image/"))) return attachFiles(files);
  goto("wardrobe"); uploadFiles(e.dataTransfer.files);
});

// ---------- старт ----------
if (window.shikigami) document.body.classList.add("in-shell");
connect();
loadWeather(); setInterval(loadWeather, 15 * 60 * 1000);
loadCurrency(); setInterval(loadCurrency, 60 * 60 * 1000);
loadFriend();
loadUpcoming();
loadSys();

// ---------- поиск локальной модели: Ollama / LM Studio / llama.cpp на этом ПК ----------
$("#llm-detect").onclick = async () => {
  const out = $("#llm-detect-out");
  out.textContent = "ищу…";
  const found = await (await fetch("/api/llm/detect", { method: "POST" })).json().catch(() => []);
  const first = found.find((f) => f.models.length);
  if (!first) {
    out.textContent = "Ничего не запущено. Откройте Ollama или LM Studio с загруженной моделью и нажмите ещё раз (инструкция — во вкладке «Помощь»).";
    return;
  }
  const set = (n, v) => { const el = $(`#settings [name="${n}"]`); if (el) el.value = v; };
  set("llm.providers.local.base_url", first.base_url);
  set("llm.providers.local.model", first.models[0]);
  set("llm.providers.local.model_path", "");
  out.textContent = `Нашла: ${found.map((f) => `${f.app} (${f.models.join(", ") || "без модели"})`).join("; ")}. Вписала ${first.app} — нажмите «Сохранить».`;
};

// ---------- «Улучшить промпт»: ИИ доводит набросок характера, человек проверяет и сохраняет ----------
$("#persona-improve").onclick = async () => {
  const ta = $('#settings [name="persona_custom"]'), out = $("#persona-improve-out"), btn = $("#persona-improve");
  btn.disabled = true; out.textContent = "переписываю… (до минуты)";
  try {
    const r = await (await fetch("/api/persona/improve", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: ta.value }) })).json();
    if (r.error) { out.textContent = r.error; return; }
    ta.dataset.before = ta.value; ta.value = r.text;
    const sel = $('#settings [name="persona"]'); if (sel) sel.value = "custom";
    out.innerHTML = 'Готово — проверьте и нажмите «Сохранить». <button class="btn" type="button" id="persona-undo">Вернуть мой вариант</button>';
    $("#persona-undo").onclick = () => { ta.value = ta.dataset.before; out.textContent = "Вернула ваш вариант."; };
  } finally { btn.disabled = false; }
};

// ---------- «Помощь»: логи остаются у человека, отчёт он отправляет сам ----------
$("#help-logs").onclick = () => fetch("/api/logs/open", { method: "POST" });
$("#help-report").onclick = async () => {
  const r = await (await fetch("/api/logs/report")).json();
  await navigator.clipboard.writeText(r.text);
  $("#help-out").textContent = "Скопировано. Вставьте в сообщение тому, кто помогает разобраться.";
};

// ---------- главная: приветствие, подсказки «что сказать», быстрые просьбы ----------
const QUICK_ASKS = [["🔮", "Погадай мне"], ["🌤", "Какая погода завтра?"], ["⏱", "Поставь таймер на 10 минут"],
  ["🍅", "Запусти фокус на 25 минут"], ["♟", "Сыграем в шахматы"], ["🙈", "Давай в прятки"],
  ["💱", "Какой курс доллара?"], ["❓", "Что ты умеешь?"]];
const TIPS = ["{name}, напомни через час выпить воды", "{name}, открой YouTube и включи лоу-фай",
  "{name}, что у меня сегодня по делам?", "{name}, переведи текст из буфера на английский",
  "{name}, сделай громкость 30", "{name}, найди в интернете новости про…", "{name}, разбуди меня в 7:30",
  "{name}, сделай команду: по фразе «работаем» открой почту и календарь", "{name}, скинь скриншот на телефон"];
async function greet() {
  state.cfg = await api("/api/config").catch(() => state.cfg);
  const h = new Date().getHours(), user = (state.cfg?.user_name || "").trim();
  const part = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
  const p = window.i18n?.t(part) ?? part;
  $("#hero-greet").textContent = user ? `${p}, ${user}!` : `${p}!`;
  const loc = state.cfg?.llm?.providers?.local || {};          // первый запуск: ни ключа, ни локальной модели
  state.noBrain = !!state.cfg && !state.cfg._secrets?.NVIDIA_API_KEY && !loc.model && !loc.model_path;
}
function askNow(text) {
  goto("chat");
  $("#input").value = text;
  $("#composer").requestSubmit();
}
$("#quick-asks").innerHTML = QUICK_ASKS.map(([i, t]) => `<button type="button" data-ask="${esc(t)}">${i} ${esc(t)}</button>`).join("");
// отправляем то, что видно на кнопке (на языке интерфейса), без эмодзи в начале
$("#quick-asks").onclick = (e) => { const b = e.target.closest("[data-ask]"); if (b) askNow(b.textContent.replace(/^[^\p{L}\d]+/u, "").trim()); };
let tipN = Math.floor(Math.random() * TIPS.length);
function nextTip() {
  const el = $("#hero-tip");
  el.style.opacity = 0;
  setTimeout(() => {
    el.innerHTML = state.noBrain ? `Сначала подключите «мозг»: <b class="tip-link" data-goto="help">ключ NVIDIA или локальная модель</b>`
      : `Скажите: <b>«${esc(TIPS[tipN++ % TIPS.length])}»</b>`;
    el.style.opacity = 1; window.applyName?.(state.cfg?.assistant_name);
  }, 400);
}
greet().then(nextTip); setInterval(nextTip, 9000);
setInterval(greet, 60000);

// комната «Домой» — бета, видна только с флагом features.room (сборка разработчика)
api("/api/config").then((c) => { $("#room-section").hidden = !c.features?.room; }).catch(() => {});

// ---------- выбор микрофона: список устройств Windows; «как в Windows» — следовать за основным ----------
async function loadMics() {
  const r = await api("/api/voice/devices").catch(() => null);
  const sel = $("#mic-select");
  if (!r || !sel) return;
  sel.querySelectorAll("option:not([value=''])").forEach((o) => o.remove());
  for (const n of r.devices) sel.insertAdjacentHTML("beforeend", `<option value="${esc(n)}">${esc(n)}</option>`);
  if (r.current && !r.devices.includes(r.current)) sel.insertAdjacentHTML("beforeend", `<option value="${esc(r.current)}">${esc(r.current)} (не подключён)</option>`);
  sel.value = r.current;
}
loadMics();
$$('.tab[data-page="settings"]').forEach((t) => t.addEventListener("click", loadMics));

$("#lines-regen").onclick = async () => {
  $("#lines-out").textContent = "сохраняю настройки…";
  await $("#settings").requestSubmit?.();                   // число вариантов — из поля, даже если не нажали «Сохранить»
  const r = await api("/api/lines/regenerate", { method: "POST", body: {} })
    .catch(() => ({ error: "ядро не знает этой кнопки — перезапустите программу целиком (значок в трее → «Выход»)" }));
  if (!r.ok) { $("#lines-out").textContent = r.error || "не вышло"; return; }
  $("#lines-regen").disabled = true;
  $("#lines-out").textContent = "пишу реплики под характер…";
};
// ---------- язык: меняется сразу, окна перечитываются (словари — ui/lang/*.json) ----------
api("/api/config").then((c) => { $("#lang-select").value = c.ui?.lang || "auto"; }).catch(() => {});
$("#lang-select").onchange = async (e) => {
  await api("/api/config", { method: "POST", body: { ui: { lang: e.target.value } } });
  location.reload();
};

// ---------- модули: что поставить под свой ПК ----------
const MOD_STATUS = { installed: "установлен", missing: "не установлен", off: "выключен", busy: "работаю…" };
async function loadModules() {
  const mods = await api("/api/modules");
  const groups = {};
  for (const m of mods) (groups[m.group] ||= []).push(m);
  $("#modules-list").innerHTML = Object.entries(groups).map(([g, ms]) => `<h3 class="modules-group">${esc(g)}</h3>
    <div class="plugin-grid">${ms.map((m) => {
      const on = m.status === "installed";
      const btn = m.status === "busy" ? `<button class="btn" disabled>…</button>`
        : m.toggle ? `<button class="btn" data-mod="${m.id}" data-act="${on ? "remove" : "install"}">${on ? "Выключить" : "Включить"}</button>`
        : on ? `<button class="btn" data-mod="${m.id}" data-act="remove">Удалить</button>`
        : `<button class="btn primary" data-mod="${m.id}" data-act="install">Скачать${m.size ? ` · ${m.size >= 1000 ? (m.size / 1000).toFixed(1) + " ГБ" : m.size + " МБ"}` : ""}</button>`;
      return `<div class="plugin-card glass module ${m.status}" id="mod-${m.id}">
        <h3>${esc(m.name)} <span class="mod-status">${MOD_STATUS[m.status] || m.status}</span></h3>
        <p>${esc(m.desc)}</p>
        <div class="mod-meta">Нагрузка: ${esc(m.needs)}<br>Лицензия: ${esc(m.license)} · ${esc(m.source)}</div>
        <div class="mod-progress hidden"><div class="bar"><i></i></div><span></span></div>
        ${btn}</div>`;
    }).join("")}</div>`).join("");
}
$("#modules-list").onclick = async (e) => {
  const b = e.target.closest("[data-mod]");
  if (!b) return;
  if (b.dataset.act === "remove" && !b.textContent.startsWith("Выкл") && !confirm("Удалить модуль и освободить место?")) return;
  b.disabled = true;
  await api(`/api/modules/${b.dataset.mod}/${b.dataset.act}`, { method: "POST" });
  setTimeout(loadModules, 300);
};
function moduleProgress(m) {
  const card = document.getElementById(`mod-${m.id}`);
  if (card) {
    const p = card.querySelector(".mod-progress");
    p.classList.remove("hidden");
    p.querySelector("span").textContent = m.text || "";
    p.querySelector("i").style.width = m.pct == null ? "100%" : `${m.pct}%`;
    p.classList.toggle("indet", m.pct == null);
  }
  if (m.done) {
    if (m.error) toast("Модуль", m.text, "error");
    else if (m.restart) toast("Модуль", "Перезапустите программу, чтобы изменение заработало.");
    if (state.page === "modules") setTimeout(loadModules, 600);
  }
}

// ---------- мастер первого запуска: имя, характер, модель, «мозг» ----------
// Пока он не пройден, ядро молчит само по себе, а окно аватара не показывается (shell/main.js).
const setup = { step: 1, model: "" };
function setupShow(n) {
  setup.step = n;
  $$("#setup .setup-step").forEach((s) => s.classList.toggle("hidden", +s.dataset.step !== n));
  $("#setup-step-n").textContent = `· ${n} / 4`;
  $("#setup-back").classList.toggle("hidden", n === 1);
  $("#setup-next").textContent = n === 4 ? "Готово" : "Дальше";
  $("#setup-err").textContent = "";
  if (n === 3) setupModels();
}
async function setupModels() {
  const ms = await api("/api/models").catch(() => []);
  if (!setup.model) setup.model = (ms.find((m) => m.current) || ms[0])?.name || "";
  $("#setup-models").innerHTML = ms.map((m) => `<button type="button" class="setup-model ${m.name === setup.model ? "on" : ""}" data-model="${esc(m.name)}">
    ${m.thumb ? `<img src="${m.thumb}" alt="">` : "👤"}<div>${esc(m.title || m.name)}</div></button>`).join("")
    || '<div class="muted">Моделей нет — добавьте свою .vrm.</div>';
}
$("#setup-models").onclick = (e) => { const b = e.target.closest("[data-model]"); if (b) { setup.model = b.dataset.model; setupModels(); } };
$("#setup-upload").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  const r = await fetch("/api/models", { method: "POST", body: fd }).then((x) => x.json()).catch(() => ({}));
  if (r.ok) setup.model = r.name || f.name; else $("#setup-err").textContent = r.error || "Не получилось добавить модель";
  setupModels();
};
$$('#setup [name="setup-persona"]').forEach((r) => (r.onchange = () => $("#setup-custom").classList.toggle("hidden", r.value !== "custom" || !r.checked)));
$("#setup-lang").onchange = async (e) => {                 // язык — сразу: окна перечитываются, мастер откроется снова
  await api("/api/config", { method: "POST", body: { ui: { lang: e.target.value } } });
  location.reload();
};
$("#setup-detect").onclick = async () => {
  const out = $("#setup-detect-out");
  out.textContent = "ищу…";
  const found = await (await fetch("/api/llm/detect", { method: "POST" })).json().catch(() => []);
  const first = found.find((f) => f.models.length);
  if (!first) { out.textContent = "Ничего не запущено. Откройте Ollama или LM Studio с загруженной моделью."; return; }
  setup.local = { base_url: first.base_url, model: first.models[0] };
  out.textContent = `Нашла: ${first.app} (${first.models[0]}).`;
};
$("#setup-back").onclick = () => setupShow(Math.max(1, setup.step - 1));
$("#setup-next").onclick = async () => {
  const name = $("#setup-name").value.trim();
  if (setup.step === 1 && !name) { $("#setup-err").textContent = "Придумайте ей имя — по нему она откликается."; return; }
  if (setup.step < 4) return setupShow(setup.step + 1);
  const persona = $('#setup [name="setup-persona"]:checked').value, key = $("#setup-key").value.trim();
  const patch = {
    assistant_name: name, user_name: $("#setup-user").value.trim(), city: $("#setup-city").value.trim(), persona,
    voice: { wake_word: name.toLowerCase() }, setup_done: true,
  };
  const address = $("#setup-address").value.trim();
  if (address) patch.address = address;
  if (persona === "custom") patch.persona_custom = $("#setup-custom").value.trim();
  if (setup.model) patch.avatar = { model: setup.model };
  if (setup.local) patch.llm = { providers: { local: { ...setup.local, model_path: "" } }, ...(key ? {} : { primary: "local", fallback: "" }) };
  if (key) patch._secrets = { NVIDIA_API_KEY: key };
  await api("/api/config", { method: "POST", body: patch });
  $("#setup").classList.add("hidden");
  window.applyName?.(name);
  greet();
  toast("Готово", `${name} уже идёт на рабочий стол. Голос — по желанию, во вкладке «Модули».`);
};
api("/api/config").then((c) => {
  if (c.setup_done !== false) return;
  $("#setup-lang").value = (c.ui?.lang && c.ui.lang !== "auto") ? c.ui.lang : (window.i18n?.lang || "ru");
  $("#setup-address").value = c.address || "";
  $("#setup").classList.remove("hidden");
  setupShow(1);
}).catch(() => {});
