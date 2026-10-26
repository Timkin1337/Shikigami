// Визуальный редактор команд: узлы (триггеры, условия, действия) и связи между ними.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = (p, o = {}) => fetch(p, { headers: { "Content-Type": "application/json" }, ...o,
  body: o.body && JSON.stringify(o.body) }).then((r) => r.json());

const PORT_NAMES = { out: "", true: "да", false: "нет", body: "тело", done: "после" };
const CHECK_NAMES = { process_running: "процесс запущен", window_open: "окно открыто", time_between: "время между (09:00-18:00)",
  clipboard_contains: "в буфере есть текст", var_equals: "переменная равна (x=1)", ai_yes: "ИИ ответит «да» на вопрос" };
const NODE_W = 200;

let types = null, checks = [], cmd = null, selected = null, inited = false;
let pan = { x: 0, y: 0 }, zoom = 1, drag = null, link = null, dirty = false;

export async function initCommands() {
  if (!inited) {
    inited = true;
    const t = await api("/api/commands/types");
    types = t.types; checks = t.if_checks;
    buildPalette();
    bindCanvas();
    bindToolbar();
  }
  await loadList(cmd?.id);
}

export function onCommandEvent(m) {
  if (m.type === "commands.changed" && inited) loadList(cmd?.id, true);
  if (m.type === "command.step") {
    const el = document.querySelector(`.node[data-id="${m.node}"]`);
    if (el) { el.classList.add("running"); setTimeout(() => el.classList.remove("running"), 600); }
  }
}

// ---------- список ----------
async function loadList(selectId, keepCurrent) {
  const list = await api("/api/commands");
  const groups = {};
  for (const c of list) (groups[c.grp || "Без группы"] ??= []).push(c);
  $("#cmd-groups").innerHTML = Object.entries(groups).map(([g, cs]) => `<div class="cmd-group-title">${esc(g)}</div>` +
    cs.map((c) => `<div class="cmd-item ${c.id === cmd?.id ? "active" : ""}" data-id="${c.id}">
      <div>${c.enabled ? "" : "⏸ "}${esc(c.name)}</div><div class="muted">${esc(summary(c))}</div></div>`).join("")).join("");
  if (keepCurrent && cmd) return;
  const pick = list.find((c) => c.id === selectId) || list[0];
  pick ? open(pick) : open(blank());
}
function summary(c) {
  const t = c.graph.nodes.filter((n) => n.type.startsWith("trigger."));
  const acts = c.graph.nodes.length - t.length;
  return t.map((n) => n.params.accelerator || n.params.phrases?.split(";")[0] || n.params.time || types?.[n.type]?.title).join(" · ") + ` → ${acts} шаг.`;
}
$("#cmd-groups").onclick = async (e) => {
  const it = e.target.closest(".cmd-item");
  if (!it) return;
  if (dirty && !confirm("Есть несохранённые изменения. Перейти без сохранения?")) return;
  const list = await api("/api/commands");
  open(list.find((c) => c.id === +it.dataset.id));
};
function blank() {
  return { id: null, name: "Новая команда", grp: "", enabled: 1,
    graph: { nodes: [{ id: "n0", type: "trigger.phrase", params: { phrases: "" }, x: 60, y: 120 }], edges: [] } };
}
function open(c) {
  cmd = structuredClone(c);
  selected = null; dirty = false; pan = { x: 0, y: 0 }; zoom = 1;
  $("#cmd-name").value = cmd.name;
  $("#cmd-group").value = cmd.grp || "";
  $("#cmd-enabled").checked = !!cmd.enabled;
  document.querySelectorAll(".cmd-item").forEach((i) => i.classList.toggle("active", +i.dataset.id === cmd.id));
  render();
  inspect();
}

// ---------- палитра ----------
function buildPalette() {
  const groups = { trigger: "Триггеры", flow: "Управление", action: "Действия" };
  // группы — отдельными блоками с цветной меткой; сверху поиск по названию узла
  const ICON = { trigger: "⚡", flow: "⑂", action: "▶" };
  $("#palette").innerHTML = `<input class="pal-search" id="pal-search" placeholder="Найти узел…">` +
    Object.entries(groups).map(([g, title]) => `<div class="pal-group g-${g}"><div class="pal-group-title">${ICON[g]} ${title}</div>` +
      Object.entries(types).filter(([, t]) => t.group === g)
        .map(([k, t]) => `<div class="pal-item g-${g}" draggable="true" data-type="${k}" title="Клик — добавить, или перетащите на поле">${esc(t.title)}</div>`).join("") +
      `</div>`).join("");
  $("#pal-search").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    document.querySelectorAll("#palette .pal-item").forEach((it) => { it.style.display = !q || it.textContent.toLowerCase().includes(q) ? "" : "none"; });
    document.querySelectorAll("#palette .pal-group").forEach((gr) => { gr.style.display = [...gr.querySelectorAll(".pal-item")].some((i) => i.style.display !== "none") ? "" : "none"; });
  });
  $("#palette").addEventListener("dragstart", (e) => e.dataTransfer.setData("text/node-type", e.target.dataset.type));
  $("#palette").addEventListener("click", (e) => {
    const it = e.target.closest(".pal-item");
    if (!it) return;
    const r = $("#canvas").getBoundingClientRect();
    addNode(it.dataset.type, (r.width / 2 - pan.x) / zoom - NODE_W / 2, (r.height / 2 - pan.y) / zoom - 40);
  });
}
function addNode(type, x, y) {
  const id = "n" + Math.random().toString(36).slice(2, 7);
  cmd.graph.nodes.push({ id, type, params: structuredClone(types[type].params), x: Math.round(x), y: Math.round(y) });
  // Новый узел сразу цепляем за выделенный — так быстрее собирать цепочки
  if (selected && !type.startsWith("trigger.")) {
    const from = node(selected);
    const port = types[from.type].outputs[0];
    if (!cmd.graph.edges.some((e) => e.from === selected && e.port === port)) cmd.graph.edges.push({ from: selected, port, to: id });
  }
  selected = id; dirty = true;
  render(); inspect();
}
const node = (id) => cmd.graph.nodes.find((n) => n.id === id);

// ---------- отрисовка ----------
function preview(n) {
  const p = n.params || {};
  if (n.type === "flow.if") return `${CHECK_NAMES[p.check] || p.check}: ${p.value || ""}`;
  return Object.values(p).filter((v) => v !== "").join(" · ").slice(0, 70);
}
function render() {
  const wrap = $("#nodes");
  wrap.style.transform = $("#edges").style.transform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`;
  wrap.style.transformOrigin = $("#edges").style.transformOrigin = "0 0";
  wrap.innerHTML = cmd.graph.nodes.map((n) => {
    const t = types[n.type] || { title: n.type, outputs: ["out"], group: "action" };
    const outs = t.outputs.map((o, i) => `<div class="port out" data-port="${o}" style="top:${34 + i * 22}px">
      ${PORT_NAMES[o] ? `<span class="port-label">${PORT_NAMES[o]}</span>` : ""}</div>`).join("");
    return `<div class="node g-${t.group} ${n.id === selected ? "selected" : ""}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px;width:${NODE_W}px">
      ${t.group !== "trigger" ? '<div class="port in"></div>' : ""}
      <div class="node-head">${esc(t.title)}</div><div class="node-body">${esc(preview(n)) || "&nbsp;"}</div>${outs}</div>`;
  }).join("");
  drawEdges();
}
function portPos(nodeId, port) {
  const el = document.querySelector(`.node[data-id="${nodeId}"]`);
  if (!el) return null;
  const n = node(nodeId);
  if (port === "in") return { x: n.x, y: n.y + 22 };
  const p = el.querySelector(`.port.out[data-port="${port}"]`);
  return { x: n.x + NODE_W, y: n.y + (p ? p.offsetTop + 6 : 22) };
}
function curve(a, b) {
  const dx = Math.max(40, Math.abs(b.x - a.x) / 2);
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}
function drawEdges() {
  const svg = $("#edges");
  svg.setAttribute("width", 4000); svg.setAttribute("height", 3000);
  svg.style.overflow = "visible";
  svg.innerHTML = cmd.graph.edges.map((e, i) => {
    const a = portPos(e.from, e.port), b = portPos(e.to, "in");
    return a && b ? `<path class="edge" data-i="${i}" d="${curve(a, b)}" style="pointer-events:stroke;cursor:pointer"><title>Щёлкните, чтобы удалить связь</title></path>` : "";
  }).join("") + (link ? `<path class="edge temp" d="${curve(link.a, link.b)}"/>` : "");
}

// ---------- мышь ----------
function toWorld(e) {
  const r = $("#canvas").getBoundingClientRect();
  return { x: (e.clientX - r.left - pan.x) / zoom, y: (e.clientY - r.top - pan.y) / zoom };
}
function bindCanvas() {
  const cv = $("#canvas");
  cv.addEventListener("mousedown", (e) => {
    const out = e.target.closest(".port.out");
    const nd = e.target.closest(".node");
    if (out) {
      const id = out.closest(".node").dataset.id;
      link = { from: id, port: out.dataset.port, a: portPos(id, out.dataset.port), b: toWorld(e) };
      return;
    }
    if (e.target.closest(".edge")) {
      cmd.graph.edges.splice(+e.target.closest(".edge").dataset.i, 1);
      dirty = true; drawEdges(); return;
    }
    if (nd) {
      selected = nd.dataset.id;
      const n = node(selected), w = toWorld(e);
      drag = { kind: "node", id: selected, dx: w.x - n.x, dy: w.y - n.y };
      document.querySelectorAll(".node").forEach((x) => x.classList.toggle("selected", x === nd));
      inspect();
      return;
    }
    selected = null; inspect();
    document.querySelectorAll(".node.selected").forEach((x) => x.classList.remove("selected"));
    drag = { kind: "pan", sx: e.clientX - pan.x, sy: e.clientY - pan.y };
  });
  window.addEventListener("mousemove", (e) => {
    if (link) { link.b = toWorld(e); drawEdges(); return; }
    if (!drag) return;
    if (drag.kind === "pan") { pan = { x: e.clientX - drag.sx, y: e.clientY - drag.sy }; render(); return; }
    const n = node(drag.id), w = toWorld(e);
    n.x = Math.round((w.x - drag.dx) / 10) * 10; n.y = Math.round((w.y - drag.dy) / 10) * 10;
    const el = document.querySelector(`.node[data-id="${drag.id}"]`);
    el.style.left = n.x + "px"; el.style.top = n.y + "px";
    dirty = true; drawEdges();
  });
  window.addEventListener("mouseup", (e) => {
    if (link) {
      const target = e.target.closest?.(".node");
      const to = target?.dataset.id;
      if (to && to !== link.from && !node(to).type.startsWith("trigger.") &&
          !cmd.graph.edges.some((x) => x.from === link.from && x.port === link.port && x.to === to)) {
        cmd.graph.edges.push({ from: link.from, port: link.port, to });
        dirty = true;
      }
      link = null; drawEdges();
    }
    drag = null;
  });
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const nz = Math.min(1.6, Math.max(0.4, zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
    pan.x = mx - ((mx - pan.x) * nz) / zoom; pan.y = my - ((my - pan.y) * nz) / zoom; zoom = nz;
    render();
  }, { passive: false });
  cv.addEventListener("dragover", (e) => e.preventDefault());
  cv.addEventListener("drop", (e) => {
    const t = e.dataTransfer.getData("text/node-type");
    if (!t) return;
    const w = toWorld(e);
    addNode(t, w.x - NODE_W / 2, w.y - 20);
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Delete" && selected && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)
        && document.getElementById("page-commands").classList.contains("active")) removeSelected();
  });
}
function removeSelected() {
  cmd.graph.nodes = cmd.graph.nodes.filter((n) => n.id !== selected);
  cmd.graph.edges = cmd.graph.edges.filter((e) => e.from !== selected && e.to !== selected);
  selected = null; dirty = true;
  render(); inspect();
}

// ---------- свойства узла ----------
const FIELD_HINTS = {
  phrases: "Фразы через «;». {слово} — переменная: «найди на ютубе {запрос}»",
  accelerator: "Например Alt+Y, Ctrl+Shift+K, F8",
  time: "ЧЧ:ММ", days: "пн,вт,ср,чт,пт,сб,вс",
  prompt: "Можно {clipboard}, {time}, {date}, свои {переменные}. Ответ — в {ai}",
  text: "Можно {ai}, {clipboard}, {result}, {i} в цикле",
  seconds: "Секунд", count: "Сколько раз",
  args: "JSON с аргументами инструмента",
};
function inspect() {
  const box = $("#inspector");
  const n = selected && node(selected);
  if (!n) { box.innerHTML = '<div class="muted">Выберите узел. Узлы — из панели слева (клик или перетаскивание). Связь — тянуть от правого кружка к узлу. Связь удаляется щелчком, узел — клавишей Delete.</div>'; return; }
  const t = types[n.type];
  const fields = Object.entries(n.params || {}).map(([k, v]) => {
    if (n.type === "flow.if" && k === "check")
      return `<label>Проверка<select data-k="check">${checks.map((c) => `<option value="${c}" ${c === v ? "selected" : ""}>${CHECK_NAMES[c] || c}</option>`).join("")}</select></label>`;
    const long = ["prompt", "text", "command", "phrases", "args"].includes(k);
    const input = long ? `<textarea data-k="${k}" rows="3">${esc(v)}</textarea>` : `<input data-k="${k}" value="${esc(v)}">`;
    return `<label>${esc(k)}${input}<span class="muted">${esc(FIELD_HINTS[k] || "")}</span></label>`;
  }).join("");
  box.innerHTML = `<div class="node-head g-${t.group}">${esc(t.title)}</div>${fields || '<div class="muted">Без настроек</div>'}
    <button class="btn danger wide" id="node-del">Удалить узел</button>`;
  box.querySelectorAll("[data-k]").forEach((el) => (el.oninput = () => {
    n.params[el.dataset.k] = el.value; dirty = true;
    const body = document.querySelector(`.node[data-id="${n.id}"] .node-body`);
    if (body) body.textContent = preview(n);
  }));
  $("#node-del").onclick = removeSelected;
}

// ---------- панель инструментов ----------
function autoLayout() {
  const g = cmd.graph, level = {}, order = [];
  const roots = g.nodes.filter((n) => n.type.startsWith("trigger."));
  roots.forEach((r) => { level[r.id] = 0; order.push(r.id); });
  for (let i = 0; i < order.length; i++) {
    for (const e of g.edges.filter((e) => e.from === order[i])) {
      if (level[e.to] === undefined) { level[e.to] = level[order[i]] + 1; order.push(e.to); }
    }
  }
  const rows = {};
  for (const n of g.nodes) {
    const l = level[n.id] ?? 0;
    rows[l] = (rows[l] || 0) + 1;
    n.x = 60 + l * 260; n.y = 60 + (rows[l] - 1) * 140;
  }
  pan = { x: 0, y: 0 }; dirty = true; render();
}
function bindToolbar() {
  $("#cmd-new").onclick = () => open(blank());
  // шаблоны и журнал запусков (совет ИИ: простой сценарий — в один клик, всегда видно, что сработало)
  document.body.appendChild($("#cmd-modal"));     // у колонки стеклянный фон (backdrop-filter) — внутри неё fixed не на весь экран
  const modal = (title, html) => { $("#cmd-modal-title").textContent = title; $("#cmd-modal-body").innerHTML = html; $("#cmd-modal").classList.remove("hidden"); };
  const closeModal = () => $("#cmd-modal").classList.add("hidden");
  $("#cmd-modal-close").onclick = closeModal;
  $("#cmd-modal").onclick = (e) => { if (e.target.id === "cmd-modal") closeModal(); };
  addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  $("#cmd-templates").onclick = async () => {
    const ts = await api("/api/commands/templates");
    modal("Готовые сценарии — клик создаёт команду, потом её можно поправить",
      `<div class="tpl-grid">${ts.map((t, i) => `<button class="tpl" data-i="${i}"><span class="tpl-ico">${t.icon}</span><b>${esc(t.name)}</b>
        <span class="muted">${esc(t.desc)}</span><span class="tpl-grp">${esc(t.group)}</span></button>`).join("")}</div>`);
    $("#cmd-modal-body").onclick = async (e) => {
      const b = e.target.closest(".tpl"); if (!b) return;
      const r = await api("/api/commands/from-template", { method: "POST", body: { i: +b.dataset.i } });
      closeModal();
      window.shikigami?.reloadHotkeys?.();
      await loadList(r.id);
    };
  };
  $("#cmd-log").onclick = async () => {
    const log = await api("/api/commands/log");
    const fmt = (t) => new Date(t * 1000).toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const SRC = { voice: "голос", ui: "вручную", schedule: "по расписанию", startup: "при запуске", hotkey: "клавиша", ai: "ИИ", telegram: "Telegram" };
    modal("Журнал запусков", log.length ? `<ul class="items run-log">${log.map((r) => `<li class="${r.ok ? "" : "bad"}">
      <span class="text">${r.ok ? "✓" : "✗"} <b>${esc(r.name)}</b> <span class="muted">— ${esc(SRC[r.source] || r.source)}, ${r.sec} с</span>
      ${r.error ? `<br><span class="err">${esc(r.error)}</span>` : ""}</span><span class="muted">${fmt(r.at)}</span></li>`).join("")}</ul>`
      : '<p class="muted">Пока ничего не запускалось.</p>');
  };
  $("#cmd-layout").onclick = autoLayout;
  for (const id of ["#cmd-name", "#cmd-group"]) $(id).oninput = () => (dirty = true);
  $("#cmd-save").onclick = save;
  $("#cmd-run").onclick = async () => { if (dirty || !cmd.id) await save(); api(`/api/commands/${cmd.id}/run`, { method: "POST" }); };
  $("#cmd-delete").onclick = async () => {
    if (!cmd.id || !confirm(`Удалить команду «${cmd.name}»?`)) return;
    await api(`/api/commands/${cmd.id}`, { method: "DELETE" });
    cmd = null; loadList();
  };
}
async function save() {
  cmd.name = $("#cmd-name").value.trim() || "Без названия";
  cmd.grp = $("#cmd-group").value.trim();
  cmd.enabled = $("#cmd-enabled").checked ? 1 : 0;
  const r = await api("/api/commands", { method: "POST", body: cmd });
  cmd.id = r.id; dirty = false;
  window.shikigami?.reloadHotkeys?.();
}
