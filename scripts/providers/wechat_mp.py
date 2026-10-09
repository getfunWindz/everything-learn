"""微信公众号适配器（document）：单篇文章（正文/图片）+ 搜狗微信搜索（实验性）。

尽调（2026-10-09）：canonical 链接无需 cookie；正文服务端渲染（id="js_content"）；
图片 data-src（mmbiz.qpic.cn 免 Referer 可下）；搜狗搜索路径会话绑定（实验性，可能失效）。
无需 COOKIE_EXPORT（无登录凭据）。
"""
import re
from urllib.parse import parse_qs, urlparse

import requests

from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_HOST_RE = re.compile(r"mp\.weixin\.qq\.com/s\b")
_SHORT_RE = re.compile(r"mp\.weixin\.qq\.com/s/([A-Za-z0-9_\-]{10,})")
_SOGOU_SEARCH = "https://weixin.sogou.com/weixin?type=2&query={q}"


class WeChatError(ValueError):
    """公众号取数失败（页面异常/搜狗跳转失效，可读）"""


@register
class WeChatMpAdapter:
    name = "wechat_mp"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("wechat_mp")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept-Language": "zh-CN,zh;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        url = url or ""
        if not _HOST_RE.search(url):
            return False
        return bool(_SHORT_RE.search(url) or re.search(r"/s\?\S*__biz=", url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        m = _SHORT_RE.search(q)
        if m:
            token = m.group(1)
            return ItemRef(platform="wechat_mp", item_id=f"wx-{token}",
                           url=f"https://mp.weixin.qq.com/s/{token}", sub_id="1",
                           extra={"kind": "article", "token": token})
        if _HOST_RE.search(q) and "?" in q:
            qs = parse_qs(urlparse(q).query)
            biz = (qs.get("__biz") or [""])[0]
            mid = (qs.get("mid") or [""])[0]
            idx = (qs.get("idx") or ["1"])[0]
            if biz and mid:
                slug = re.sub(r"[^A-Za-z0-9]+", "_", biz)[:24].strip("_")
                return ItemRef(platform="wechat_mp", item_id=f"wx-{slug}_{mid}_{idx}",
                               url=q, sub_id="1",
                               extra={"kind": "article", "biz": biz, "mid": mid, "idx": idx})
        raise SearchNeeded(q)

    def list_items(self, ref):
        return [ref]

    # ---------------- 内容（Task 3 实现） ----------------

    def fetch_meta(self, ref) -> ItemMeta:
        raise NotImplementedError

    def fetch_content(self, ref) -> ContentResult:
        raise NotImplementedError

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
