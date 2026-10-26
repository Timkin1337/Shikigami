r"""Реплики ядра и плагинов (то, что пользователь слышит и видит) → в общий словарь ui/lang/en.json.

Берутся строковые литералы с кириллицей, кроме: описаний инструментов (@tool — их читает модель),
длинных промптов, регулярных выражений и докстрингов. Запуск после правок реплик:
    .venv\Scripts\python.exe devtools\i18n_extract_core.py
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ui" / "lang" / "en.json"
CYR = re.compile(r"[А-Яа-яЁё]")
REGEXY = re.compile(r"\[bwsd]|\(\?|\|\w|\[\^|\w\||\.\*|\\.")
FILES = [*sorted((ROOT / "plugins").glob("*/*.py")),
         *[ROOT / "core" / f for f in ("reactions.py", "behavior.py", "telegram.py", "agent.py", "chatter.py", "scheduler.py", "clip.py", "tools/everyday.py", "commands.py", "vrm_meta.py")],
         *sorted((ROOT / "core" / "voice").glob("*.py"))]


def strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                skip.add(id(node.body[0].value))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:                      # @tool(...) — для модели, не для человека
                for sub in ast.walk(d):
                    skip.add(id(sub))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("compile", "search", "fullmatch", "match", "sub", "findall"):
            for sub in ast.walk(node):
                skip.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            s = node.value
            if CYR.search(s) and len(s) <= 220 and not REGEXY.search(s) and "\n" not in s.strip():
                yield re.sub(r"\s+", " ", s).strip()
        elif isinstance(node, ast.JoinedStr) and id(node) not in skip:   # f-строка → шаблон с {}
            parts = [v.value if isinstance(v, ast.Constant) else "{}" for v in node.values]
            s = re.sub(r"\s+", " ", "".join(parts)).strip()
            if CYR.search(s) and len(s) <= 220 and not REGEXY.search(s):
                yield s


def main():
    found = []
    for f in FILES:
        if f.exists():
            found += [s for s in strings(f) if not re.fullmatch(r"[{}\s\W]*", s)]
    d = json.loads(OUT.read_text(encoding="utf-8"))
    new = [s for s in dict.fromkeys(found) if s not in d]
    for s in new:
        d[s] = ""
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"новых строк: {len(new)}, всего без перевода: {sum(1 for v in d.values() if not v)}")


if __name__ == "__main__":
    main()
