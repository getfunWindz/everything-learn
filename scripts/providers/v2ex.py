"""V2EX 主题帖适配器（document）：主题正文 + 前排回复。

- 公开 API：/api/topics/show.json?id=<id> 返回主题（含 content_rendered）。
- 回复：主题页 HTML 中 `div#r_<id>.cell` → `reply_content`（取前 N 条）。
- 无需 cookie；缓速 6s（社区站点，勿高频）。
"""
import html as html_mod
import re
from datetime import datetime, timezone

import requests

from core import browserfetch  # noqa: F401  (保持适配器目录一致的可选依赖风格)
from core.htmlblocks import html_to_blocks
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_URL_RE = re.compile(r"v2ex\.com/t/(\d+)")
_API = "https://www.v2ex.com/api/topics/show.json?id={id}"
_REPLY_LIMIT = 30


def strip_tags(raw: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", raw or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"[ \t]+", " ", html_mod.unescape(s)).strip()


def parse_topic(payload) -> dict:
    """API 响应（list 或单 dict）→ 主题字段"""
    t = payload[0] if isinstance(payload, list) else (payload or {})
    member = t.get("member") or {}
    node = t.get("node") or {}
    created = t.get("created")
    try:
        published = datetime.fromtimestamp(int(created), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        published = ""
    return {"id": t.get("id"), "title": (t.get("title") or "").strip(),
            "author": member.get("username") or "", "published": published,
            "replies": int(t.get("replies") or 0), "node": node.get("name") or "",
            "url": t.get("url") or "", "html": t.get("content_rendered") or ""}


def parse_replies(page_html: str, limit: int = _REPLY_LIMIT) -> list:
    """主题页 HTML → [{author, time, text}]（按出现顺序，去重 id）"""
    text = page_html or ""
    marks = list(re.finditer(r'<div id="r_(\d+)" class="cell">', text))
    out, seen = [], set()
    for i, m in enumerate(marks):
        rid = m.group(1)
        if rid in seen:
            continue
        seen.add(rid)
        seg = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        am = re.search(r'<a href="/member/[^"]+" class="dark">([^<]+)</a>', seg)
        cm = re.search(r'<div class="reply_content">(.*?)</div>', seg, re.S)
        tm = re.search(r'<span class="ago" title="([^"]+)"', seg)
        body = strip_tags(cm.group(1)) if cm else ""
        if not body:
            continue
        out.append({"author": (am.group(1).strip() if am else ""),
                    "time": (tm.group(1).strip() if tm else ""), "text": body})
        if len(out) >= max(0, limit):
            break
    return out


@register
class V2EXAdapter:
    name = "v2ex"
    kinds = {"article"}
    has_video = False
    language_hint = "zh"

    def __init__(self, session: requests.Session = None):
        self._session = session
        self._topic = None

    @property
    def session(self):
        if self._session is None:
            self._session = ThrottledSession("v2ex")
            self._session.headers.update({"User-Agent": _UA})
        return self._session

    def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url or ""))

    def resolve(self, q: str) -> ItemRef:
        m = _URL_RE.search(q or "")
        if not m:
            raise ValueError(f"不是 V2EX 主题链接：{q}")
        tid = m.group(1)
        return ItemRef(platform="v2ex", item_id=f"v2ex-{tid}",
                       url=f"https://www.v2ex.com/t/{tid}", sub_id="1",
                       extra={"topic_id": tid})

    def list_items(self, ref):
        return [ref]

    def _topic_data(self, ref) -> dict:
        if self._topic is None:
            r = self.session.get(_API.format(id=ref.extra["topic_id"]), timeout=25)
            r.raise_for_status()
            self._topic = parse_topic(r.json())
        return self._topic

    def fetch_meta(self, ref) -> ItemMeta:
        t = self._topic_data(ref)
        return ItemMeta(title=t["title"] or f"V2EX {t['id']}", author=t["author"],
                        url=ref.url, published=t["published"], desc=t["node"],
                        stats={"replies": t["replies"]}, page=ref.sub_id or "1")

    def fetch_content(self, ref) -> ContentResult:
        t = self._topic_data(ref)
        blocks, images = html_to_blocks(t.get("html") or "")
        try:
            page = self.session.get(ref.url, timeout=25)
            page.raise_for_status()
            replies = parse_replies(page.text, _REPLY_LIMIT)
        except Exception:
            replies = []
        for i, rep in enumerate(replies, 1):
            blocks.append({"heading": f"回复 {i}｜{rep['author']}",
                           "text": rep["text"]})
        text_len = sum(len(b.get("text") or "") for b in blocks)
        if not blocks:
            return ContentResult(kind="document", status="empty", reason="主题正文为空",
                                 label="正文")
        status = "ok" if text_len >= 20 else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字）"
        return ContentResult(kind="document", status=status, blocks=blocks, images=images,
                             reason=reason, label="正文")

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
