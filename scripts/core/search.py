"""跨平台关键词搜索（B1，2026-10-17）

统一 `el.py search --platform <p>` 的取数层：
- native：bilibili（ApiClient.search）/ 微信公众号（搜狗，实验性）；
- api：YouTube（yt-dlp ytsearch）、播客（iTunes Search API）；
- browser：知乎 / 微博 / X / 小红书 / 抖音 / Medium / TED（复用已登录 profile）。

返回统一结构：{"platform","title","url","author","duration","duration_text"}。
注意：浏览器搜索只负责“找到候选”，抓取仍走 `run <url>`（小红书链接需 App 分享 token）。
"""
import json
import re
import urllib.parse

from core import browserfetch

PLATFORM_SEARCH = {
    "bilibili": "native",
    "wechat_mp": "native",
    "youtube": "api",
    "podcast": "api",
    "zhihu": "browser",
    "weibo": "browser",
    "x_twitter": "browser",
    "xiaohongshu": "browser",
    "douyin": "browser",
    "medium": "browser",
    "ted": "browser",
}

BROWSER_SPECS = {
    "zhihu": {"url": "https://www.zhihu.com/search?type=content&q={q}",
              "pattern": r"zhihu\.com/(?:question/\d+|p/\d+)", "title_mode": "first"},
    "weibo": {"url": "https://s.weibo.com/weibo?q={q}",
              "pattern": r"weibo\.com/\d+/\w+", "title_mode": "first"},
    "x_twitter": {"url": "https://x.com/search?q={q}&f=live",
                  "pattern": r"x\.com/[^/]+/status/\d+", "title_mode": "longest"},
    "xiaohongshu": {"url": "https://www.xiaohongshu.com/search_result?keyword={q}",
                    "pattern": r"xiaohongshu\.com/explore/[0-9a-f]{24}", "title_mode": "first"},
    "douyin": {"url": "https://www.douyin.com/search/{q}?type=video", "wait_ms": 7000, "esc": True,
               "pattern": r"douyin\.com/video/\d+", "title_mode": "longest"},
    "medium": {"url": "https://medium.com/search?q={q}",
               "pattern": r"medium\.com/@[^/]+/[a-z0-9-]+-[0-9a-f]{8,}", "title_mode": "first"},
    "ted": {"url": "https://www.ted.com/talks?sort=newest&q={q}",
            "pattern": r"ted\.com/talks/[a-z0-9_]+$", "title_mode": "first"},
}


class SearchUnavailable(ValueError):
    """平台不支持搜索 / 依赖缺失（可读）"""


def available() -> dict:
    return dict(PLATFORM_SEARCH)


def _duration_text(seconds) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}:{s % 60:02d}" if s > 0 else ""


_DUR_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")


def _clean_lines(text: str):
    """拆行并去噪：时长、时间戳、「来自 …」、单符号行"""
    lines, duration = [], ""
    for ln in (text or "").splitlines():
        ln = ln.strip().strip("·").strip()
        if not ln:
            continue
        if _DUR_RE.match(ln):
            duration = duration or ln
            continue
        if re.match(r"^(今天|昨天|前天|\d{1,2}月\d{1,2}日|\d{1,2}-\d{1,2}|"
                    r"\d+分钟前|\d+小时前|\d+天前)", ln):
            continue
        if "来自" in ln and len(ln) <= 30:
            continue
        if ln in ("·", "•"):
            continue
        if ln.isdigit() and len(ln) <= 8:      # 抖音卡片里的点赞数
            continue
        lines.append(ln)
    return lines, duration


def _pick_title_author(lines, mode: str):
    if not lines:
        return "", ""
    if mode == "longest":
        cand = [l for l in lines if 6 <= len(l) <= 120] or lines
        title = max(cand, key=len)[:120]
    else:
        title = lines[0][:120]
    author = ""
    for l in lines:
        if l != title and len(l) <= 40:
            author = l[:60]
            break
    return title, author


def _anchor_items(anchors, pattern, limit, platform, title_mode="first") -> list:
    """从浏览器锚点（[{href,own,card}]）提取候选；按 URL 去重 + 过滤导航链接"""
    rx = re.compile(pattern)
    seen, out = set(), []
    for a in anchors or []:
        href = (a or {}).get("href") or ""
        if not href or href in seen or not rx.search(href):
            continue
        if platform == "ted" and href.rstrip("/").endswith("/talks"):
            continue
        seen.add(href)
        own, dur1 = _clean_lines((a or {}).get("own") or "")
        card, dur2 = _clean_lines((a or {}).get("card") or "")
        duration = dur1 or dur2
        lines = own or card
        if not lines:
            continue
        title, author = _pick_title_author(lines, title_mode)
        if not title:
            continue
        out.append({"platform": platform, "title": title[:120], "url": href,
                    "author": author, "duration": None, "duration_text": duration})
        if len(out) >= limit:
            break
    return out


_X_JS = r"""(() => Array.from(document.querySelectorAll('article')).map(art => {
  const links = [...art.querySelectorAll('a[href*="/status/"]')]
    .map(a => a.href).filter(h => !/\/analytics|\/photo\//.test(h));
  return {href: links[0] || '', own: ((art.innerText || '').trim()).slice(0, 400), card: ''};
}))()"""


_WEIBO_JS = """(() => Array.from(document.querySelectorAll('div.card-wrap')).map(c => {
  const a = c.querySelector('.from a') || c.querySelector('a[href*="weibo.com/"]');
  const t = c.querySelector('.txt') || c;
  return {href: a ? a.href : '', own: ((t.innerText || '').trim()).slice(0, 300), card: ''};
}))()"""


_BROWSER_JS = """(() => Array.from(document.querySelectorAll('a')).map(a => {
  const card = a.closest('article, li, section') || a.parentElement;
  return {href: a.href || '',
          own: ((a.innerText || '').trim()).slice(0, 300),
          card: (((card && card.innerText) || '').trim()).slice(0, 300)};
}))()"""


_SPECIAL_JS = {"weibo": _WEIBO_JS, "x_twitter": _X_JS}


def _browser_anchors(platform: str, url: str, wait_ms: int = 4000, esc: bool = False) -> list:
    js = _SPECIAL_JS.get(platform, _BROWSER_JS)
    with browserfetch.BrowserSession(platform) as s:
        s.goto(url, wait_until="domcontentloaded")
        s.pause(wait_ms)
        if esc:
            try:
                s.page.keyboard.press("Escape")
            except Exception:
                pass
            s.pause(1500)
        try:
            s.scroll_wheel(times=1)
        except Exception:
            pass
        return s.evaluate(js) or []


def _browser_search(platform: str, keyword: str, limit: int) -> list:
    spec = BROWSER_SPECS.get(platform)
    if not spec:
        raise SearchUnavailable(f"{platform} 暂不支持搜索")
    url = spec["url"].format(q=urllib.parse.quote(keyword))
    anchors = _browser_anchors(platform, url, wait_ms=spec.get("wait_ms", 4000),
                               esc=spec.get("esc", False))
    return _anchor_items(anchors, spec["pattern"], limit, platform,
                         title_mode=spec.get("title_mode", "first"))


def _youtube_hits(info, limit: int) -> list:
    out = []
    for e in (info or {}).get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append({"platform": "youtube", "title": e.get("title") or "",
                    "url": f"https://www.youtube.com/watch?v={e['id']}",
                    "author": e.get("uploader") or e.get("channel") or "",
                    "duration": e.get("duration"),
                    "duration_text": _duration_text(e.get("duration"))})
        if len(out) >= limit:
            break
    return out


def _youtube_search(keyword: str, limit: int) -> list:
    from providers.youtube import YouTubeAdapter
    adapter = YouTubeAdapter()
    info = adapter._extract(f"ytsearch{int(limit)}:{keyword}", extract_flat=True)
    return _youtube_hits(info, limit)


def _itunes_hits(payload, limit: int) -> list:
    out = []
    for it in (payload or {}).get("results") or []:
        url = it.get("trackViewUrl") or ""
        if not url:
            continue
        ms = it.get("trackTimeMillis")
        out.append({"platform": "podcast",
                    "title": it.get("trackName") or "",
                    "url": url,
                    "author": it.get("collectionName") or "",
                    "duration": (round(ms / 1000) if ms else None),
                    "duration_text": _duration_text((ms / 1000) if ms else None)})
        if len(out) >= limit:
            break
    return out


def _itunes_search(keyword: str, limit: int) -> list:
    import requests
    r = requests.get("https://itunes.apple.com/search",
                     params={"term": keyword, "entity": "podcastEpisode",
                             "limit": max(1, limit), "country": "us"},
                     timeout=20)
    r.raise_for_status()
    return _itunes_hits(r.json(), limit)


def _native_bilibili(keyword: str, limit: int) -> list:
    from providers.bilibili import ApiClient
    results = ApiClient().search(keyword, limit=limit)
    return [{"platform": "bilibili", "title": r.get("title") or "",
             "url": f"https://www.bilibili.com/video/{r['bvid']}"
                    if r.get("bvid") else "",
             "author": r.get("author") or "",
             "duration": r.get("duration"),
             "duration_text": r.get("duration") or ""}
            for r in results if r.get("bvid")]


def _native_wechat(keyword: str, limit: int) -> list:
    from core import registry
    registry.load_providers()
    adapter = registry.get("wechat_mp")
    if adapter is None:
        raise SearchUnavailable("未注册 wechat_mp 适配器")
    hits = adapter.search_articles(keyword, limit=limit)
    return [{"platform": "wechat_mp", "title": h.get("title") or "",
             "url": "", "author": h.get("account") or "",
             "duration": None, "duration_text": "",
             "note": "搜狗链接会话绑定，抓取请用 run --platform wechat --pick N",
             "date": h.get("date") or ""} for h in hits]


def search(platform: str, keyword: str, limit: int = 5) -> list:
    """统一搜索入口；未知平台/依赖缺失 → SearchUnavailable（可读）"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise SearchUnavailable("关键词为空")
    platform = "wechat_mp" if platform == "wechat" else platform
    kind = PLATFORM_SEARCH.get(platform)
    if not kind:
        raise SearchUnavailable(
            f"{platform} 暂未接入搜索；可提供链接，或运行 el.py platforms 查看可用平台")
    try:
        if platform == "bilibili":
            return _native_bilibili(keyword, limit)
        if platform == "wechat_mp":
            return _native_wechat(keyword, limit)
        if platform == "youtube":
            return _youtube_search(keyword, limit)
        if platform == "podcast":
            return _itunes_search(keyword, limit)
        return _browser_search(platform, keyword, limit)
    except SearchUnavailable:
        raise
    except Exception as e:
        raise SearchUnavailable(f"{platform} 搜索失败：{str(e)[:120]}")


def to_json(hits: list) -> str:
    return json.dumps(hits, ensure_ascii=False, indent=1)
