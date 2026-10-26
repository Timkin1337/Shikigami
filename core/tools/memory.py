"""Долгая память о пользователе: факты, предпочтения, привычки."""
import time

from .. import db
from . import tool, S, I


@tool("remember", "Запомнить важный факт о пользователе надолго (предпочтения, привычки, люди, планы). "
      "Вызывай сама, когда узнаёшь что-то стоящее, — без просьбы.",
      {"fact": S("Факт одной фразой, от третьего лица: 'Любит лофи по вечерам'")}, ["fact"])
def remember(fact):
    dup = db.one("SELECT id FROM facts WHERE text=?", (fact,))
    if dup:
        return {"ok": True, "id": dup["id"], "note": "уже помню"}
    fid = db.ex("INSERT INTO facts(text, created) VALUES(?,?)", (fact, time.time()))
    return {"ok": True, "id": fid}


@tool("forget", "Забыть факт по id (если устарел или пользователь просит).", {"id": I("id факта")}, ["id"])
def forget(id):
    db.ex("DELETE FROM facts WHERE id=?", (id,))
    return {"ok": True}


@tool("recall", "Поискать в памяти факты по слову.", {"query": S("Слово")}, ["query"])
def recall(query):
    return db.q("SELECT id, text FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT 20", (f"%{query}%",))


def facts_for_prompt(limit=60):
    rows = db.q("SELECT id, text FROM facts ORDER BY id DESC LIMIT ?", (limit,))
    rows.reverse()
    return rows
