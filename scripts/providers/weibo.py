"""微博适配器（document）：单条微博（正文/长文/图片/转发）+ 用户时间线批量。

尽调（2026-10-08）：m.weibo.cn JSON API；单帖 `statuses/show` 可用（访客 cookie 预热）；
时间线容器 `getIndex` 更稳需 cookie（SUB）且易 432；严格缓速（默认 6s），432 一律可读。
"""
import html as html_mod
import os
import re
from datetime import datetime

import requests

from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.retry import retry_with_backoff
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_POST_RE = re.compile(r"m\.weibo\.cn/(?:status|detail)/([0-9A-Za-z]+)")
_WEB_POST_RE = re.compile(r"weibo\.com/\d+/([0-9A-Za-z]+)")
_USER_RE = re.compile(r"(?:m\.weibo\.cn/u/|weibo\.com/u/|weibo\.com/)(\d{6,})")
_FAVS_RE = re.compile(r"weibo\.com/u/favorites")
_FAVS_PAGE = "https://weibo.com/u/favorites?page=1"
_FAV_API = "https://weibo.com/ajax/favorites/all_fav?page={page}&with_total=true"
_FAV_MAX_PAGES = 50           # 安全上限（约 1000 条），防无限翻页
_FOLLOW_RE = re.compile(r"weibo\.com/u/page/follow/(\d{6,})")
_FOLLOW_API = "https://weibo.com/ajax/friendships/friends?page={page}&uid={uid}"
_FOLLOW_MAX_PAGES = 50        # 安全上限（约 1000 人）
_DESKTOP_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://weibo.com/u/favorites",
    "X-Requested-With": "XMLHttpRequest",
}
_SHOW = "https://m.weibo.cn/statuses/show?id={sid}"
_EXTEND = "https://m.weibo.cn/statuses/extend?id={sid}"
_TIMELINE = "https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=107603{uid}"
_HOME = "https://m.weibo.cn/"
_COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".weibo_cookie")
_TIMELINE_LIMIT = 10
_TIMELINE_MAX_PAGES = 20      # 安全上限（200 条），防超活跃账号无限翻页

# cookie 一键导出规格（el.py cookie weibo；见 ADAPTER_SPEC §4）
COOKIE_EXPORT = {
    "url": "https://m.weibo.cn/",
    "read_url": "https://m.weibo.cn",
    "required": ["SUB"],
    "ready": ["SUB", "WEIBOCN_FROM"],
    # 防「假登录」：m.weibo.cn 游客也会下发 SUB → 必须校验 /api/config login:true 才落盘
    "verify": {"url": "https://m.weibo.cn/api/config", "expect": '\"login\":true'},
    "wanted": ["SUB", "SUBP", "WEIBOCN_FROM", "_T_WM", "M_WEIBOCN_PARAMS"],
    "file": ".weibo_cookie",
}


class WeiboError(ValueError):
    """微博取数失败（432 风控/接口异常，可读）"""


def _cookie_path() -> str:
    try:
        from core import config as core_config
        cfg = (core_config.load_config().get("auth") or {}).get("weibo") or {}
        if cfg.get("cookies_file"):
            return cfg["cookies_file"]
    except Exception:
        pass
    return _COOKIE_FILE


def _raw_cookie() -> str:
    """原始 cookie 串：环境变量 WEIBO_COOKIE 优先，其次 cookie 文件"""
    raw = os.environ.get("WEIBO_COOKIE", "") or ""
    path = _cookie_path()
    if not raw and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    return raw


def load_cookie(session: requests.Session) -> bool:
    """从环境变量 WEIBO_COOKIE 或 cookie 文件注入；有则 True"""
    raw = _raw_cookie()
    if not raw:
        return False
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            session.cookies.set(k.strip(), v.strip(), domain=".weibo.cn")
    return True


def strip_html(raw: str) -> str:
    """微博 HTML 文本 → 纯文本（<br>换行 / 去表情 img / 去标签 / 实体反转义）"""
    s = raw or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<img[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def pics_to_urls(pics) -> list:
    """图片 URL（large 优先，去重保序）"""
    out = []
    for p in pics or []:
        if not isinstance(p, dict):
            continue
        u = ((p.get("large") or {}).get("url")) or p.get("url") or ((p.get("bmiddle") or {}).get("url"))
        if u:
            if u.startswith("//"):
                u = "https:" + u
            if u not in out:
                out.append(u)
    return out


def video_url(mblog: dict):
    """视频直链（media_info 或 urls 质量字典）"""
    pi = (mblog or {}).get("page_info") or {}
    mi = pi.get("media_info") or {}
    u = mi.get("stream_url_hd") or mi.get("stream_url")
    if not u:
        urls = pi.get("urls") or {}
        for k in ("mp4_720p_mp4", "mp4_hd_mp4", "hevc_mp4_hd", "mp4_ld_mp4"):
            item = urls.get(k) or {}
            if isinstance(item, dict) and item.get("url"):
                u = item["url"]
                break
    return u or None


def parse_created_at(s: str) -> str:
    """绝对时间 → YYYY-MM-DD；相对时间（1小时前）无法精确 → 空"""
    s = (s or "").strip()
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _long_text_value(v):
    """longText 兼容：字符串 或 {longTextContent: ...} 对象"""
    if isinstance(v, dict):
        v = v.get("longTextContent") or v.get("text") or ""
    return v if isinstance(v, str) else ""


def mblog_to_document(mblog: dict):
    """mblog → (blocks, images)：正文 + 长文 + 转发段"""
    text = strip_html(mblog.get("text") or "")
    if mblog.get("isLongText"):
        lt = _long_text_value(mblog.get("longText"))
        if lt:
            text = strip_html(lt)
    parts = [text] if text else []
    images = pics_to_urls(mblog.get("pics"))
    rt = mblog.get("retweeted_status")
    if rt:
        rt_user = (rt.get("user") or {}).get("screen_name") or "原博主"
        rt_text = strip_html(rt.get("text") or "")
        parts.append(f"【转发 @{rt_user}】{rt_text}")
        images += [u for u in pics_to_urls(rt.get("pics")) if u not in images]
    return [{"heading": "", "text": "\n\n".join(p for p in parts if p)}], images


@register
class WeiboAdapter:
    name = "weibo"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session
        self._warmed = False

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("weibo")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept": "application/json, text/plain, */*",
                                          "Accept-Language": "zh-CN,zh;q=0.9",
                                          "Referer": "https://m.weibo.cn/",
                                          "X-Requested-With": "XMLHttpRequest",
                                          "MWeibo-Pwa": "1"})
            load_cookie(self._session)
        return self._session

    def reload_auth(self) -> None:
        """登录态自动恢复后重新注入 cookie（供 pipeline 重试）"""
        load_cookie(self.session)

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        return bool(_POST_RE.search(url or "") or _WEB_POST_RE.search(url or "")
                    or _FAVS_RE.search(url or "") or _FOLLOW_RE.search(url or "")
                    or _USER_RE.search(url or ""))

    def resolve(self, q: str) -> ItemRef:
        fm = _FOLLOW_RE.search(q or "")
        if fm:
            uid = fm.group(1)
            return ItemRef(platform="weibo", item_id=f"followings-{uid}",
                           url=f"https://weibo.com/u/page/follow/{uid}", sub_id="1",
                           extra={"kind": "followings", "uid": uid})
        if _FAVS_RE.search(q or ""):
            return ItemRef(platform="weibo", item_id="favorites-self",
                           url=_FAVS_PAGE, sub_id="1",
                           extra={"kind": "favorites"})
        m = _POST_RE.search(q or "") or _WEB_POST_RE.search(q or "")
        if m:
            return ItemRef(platform="weibo", item_id=f"post-{m.group(1)}",
                           url=f"https://m.weibo.cn/status/{m.group(1)}", sub_id="1",
                           extra={"kind": "post", "sid": m.group(1)})
        m = _USER_RE.search(q or "")
        if m:
            return ItemRef(platform="weibo", item_id=f"user-{m.group(1)}",
                           url=f"https://m.weibo.cn/u/{m.group(1)}", sub_id="1",
                           extra={"kind": "user", "uid": m.group(1)})
        raise SearchNeeded(q)

    # ---------------- 取数 ----------------

    def _warm(self):
        """访问首页取访客 cookie（WEIBOCN_FROM 等）"""
        if self._warmed:
            return
        try:
            self.session.get(_HOME, timeout=20)
        except Exception:
            pass
        self._warmed = True

    def _get_json(self, url: str) -> dict:
        self._warm()
        r = self.session.get(url, timeout=25)
        if r.status_code == 432 or not (r.text or "").strip():
            raise WeiboError("微博风控拦截（432/空响应）：请稍后重试；"
                             "时间线等接口建议先 `python scripts/el.py cookie weibo` 导出 cookie")
        if r.status_code in (429, 500, 502, 503, 504):
            def once():
                rr = self.session.get(url, timeout=25)
                if rr.status_code == 432 or not (rr.text or "").strip():
                    raise WeiboError("微博风控拦截（432/空响应，重试后仍失败）")
                rr.raise_for_status()
                return rr.json()
            return retry_with_backoff(once, attempts=2, base_delay=3.0)
        if r.status_code != 200:
            raise WeiboError(f"微博接口异常（HTTP {r.status_code}）")
        try:
            return r.json()
        except Exception:
            raise WeiboError("微博接口返回非 JSON（可能被风控）")

    def _get_json_desktop(self, url: str) -> dict:
        """桌面域（weibo.com）JSON：原始 cookie 串 + 桌面头（收藏等接口；GET 免签名）"""
        headers = dict(_DESKTOP_HEADERS)
        raw = _raw_cookie()
        if raw:
            headers["Cookie"] = raw
        r = self.session.get(url, headers=headers, timeout=25)
        if r.status_code == 432 or not (r.text or "").strip():
            raise WeiboError("微博风控拦截（432/空响应）：请稍后重试或重新导出 cookie")
        if r.status_code in (429, 500, 502, 503, 504):
            def once():
                rr = self.session.get(url, headers=headers, timeout=25)
                if rr.status_code == 432 or not (rr.text or "").strip():
                    raise WeiboError("微博风控拦截（432/空响应，重试后仍失败）")
                rr.raise_for_status()
                return rr.json()
            return retry_with_backoff(once, attempts=2, base_delay=3.0)
        if r.status_code != 200:
            raise WeiboError(f"微博接口异常（HTTP {r.status_code}）")
        try:
            return r.json()
        except Exception:
            raise WeiboError("微博接口返回非 JSON（可能被风控）")

    def _get_mblog(self, ref) -> dict:
        d = self._get_json(_SHOW.format(sid=ref.extra.get("sid")))
        mblog = d.get("data") or {}
        if not mblog.get("id") and not mblog.get("text"):
            raise WeiboError("微博不存在或已被删除")
        return mblog

    def _extend_long_text(self, ref, mblog: dict) -> dict:
        """长文补全：longText 字段缺失时调 extend 接口（失败不阻塞）"""
        if not mblog.get("isLongText") or _long_text_value(mblog.get("longText")):
            return mblog
        try:
            d = self._get_json(_EXTEND.format(sid=ref.extra.get("sid")))
            lt = (d.get("data") or {}).get("longTextContent")
            if lt:
                return {**mblog, "longText": lt}
        except WeiboError:
            pass
        return mblog

    # ---------------- 列表 / 元信息 / 内容 ----------------

    def _timeline_mblogs(self, uid: str) -> list:
        """时间线 since_id 翻页拉全量（安全上限 _TIMELINE_MAX_PAGES 页）；
        中途风控时若有已拉取内容则优雅停止，否则抛出可读异常。"""
        base = _TIMELINE.format(uid=uid)
        mblogs, since_id, seen = [], None, set()
        for _ in range(_TIMELINE_MAX_PAGES):
            url = base + (f"&since_id={since_id}" if since_id else "")
            try:
                d = self._get_json(url)
            except WeiboError:
                if mblogs:
                    break
                raise
            cards = ((d.get("data") or {}).get("cards") or [])
            page = []
            for c in cards:
                if c.get("mblog"):
                    page.append(c["mblog"])
                for g in (c.get("card_group") or []):
                    if g.get("mblog"):
                        page.append(g["mblog"])
            new = [m for m in page if str(m.get("id") or m.get("idstr")) not in seen]
            if not new:
                break
            for m in new:
                seen.add(str(m.get("id") or m.get("idstr")))
            mblogs.extend(new)
            since_id = ((d.get("data") or {}).get("cardlistInfo") or {}).get("since_id")
            if not since_id:
                break
        return mblogs

    def _list_favorites(self, ref) -> list:
        """自己的收藏：桌面 all_fav 分页（需登录 cookie；with_total=true 形状）。
        中途风控/未登录时：已有内容优雅停止，否则抛可读异常。"""
        items, seen, total = [], set(), None
        for page in range(1, _FAV_MAX_PAGES + 1):
            try:
                d = self._get_json_desktop(_FAV_API.format(page=page))
            except WeiboError:
                if items:
                    break
                raise
            if d.get("ok") is not None and d.get("ok") <= 0:
                if items:
                    break
                raise WeiboError("微博未登录或登录已失效：请先 "
                                 "`python scripts/el.py cookie weibo` 重新导出 cookie")
            data = d.get("data")
            if isinstance(data, dict):
                page_items, total = (data.get("status") or []), data.get("total_number")
            else:                                # 兼容不带 with_total 的纯数组形状
                page_items, total = (data or []), None
            new = [m for m in page_items
                   if str(m.get("id") or m.get("idstr")) not in seen]
            for m in new:
                seen.add(str(m.get("id") or m.get("idstr")))
            items.extend(new)
            if not page_items or len(page_items) < (d.get("count") or 20):
                break
            if total is not None and len(items) >= total:
                break
        out = []
        for i, m in enumerate(items, 1):
            sid = str(m.get("mblogid") or m.get("bid") or m.get("id") or m.get("idstr") or "")
            if not sid:
                continue
            extra = {"kind": "post", "sid": sid, "mblog": m}
            if m.get("attitudes_count") is not None:
                extra["like"] = m.get("attitudes_count")
            out.append(ItemRef(platform="weibo", item_id=f"post-{sid}",
                               url=f"https://m.weibo.cn/status/{sid}", sub_id=str(i),
                               title=strip_html(m.get("text") or "")[:40], extra=extra))
        return out

    def _list_followings(self, ref) -> list:
        """关注列表：桌面 friendships/friends 分页（需登录 cookie；users 空即止）"""
        uid = ref.extra["uid"]
        users, seen = [], set()
        for page in range(1, _FOLLOW_MAX_PAGES + 1):
            try:
                d = self._get_json_desktop(_FOLLOW_API.format(page=page, uid=uid))
            except WeiboError:
                if users:
                    break
                raise
            page_users = d.get("users") or []
            new = [u for u in page_users
                   if str(u.get("idstr") or u.get("id") or "") not in seen]
            for u in new:
                seen.add(str(u.get("idstr") or u.get("id") or ""))
            users.extend(new)
            if not page_users or len(page_users) < 20:
                break
            total = d.get("total_number")
            if total is not None and len(users) >= total:
                break
        out = []
        for i, u in enumerate(users, 1):
            uu = str(u.get("idstr") or u.get("id") or "")
            if not uu:
                continue
            out.append(ItemRef(platform="weibo", item_id=f"user-{uu}",
                               url=f"https://m.weibo.cn/u/{uu}", sub_id=str(i),
                               title=(u.get("screen_name") or "").strip(),
                               extra={"kind": "user", "uid": uu,
                                      "desc": (u.get("description") or "")[:200],
                                      "view": u.get("followers_count") or 0}))
        return out

    def list_items(self, ref):
        kind = (ref.extra or {}).get("kind")
        if kind == "followings":
            return self._list_followings(ref)
        if kind == "favorites":
            return self._list_favorites(ref)
        if kind != "user":
            return [ref]
        uid = ref.extra["uid"]
        mblogs = self._timeline_mblogs(uid)
        items = []
        for i, m in enumerate(mblogs, 1):
            sid = str(m.get("id") or m.get("idstr") or "")
            if not sid:
                continue
            bid = m.get("bid") or sid
            items.append(ItemRef(platform="weibo", item_id=f"post-{sid}",
                                 url=f"https://m.weibo.cn/status/{bid}", sub_id=str(i),
                                 title=strip_html(m.get("text") or "")[:40],
                                 extra={"kind": "post", "sid": bid, "mblog": m}))
        return items or [ref]

    def fetch_root_meta(self, ref, refs) -> ItemMeta:
        """合集根元信息：收藏列表用列表自身 meta；其余沿用首条（默认行为）"""
        if (ref.extra or {}).get("kind") == "favorites":
            return ItemMeta(title="我的微博收藏", author="我", url=ref.url, page="1")
        return self.fetch_meta(refs[0])

    def fetch_meta(self, ref) -> ItemMeta:
        if ref.extra.get("kind") != "post":
            return ItemMeta(title="微博用户时间线", author="", url=ref.url,
                            page=ref.sub_id or "1")
        mblog = ref.extra.get("mblog") or self._get_mblog(ref)
        user = mblog.get("user") or {}
        text = strip_html(mblog.get("text") or "")
        return ItemMeta(title=(text[:40] + "…") if text else (user.get("screen_name") or "微博"),
                        author=user.get("screen_name") or "", url=ref.url,
                        published=parse_created_at(mblog.get("created_at")), desc=text[:200],
                        stats={"repost": mblog.get("reposts_count"),
                               "comment": mblog.get("comments_count"),
                               "like": mblog.get("attitudes_count")},
                        page=ref.sub_id or "1", page_part="")

    def fetch_content(self, ref) -> ContentResult:
        if ref.extra.get("kind") != "post":
            return ContentResult(kind="document", status="empty",
                                 reason="用户页请用 --all 展开时间线", label="正文")
        try:
            mblog = ref.extra.get("mblog") or self._get_mblog(ref)
            mblog = self._extend_long_text(ref, mblog)
        except WeiboError as e:
            return ContentResult(kind="document", status="empty", reason=str(e)[:120], label="正文")
        blocks, images = mblog_to_document(mblog)
        if not blocks or not blocks[0]["text"]:
            if images:
                return ContentResult(kind="document", status="ok", blocks=blocks, images=images,
                                     label="正文")
            return ContentResult(kind="document", status="empty", reason="正文为空", label="正文")
        text_len = len(blocks[0]["text"])
        status = "ok" if text_len >= 20 or images else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字）"
        return ContentResult(kind="document", status=status, blocks=blocks, images=images,
                             reason=reason, label="正文")

    def fetch_media_url(self, ref, media="audio", lowest=False):
        """仅视频帖返回直链（供读图/后续视频转写）；其余 None"""
        mblog = ref.extra.get("mblog")
        if not mblog:
            try:
                mblog = self._get_mblog(ref)
            except WeiboError:
                return None
        return video_url(mblog)
