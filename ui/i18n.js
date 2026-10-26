// Язык интерфейса и имя персонажа — в одном месте, потому что порядок важен.
// Разметка и скрипты написаны по-русски, вместо имени помощницы — метка {name}. Здесь каждая видимая строка сначала
// переводится по словарю ui/lang/<язык>.json (точное совпадение; «{}» в ключе — любая подстановка),
// а потом {name} заменяется на имя из настроек (до мастера первого запуска — Shikigami). Новые строки (чат, уведомления, панели)
// ловит MutationObserver. Словарь пополняется скриптом devtools/i18n_extract.py.
(() => {
  const BASES = ["{name}"];
  const ATTRS = ["placeholder", "title", "aria-label"];
  // пользовательский текст не трогаем: сообщения чата, заметки, история буфера, поля ввода
  const SKIP = "#messages, #chats, #list-items, textarea, input, select option[data-raw], .no-i18n";
  const orig = new WeakMap();          // узел → исходный текст (чтобы пересчитать при смене имени/языка)
  let name = "Shikigami", dict = null, patterns = [];

  const norm = (s) => s.replace(/\s+/g, " ").trim();
  // «{} из {}» или «в {}» поймали бы любую фразу с этим словом — шаблону нужно хотя бы 4 своих буквы
  const UNITS = new Set(["{} МБ", "{} ГБ", "{}/{} ГБ", "{} мс!", "{} мин", "{} → {} шаг.", "Экран {} — {}", "Экран {} — {} (основной)"]);
  const usefulPattern = (k) => UNITS.has(k) || k.replace(/\{\}/g, "").replace(/[^\p{L}]/gu, "").length >= 4;
  function translate(text) {
    if (!dict) return text;
    const key = norm(text);
    if (!key) return text;
    let out = dict[key];
    if (out == null) {                    // «🔮 Погадай мне», «{name}, …» в кавычках — переводим середину, края оставляем
      const pre = key.match(/^[^\p{L}\d{]*/u)[0], core = key.slice(pre.length);   // «{» — начало метки {name}, не край
      if (dict[core]) out = pre + dict[core];
      else {
        const m = core.match(/^(.*?)([^\p{L}\d]*)$/u);
        if (m[1] && dict[m[1]]) out = pre + dict[m[1]] + m[2];
      }
    }
    if (out == null && key.includes(" · ")) {   // «автор · VRM 0.x · 16 МБ» — по частям
      const parts = key.split(" · "), tr = parts.map((p) => translate(p).trim());
      if (tr.some((t, i) => t !== parts[i])) out = tr.join(" · ");
    }
    if (out == null) {
      for (const [rx, tpl] of patterns) {
        const m = key.match(rx);
        if (m) { let i = 1; out = tpl.replace(/\{\}/g, () => m[i++]); break; }
      }
    }
    if (!out) return text;
    const lead = text.match(/^\s*/)[0], trail = text.match(/\s*$/)[0];
    return lead + out + trail;
  }
  function withName(text) {
    if (!name) return text;
    for (const b of BASES) if (b !== name && text.includes(b)) text = text.split(b).join(name);
    return text;
  }
  const render = (text) => withName(translate(text));

  function fixText(node) {
    if (node.parentElement?.closest(SKIP)) return;
    if (!orig.has(node)) orig.set(node, node.nodeValue);
    const want = render(orig.get(node));
    if (node.nodeValue !== want) { node.__self = true; node.nodeValue = want; }
  }
  function fixEl(el) {
    if (el.closest?.(SKIP) && !/^(INPUT|TEXTAREA)$/.test(el.tagName)) return;
    for (const a of ATTRS) {
      const v = el.getAttribute?.(a);
      if (!v) continue;
      const key = "data-orig-" + a;
      if (!el.hasAttribute(key)) el.setAttribute(key, v);
      const want = render(el.getAttribute(key));
      if (v !== want) el.setAttribute(a, want);
    }
  }
  function walk(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) return fixText(root);
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) fixEl(root);
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) { if (!/^(SCRIPT|STYLE)$/.test(n.parentElement?.tagName)) fixText(n); }
      else fixEl(n);
    }
  }
  function refresh() {
    walk(document.body);
    if (!document.title.includes("аватар")) {            // по заголовку окна аватара ядро находит его — не трогаем
      document.__titleOrig ??= document.title;
      document.title = render(document.__titleOrig);
    }
  }

  window.applyName = (n) => { n = (n || "").trim() || "Shikigami"; if (n === name) return; name = n; refresh(); };
  window.i18n = { t: render, get lang() { return dict ? "en" : "ru"; } };

  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "characterData") {
        if (m.target.__self) { m.target.__self = false; continue; }
        orig.set(m.target, m.target.nodeValue);          // текст поменял скрипт — это новый исходник
        fixText(m.target);
      } else m.addedNodes.forEach(walk);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  async function start() {
    const cfg = await fetch("/api/config").then((r) => r.json()).catch(() => ({}));
    let lang = cfg.ui?.lang || "auto";
    if (lang === "auto") lang = /^(ru|uk|be|kk)/i.test(navigator.language) ? "ru" : "en";
    document.documentElement.lang = lang;
    if (lang !== "ru") {
      const d = await fetch(`/ui/lang/${lang}.json`).then((r) => r.json()).catch(() => null);
      if (d) {
        dict = {};
        for (const [k, v] of Object.entries(d)) {
          if (!v) continue;
          if (k.includes("{}") && !usefulPattern(k)) continue;
          if (k.includes("{}")) {
            const rx = new RegExp("^" + k.split("{}").map((p) => p.replace(/[.*+?^$()|[\]\\]/g, "\\$&")).join("(.+?)") + "$");
            patterns.push([rx, v]);
          } else dict[k] = v;
        }
      }
    }
    name = (cfg.assistant_name || "").trim() || "Shikigami";
    refresh();
  }
  start();
})();
