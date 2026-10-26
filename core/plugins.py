"""Плагины: папка plugins/<имя>/ с plugin.py.

Внутри plugin.py доступно всё ядро. Обычно плагин:
  - регистрирует инструменты агента декоратором core.tools.tool(..., group="plugin:<имя>");
  - может объявить setup(api) — вызывается при старте, туда приходит PluginAPI;
  - может положить рядом panel.html — она появится вкладкой в окне (адрес /plugins/<имя>/panel.html).
Отключить плагин — переименовать папку с точкой в начале или добавить файл disabled.
"""
import importlib.util
import sys
import traceback

from . import bus, scheduler
from .config import ROOT, config

PLUGINS_DIR = ROOT / "plugins"
loaded = {}
# Перехват реплик до ИИ: async def fn(text, source) -> str | None. Пасхалки, ответы «да/нет» в играх —
# мгновенно и без запросов к модели. Вернул текст — реплика обработана, ИИ её не видит.
interceptors = []
# События окна аватара (накормили, поймала мячик, нашёлся в прятках): async def fn(kind, msg)
avatar_listeners = []


class PluginAPI:
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.config = config
        self.emit = bus.emit
        self.on_tick = scheduler.on_tick
        self.routes = []      # (method, path, handler) — добавятся в веб-сервер

    def route(self, method, path):
        def deco(fn):
            self.routes.append((method, f"/api/plugins/{self.name}{path}", fn))
            return fn
        return deco

    def on_text(self, fn):
        interceptors.append(fn)
        return fn

    def on_avatar_event(self, fn):
        avatar_listeners.append(fn)
        return fn


def load_all():
    PLUGINS_DIR.mkdir(exist_ok=True)
    for d in sorted(PLUGINS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")) or (d / "disabled").exists():
            continue
        f = d / "plugin.py"
        if not f.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"shikigami_plugin_{d.name}", f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            api = PluginAPI(d.name, d)
            if hasattr(mod, "setup"):
                mod.setup(api)
            loaded[d.name] = {"module": mod, "api": api, "panel": (d / "panel.html").exists(),
                              "title": getattr(mod, "TITLE", d.name), "description": getattr(mod, "DESCRIPTION", "")}
            print(f"плагин {d.name} загружен")
        except Exception:
            print(f"плагин {d.name} не загрузился:")
            traceback.print_exc()


def info():
    return [{"name": n, "title": p["title"], "description": p["description"], "panel": p["panel"]}
            for n, p in loaded.items()]


async def intercept(text, source):
    for fn in interceptors:
        try:
            said = await fn(text, source)
            if said:
                return said
        except Exception:
            traceback.print_exc()
    return None


async def avatar_event(kind, msg):
    for fn in avatar_listeners:
        try:
            await fn(kind, msg)
        except Exception:
            traceback.print_exc()
