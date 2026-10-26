"""Реестр инструментов агента.

Инструмент — обычная функция (sync или async) с декоратором @tool. Схема параметров
пишется вручную: так модель видит ровно те описания, что мы задумали.
danger=True — опасный: выключен, пока хозяин не разрешит, и спрашивает подтверждение.
ask=True — не опасный, но резкий (погасить экран, заблокировать): всегда спрашивает подтверждение.
after_reply=True — выполняется, когда она договорила ответ, а не до него (случай 25.10: экран гас раньше ответа).
on_request=True — только по прямой просьбе: модель видит это в описании («сама» тему и фото не меняет).
intent=<regex> — резкий инструмент вообще не показывается модели, пока человек в последних репликах не попросил
об этом словами (и не показывается, если в текущей реплике «не надо / не просил»). Случай 25.10: быстрые модели
на запрет «не надо так делать» 5 раз из 5 снова гасили экран — промпт их не держит, список инструментов держит.
"""
import asyncio
import inspect
import json
import re
import traceback

REGISTRY = {}


class Tool:
    def __init__(self, fn, name, description, params, required, danger, confirm_text, group, ask=False, after_reply=False,
                 on_request=False, intent=None):
        self.fn = fn
        self.name = name
        self.description = description
        self.params = params
        self.required = required
        self.danger = danger
        self.confirm_text = confirm_text
        self.group = group
        self.ask = ask
        self.after_reply = after_reply
        self.on_request = on_request
        self.intent = re.compile(intent, re.I) if intent else None

    def schema(self):
        from ..persona import named          # в описаниях «{name}» — модель должна видеть своё настоящее имя
        desc = ("Только по прямой просьбе пользователя (вопрос, подкол или эмоция — не просьба). " if self.on_request else "") + self.description
        return {"type": "function", "function": {
            "name": self.name, "description": named(desc),
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}

    def describe_call(self, args):
        if self.confirm_text:
            try:
                return self.confirm_text.format(**args)
            except Exception:
                pass
        return f"{self.name}({json.dumps(args, ensure_ascii=False)})"

    async def __call__(self, args):
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(**args)
        return await asyncio.to_thread(self.fn, **args)


def tool(name, description, params=None, required=None, danger=False, confirm=None, group="core", ask=False, after_reply=False,
         on_request=False, intent=None):
    def deco(fn):
        REGISTRY[name] = Tool(fn, name, description, params or {}, required or [], danger, confirm, group, ask, after_reply,
                              on_request, intent)
        return fn
    return deco


def S(desc, **kw):
    """Строковый параметр."""
    return {"type": "string", "description": desc, **kw}


def I(desc, **kw):
    return {"type": "integer", "description": desc, **kw}


async def confirm(text, tool_name="tool"):
    """Спросить разрешение изнутри инструмента — для случаев, опасных только при некоторых аргументах."""
    from ..config import config
    if not config["confirm_dangerous"]:
        return True
    from .. import agent
    return await agent.ask_confirm(None, tool_name, text, agent.current_source.get())


def blocked(t):
    """Опасный встроенный инструмент, пока хозяин не включил их в настройках. MCP-серверы человек
    подключает сам — у них остаётся подтверждение."""
    from ..config import config
    return t.danger and not t.group.startswith("mcp:") and not config.get("allow_dangerous")


# «не надо», «не просил», «не делай», «don't» — в текущей реплике; тогда резкие инструменты не показываем вовсе
NEGATION = re.compile(r"\bне\s+(надо|нужно|просил\w*|делай|трогай|гаси|выключай|блокируй|смей)|хватит|\bdon'?t\b|\bstop\b", re.I)


def asked_for(t, recent_user):
    """recent_user — последние реплики человека (текущая — последней). None — без фильтра (команды, тесты)."""
    if not t.intent or recent_user is None:
        return True
    if recent_user and NEGATION.search(recent_user[-1]):
        return False
    return any(t.intent.search(x or "") for x in recent_user[-2:])


def schemas(exclude_groups=(), recent_user=None):
    return [t.schema() for t in REGISTRY.values()
            if t.group not in exclude_groups and not blocked(t) and asked_for(t, recent_user)]


async def run(name, args):
    """Выполнить инструмент; любые ошибки возвращаются модели текстом, а не роняют агента."""
    t = REGISTRY.get(name)
    if not t:
        return {"error": f"нет инструмента {name}"}
    if blocked(t):
        return {"error": "опасные инструменты выключены в настройках (Настройки → Опасные инструменты)"}
    try:
        res = await t(args)
        return res if res is not None else {"ok": True}
    except TypeError as e:
        return {"error": f"неверные аргументы: {e}"}
    except Exception as e:
        traceback.print_exc()
        return {"error": f"{type(e).__name__}: {e}"}


def load_all():
    from . import apps, files, web, system, everyday, memory, avatar, phone, shikigami, vision  # noqa: F401 — регистрируются при импорте
