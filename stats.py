"""Site stats: GitHub + itch.io + Telegram -> stats.json, then commit and push gh-pages if the numbers changed.

The itch API key stays on this PC (read from dev/.env); only the numbers go to the site.
Run: dev/.venv/Scripts/python.exe site/stats.py   (--no-push to only write the file, --selftest for the parsers)
Windows Task Scheduler runs it every 3 hours (task "Shikigami site stats").
"""
import json, re, subprocess, sys, time, urllib.request
from pathlib import Path

SITE = Path(__file__).resolve().parent
ENV = SITE.parent / "dev" / ".env"
OUT = SITE / "stats.json"
REPO = "Timkin1337/Shikigami"
ITCH_PAGE = "https://timkin-died.itch.io/shikigami"
TG = "https://t.me/shikigami_ai"
HISTORY_MAX = 1000                                   # ~4 months of 3-hour points


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "shikigami-site-stats"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def env(name):
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit(f"{name} not found in {ENV}")


def count_int(text):
    return int(re.sub(r"[^\d]", "", text) or 0)


def tg_subscribers(html):
    m = re.search(r'tgme_page_extra">([\d\s ,]+)\s*(subscribers|members|подписчик)', html)
    return count_int(m.group(1)) if m else None


def itch_rating(html):
    # shortcut: reads the schema.org block itch adds once a game has ratings; 0 until then
    v = re.search(r'"ratingValue"\s*:\s*"?([\d.]+)', html)
    c = re.search(r'"ratingCount"\s*:\s*"?(\d+)', html)
    return (float(v.group(1)) if v else None, int(c.group(1)) if c else 0)


def itch_comments(html):
    # shortcut: counts post blocks on the comments page (first page only), fine while there are < ~20 comments
    return len(re.findall(r'class="community_post(?:\s[^"]*)?"\s', html))


def collect():
    gh = json.loads(get(f"https://api.github.com/repos/{REPO}"))
    rels = json.loads(get(f"https://api.github.com/repos/{REPO}/releases?per_page=100"))
    per_rel = {r["tag_name"].lstrip("v"): sum(a["download_count"] for a in r["assets"]) for r in rels}
    games = json.loads(get(f"https://itch.io/api/1/{env('ITCH_API_KEY')}/my-games")).get("games", [])
    game = next(g for g in games if g["id"] == 5238658)
    page = get(ITCH_PAGE)
    rating, ratings = itch_rating(page)
    s = {
        "github": {"downloads": sum(per_rel.values()), "per_release": per_rel, "stars": gh["stargazers_count"],
                   "forks": gh["forks_count"], "issues": gh["open_issues_count"],
                   "version": rels[0]["tag_name"].lstrip("v") if rels else ""},
        "itch": {"views": game["views_count"], "downloads": game["downloads_count"], "rating": rating, "ratings": ratings,
                 "comments": itch_comments(get(ITCH_PAGE + "/comments")),
                 "devlogs": get(ITCH_PAGE + "/devlog.rss").count("<item>")},
        "telegram": {"subscribers": tg_subscribers(get(TG))},
    }
    s["total_downloads"] = s["github"]["downloads"] + s["itch"]["downloads"]
    return s


def main():
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    new = collect()
    if {k: v for k, v in old.items() if k not in ("updated", "history")} == new:
        print("no change"); return
    point = {"t": int(time.time()), "dl": new["total_downloads"], "views": new["itch"]["views"],
             "stars": new["github"]["stars"], "tg": new["telegram"]["subscribers"]}
    new["history"] = (old.get("history", []) + [point])[-HISTORY_MAX:]
    new["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print("updated:", new["total_downloads"], "downloads,", new["itch"]["views"], "views")
    if "--no-push" in sys.argv:
        return
    git = lambda *a: subprocess.run(["git", "-C", str(SITE), *a], check=True, capture_output=True)
    git("add", "stats.json")
    git("commit", "-m", "Статистика сайта")
    git("push", "-q")


def selftest():
    assert tg_subscribers('<div class="tgme_page_extra">1 234 subscribers</div>') == 1234
    assert tg_subscribers('<div class="tgme_page_extra">12 subscribers</div>') == 12
    assert itch_rating('{"ratingValue":"4.8","ratingCount":"15"}') == (4.8, 15)
    assert itch_rating("<html></html>") == (None, 0)
    assert itch_comments('<div class="community_post has_vote_column" id="p1"><div class="community_post" x>') == 2
    assert itch_comments('<div class="community_post_list_widget base_widget">') == 0
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
