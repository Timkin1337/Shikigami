"""Сборка Shikigami для людей: чистая папка программы и установщик.

    .venv\\Scripts\\python.exe devtools\\build_release.py [--no-installer]

Что делает:
1. Берёт исходники только из git (HEAD) и только нужное программе: ядро, интерфейс, плагины, движения,
   тембры. Ни docs, ни devtools, ни личных данных, ни незакоммиченных правок.
2. Кладёт свой Python (переносимый CPython из uv) с базовыми пакетами — без голоса: голос и распознавание
   человек ставит сам во вкладке «Модули» (их библиотеки докачивает runtime/uv.exe).
3. Electron → Shikigami.exe с иконкой, оболочка — в resources/app; three.js и three-vrm — для окон.
4. Модели-заготовки, которые можно раздавать (Seed-san, образец pixiv).
5. Проверяет папку на личное: имена, пути разработчика, ключи. Нашлось — сборка останавливается.
6. Собирает установщик Inno Setup (devtools/installer.iss) — для одного пользователя, без прав администратора.

Пути инструментов (переносимый Python, uv, rcedit, Inno Setup) — в data/dev-paths.json разработчика:
{"python_dist": …, "uv": …, "rcedit": …, "iscc": …}.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.9.0"
OUT = ROOT.parent / "Shikigami-release"
APP = OUT / "Shikigami"
DEV = json.loads((ROOT / "data" / "dev-paths.json").read_text(encoding="utf-8"))

# из git — только это
TAKE = ["core", "plugins", "ui", "data/animations", "data/voices", "shikigami.py", "LICENSE", "CREDITS.md"]
SKIP = ["ui/avatar-debug.js", "core/voice/voice_design.py"]          # отладка и инструмент разработчика
MODELS = ["Seed-san.vrm", "Twist-Sample.vrm"]                       # VRM Public License 1.0, распространять можно
# своя модель проекта (лиса-горничная, VRoid Studio): в релиз — под нейтральным именем, без имени разработчика внутри
# источник — data/models/<own_model из dev-paths.json>, в публичном репозитории уже Kitsune-Maid.vrm
OWN_MODEL = (DEV.get("own_model", "Kitsune-Maid.vrm"), "Kitsune-Maid.vrm", "Kitsune Maid")
# базовые пакеты — версии как у разработчика (проверены); голосовые ставят модули
PACKAGES = [l.strip() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
# в релизе этого быть не должно ни в одном нашем файле
# свои слова (имя, ник, пути) — в dev-paths.json: {"private_words": ["…"]}; плюс всё, что похоже на ключи
# и сами значения ключей из .env (NVIDIA, Telegram, itch.io…) — буквально они не уедут, что бы ни случилось
ENV = ROOT / ".env"
SECRETS = [l.split("=", 1)[1].strip().strip("\"'") for l in ENV.read_text(encoding="utf-8").splitlines()
           if "=" in l and not l.lstrip().startswith("#")] if ENV.exists() else []
FORBIDDEN = re.compile("|".join([re.escape(w) for w in DEV.get("private_words", []) + [s for s in SECRETS if len(s) >= 12]]
                                + [r"nvapi-[A-Za-z0-9_-]{8}", r"\d{8,}:[A-Za-z0-9_-]{30}"]))


def run(*a, **kw):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        sys.exit(f"ошибка: {' '.join(map(str, a))[:200]}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r.stdout


def step(t):
    print(f"\n== {t}", flush=True)


def export_sources(dst):
    step("исходники из git (HEAD)")
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / "src.tar"
        run("git", "-C", ROOT, "archive", "-o", tar, "HEAD", *TAKE, "shell/main.js", "shell/preload.js", "shell/package.json",
            "shell/assets")
        with tarfile.open(tar) as t:
            t.extractall(Path(tmp) / "src", filter="data")
        src = Path(tmp) / "src"
        for item in TAKE:
            s = src / item
            (shutil.copytree if s.is_dir() else shutil.copy2)(s, dst / item)
        for item in SKIP:
            (dst / item).unlink(missing_ok=True)
        app = dst / "resources" / "app"                              # оболочка Electron
        shutil.copytree(src / "shell", app)
    (dst / "data" / "models").mkdir(parents=True, exist_ok=True)
    for m in MODELS:
        shutil.copy2(ROOT / "data" / "models" / m, dst / "data" / "models" / m)
    src, name, title = OWN_MODEL
    rename_vrm(ROOT / "data" / "models" / src, dst / "data" / "models" / name, title)
    print("модели:", ", ".join(MODELS + [name]))


def rename_vrm(src, dst, title):
    """Копия .vrm с другим названием в метаданных (VRMC_vrm.meta.name / VRM.meta.title): GLB — заголовок и JSON-чанк."""
    import struct
    b = src.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + n])
    ext = j.get("extensions", {})
    if "VRMC_vrm" in ext:
        ext["VRMC_vrm"]["meta"]["name"] = title
    else:
        ext["VRM"]["meta"]["title"] = title
    js = json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)                                       # чанки GLB выровнены по 4 байта
    rest = b[20 + n:]                                                 # бинарный чанк — как был
    body = struct.pack("<I", len(js)) + b"JSON" + js + rest
    dst.write_bytes(b[:8] + struct.pack("<I", 12 + len(body)) + body)


def electron(dst):
    step("Electron → Shikigami.exe")
    dist = ROOT / "shell" / "node_modules" / "electron" / "dist"
    for p in dist.iterdir():
        if p.suffix == ".exe":                                        # electron.exe станет Shikigami.exe ниже; свои копии — мимо
            continue
        if p.name == "resources":
            (dst / "resources").mkdir(exist_ok=True)
            continue                                                  # default_app.asar не нужен: своя app
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, dst / p.name)
    exe = dst / "Shikigami.exe"
    shutil.copy2(dist / "electron.exe", exe)
    ico = ROOT / "shell" / "assets" / "shikigami.ico"
    run(DEV["rcedit"], exe, "--set-icon", ico, "--set-file-version", VERSION, "--set-product-version", VERSION,
        "--set-version-string", "FileDescription", "Shikigami", "--set-version-string", "ProductName", "Shikigami",
        "--set-version-string", "OriginalFilename", "Shikigami.exe", "--set-version-string", "InternalName", "Shikigami",
        "--set-version-string", "CompanyName", "Shikigami contributors", "--set-version-string", "LegalCopyright", "MIT License")
    vend = dst / "shell" / "node_modules"                             # /vendor/ — three.js и three-vrm для окон
    vend.mkdir(parents=True)
    shutil.copytree(ROOT / "shell" / "node_modules" / "three", vend / "three",
                    ignore=shutil.ignore_patterns("src", "*.d.ts", "*.md", "*.cjs"))
    shutil.copytree(ROOT / "shell" / "node_modules" / "@pixiv", vend / "@pixiv", ignore=shutil.ignore_patterns("types-*", "*.d.ts"))


def python_runtime(dst):
    step("Python и базовые пакеты")
    py = dst / "runtime" / "python"
    shutil.copytree(DEV["python_dist"], py, ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "idlelib", "tkinter",
                                                                          "turtledemo", "EXTERNALLY-MANAGED"))
    for f in py.rglob("EXTERNALLY-MANAGED"):
        f.unlink()
    env = {**os.environ, "UV_CACHE_DIR": DEV.get("uv_cache", str(OUT / "uv-cache"))}
    run(DEV["uv"], "pip", "install", "--python", py / "python.exe", "--no-progress", *PACKAGES, env=env)
    shutil.copy2(DEV["uv"], dst / "runtime" / "uv.exe")
    out = run(py / "python.exe", "-c", "import aiohttp, win32gui, psutil, PIL, numpy, chess, mcp, pypdf, winrt.windows.media.control;"
                                         "print('базовые пакеты импортируются')")
    print(out.strip())


def readme(dst):
    (dst / "README.txt").write_text(
        "Shikigami 式神 — ИИ-компаньон на рабочем столе Windows.\n\n"
        "Первый запуск: мастер спросит имя помощницы, характер, модель и «мозг» (бесплатный ключ NVIDIA\n"
        "или своя локальная модель). Голос и распознавание речи — по желанию, вкладка «Модули».\n"
        "Все данные остаются на этом ПК, в папке data рядом с программой.\n\n"
        "Shikigami — an AI desktop companion for Windows. On first launch a wizard asks for the assistant's name,\n"
        "personality, model and a “brain” (a free NVIDIA key or your own local model). Voice and speech recognition\n"
        "are optional (Modules tab). All data stays on this PC, in the data folder next to the program.\n\n"
        "Windows SmartScreen и антивирусы могут предупредить: установщик не подписан (сертификат подписи платный, программа\n"
        "бесплатная), а модули сами докачивают библиотеки. «Подробнее» → «Выполнить в любом случае». Исходный код открыт.\n"
        "SmartScreen and antivirus may warn: the installer is unsigned (signing certificates cost money, the program is free)\n"
        "and modules download libraries themselves. Click “More info” → “Run anyway”. The source code is open.\n\n"
        "Лицензия кода — MIT (LICENSE). Модели, движения, голоса — под своими лицензиями (CREDITS.md):\n"
        "движения и голос Silero — только некоммерческое использование.\n", encoding="utf-8")


def scan(dst):
    step("проверка на личное")
    bad = []
    for p in dst.rglob("*"):
        rel = p.relative_to(dst).as_posix()
        if FORBIDDEN.search(rel):
            bad.append(f"{rel}: в имени файла")
        if not p.is_file() or rel.startswith(("runtime/", "shell/node_modules/", "locales/")) or p.suffix in (".dll", ".pak", ".bin", ".dat", ".exe", ".wav", ".vrm"):
            continue
        if p.suffix not in (".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".bat", ".iss"):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if FORBIDDEN.search(line):
                bad.append(f"{rel}:{i}: {line.strip()[:120]}")
    for name in ("config.json", ".env", "data/shikigami.db", "data/dev-paths.json", "data/friendship.json", "data/creator.sig"):
        if (dst / name).exists():
            bad.append(f"{name}: личный файл в сборке")
    if bad:
        sys.exit("НАЙДЕНО ЛИЧНОЕ — сборка остановлена:\n" + "\n".join(bad[:60]))
    print("чисто")


def installer():
    step("установщик")
    run(DEV["iscc"], f"/DAppVersion={VERSION}", f"/DSrc={APP}", f"/DOut={OUT}", ROOT / "devtools" / "installer.iss")
    exe = OUT / f"Shikigami-Setup-{VERSION}.exe"
    print(exe, f"{exe.stat().st_size >> 20} МБ")


def size(p):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) >> 20


def main():
    if run("git", "-C", ROOT, "status", "--porcelain", "--untracked-files=no").strip():
        print("внимание: есть незакоммиченные правки — в сборку идёт только закоммиченное (HEAD)")
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)
    export_sources(APP)
    electron(APP)
    python_runtime(APP)
    readme(APP)
    scan(APP)
    for p in APP.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    print(f"\nпапка программы: {APP} — {size(APP)} МБ")
    if "--no-installer" not in sys.argv:
        installer()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
