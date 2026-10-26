"""Приложения, сайты и окна."""
import difflib
import json
import os
import re
import subprocess
import time
import webbrowser
from pathlib import Path

import win32con
import win32gui
import win32process
import psutil

from . import tool, S

# Разговорные названия → то, что ищем в меню «Пуск», или прямая ссылка.
ALIASES = {
    "хром": "Google Chrome", "гугл хром": "Google Chrome", "браузер": "Google Chrome",
    "телега": "Telegram", "телеграм": "Telegram", "дискорд": "Discord", "стим": "Steam",
    "блокнот": "Notepad", "проводник": "File Explorer", "калькулятор": "Calculator",
    "диспетчер задач": "Task Manager", "параметры": "Settings", "настройки windows": "Settings",
    "ворд": "Word", "эксель": "Excel", "вс код": "Visual Studio Code", "vscode": "Visual Studio Code",
    "обс": "OBS Studio", "спотифай": "Spotify", "майнкрафт": "Minecraft",
}
SITES = {
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
    "гугл": "https://www.google.com", "вк": "https://vk.com", "вконтакте": "https://vk.com",
    "гитхаб": "https://github.com", "твич": "https://www.twitch.tv", "почта": "https://mail.google.com",
    "кинопоиск": "https://www.kinopoisk.ru", "википедия": "https://ru.wikipedia.org",
}
BUILTIN = {  # то, чего нет ярлыками в «Пуске»
    "Notepad": "notepad.exe", "File Explorer": "explorer.exe", "Calculator": "calc.exe",
    "Task Manager": "taskmgr.exe", "Settings": "ms-settings:", "Paint": "mspaint.exe",
    "Command Prompt": "cmd.exe", "PowerShell": "powershell.exe",
}

SAFE_SCHEMES = {"http", "https", "mailto", "tg", "steam", "spotify", "discord", "yandexmusic", "zoommtg", "ms-settings"}

_index = {"t": 0, "items": {}}


def _scan_index():
    items = {}
    roots = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ["USERPROFILE"]) / "Desktop", Path(r"C:\Users\Public\Desktop")]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() in (".lnk", ".url", ".appref-ms"):
                name = p.stem
                if re.search(r"uninstall|удал|readme|help|справка|website|веб-сайт", name, re.I):
                    continue
                items.setdefault(name, str(p))
    # Приложения из Магазина (UWP) знает только Get-StartApps
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        for a in json.loads(out.stdout.decode("utf-8", "replace") or "[]"):
            if a.get("AppID") and a.get("Name") and "!" in a["AppID"]:
                items.setdefault(a["Name"], "shell:AppsFolder\\" + a["AppID"])
    except Exception as e:
        print("Get-StartApps:", e)
    for k, v in BUILTIN.items():
        items.setdefault(k, v)
    _index.update(t=time.time(), items=items)


def app_index():
    if time.time() - _index["t"] > 600:
        _scan_index()
    return _index["items"]


def find_app(name):
    items = app_index()
    key = name.strip().lower()
    key = ALIASES.get(key, name).lower()
    names = list(items)
    lower = {n.lower(): n for n in names}
    if key in lower:
        return [lower[key]]
    subs = [n for n in names if key in n.lower()]
    if subs:
        return sorted(subs, key=len)[:5]
    return [lower[m] for m in difflib.get_close_matches(key, list(lower), n=5, cutoff=0.6)]


def launch(target):
    if target.startswith("shell:"):
        subprocess.Popen(["explorer.exe", target])
    else:
        os.startfile(target)


@tool("open_app", "Запустить программу или игру на ПК по названию (как в меню Пуск). "
      "Если найдено несколько вариантов — вернёт список, уточни у пользователя.",
      {"name": S("Название программы, например 'Telegram', 'Steam', 'блокнот'")}, ["name"])
def open_app(name):
    if name.strip().lower() in SITES:
        return open_url(SITES[name.strip().lower()])
    found = find_app(name)
    if not found:
        return {"error": f"не нашла программу «{name}» в меню Пуск"}
    best = found[0]
    if len(found) > 1 and best.lower() != ALIASES.get(name.lower(), name).lower():
        exact = [f for f in found if f.lower().startswith(name.lower())]
        if len(exact) != 1:
            return {"ambiguous": found, "hint": "уточни, какую из них запустить"}
        best = exact[0]
    launch(app_index()[best])
    return {"ok": True, "started": best}


@tool("open_url", "Открыть сайт или ссылку в браузере по умолчанию.",
      {"url": S("Полный адрес https://... или название сайта (ютуб, вк, гитхаб)")}, ["url"])
def open_url(url):
    u = url.strip()
    if u.lower() in SITES:
        u = SITES[u.lower()]
    elif not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        u = "https://" + u
    # webbrowser на Windows отдаёт адрес оболочке: file:///…/x.exe запустил бы программу
    scheme = u.split(":", 1)[0].lower()
    if scheme not in SAFE_SCHEMES:
        return {"error": f"ссылки вида «{scheme}:» не открываю — только сайты и приложения вроде tg:, steam:"}
    webbrowser.open(u)
    return {"ok": True, "opened": u}


SEARCH_ENGINES = {"google": "https://www.google.com/search?q={q}", "yandex": "https://yandex.ru/search/?text={q}",
                  "bing": "https://www.bing.com/search?q={q}", "duckduckgo": "https://duckduckgo.com/?q={q}"}


@tool("search_in_browser", "Открыть поиск в браузере пользователя (его поисковик из настроек), чтобы он сам посмотрел выдачу.",
      {"query": S("Что искать")}, ["query"])
def search_in_browser(query):
    from urllib.parse import quote_plus
    from ..config import config
    url = SEARCH_ENGINES.get(config.get("search_engine") or "google", SEARCH_ENGINES["google"]).format(q=quote_plus(query))
    webbrowser.open(url)
    return {"ok": True, "opened": url}


@tool("youtube", "Найти и открыть видео/музыку на YouTube по запросу.",
      {"query": S("Что искать, например 'lofi hip hop radio'")}, ["query"])
def youtube(query):
    from urllib.parse import quote_plus
    import urllib.request
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    # Пробуем сразу открыть первое видео, а не страницу поиска
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru"})
        html = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
        m = re.search(r'"videoId":"([\w-]{11})"', html)
        if m:
            url = "https://www.youtube.com/watch?v=" + m.group(1)
    except Exception:
        pass
    webbrowser.open(url)
    return {"ok": True, "opened": url}


def _windows():
    out = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip():
            try:
                pid = win32process.GetWindowThreadProcessId(h)[1]
                proc = psutil.Process(pid).name()
            except Exception:
                proc = "?"
            if proc.lower() not in ("textinputhost.exe", "applicationframehost.exe") or win32gui.GetWindowText(h):
                out.append({"hwnd": h, "title": win32gui.GetWindowText(h), "process": proc})
    win32gui.EnumWindows(cb, None)
    return [w for w in out if w["title"] not in ("Program Manager",)]


def _find_window(title):
    t = title.lower()
    ws = _windows()
    for w in ws:
        if t in w["title"].lower() or t in w["process"].lower():
            return w
    names = [w["title"] for w in ws]
    m = difflib.get_close_matches(title, names, n=1, cutoff=0.5)
    return next((w for w in ws if m and w["title"] == m[0]), None)


@tool("list_windows", "Список открытых окон (заголовок и процесс).")
def list_windows():
    return [{"title": w["title"][:80], "process": w["process"]} for w in _windows()][:40]


@tool("window_action", "Действие с окном: focus (переключиться), minimize, maximize, restore, close.",
      {"title": S("Часть заголовка окна или имени процесса"),
       "action": S("focus | minimize | maximize | restore | close", enum=["focus", "minimize", "maximize", "restore", "close"])},
      ["title", "action"], danger=False)
def window_action(title, action):
    w = _find_window(title)
    if not w:
        return {"error": f"окно «{title}» не найдено"}
    h = w["hwnd"]
    if action == "focus":
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        try:
            # SetForegroundWindow срабатывает только если «разбудить» ввод — Alt выручает
            import win32api
            win32api.keybd_event(0x12, 0, 0, 0)
            win32gui.SetForegroundWindow(h)
            win32api.keybd_event(0x12, 0, 2, 0)
        except Exception:
            pass
    elif action == "minimize":
        win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
    elif action == "maximize":
        win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
    elif action == "restore":
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
    elif action == "close":
        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
    return {"ok": True, "window": w["title"][:80]}


@tool("kill_process", "Принудительно завершить зависшую программу по имени процесса. Несохранённое пропадёт.",
      {"name": S("Имя процесса, например 'chrome.exe' или 'chrome'")}, ["name"],
      danger=True, confirm="Убить процесс {name}? Несохранённые данные пропадут.")
def kill_process(name):
    n = name.lower().removesuffix(".exe")
    killed = 0
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower().removesuffix(".exe") == n:
            try:
                p.kill()
                killed += 1
            except Exception:
                pass
    return {"killed": killed}
