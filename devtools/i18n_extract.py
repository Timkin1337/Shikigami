"""Собрать все русские строки интерфейса в ui/lang/en.json (новые — с пустым переводом).

Перевод устроен по точному совпадению текста: ui/i18n.js заменяет в окне видимые строки и подсказки
по словарю, поэтому в разметке и скриптах тексты остаются как есть — по-русски, без ключей.
Запуск после правок интерфейса:  .venv\\Scripts\\python.exe devtools\\i18n_extract.py
Строки с подстановками ${...} попадают в словарь шаблоном: «Ходов: {}» → «Moves: {}».
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ui" / "lang" / "en.json"
CYR = re.compile(r"[А-Яа-яЁё]")
SOURCES = [ROOT / "ui" / "index.html", ROOT / "ui" / "avatar.html", *sorted((ROOT / "ui").glob("*.js")),
           *sorted((ROOT / "plugins").glob("*/panel.html"))]
SKIP_JS = {"i18n.js", "avatar-debug.js"}


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


class Texts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k in ("placeholder", "title", "aria-label") and v and CYR.search(v):
                self.out.append(norm(v))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and CYR.search(data):
            self.out.append(norm(data))


def from_js(code):
    out = []
    for m in re.finditer(r"""(["'`])((?:\\.|(?!\1).)*?)\1""", code, re.S):
        s = m.group(2)
        if not CYR.search(s):
            continue
        if m.group(1) == "`":
            # шаблон: html-разметку режем на текстовые куски, подстановки → {}
            s = re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", "{}", s)
            parts = re.split(r"<[^>]+>", s)
        else:
            parts = re.split(r"<[^>]+>", s.encode().decode("unicode_escape", "ignore") if "\\u" in s else s)
        out += [norm(p) for p in parts if CYR.search(p)]
    return out


def main():
    found = []
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".html":
            p = Texts()
            p.feed(text)
            found += p.out
            for sc in re.findall(r"<script[^>]*>(.*?)</script>", text, re.S):
                found += from_js(sc)
        elif f.name not in SKIP_JS:
            code = re.sub(r"^\s*//.*$", "", text, flags=re.M)          # комментарии не переводим
            found += from_js(code)
    found = [s for s in dict.fromkeys(found) if s and len(s) < 600]
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    # дополняем, а не перезаписываем: в словаре живут и реплики ядра (i18n_extract_core.py) и ручные строки
    merged = {**old, **{s: old.get(s, "") for s in found}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"строк: {len(merged)}, без перевода: {sum(1 for v in merged.values() if not v)}")


if __name__ == "__main__":
    main()
