// Shikigami site — shared script: nav/footer, language, themes, reveal, lightbox, stats, visit/click counter.
(function () {
  // Visit and click counter (GoatCounter: free, no cookies). Put the code from goatcounter.com here, e.g. 'shikigami'.
  // Empty = counter off, nothing is loaded.
  var GOATCOUNTER = '';

  var REPO = 'Timkin1337/Shikigami';
  var L = {
    gh: 'https://github.com/' + REPO,
    rel: 'https://github.com/' + REPO + '/releases/latest',
    itch: 'https://timkin-died.itch.io/shikigami',
    tg: 'https://t.me/shikigami_ai',
    mail: 'mailto:timkin.dev@gmail.com'
  };
  window.LINKS = L;
  var h = document.documentElement;
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  // ---------- language ----------
  var lang = store('lang') || (/^(ru|uk|be|kk)/i.test(navigator.language || '') ? 'ru' : 'en');
  h.lang = lang;
  function setLang(x) { h.lang = x; store('lang', x); var b = document.getElementById('lang'); if (b) b.textContent = x == 'ru' ? 'EN' : 'RU'; document.dispatchEvent(new Event('langchange')); }

  // ---------- theme ----------
  var THEMES = ['amber', 'sakura', 'night'];
  var THEME_ICON = { amber: '🦊', sakura: '🌸', night: '🌙' };
  var theme = store('theme') || 'amber';
  h.dataset.theme = theme;
  function cycleTheme() {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    h.dataset.theme = theme; store('theme', theme);
    var b = document.getElementById('theme'); if (b) b.textContent = THEME_ICON[theme];
    track('theme-' + theme);
    document.dispatchEvent(new Event('themechange'));
  }

  // ---------- nav + footer ----------
  var PAGES = [
    ['index.html', 'Главная', 'Home'],
    ['features.html', 'Возможности', 'Features'],
    ['about.html', 'Как устроено', 'Inside'],
    ['changelog.html', 'Версии', 'Changelog'],
    ['reviews.html', 'Отзывы', 'Feedback'],
    ['faq.html', 'Вопросы', 'FAQ']
  ];
  function t(ru, en) { return '<span class="ru">' + ru + '</span><span class="en">' + en + '</span>'; }
  window.t = t;
  var here = (location.pathname.split('/').pop() || 'index.html');
  function renderChrome() {
    var nav = document.querySelector('nav.top');
    if (nav) nav.innerHTML = '<div class="wrap"><a class="logo" href="index.html"><img src="img/favicon-256.png" alt="">Shikigami</a>' +
      '<button class="chip" id="burger" aria-label="Menu">☰</button><div class="menu" id="menu">' +
      PAGES.map(function (p) { return '<a href="' + p[0] + '"' + (p[0] == here ? ' class="on"' : '') + '>' + t(p[1], p[2]) + '</a>'; }).join('') +
      '</div><div class="tools"><button class="chip" id="theme" title="Theme">' + THEME_ICON[theme] + '</button>' +
      '<button class="chip" id="lang" title="Language">' + (h.lang == 'ru' ? 'EN' : 'RU') + '</button></div></div>';
    var f = document.querySelector('footer');
    if (f) f.innerHTML = '<div class="wrap"><div class="cols">' +
      '<div><a class="logo" href="index.html" style="display:flex"><img src="img/favicon-256.png" alt="">Shikigami 式神</a><p>' +
      t('ИИ-компаньон на рабочем столе Windows. Бесплатно, с открытым кодом, без телеметрии.', 'An AI companion for your Windows desktop. Free, open source, no telemetry.') + '</p></div>' +
      '<div><h4>' + t('Скачать', 'Download') + '</h4><a href="' + L.rel + '">GitHub Releases</a><a href="' + L.itch + '">itch.io</a></div>' +
      '<div><h4>' + t('Сообщество', 'Community') + '</h4><a href="' + L.tg + '">Telegram</a><a href="' + L.gh + '">GitHub</a><a href="' + L.gh + '/issues">Issues</a><a href="' + L.mail + '">' + t('Почта', 'Email') + '</a></div>' +
      '<div><h4>' + t('Сайт', 'Site') + '</h4>' + PAGES.slice(1).map(function (p) { return '<a href="' + p[0] + '">' + t(p[1], p[2]) + '</a>'; }).join('') + '</div>' +
      '</div><small>' + t('Код — GNU AGPL-3.0. Модель Kitsune Maid — Shikigami project, VRM Public License 1.0 (некоммерческое использование, с указанием автора). Движения и голоса Silero — только некоммерческое использование, подробно в ',
        'Code — GNU AGPL-3.0. Kitsune Maid model — Shikigami project, VRM Public License 1.0 (non-commercial, attribution). Motions and Silero voices are non-commercial, see ') +
      '<a style="display:inline" href="' + L.gh + '/blob/main/CREDITS.md">CREDITS.md</a>.' +
      (GOATCOUNTER ? ' ' + t('Сайт считает визиты и клики через GoatCounter: без cookies и без личных данных.', 'This site counts visits and clicks with GoatCounter: no cookies, no personal data.') : '') +
      '</small></div>';
    var burger = document.getElementById('burger');
    if (burger) burger.onclick = function () { document.getElementById('menu').classList.toggle('open'); };
    document.getElementById('lang').onclick = function () { setLang(h.lang == 'ru' ? 'en' : 'ru'); track('lang-' + h.lang); };
    document.getElementById('theme').onclick = cycleTheme;
  }

  // ---------- counter (GoatCounter) ----------
  function kind(href) {
    if (/t\.me\//.test(href)) return 'tg';
    if (/itch\.io/.test(href)) return 'itch';
    if (/releases/.test(href)) return 'download-github';
    if (/github\.com/.test(href)) return 'github';
    if (/^mailto:/.test(href)) return 'mail';
    return null;
  }
  function track(name) {
    if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: name, title: name, event: true });
  }
  window.track = track;
  function startCounter() {
    if (!GOATCOUNTER) return;
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      var k = kind(a.getAttribute('href'));
      if (k) track('click-' + k + '-' + here.replace('.html', ''));
    }, true);
    var s = document.createElement('script');
    s.async = true; s.src = 'https://gc.zgo.at/count.js';
    s.dataset.goatcounter = 'https://' + GOATCOUNTER + '.goatcounter.com/count';
    document.head.appendChild(s);
  }

  // ---------- toast ----------
  function toast(html, ms) {
    var el = document.getElementById('toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
    el.innerHTML = html; el.classList.add('show');
    clearTimeout(el._t); el._t = setTimeout(function () { el.classList.remove('show'); }, ms || 3200);
  }
  window.toast = toast;

  // ---------- effects ----------
  function effects() {
    var bar = document.createElement('div'); bar.id = 'progress'; document.body.appendChild(bar);
    addEventListener('scroll', function () {
      var m = document.documentElement.scrollHeight - innerHeight;
      bar.style.width = (m > 0 ? scrollY / m * 100 : 0) + '%';
    }, { passive: true });

    var io = 'IntersectionObserver' in window && new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { threshold: .12 });
    document.querySelectorAll('.rv').forEach(function (el, i) {
      if (!el.style.transitionDelay && el.parentElement && el.parentElement.classList.contains('grid'))
        el.style.transitionDelay = (Array.prototype.indexOf.call(el.parentElement.children, el) % 4) * 80 + 'ms';
      io ? io.observe(el) : el.classList.add('in');
    });

    var fine = matchMedia('(pointer:fine)').matches && !matchMedia('(prefers-reduced-motion:reduce)').matches;
    if (fine) document.querySelectorAll('[data-tilt]').forEach(function (el) {
      el.addEventListener('mousemove', function (e) {
        var r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
        el.style.transform = 'perspective(800px) rotateY(' + x * 8 + 'deg) rotateX(' + -y * 8 + 'deg) translateY(-4px)';
      });
      el.addEventListener('mouseleave', function () { el.style.transform = ''; });
    });

    var lb = document.createElement('dialog'); lb.id = 'lb'; lb.innerHTML = '<img alt=""><p></p>'; document.body.appendChild(lb);
    lb.onclick = function () { lb.close(); };
    document.addEventListener('click', function (e) {
      var im = e.target.closest && e.target.closest('img.zoom');
      if (!im || !lb.showModal) return;
      lb.querySelector('img').src = im.dataset.full || im.src;
      lb.querySelector('p').textContent = im.alt;
      lb.showModal(); track('zoom');
    });

    document.querySelectorAll('[data-n]').forEach(function (el) { countUp(el, +el.dataset.n); });
  }
  function countUp(el, n) {
    var t0 = performance.now();
    (function f(tm) { var k = Math.min(1, (tm - t0) / 1400); el.textContent = Math.round(n * (1 - Math.pow(1 - k, 3))).toLocaleString(); if (k < 1) requestAnimationFrame(f); })(t0);
  }
  window.countUp = countUp;

  // ---------- project numbers: stats.json (GitHub + itch + Telegram), refreshed by stats.py every 3 hours ----------
  function ghStats() {
    var els = document.querySelectorAll('[data-stat]');
    if (!els.length) return;
    fetch('stats.json', { cache: 'no-cache' }).then(function (r) { return r.json(); }).then(function (d) {
      window.STATS = d;
      els.forEach(function (el) {
        var v = el.dataset.stat.split('.').reduce(function (o, k) { return o == null ? o : o[k]; }, d);
        if (v === undefined || v === null || v === '') return;
        if (typeof v == 'number') countUp(el, v); else el.textContent = v;
      });
      document.dispatchEvent(new Event('stats'));
    }).catch(function () { });
  }

  // ---------- easter egg: Konami code ----------
  var K = [38, 38, 40, 40, 37, 39, 37, 39, 66, 65], ki = 0;
  addEventListener('keydown', function (e) {
    ki = e.keyCode === K[ki] ? ki + 1 : (e.keyCode === K[0] ? 1 : 0);
    if (ki === K.length) {
      ki = 0; track('konami');
      document.dispatchEvent(new Event('konami'));
      toast(t('🦊 Секрет найден! Сайт сменил шкурку.', '🦊 Secret found! The site changed its skin.'));
      cycleTheme();
    }
  });

  function init() { renderChrome(); effects(); ghStats(); startCounter(); }
  if (document.readyState == 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
