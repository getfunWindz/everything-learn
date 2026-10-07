"""播客内容源适配器：通用 RSS/Atom + Apple Podcasts 链接

- 音频 → 复用流水线 Whisper；`podcast:transcript` 存在时直接解析（vtt/srt）免转写；
- 无画面媒体（fetch_media_url(video) → None，多模态复检自动跳过）；
- 尽调结论与实现要点见 docs/plans/2026-10-08-m2-podcast.md（RSS 无鉴权；Apple 经 iTunes 查找换 feedUrl）。
"""
import re

from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.subtitles import parse_srt, parse_vtt

_UA = {"User-Agent": "Mozilla/5.0"}
_MATCH_HOSTS = ("feeds.", "simplecast.com", "megaphone.fm", "acast.com", "blubrry.com",
                "fireside.fm", "anchor.fm", "podcasts.apple.com", "npr.org")
_MATCH_PATH = re.compile(r"(/feed|/rss|\.rss$|\.xml$|podcast)", re.I)
_APPLE_ID = re.compile(r"/id(\d+)")


def parse_duration(text) -> int:
    """itunes:duration 归一化：'280' | '4:40' | '5:21:57' → 秒"""
    text = (text or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _itunes_lookup_feed(podcast_id: str) -> str:
    """Apple Podcasts id → RSS feedUrl（iTunes 公开查找 API，无需 key）"""
    import requests
    r = requests.get("https://itunes.apple.com/lookup", params={"id": podcast_id},
                     headers=_UA, timeout=30)
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    feed = results[0].get("feedUrl") if results else ""
    if not feed:
        raise ValueError(f"Apple 查找失败：未找到 id={podcast_id} 的 feedUrl（可改用其 RSS 地址）")
    return feed


@register
class PodcastAdapter:
    name = "podcast"
    kinds = {"audio"}

    def __init__(self, client=None):
        self.lang = None
        self._feed_cache = {}

    # ---------------- 路由 ----------------

    def match(self, url) -> bool:
        if not url:
            return False
        u = url.strip()
        if "podcasts.apple.com" in u:
            return True
        host_path = u.split("://", 1)[-1]
        host = host_path.split("/", 1)[0].lower()
        if any(h in host for h in _MATCH_HOSTS):
            return True
        return bool(_MATCH_PATH.search(host_path))

    def sniff(self, url) -> bool:
        """无特征 URL 的 XML 探测（仅在 match 全未命中且为 http(s) 时被调用）"""
        import requests
        r = requests.get(url, headers=_UA, timeout=10)
        ct = (r.headers.get("content-type") or "").lower()
        if any(k in ct for k in ("xml", "rss", "atom")):
            return True
        head = (r.text or "")[:300].lower()
        return any(k in head for k in ("<?xml", "<rss", "<feed"))

    def resolve(self, q) -> ItemRef:
        q = (q or "").strip()
        if "podcasts.apple.com" in q:
            m = _APPLE_ID.search(q)
            if not m:
                raise ValueError(f"无法从 Apple 链接解析播客 ID：{q}")
            feed = _itunes_lookup_feed(m.group(1))
            return ItemRef(platform="podcast", item_id=feed, url=feed,
                           extra={"feed": feed, "apple_id": m.group(1)})
        if not self.match(q):
            raise ValueError(f"不是播客 RSS/Apple 链接：{q}")
        return ItemRef(platform="podcast", item_id=q, url=q, extra={"feed": q})

    # ---------------- feed 获取与解析 ----------------

    def _fetch_feed(self, feed_url: str) -> str:
        if feed_url not in self._feed_cache:
            import requests
            from core.retry import retry_with_backoff

            def _once():
                r = requests.get(feed_url, headers=_UA, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {r.status_code}")
                r.raise_for_status()
                r.encoding = r.encoding or "utf-8"
                return r.text
            self._feed_cache[feed_url] = retry_with_backoff(_once, attempts=3, base_delay=2.0)
        return self._feed_cache[feed_url]

    def _download(self, url: str) -> str:
        """转写稿下载（429/5xx 退避重试）"""
        import requests
        from core.retry import retry_with_backoff

        def _once():
            r = requests.get(url, headers=_UA, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        return retry_with_backoff(_once, attempts=3, base_delay=2.0)

    @staticmethod
    def _parse_feed(xml_text: str) -> dict:
        import xml.etree.ElementTree as ET
        NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
              "podcast": "https://podcastindex.org/namespace/1.0"}
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            raise ValueError("不是有效的播客 feed（缺少 channel）")

        def _text(el, path):
            node = el.find(path, NS)
            return (node.text or "").strip() if node is not None and node.text else ""

        feed = {"title": _text(channel, "title"),
                "author": _text(channel, "itunes:author") or _text(channel, "author"),
                "items": []}
        for i, it in enumerate(channel.findall("item"), 1):
            enc = it.find("enclosure")
            tr = it.find("podcast:transcript", NS)
            feed["items"].append({
                "index": i,
                "guid": _text(it, "guid") or (enc.get("url") if enc is not None else f"item-{i}"),
                "title": _text(it, "title"),
                "link": _text(it, "link"),
                "published": _text(it, "pubDate"),
                "duration": parse_duration(_text(it, "itunes:duration")),
                "enclosure": (enc.get("url") if enc is not None and enc.get("url") else ""),
                "transcript_url": (tr.get("url") if tr is not None else "") or "",
                "transcript_type": (tr.get("type") if tr is not None else "") or "",
            })
        return feed

    # ---------------- 列表 / 元信息 ----------------

    def list_items(self, ref):
        feed_url = ref.extra.get("feed") or ref.url
        feed = self._parse_feed(self._fetch_feed(feed_url))
        items = []
        for ep in feed["items"]:
            items.append(ItemRef(
                platform="podcast", item_id=ep["guid"], url=ep["link"] or feed_url,
                sub_id=str(ep["index"]), title=ep["title"], duration=ep["duration"],
                extra={"feed": feed_url, "enclosure": ep["enclosure"],
                       "transcript_url": ep["transcript_url"],
                       "transcript_type": ep["transcript_type"],
                       "published": ep["published"],
                       "feed_title": feed["title"], "feed_author": feed["author"]}))
        return items

    def fetch_meta(self, ref) -> ItemMeta:
        feed_title = ref.extra.get("feed_title")
        feed_author = ref.extra.get("feed_author")
        if not feed_title:
            feed = self._parse_feed(self._fetch_feed(ref.extra.get("feed") or ref.url))
            feed_title, feed_author = feed["title"], feed["author"]
        return ItemMeta(title=feed_title or ref.title, author=feed_author or "",
                        url=ref.url, published=ref.extra.get("published") or "",
                        duration=ref.duration, desc="", page=str(ref.sub_id or ""),
                        page_part=ref.title or "", extra={"feed": ref.extra.get("feed")})

    # ---------------- 内容 / 媒体 ----------------

    def fetch_content(self, ref) -> ContentResult:
        url = ref.extra.get("transcript_url") or ""
        if not url:
            return ContentResult(kind="timeline", status="empty", reason="无转写稿（将走 Whisper）")
        try:
            text = self._download(url)
        except Exception as e:
            return ContentResult(kind="timeline", status="empty",
                                 reason=f"转写稿下载失败：{str(e)[:80]}")
        ttype = (ref.extra.get("transcript_type") or "").lower()
        segs = parse_srt(text) if "srt" in ttype else parse_vtt(text)
        if not segs:
            return ContentResult(kind="timeline", status="empty", reason="转写稿解析为空")
        duration = float(ref.duration or 0)
        last = segs[-1]["end"]
        coverage = (last / duration) if duration else 1.0
        if len(segs) < 10 or coverage < 0.5:
            return ContentResult(kind="timeline", status="suspect", segments=segs,
                                 label="字幕(转写稿)",
                                 reason=f"覆盖不足({len(segs)}段 末条{last:.0f}s)")
        return ContentResult(kind="timeline", status="ok", segments=segs, label="字幕(转写稿)")

    def fetch_media_url(self, ref, media: str = "audio", lowest: bool = False):
        if media == "video":
            return None       # 播客无画面（多模态复检自动跳过）
        return ref.extra.get("enclosure") or None
