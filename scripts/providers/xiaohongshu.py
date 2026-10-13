"""小红书适配器（document）：单篇笔记（图文/视频）+ explore token 兜底（实验性）。

尽调（2026-10-13）：访客可读但依赖短时效 xsec_token（分享链自带或 explore 现取）；
note 页 SSR __INITIAL_STATE__.noteDetailMap；图/视频 CDN 直链可下。缓速 12s。
账号级能力（主页/收藏）未来里程碑，见 docs/plans/2026-10-13-m10-xiaohongshu.md。
"""
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_ID_RE = re.compile(r"xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]{24})")
_SHORT_RE = re.compile(r"https?://xhslink\.(?:cn|com)/[A-Za-z0-9/]+")
_TOKEN_IN_EXPLORE = r"/explore/{nid}\?xsec_token=([A-Za-z0-9_\-=]+)"
_EXPLORE_URL = "https://www.xiaohongshu.com/explore"


class XhsError(ValueError):
    """小红书取数失败（token 失效/笔记不可读，可读）"""


@register
class XiaohongshuAdapter:
    name = "xiaohongshu"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None, harvest_loads: int = 2):
        self._session = session
        self._notes = {}                  # item_id -> parsed dict（同一运行内一次请求）
        self.harvest_loads = harvest_loads  # explore 兜底最大加载次数（实验性）

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("xiaohongshu")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept-Language": "zh-CN,zh;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        url = url or ""
        return bool(_ID_RE.search(url) or _SHORT_RE.match(url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        m = _ID_RE.search(q)
        if not m and _SHORT_RE.match(q):
            try:
                r = self.session.get(q, allow_redirects=True, timeout=20, stream=True)
                r.close()
                m = _ID_RE.search(getattr(r, "url", "") or "")
                q = getattr(r, "url", q)
            except requests.RequestException as e:
                raise XhsError(f"短链展开失败：{str(e)[:80]}")
        if m:
            nid = m.group(1)
            token = (parse_qs(urlparse(q).query).get("xsec_token") or [""])[0]
            return ItemRef(platform="xiaohongshu", item_id=f"xhs-{nid}",
                           url=q, sub_id="1",
                           extra={"kind": "note", "note_id": nid, "token": token})
        raise SearchNeeded(q)

    def list_items(self, ref):
        return [ref]

    # ---------------- 数据 / 元信息 / 内容（Task 2/3 实现） ----------------

    def fetch_meta(self, ref) -> ItemMeta:
        raise NotImplementedError

    def fetch_content(self, ref) -> ContentResult:
        raise NotImplementedError

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
