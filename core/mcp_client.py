"""MCP-серверы: их инструменты становятся инструментами {name} без программирования.

Настройка — JSON как в Claude Desktop:
  {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/Documents"]},
   "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
Инструмент сервера X с именем y виден агенту как X__y.
"""
import asyncio
import json
import os
import re
import shutil

from . import bus, tools
from .config import config

sessions = {}


def _safe(name):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:60]


async def _serve(name, spec):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    cmd = spec.get("command")
    exe = shutil.which(cmd) or cmd
    params = StdioServerParameters(command=exe, args=spec.get("args", []),
                                   env={**os.environ, **(spec.get("env") or {})})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            listed = await s.list_tools()
            sessions[name] = s
            for t in listed.tools:
                _register(name, s, t)
            print(f"MCP {name}: {len(listed.tools)} инструментов", flush=True)
            bus.emit("data.changed", what="tools")
            await asyncio.Event().wait()   # держим сессию открытой


def _register(server, session, t):
    schema = t.inputSchema or {}
    ann = getattr(t, "annotations", None)
    # по спецификации MCP без подсказок инструмент считается разрушительным (destructiveHint=true по умолчанию):
    # без подтверждения — только явно «только чтение» или явно неразрушительные
    danger = not (ann and (getattr(ann, "readOnlyHint", None) is True or getattr(ann, "destructiveHint", None) is False))
    full = f"{_safe(server)[:20]}__{_safe(t.name)}"[:64]   # у API моделей предел имени инструмента — 64 символа

    async def call(**args):
        res = await session.call_tool(t.name, args)
        parts = []
        for c in res.content:
            parts.append(getattr(c, "text", None) or json.dumps(c.model_dump(), ensure_ascii=False)[:2000])
        out = "\n".join(parts)[:8000]
        return {"error": out} if res.isError else {"result": out}

    tools.REGISTRY[full] = tools.Tool(call, full, f"[{server}] {t.description or t.name}",
                                      schema.get("properties", {}), schema.get("required", []),
                                      danger, None, f"mcp:{server}")


async def start_all():
    servers = config.get("mcp.servers") or {}
    for name, spec in servers.items():
        asyncio.create_task(_keep(name, spec))


async def _keep(name, spec):
    for attempt in range(3):
        try:
            await _serve(name, spec)
        except Exception as e:
            print(f"MCP {name} упал: {e}", flush=True)
            for k in [k for k, t in tools.REGISTRY.items() if t.group == f"mcp:{name}"]:
                tools.REGISTRY.pop(k, None)
            await asyncio.sleep(5 * (attempt + 1))
