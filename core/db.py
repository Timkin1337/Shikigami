"""SQLite: чаты, память, заметки, напоминания, задачи, календарь, команды."""
import json
import sqlite3
import threading
import time

from .config import DATA

_lock = threading.RLock()
_conn = sqlite3.connect(DATA / "shikigami.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")

_conn.executescript("""
CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, title TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, chat_id INTEGER, role TEXT, content TEXT,
    tool_calls TEXT, tool_call_id TEXT, name TEXT, created REAL);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, text TEXT, created REAL);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, title TEXT, text TEXT, created REAL, updated REAL, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, text TEXT, due REAL, repeat TEXT, done INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, text TEXT, done INTEGER DEFAULT 0, due REAL, created REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT, start REAL, end REAL, place TEXT, created REAL);
CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY, name TEXT, grp TEXT, enabled INTEGER DEFAULT 1, graph TEXT, updated REAL);
""")
# 25.10: сухой черновик исполнителя рядом с ответом в образе — исполнитель видит в истории черновики
if "draft" not in {r[1] for r in _conn.execute("PRAGMA table_info(messages)")}:
    _conn.execute("ALTER TABLE messages ADD COLUMN draft TEXT")
_conn.commit()


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


# --- чаты ---

def new_chat(title="Новый чат"):
    now = time.time()
    return ex("INSERT INTO chats(title, created, updated) VALUES(?,?,?)", (title, now, now))


def list_chats(limit=50):
    return q("SELECT * FROM chats ORDER BY updated DESC LIMIT ?", (limit,))


def add_message(chat_id, role, content=None, tool_calls=None, tool_call_id=None, name=None, draft=None):
    now = time.time()
    ex("UPDATE chats SET updated=? WHERE id=?", (now, chat_id))
    return ex("INSERT INTO messages(chat_id, role, content, tool_calls, tool_call_id, name, created, draft) VALUES(?,?,?,?,?,?,?,?)",
              (chat_id, role, content, json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
               tool_call_id, name, now, draft))


def history(chat_id, limit, drafts=False):
    """drafts=True — для исполнителя: вместо ответов в образе — его же сухие черновики (у старых сообщений их нет)."""
    rows = q("SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
    rows.reverse()
    # история не должна начинаться с ответа инструмента без вызова
    while rows and rows[0]["role"] == "tool":
        rows.pop(0)
    out = []
    for r in rows:
        m = {"role": r["role"], "content": (r["draft"] if drafts and r["role"] == "assistant" and r["draft"] else r["content"]) or ""}
        if r["tool_calls"]:
            m["tool_calls"] = json.loads(r["tool_calls"])
        if r["tool_call_id"]:
            m["tool_call_id"] = r["tool_call_id"]
        out.append(m)
    return out


def chat_messages(chat_id):
    return q("SELECT id, role, content, tool_calls, name, created FROM messages WHERE chat_id=? ORDER BY id", (chat_id,))
