"""Сколько {name} говорит сама (по совету ChatGPT, Gemini и DeepSeek — docs/ai/council_web_2026-10-18.md).

Через allow() проходит каждая инициативная реплика: «с возвращением», перерыв, чихнула, комментарии в Майнкрафте.
Ответы на прямое обращение и действия хозяина (позвал, ткнул, погладил) сюда не идут — на них она отвечает всегда.

- режим behavior.chatty: silent (молчит, только облачко) / minimal (3 в час) / normal (8 в час) / chatty (20 в час);
- «Помолчи N минут» (ПКМ по ней, голосом) — до срока только облачко;
- срочное (крипер, тонет) проходит в любом режиме, кроме silent, и в лимит не входит;
- в полноэкранной игре или видео сама не заговаривает (кроме срочного).
"""
import time
from collections import deque

from aiohttp import web

from . import bus
from .config import config

BUDGET = {"silent": 0, "minimal": 3, "normal": 8, "chatty": 20}
_said = deque(maxlen=64)          # когда говорила сама
routes = web.RouteTableDef()


def mode():
    return config.get("behavior.chatty", "minimal")


def muted_until():
    return float(config.get("behavior.mute_until", 0) or 0)


def allow(urgent=False):
    """Можно ли сейчас сказать вслух инициативную реплику. Нельзя — показать облачком (или промолчать)."""
    m = mode()
    if m == "silent" or time.time() < muted_until():
        return False
    if urgent:
        return True
    try:
        from .behavior import fullscreen_app, windows_quiet
        if fullscreen_app() or (config.get("behavior.respect_dnd", True) and windows_quiet()):
            return False
    except Exception:
        pass
    now = time.time()
    if sum(1 for t in _said if now - t < 3600) >= BUDGET.get(m, 3):
        return False
    _said.append(now)
    return True


def mute(minutes):
    until = time.time() + max(0, minutes) * 60 if minutes else 0
    config.update({"behavior": {"mute_until": until}})
    bus.emit("config.changed")
    return until


@routes.post("/api/chatter/mute")
async def api_mute(r):
    minutes = (await r.json()).get("minutes", 60)
    return web.json_response({"until": mute(minutes)})


@routes.get("/api/chatter")
async def api_state(_):
    now = time.time()
    return web.json_response({"mode": mode(), "muted_for_min": max(0, round((muted_until() - now) / 60)),
                              "said_last_hour": sum(1 for t in _said if now - t < 3600), "budget": BUDGET.get(mode(), 3)})
