"""Medium 适配器（document）：单篇文章（浏览器辅助过 Cloudflare）+ feed 通道（纯 requests）。

尽调（2026-10-08）：单篇的页面与 `?format=json` 均在 Cloudflare 后，headless 被拒、
`cf_clearance` 无法迁移给 requests → 必须走 core.browserfetch（有头+持久化 profile）；
feed（medium.com/feed/...）可用 requests 直取（content:encoded 含全文）。
⚠️ 2026-10-14 实测：上游已停用 `?format=json`（导航被重定向回正文页、fetch 被 CF 403）
→ 文章通道改为浏览器内 SSR 块抽取（`_ARTICLE_JS`：h1-h6/p/li/blockquote/pre + figure img），
绕开 SSR 深层包装 div（html_to_blocks 在多层 wrapper 下会丢失段落）。
付费墙：SSR 正文过短时置 suspect。
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
    """bodyModel.paragraphs → (blocks, images)；标题去重、代码/图注保留。

    说明：`?format=json` 通道 2026-10-14 实测已被上游停用（导航重定向/fetch CF 403），
    运行路径改用浏览器 SSR 抽取（`_ARTICLE_JS` + article_items_to_blocks）；本函数保留供解析复用/单测。
    """
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


def normalize_image_url(url: str) -> str:
    """Medium 图片 URL → resize:fit:1400 规范形（去 format:webp 等中间段）；非 miro 原样返回"""
    u = (url or "").strip()
    if not u or "miro.medium.com" not in u:
        return u
    m = re.search(r"/v2/(?:resize:[^/]+/)+(?:format:[^/]+/)?([^/?#]+)", u)
    return _IMG_BASE.format(id=m.group(1)) if m else u


def article_items_to_blocks(items: list, images: list):
    """浏览器抽取块序列 [{tag,text}] → (blocks, images)：h1（文章标题）跳过；
    h2-h6 开新节，其余归当前节；32x32 头像/图标（resize:fill:）剔除；图片规范化 + 去重。"""
    blocks, cur = [], {"heading": "", "paras": []}

    def flush():
        if cur["heading"] or cur["paras"]:
            blocks.append({"heading": cur["heading"], "text": "\n\n".join(cur["paras"]).strip()})
        cur["heading"], cur["paras"] = "", []

    for it in items or []:
        tag = (it.get("tag") or "").lower()
        text = (it.get("text") or "").strip()
        if not text or tag == "h1":           # h1 = 文章标题（meta 已含）
            continue
        if tag in ("h2", "h3", "h4", "h5", "h6"):
            flush()
            cur["heading"] = text
        else:
            cur["paras"].append(text)
    flush()
    out_images = []
    for u in images or []:
        if not u or "resize:fill:" in u:     # 头像/图标
            continue
        nu = normalize_image_url(u)
        if nu and nu not in out_images:
            out_images.append(nu)
    return blocks, out_images


_ARTICLE_JS = """
() => {
  const meta = (sel, attr) => { const e = document.querySelector(sel); return e ? (e.getAttribute(attr) || e.content || '') : ''; };
  const head = {
    title: meta('meta[property="og:title"]', 'content'),
    author: meta('meta[name="author"]', 'content'),
    published: meta('meta[property="article:published_time"]', 'content'),
    desc: meta('meta[property="og:description"]', 'content')
  };
  const art = document.querySelector('article');
  if (!art) return Object.assign({items: [], images: []}, head);
  const items = [], seen = new Set();
  for (const el of art.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,blockquote,pre,figcaption')) {
    const text = (el.innerText || '').trim();
    if (!text) continue;
    let dup = false;
    for (let a = el.parentElement; a && a !== art; a = a.parentElement) {
      if (seen.has(a)) { dup = true; break; }
    }
    if (dup) continue;
    seen.add(el);
    items.push({tag: el.tagName.toLowerCase(), text});
  }
  const images = [];
  for (const img of art.querySelectorAll('figure img, p img')) {
    const src = img.currentSrc || img.src || '';
    if (src && !images.includes(src)) images.push(src);
  }
  const h1 = art.querySelector('h1');
  if (!head.title && h1) head.title = h1.innerText.trim();
  return Object.assign({items, images}, head);
}
"""


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


@register
class MediumAdapter:
    name = "medium"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session
        self._article_cache = {}

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

    def _extract_article(self, ref) -> dict:
        """浏览器内抽取文章块 + meta（上游已停用 ?format=json：导航重定向、fetch 被 CF 挑战）"""
        with browserfetch.BrowserSession("medium") as s:
            s.goto(ref.url)
            s.pause(4000)
            return s.evaluate(_ARTICLE_JS) or {}

    def _article_data(self, ref) -> dict:
        """文章数据（抽取 + 实例内缓存：meta/content 两次调用只加载一次页面）"""
        key = ref.item_id or ref.url
        if key not in self._article_cache:
            self._article_cache[key] = self._extract_article(ref)
        return self._article_cache[key]

    # ---------------- 元信息 / 内容 ----------------

    def fetch_meta(self, ref) -> ItemMeta:
        extra = ref.extra or {}
        if extra.get("feed_content"):
            return ItemMeta(title=extra.get("feed_title") or ref.title,
                            author=extra.get("feed_author", ""), url=ref.url,
                            published=extra.get("feed_published", ""),
                            page=ref.sub_id or "1", page_part="")
        data = self._article_data(ref)
        return ItemMeta(title=data.get("title") or ref.title,
                        author=data.get("author") or "", url=ref.url,
                        published=(data.get("published") or "")[:10],
                        desc=data.get("desc") or "",
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
            data = self._article_data(ref)
        except browserfetch.BrowserFetchError as e:
            return ContentResult(kind="document", status="empty", reason=str(e)[:120], label="正文")
        blocks, images = article_items_to_blocks(data.get("items"), data.get("images"))
        if not blocks:
            return ContentResult(kind="document", status="empty", reason="正文为空", label="正文")
        text_len = sum(len(b["text"]) + len(b["heading"]) for b in blocks)
        if text_len < 120:
            return ContentResult(kind="document", status="suspect", blocks=blocks, images=images,
                                 reason=f"正文过短（{text_len} 字，可能付费墙/未完整加载）", label="正文")
        return ContentResult(kind="document", status="ok", blocks=blocks,
                             images=images, label="正文")

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
