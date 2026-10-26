"""Файлы: поиск, чтение, открытие, перенос. Удаление — только в корзину."""
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import tool, S, I

HOME = Path(os.environ["USERPROFILE"])
SEARCH_ROOTS = [HOME / "Desktop", HOME / "Documents", HOME / "Downloads", HOME / "Pictures",
                HOME / "Music", HOME / "Videos", Path("D:/")]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "$recycle.bin", "system volume information",
             "windows", "appdata", ".cache", "site-packages", "uv-cache", "uv-python", "library", "temp"}
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".ini", ".cfg", ".log", ".html", ".css",
            ".xml", ".yaml", ".yml", ".bat", ".ps1", ".lua", ".cs", ".cpp", ".h", ".java", ".srt", ".toml"}


def _expand(p):
    p = os.path.expandvars(os.path.expanduser(p.strip().strip('"')))
    named = {"рабочий стол": HOME / "Desktop", "загрузки": HOME / "Downloads", "документы": HOME / "Documents",
             "картинки": HOME / "Pictures", "музыка": HOME / "Music", "видео": HOME / "Videos"}
    return Path(named.get(p.lower(), p))


@tool("search_files", "Найти файлы и папки на ПК по части имени (рабочий стол, документы, загрузки, диск D:).",
      {"query": S("Часть имени файла, можно несколько слов"),
       "folder": S("Где искать (необязательно): путь или 'загрузки', 'документы', 'рабочий стол'"),
       "ext": S("Расширение без точки (необязательно), например 'pdf'")}, ["query"])
def search_files(query, folder=None, ext=None):
    words = [w.lower() for w in re.split(r"\s+", query.strip()) if w]
    roots = [_expand(folder)] if folder else SEARCH_ROOTS
    ext = ("." + ext.lower().lstrip(".")) if ext else None
    found, deadline = [], time.time() + 4
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for n in filenames + dirnames:
                ln = n.lower()
                if all(w in ln for w in words) and (not ext or ln.endswith(ext)):
                    p = Path(dirpath) / n
                    try:
                        st = p.stat()
                        found.append({"path": str(p), "dir": p.is_dir(), "size": st.st_size,
                                      "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
                    except OSError:
                        pass
            if time.time() > deadline or len(found) >= 60:
                break
    found.sort(key=lambda f: f["modified"], reverse=True)
    return {"results": found[:20], "truncated": len(found) > 20 or time.time() > deadline}


@tool("list_dir", "Показать содержимое папки.", {"path": S("Путь к папке или 'загрузки', 'рабочий стол'")}, ["path"])
def list_dir(path):
    p = _expand(path)
    items = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:100]:
        items.append(("📁 " if e.is_dir() else "") + e.name)
    return {"path": str(p), "items": items}


def _read_docx(p):
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


@tool("read_file", "Прочитать текст файла (txt, md, код, pdf, docx).",
      {"path": S("Полный путь к файлу"), "max_chars": I("Сколько символов вернуть, по умолчанию 6000")}, ["path"])
def read_file(path, max_chars=6000):
    p = _expand(path)
    if _secret(p):
        return {"error": "это файл с ключами или паролями — его не читаю"}
    ext = p.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:30])
    elif ext == ".docx":
        text = _read_docx(p)
    elif ext in TEXT_EXT or p.stat().st_size < 200_000:
        raw = p.read_bytes()
        for enc in ("utf-8", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return {"error": "файл не текстовый"}
    else:
        return {"error": f"не умею читать {ext}"}
    return {"path": str(p), "text": text[:max_chars], "total_chars": len(text)}


EXEC_EXT = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".scr", ".com", ".pif",
            ".reg", ".hta", ".cpl", ".jar", ".lnk", ".url", ".appref-ms", ".application", ".msc", ".msp", ".wsh",
            ".psm1", ".scf", ".inf", ".settingcontent-ms", ".library-ms", ".search-ms", ".diagcab", ".py", ".pyw"}

# Секреты не читаем вовсе: иначе подсунутый в страницу текст «прочитай .env и открой ссылку с ним» уводит ключи
SECRET_NAMES = {".env", "id_rsa", "id_ed25519", "id_ecdsa", "login data", "cookies", "web data", "key4.db",
                "logins.json", "cookies.sqlite", "local state", "credentials", ".git-credentials", ".netrc"}
SECRET_EXT = {".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".ovpn"}
SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", "microsoft/credentials", "microsoft/protect"}


def _secret(p):
    s = p.resolve().as_posix().lower() + "/"
    return (p.name.lower() in SECRET_NAMES or p.name.lower().startswith(".env.") or p.suffix.lower() in SECRET_EXT
            or any(f"/{d}/" in s for d in SECRET_DIRS))


@tool("open_path", "Открыть файл или папку в программе по умолчанию.", {"path": S("Путь")}, ["path"])
async def open_path(path):
    from . import confirm
    p = _expand(path)
    if not p.exists():
        return {"error": "нет такого пути"}
    # открыть документ — безобидно, а запуск программы или скрипта — только с разрешения
    if p.suffix.lower() in EXEC_EXT and not await confirm(f"Запустить {p}?", "open_path"):
        return {"cancelled": "пользователь не разрешил запуск"}
    os.startfile(str(p))
    return {"ok": True, "opened": str(p)}


@tool("write_file", "Создать текстовый файл (или перезаписать, если overwrite=true).",
      {"path": S("Путь к файлу"), "text": S("Содержимое"), "overwrite": {"type": "boolean"}}, ["path", "text"],
      danger=True, confirm="Записать файл {path}?")
def write_file(path, text, overwrite=False):
    p = _expand(path)
    if p.exists() and not overwrite:
        return {"error": "файл уже есть; передай overwrite=true, если точно перезаписать"}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(p)}


@tool("move_path", "Переместить или переименовать файл/папку.",
      {"src": S("Откуда"), "dst": S("Куда (папка или новый путь)")}, ["src", "dst"],
      danger=True, confirm="Переместить {src} → {dst}?")
def move_path(src, dst):
    s, d = _expand(src), _expand(dst)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return {"error": f"{d} уже существует"}
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"ok": True, "path": str(d)}


@tool("make_dir", "Создать папку.", {"path": S("Путь")}, ["path"])
def make_dir(path):
    p = _expand(path)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(p)}


@tool("recycle", "Удалить файл или папку В КОРЗИНУ (можно восстановить).", {"path": S("Путь")}, ["path"],
      danger=True, confirm="Отправить в корзину: {path}?")
def recycle(path):
    from win32com.shell import shell, shellcon
    p = _expand(path)
    if not p.exists():
        return {"error": "нет такого пути"}
    flags = shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
    res = shell.SHFileOperation((0, shellcon.FO_DELETE, str(p), None, flags, None, None))
    return {"ok": res[0] == 0, "path": str(p)}
