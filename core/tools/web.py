"""Поиск в интернете (DuckDuckGo без ключа) и чтение страниц."""
import asyncio
import re

import aiohttp
import yarl
from bs4 import BeautifulSoup

from . import tool, S, I

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/141.0 Safari/537.36", "Accept-Language": "ru,en;q=0.8"}


@tool("web_search", "Поиск в интернете. Для свежих новостей, фактов, цен, релизов — всего, чего ты не знаешь наверняка.",
      {"query": S("Поисковый запрос"), "count": I("Сколько результатов, по умолчанию 6")}, ["query"])
async def web_search(query, count=6):
    async with aiohttp.ClientSession(headers=UA) as s:
        async with s.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"},
                          timeout=aiohttp.ClientTimeout(total=15)) as r:
            html = await r.text()
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for res in soup.select(".result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            from urllib.parse import unquote
            href = unquote(m.group(1))
        if "duckduckgo.com/y.js" in href:  # реклама
            continue
        snip = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True), "url": href,
                    "snippet": snip.get_text(" ", strip=True) if snip else ""})
        if len(out) >= count:
            break
    return {"results": out} if out else {"error": "поиск ничего не вернул"}


def _private(url):
    """Страница из интернета не должна заставить нас читать локальные службы (ядро, роутер)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    u = urlparse(url if "://" in url else "https://" + url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return True
    try:
        ips = {a[4][0] for a in socket.getaddrinfo(u.hostname, None)}
    except OSError:
        return False
    # не is_private: «не глобальный» ловит ещё 0.0.0.0, 100.64/10 (CGNAT) и прочие служебные сети
    return any(not ipaddress.ip_address(ip.split("%")[0]).is_global for ip in ips)


@tool("fetch_page", "Прочитать текст веб-страницы по ссылке (после web_search, чтобы узнать подробности).",
      {"url": S("Адрес страницы"), "max_chars": I("По умолчанию 6000")}, ["url"])
async def fetch_page(url, max_chars=6000):
    url = url if "://" in url else "https://" + url
    async with aiohttp.ClientSession(headers=UA) as s:
        # переадресации проверяем сами: иначе чужой сайт отправит нас редиректом на 127.0.0.1 или роутер
        for _ in range(6):
            if await asyncio.to_thread(_private, url):
                return {"error": "локальные и домашние адреса не открываю"}
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as r:
                if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    url = str(r.url.join(yarl.URL(r.headers["Location"])))
                    continue
                html = await r.text(errors="replace")
                break
        else:
            return {"error": "слишком много переадресаций"}
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        t.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\n\s*\n+", "\n\n", main.get_text("\n", strip=True))
    return {"title": soup.title.get_text(strip=True) if soup.title else "", "text": text[:max_chars]}
