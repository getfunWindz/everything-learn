"""Medium 适配器（document）：单篇文章（浏览器辅助过 Cloudflare）+ feed 通道（纯 requests）。

尽调（2026-10-08）：单篇的页面与 `?format=json` 均在 Cloudflare 后，headless 被拒、
`cf_clearance` 无法迁移给 requests → 必须走 core.browserfetch（有头+持久化 profile）；
feed（medium.com/feed/...）可用 requests 直取（content:encoded 含全文）。
段落类型：1=P / 3=大标题 / 13=次级标题 / 9=代码 / 4=图片（metadata.id → miro CDN）。
付费墙字段：isSubscriptionLocked / isMarkedPaywallOnly / isLockedResponse → suspect。
"""
import re

import requests

from core import browserfetch
from core.adapter import SearchNeeded
from core.htmlblocks import html_to_blocks
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_ARTICLE_RE = re.compile(r"medium\.com/(?!feed/)[^\s?#]+-[0-9a-f]{6,}(?:[?#]|$)", re.I)
_FEED_RE = re.compile(r"medium\.com/feed/", re.I)
_HASH_RE = re.compile(r"-([0-9a-f]{6,})(?:[?#]|$)", re.I)
_IMG_BASE = "https://miro.medium.com/v2/resize:fit:1400/{id}"


class MediumError(ValueError):
    """Medium 取数失败（可读异常）"""


def medium_to_blocks(payload: dict):
    """bodyModel.paragraphs → (blocks, images)；标题去重、代码/图注保留"""
    v = ((payload or {}).get("payload") or {}).get("value") or {}
    paras = ((v.get("content") or {}).get("bodyModel") or {}).get("paragraphs") or []
    title = v.get("title") or ""
    blocks, images = [], []
    cur = {"heading": "", "paras": []}

    def flush():
        if cur["heading"] or cur["paras"]:
            blocks.append({"heading": cur["heading"], "text": "\n\n".join(cur["paras"]).strip()})
        cur["heading"], cur["paras"] = "", []

    for p in paras:
        t = p.get("type")
        text = (p.get("text") or "").strip()
        if t in (3, 13):                       # 大标题 / 次级标题
            if not text or text == title:      # 标题段与文章标题重复 → 跳过
                continue
            flush()
            cur["heading"] = text
        elif t == 4:                           # 图片
            md = p.get("metadata") or {}
            if md.get("id"):
                images.append(_IMG_BASE.format(id=md["id"]))
            if text:                           # 图注保留为文本
                cur["paras"].append(f"（图：{text}）")
        elif text:                             # 1=P / 9=代码 / 7=引用 / 其他带文本
            cur["paras"].append(text)
    flush()
    return blocks, images


def payload_status(payload: dict):
    """付费墙判定 → (status, reason)"""
    v = ((payload or {}).get("payload") or {}).get("value") or {}
    if v.get("isSubscriptionLocked") or v.get("isMarkedPaywallOnly") or v.get("isLockedResponse"):
        return "suspect", "会员/付费墙（内容可能不完整）"
    return "ok", ""


def parse_feed(xml_text: str) -> list:
    """RSS → [{title, link, published, author, content}]（content=content:encoded 全文）"""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    ns = {"content": "http://purl.org/rss/1.0/modules/content/",
          "dc": "http://purl.org/dc/elements/1.1/"}
    root = ET.fromstring(xml_text)
    out = []
    for it in root.findall(".//item"):
        enc = it.find("content:encoded", ns)
        content = (enc.text if enc is not None else "") or ""
        pub = (it.findtext("pubDate") or "").strip()
        try:
            pub = parsedate_to_datetime(pub).strftime("%Y-%m-%d") if pub else ""
        except Exception:
            pub = pub[:10]
        out.append({"title": (it.findtext("title") or "").strip(),
                    "link": (it.findtext("link") or "").strip(),
                    "published": pub,
                    "author": (it.findtext("dc:creator", namespaces=ns) or "").strip(),
                    "content": content})
    return out


def _ms_to_date(v) -> str:
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(v) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


@register
class MediumAdapter:
    name = "medium"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("medium")
            self._session.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        return bool(_ARTICLE_RE.search(url or "") or _FEED_RE.search(url or ""))

    def resolve(self, q: str) -> ItemRef:
        if _FEED_RE.search(q or ""):
            url = (q or "").split("?")[0].rstrip("/")
            slug = url.rsplit("/", 1)[-1] or "feed"
            return ItemRef(platform="medium", item_id=f"feed-{slug}", url=url, sub_id="1",
                           extra={"kind": "feed"})
        m = _ARTICLE_RE.search(q or "")
        if not m:
            raise SearchNeeded(q)
        url = (q or "").split("?")[0].split("#")[0]
        h = _HASH_RE.search(url)
        if not h:
            raise SearchNeeded(q)
        return ItemRef(platform="medium", item_id=f"article-{h.group(1)}", url=url, sub_id="1",
                       extra={"kind": "article", "hash": h.group(1)})

    def list_items(self, ref):
        if ref.extra.get("kind") != "feed":
            return [ref]
        xml = self._get_feed(ref.url)
        items = []
        for i, it in enumerate(parse_feed(xml), 1):
            link = (it.get("link") or "").split("?")[0]
            h = _HASH_RE.search(link)
            if not (link and h):
                continue
            items.append(ItemRef(platform="medium", item_id=f"article-{h.group(1)}", url=link,
                                 sub_id=str(i), title=it.get("title", ""),
                                 extra={"kind": "article", "hash": h.group(1),
                                        "feed_title": it.get("title", ""),
                                        "feed_author": it.get("author", ""),
                                        "feed_published": it.get("published", ""),
                                        "feed_content": it.get("content", "")}))
        return items or [ref]

    # ---------------- 取数 ----------------

    def _get_feed(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        if r.status_code != 200:
            raise MediumError(f"feed 不可达（HTTP {r.status_code}；若被 CF 拦截请稍后重试）")
        return r.text

    def _fetch_payload(self, ref) -> dict:
        """单篇：浏览器内取 ?format=json（CF 平台）"""
        return browserfetch.fetch_json(f"{ref.url}?format=json", warm_url=ref.url)

    # ---------------- 元信息 / 内容 ----------------

    def fetch_meta(self, ref) -> ItemMeta:
        extra = ref.extra or {}
        if extra.get("feed_content"):
            return ItemMeta(title=extra.get("feed_title") or ref.title,
                            author=extra.get("feed_author", ""), url=ref.url,
                            published=extra.get("feed_published", ""),
                            page=ref.sub_id or "1", page_part="")
        payload = self._fetch_payload(ref)
        v = ((payload.get("payload") or {}).get("value") or {})
        author = v.get("displayAuthor") or ""
        if isinstance(author, dict):
            author = author.get("name") or ""
        if not author:
            uid = v.get("creatorId") or ""
            refs = (payload.get("payload") or {}).get("references") or {}
            author = ((refs.get("User") or {}).get(uid) or {}).get("name") or ""
        return ItemMeta(title=v.get("title") or ref.title, author=author, url=ref.url,
                        published=_ms_to_date(v.get("latestPublishedAt")),
                        desc=v.get("socialDek") or "",
                        page=ref.sub_id or "1", page_part="")

    def fetch_content(self, ref) -> ContentResult:
        extra = ref.extra or {}
        html = extra.get("feed_content")
        if html:
            blocks, images = html_to_blocks(html)
            if not blocks:
                return ContentResult(kind="document", status="empty",
                                     reason="feed 正文为空", label="正文(feed)")
            return ContentResult(kind="document", status="ok", blocks=blocks,
                                 images=images, label="正文(feed)")
        try:
            payload = self._fetch_payload(ref)
        except browserfetch.BrowserFetchError as e:
            return ContentResult(kind="document", status="empty", reason=str(e)[:120], label="正文")
        blocks, images = medium_to_blocks(payload)
        if not blocks:
            return ContentResult(kind="document", status="empty", reason="正文为空", label="正文")
        status, reason = payload_status(payload)
        return ContentResult(kind="document", status=status, blocks=blocks,
                             images=images, reason=reason, label="正文")

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
