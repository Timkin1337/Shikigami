"""Срабатывание напоминаний (и расписаний команд — их подключает commands.py)."""
import asyncio
import time

from . import bus, db

_hooks = []  # функции, которые зовутся каждый тик: async def hook(now)


def on_tick(fn):
    _hooks.append(fn)
    return fn


async def run():
    while True:
        try:
            now = time.time()
            for r in db.q("SELECT * FROM reminders WHERE done=0 AND due<=?", (now,)):
                await fire(r)
            for h in _hooks:
                await h(now)
        except Exception as e:
            print("scheduler:", e)
        await asyncio.sleep(2)


async def fire(r):
    if r["repeat"] == "daily":
        db.ex("UPDATE reminders SET due=due+86400 WHERE id=?", (r["id"],))
    elif r["repeat"] == "weekly":
        db.ex("UPDATE reminders SET due=due+604800 WHERE id=?", (r["id"],))
    else:
        db.ex("UPDATE reminders SET done=1 WHERE id=?", (r["id"],))
    bus.emit("notify", title="Напоминание", text=r["text"], kind="reminder")
    bus.emit("avatar.state", state="alert", emotion="surprised")
    bus.emit("avatar.timer.done", id=r["id"])
    bus.emit("avatar.pastime", name="letter", force=True)    # приносит напоминание конвертом, а не всплывашкой
    bus.emit("data.changed", what="reminders")
    from . import voice
    from .config import config
    # говорим отдельной задачей: пока звучит фраза, остальные напоминания и расписания не ждут
    asyncio.create_task(voice.speak(f"{config['address'].capitalize()}, напоминаю: {r['text']}", emotion="surprised"))
