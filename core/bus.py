"""Шина событий: ядро → все подключённые окна (чат, аватар) и Telegram."""
import asyncio

_subs = set()


def subscribe():
    q = asyncio.Queue(maxsize=500)
    _subs.add(q)
    return q


def unsubscribe(q):
    _subs.discard(q)


def emit(type_, **data):
    msg = {"type": type_, **data}
    for q in list(_subs):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def emit_threadsafe(loop, type_, **data):
    loop.call_soon_threadsafe(lambda: emit(type_, **data))
