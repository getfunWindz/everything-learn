"""X（Twitter）适配器（document）：单推文（正文/图片/视频口播转写）。

尽调（2026-10-09）：公开推文无需凭据；数据源 syndication tweet-result（token 本地计算）；
媒体直链（pbs/video.twimg.com）可直下；时间线/搜索不做（曾 429/SSL EOF 限流）。
无需 COOKIE_EXPORT。
"""
import math
import re
import time

import requests

from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_STATUS_RE = re.compile(
    r"https?://(?:(?:mobile|www)\.)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]+/)*(?:web/)?status/(\d{6,})")
_TCO_RE = re.compile(r"https?://t\.co/([A-Za-z0-9]+)")
_TWEET_API = "https://cdn.syndication.twimg.com/tweet-result"


class XError(ValueError):
    """X 取数失败（限流/推文不存在，可读）"""


def _js_tostr36(x: float) -> str:
    """等价 JS Number.toString(36)（供 token 计算；浮点实现足够稳定）"""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    ip, fp, out = int(x), x - int(x), ""
    if ip == 0:
        out = "0"
    while ip > 0:
        out = digits[ip % 36] + out
        ip //= 36
    if fp > 0:
        out += "."
        for _ in range(20):
            fp *= 36
            d = int(fp)
            out += digits[d]
            fp -= d
            if fp <= 0:
                break
    return out


def tweet_token(tweet_id: str) -> str:
    """syndication 接口 token：((id / 1e15) * π).toString(36) 去掉 0 与 ."""
    return re.sub(r"(0+|\.)", "", _js_tostr36(int(tweet_id) / 1e15 * math.pi))


@register
class XTwitterAdapter:
    name = "x_twitter"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session
        self._tweets = {}          # item_id -> parsed dict（同一运行内一次请求）

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("x_twitter")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        url = url or ""
        return bool(_STATUS_RE.search(url) or _TCO_RE.match(url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        m = _STATUS_RE.search(q)
        if not m and _TCO_RE.match(q):
            try:
                r = self.session.get(q, allow_redirects=True, timeout=20, stream=True)
                r.close()
                m = _STATUS_RE.search(getattr(r, "url", "") or "")
            except requests.RequestException as e:
                raise XError(f"短链展开失败：{str(e)[:80]}")
        if m:
            tid = m.group(1)
            return ItemRef(platform="x_twitter", item_id=f"tw-{tid}",
                           url=f"https://x.com/i/status/{tid}", sub_id="1",
                           extra={"kind": "tweet", "tid": tid})
        raise SearchNeeded(q)

    def list_items(self, ref):
        return [ref]

    # ---------------- 数据 / 元信息 / 内容（Task 2 实现） ----------------

    def fetch_meta(self, ref) -> ItemMeta:
        raise NotImplementedError

    def fetch_content(self, ref) -> ContentResult:
        raise NotImplementedError

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
