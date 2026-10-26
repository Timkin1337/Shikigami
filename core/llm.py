"""Модели: любой OpenAI-совместимый адрес (NVIDIA, llama.cpp, LM Studio, Ollama, OpenRouter...).

Основная — NVIDIA, запасная — локальный llama-server, который поднимается только по требованию
(он съедает видеопамять, держать его постоянно незачем).
"""
import asyncio
import json
import subprocess
import time
from collections import deque
from pathlib import Path

import aiohttp

from .config import config


class LLMError(Exception):
    pass


class RateLimiter:
    """Скользящее окно в минуту. Лимит NVIDIA — на аккаунт, поэтому держим запас."""

    def __init__(self):
        self.calls = {}

    async def wait(self, name, rpm):
        if not rpm:
            return 0
        q = self.calls.setdefault(name, deque())
        waited = 0
        while True:
            now = time.time()
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) < rpm:
                q.append(now)
                return waited
            delay = 60 - (now - q[0]) + 0.05
            waited += delay
            await asyncio.sleep(delay)


limiter = RateLimiter()


class LocalServer:
    """Свой llama-server.exe (путь в настройках) — запуск по первому обращению."""

    IDLE_STOP = 15 * 60   # локальная модель держит видеопамять — без запросов гасим

    def __init__(self):
        self.proc = None
        self.lock = asyncio.Lock()
        self.last_used = 0.0
        self.watch = None

    def touch(self):
        self.last_used = time.time()
        if self.proc and (self.watch is None or self.watch.done()):
            self.watch = asyncio.create_task(self._idle_watch())

    async def _idle_watch(self):
        while self.proc and self.proc.poll() is None:
            await asyncio.sleep(60)
            if time.time() - self.last_used > self.IDLE_STOP:
                print("локальная модель простаивает — выключаю, чтобы освободить видеопамять", flush=True)
                self.stop()

    async def ensure(self, p):
        async with self.lock:
            if await self._alive(p["base_url"]):
                return
            exe, model = p.get("server_exe"), p.get("model_path")
            if not exe or not Path(exe).exists() or not Path(model or "").exists():
                raise LLMError("локальная модель не настроена (нет llama-server или файла модели)")
            port = p["base_url"].split(":")[-1].split("/")[0]
            args = [exe, "-m", model, "--port", port, "--host", "127.0.0.1", *p.get("args", [])]
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(180):
                await asyncio.sleep(1)
                if self.proc.poll() is not None:
                    raise LLMError("llama-server упал при запуске")
                if await self._alive(p["base_url"]):
                    return
            raise LLMError("llama-server не поднялся за 3 минуты")

    async def _alive(self, base):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(base.rsplit("/v1", 1)[0] + "/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    return r.status == 200
        except Exception:
            return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


local_server = LocalServer()

# Где обычно живут локальные модели: Ollama, LM Studio, llama.cpp (llama-server)
KNOWN_LOCAL = [("Ollama", "http://127.0.0.1:11434/v1"), ("LM Studio", "http://127.0.0.1:1234/v1"),
               ("llama.cpp", "http://127.0.0.1:8080/v1"), ("llama.cpp", "http://127.0.0.1:8081/v1")]


async def detect_local():
    """Найти запущенные локальные модели: [{app, base_url, models}]. Спрашиваем только свой ПК."""
    async def probe(app, base):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(base + "/models", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    if r.status != 200:
                        return None
                    ids = [m.get("id") for m in (await r.json()).get("data", []) if m.get("id")]
                    return {"app": app, "base_url": base, "models": ids}
        except Exception:
            return None
    found = await asyncio.gather(*(probe(a, b) for a, b in KNOWN_LOCAL))
    return [f for f in found if f]


def no_brain():
    """«Мозга» нет вовсе: ни ключа NVIDIA, ни локальной модели — так у всех на первом запуске. Тогда ошибка не
    «облако перегружено, повторите через минутку», а прямо: куда вписать ключ."""
    nv, loc = config.get("llm.providers.nvidia") or {}, config.get("llm.providers.local") or {}
    return not config.secret(nv.get("api_key_env")) and not (loc.get("model") or loc.get("model_path"))


def provider(name):
    p = config.get(f"llm.providers.{name}")
    if not p:
        raise LLMError(f"нет провайдера {name}")
    return p


async def stream_chat(name, messages, tools=None, on_text=None, on_reasoning=None, max_tokens=2048,
                      temperature=0.7, model=None):
    """Один запрос со стримингом. Возвращает {"content", "tool_calls"}.

    Вызовы инструментов приходят кусками (имя в первом, аргументы — строкой по частям),
    поэтому собираем их по index.
    """
    p = provider(name)
    if p.get("server_exe") and p.get("model_path"):   # свой llama-server поднимаем сами; Ollama/LM Studio человек запускает сам
        await local_server.ensure(p)
        local_server.touch()
    await limiter.wait(name, p.get("rpm", 0))
    body = {"model": model or p["model"], "messages": messages, "stream": True, "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        body["tools"] = tools
    body.update(p.get("extra") or {})
    headers = {"Content-Type": "application/json; charset=utf-8"}
    key = config.secret(p.get("api_key_env"))
    if key:
        headers["Authorization"] = f"Bearer {key}"

    content, calls = [], {}
    # первый кусок ответа обычно приходит за секунды; 25 с тишины — модель перегружена, лучше сразу запасная
    timeout = aiohttp.ClientTimeout(total=120, sock_read=25)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(p["base_url"].rstrip("/") + "/chat/completions",
                          data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers) as r:
            if r.status != 200:
                txt = (await r.text())[:300]
                raise LLMError(f"{name}: HTTP {r.status} {txt}")
            buf = b""
            async for chunk in r.content.iter_any():
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        break
                    try:
                        ev = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not ev.get("choices"):
                        continue
                    d = ev["choices"][0].get("delta") or {}
                    if d.get("reasoning_content") and on_reasoning:
                        on_reasoning(d["reasoning_content"])
                    if d.get("content"):
                        content.append(d["content"])
                        if on_text:
                            on_text(d["content"])
                    for tc in d.get("tool_calls") or []:
                        i = tc.get("index", 0)
                        c = calls.setdefault(i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            c["id"] = tc["id"]
                        f = tc.get("function") or {}
                        if f.get("name"):
                            c["function"]["name"] += f["name"]
                        if f.get("arguments"):
                            c["function"]["arguments"] += f["arguments"]
    tool_calls = []
    for i in sorted(calls):
        c = calls[i]
        if not c["id"]:
            c["id"] = f"call_{int(time.time() * 1000)}_{i}"
        tool_calls.append(c)
    return {"content": "".join(content), "tool_calls": tool_calls}


# Облачные модели снимают с раздачи без предупреждения (так 20.10 пропала nemotron-3-super у NVIDIA: 404).
# Тогда сами находим замену: из списка моделей провайдера — первую рабочую по этому порядку.
PREFERRED = ["nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3.5-super-vl-120b-a12b",
             "nvidia/nemotron-3-super-120b-a12b",
             "deepseek-ai/deepseek-v4.1-flash", "moonshotai/kimi-k2.6", "z-ai/glm-5.3", "z-ai/glm-5.3-flash",
             "nvidia/llama-3.1-nemotron-ultra-253b-v1", "mistralai/mistral-large-2-instruct", "openai/gpt-oss-20b"]
_dead = set()          # модели, ответившие «нет такой», — до перезапуска не трогаем
_slow = {}             # модель → до какого времени не трогать (тормозила/молчала) — иначе каждый обход ждал бы её снова
# Остальные модели каталога — тоже кандидаты, если из своего списка не ответил никто. Отсекаем то, что не болтает.
NOT_CHAT = ("embed", "rerank", "retriev", "guard", "reward", "safety", "parse", "clip", "nvclip", "vila", "paligemma",
            "kosmos", "deplot", "neva", "fuyu", "detector", "ocr", "translate", "riva", "asr", "tts", "audio", "coder-6.7b",
            "diffusion", "flux", "sdxl", "stable-", "cosmos", "-vl-", "vision",
            "-1b", "-2b", "-3b", "-4b")


def _status(text):
    """Поиск модели виден человеку: в чате и облачком над ней — а не «зависла»."""
    from . import bus
    bus.emit("llm.search", text=text)


def _usable(m):
    return m not in _dead and _slow.get(m, 0) < time.time()


async def _catalog(p):
    key = config.secret(p.get("api_key_env", ""))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(p["base_url"] + "/models", headers={"Authorization": f"Bearer {key}"} if key else {}) as r:
            return [m.get("id") for m in (await r.json()).get("data", []) if m.get("id")]


async def _race(p, models, limit=20):
    """Проверить модели разом (по 6 одновременно), вернуть первую, что ответила с вызовом инструмента."""
    sem = asyncio.Semaphore(6)

    async def one(m):
        async with sem:
            t0 = time.time()
            ok = await _probe(p, m)
            if not ok and time.time() - t0 > 15:
                _slow[m] = time.time() + 1800
            return m if ok else None

    tasks = [asyncio.create_task(one(m)) for m in models]
    try:
        for fut in asyncio.as_completed(tasks, timeout=limit):
            try:
                m = await fut
            except Exception:
                m = None
            if m:
                return m
    except asyncio.TimeoutError:
        pass
    finally:
        for t in tasks:
            t.cancel()
    return None


def _gone(err):
    e = str(err)
    return "HTTP 404" in e or "HTTP 410" in e or "not found" in e.lower() and "HTTP 4" in e


async def _probe(p, model):
    """Жива ли модель и умеет ли инструменты — тем же путём, что настоящий запрос (поток, параметры провайдера,
    запас токенов на размышление). Короткий непотоковый запрос пропускал модели, которые потом отвечали 404."""
    name = next((n for n, v in config["llm"]["providers"].items() if v.get("base_url") == p.get("base_url")), "nvidia")
    tool = [{"type": "function", "function": {"name": "weather", "description": "Погода в городе",
             "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
    try:
        res = await asyncio.wait_for(stream_chat(name, [{"role": "user", "content": "Какая погода в Париже? Узнай инструментом."}],
                                                 tool, None, None, 300, 0.3, model=model), 20)
        return bool(res.get("tool_calls"))
    except Exception as e:
        if _gone(e):
            _dead.add(model)
        return False


async def auto_switch(name):
    """Модель провайдера пропала — найти и сохранить замену. Возвращает новое имя модели или None.
    Сначала свой список, потом остальной каталог; модели проверяются разом, а не по 40 секунд каждая."""
    p = provider(name)
    old = p["model"]
    _dead.add(old)
    try:
        have = await _catalog(p)
    except Exception:
        _status("Не достучалась до каталога моделей — проверьте интернет и ключ")
        return None
    own = [m for m in PREFERRED if m in have and _usable(m)]
    rest = [m for m in have if m not in PREFERRED and _usable(m) and not any(k in m.lower() for k in NOT_CHAT)]
    m = None
    for group, label in ((own, "из проверенных"), (rest[:24], "из остального каталога")):
        if not group:
            continue
        _status(f"Модель {old.split('/')[-1]} больше не отвечает — ищу замену {label} (проверяю {len(group)})…")
        m = await _race(p, group)
        if m:
            break
    if not m:
        _status("Рабочей модели не нашлось — попробую позже. Можно включить локальную модель в настройках.")
        return None
    config.update({"llm": {"providers": {name: {"model": m}}}})
    print(f"модель {old} недоступна — переключилась на {m}", flush=True)
    _status(f"Нашла замену: {m.split('/')[-1]}")
    from . import bus
    bus.emit("notify", title="Модель ИИ сменилась", text=f"{old} больше не отвечает — теперь {m}")
    return m


# ---------- автоподбор: самая быстрая из рабочих моделей «здесь и сейчас» ----------
# Скорость моделей у NVIDIA плавает: 24.10 одна отвечала 52–71 с, соседняя — 2–8 с. Человеку не нужно
# переключать руками: ядро само замеряет модели настоящим запросом (характер + инструмент) и ставит лучшую.
PICK_EVERY = 6 * 3600
_pick = {"at": 0.0, "busy": False, "slow": False, "results": []}


async def _bench(p, model, system):
    """Секунды до готового ответа с вызовом инструмента; None — не умеет инструменты или не ответила."""
    body = {"model": model, "max_tokens": 120, "temperature": 0.3, **(p.get("extra") or {}),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": "Какая погода в Париже?"}],
            "tools": [{"type": "function", "function": {"name": "weather", "description": "Погода в городе",
                       "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]}
    key = config.secret(p.get("api_key_env", ""))
    t0 = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(p["base_url"] + "/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {key}"} if key else {}) as r:
                if r.status != 200:
                    if r.status in (404, 410):
                        _dead.add(model)
                    return None
                ok = bool((await r.json())["choices"][0]["message"].get("tool_calls"))
    except Exception:
        return None
    return round(time.time() - t0, 1) if ok else None


async def auto_pick(name="nvidia", reason="плановая проверка", force=False):
    """Замерить кандидатов и поставить самую быструю рабочую модель. Возвращает её имя или None.
    force — попросили явно (голосом/кнопкой): и при выключенном автоподборе."""
    if _pick["busy"] or not (force or config.get("llm.auto_model", True)):
        return None
    p = provider(name)
    if p.get("server_exe") or "nvidia" not in p.get("base_url", ""):
        return None
    _pick["busy"] = True
    try:
        key = config.secret(p.get("api_key_env", ""))
        if not key:
            return None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(p["base_url"] + "/models", headers={"Authorization": f"Bearer {key}"}) as r:
                    have = {m.get("id") for m in (await r.json()).get("data", [])}
        except Exception:
            return None
        from . import persona
        system = persona.system_prompt(True)                 # настоящий размер запроса — на коротком все быстрые
        results = []
        for m in [m for m in PREFERRED if m in have and _usable(m)][:6]:
            sec = await _bench(p, m, system)
            results.append((m, sec))
            print(f"автоподбор модели: {m} → {sec if sec is not None else 'не подходит'}", flush=True)
        _pick.update(at=time.time(), slow=False, results=results)
        ok = sorted((sec, m) for m, sec in results if sec is not None)
        if not ok:
            return None
        best = ok[0][1]
        old = p["model"]
        # меняем, только если заметно быстрее: иначе модель скакала бы от каждой случайной секунды
        cur = next((sec for m, sec in results if m == old), None)
        if best != old and (cur is None or ok[0][0] < cur * 0.7 - 1):
            config.update({"llm": {"providers": {name: {"model": best}}}})
            print(f"автоподбор: {old} → {best} ({reason})", flush=True)
            from . import bus
            bus.emit("notify", title="Модель ИИ", text=f"Переключилась на более быструю: {best.split('/')[-1]} ({ok[0][0]} с)")
        return best
    finally:
        _pick["busy"] = False


def note_slow(seconds):
    """Ответ шёл долго — при следующей возможности перепроверить модели."""
    if seconds > 20:
        _pick["slow"] = True


async def auto_pick_loop():
    await asyncio.sleep(30)                                  # не мешаем запуску: Whisper и голос грузятся первыми
    while True:
        due = time.time() - _pick["at"] > PICK_EVERY
        if due or (_pick["slow"] and time.time() - _pick["at"] > 300):
            try:
                await auto_pick(reason="ответы шли медленно" if _pick["slow"] else "плановая проверка")
            except Exception as e:
                print("автоподбор модели не удался:", e, flush=True)
        await asyncio.sleep(60)


async def _detour(name, messages, tools, on_text, on_reasoning, max_tokens, temperature, errors):
    """Модель сбоит или молчит — этот запрос отдаём трём запасным РАЗОМ и берём первый ответ.
    Настройки не меняем: сбой временный, основная модель вернётся сама. Тормозившую — полчаса не трогаем."""
    cur = provider(name)["model"]
    _slow[cur] = time.time() + 600
    cands = [m for m in PREFERRED if m != cur and _usable(m)][:3]
    if not cands:
        return None
    _status(f"{cur.split('/')[-1]} не отвечает — спрашиваю запасные модели ({len(cands)})…")

    async def one(m):
        t0 = time.time()
        try:
            return m, await stream_chat(name, messages, tools, None, None, max_tokens, temperature, model=m)
        except (aiohttp.ClientError, asyncio.TimeoutError, LLMError) as e:
            errors.append(f"{name}/{m}: {e}")
            if _gone(e):
                _dead.add(m)
            elif time.time() - t0 > 15:
                _slow[m] = time.time() + 1800
            return m, None

    tasks = [asyncio.create_task(one(m)) for m in cands]
    try:
        for fut in asyncio.as_completed(tasks):
            m, res = await fut
            if res:
                print(f"{cur} сбоит — ответила {m}", flush=True)
                _status(f"Ответила запасная модель: {m.split('/')[-1]}")
                _pick["slow"] = True
                if on_text and res.get("content"):        # потоком не выйдет — запасные шли параллельно; отдаём целиком
                    on_text(res["content"])
                return res
    finally:
        for t in tasks:
            t.cancel()
    return None


async def chat(messages, tools=None, on_text=None, on_reasoning=None, on_switch=None, prefer=None,
               temperature=0.7, max_tokens=2048, model=None):
    """Основная модель, при сбое — запасная. on_switch сообщает интерфейсу о переключении.
    model — особая модель основного провайдера для этого запроса (ответ в характере пишет качественная, а не быстрая);
    не ответила — тот же запрос обычной моделью."""
    order = [prefer] if prefer else [config.get("llm.primary"), config.get("llm.fallback")]
    errors = []
    if model and order[0] and not provider(order[0]).get("server_exe") and _usable(model):
        try:
            t0 = time.time()
            res = await asyncio.wait_for(stream_chat(order[0], messages, tools, on_text, on_reasoning, max_tokens, temperature,
                                                     model=model), 45)
            return res, order[0]
        except (aiohttp.ClientError, asyncio.TimeoutError, LLMError) as e:
            errors.append(f"{order[0]}/{model}: {e}")
            if _gone(e):
                _dead.add(model)
            else:
                _slow[model] = time.time() + 600
    for name in [n for n in order if n]:
        for attempt in range(2):
            try:
                if on_switch and (errors or attempt):
                    on_switch(name)
                t0 = time.time()
                res = await stream_chat(name, messages, tools, on_text, on_reasoning, max_tokens, temperature)
                note_slow(time.time() - t0)
                return res, name
            except (aiohttp.ClientError, asyncio.TimeoutError, LLMError) as e:
                errors.append(f"{name}: {e}")
                if "429" in str(e) and attempt == 0:
                    await asyncio.sleep(5)
                    continue
                if _gone(e) and attempt == 0 and not provider(name).get("server_exe") and await auto_switch(name):
                    continue                 # нашлась замена — повторяем тот же запрос на ней
                slow = isinstance(e, asyncio.TimeoutError) or "Timeout" in str(e)
                if (slow or "HTTP 5" in str(e) and attempt == 1) and not provider(name).get("server_exe"):
                    res = await _detour(name, messages, tools, on_text, on_reasoning, max_tokens, temperature, errors)
                    if res:
                        return res, name
                break
    raise LLMError("; ".join(errors))


async def simple(prompt, system=None, prefer=None, max_tokens=512):
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    res, _ = await chat(msgs, prefer=prefer, temperature=0.3, max_tokens=max_tokens)
    return res["content"]
