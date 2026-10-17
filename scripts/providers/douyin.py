"""抖音适配器（timeline）：yt-dlp（Douyin 提取器）+ 访客 cookie；无平台字幕 → Whisper。

尽调（2026-10-08）：裸 requests 仅 JS 壳；yt-dlp 需「新鲜 cookie（不必登录）」；
访客 cookie（ttwid 等）经 COOKIE_EXPORT 标配导出（el.py cookie douyin）；严格缓速（默认 4s）。
"""
import os
import re
import tempfile
import time
from urllib.parse import parse_qs, urlparse

from core import browserfetch
from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_VIDEO_RE = re.compile(r"douyin\.com/video/(\d+)")
_SHORT_RE = re.compile(r"v\.douyin\.com/([0-9A-Za-z]+)")
_USER_RE = re.compile(r"douyin\.com/user/([A-Za-z0-9_\-]+)")
_USER_POST_API = re.compile(r"/aweme/v1/web/aweme/post/")   # 取证确认（见 docs/plans/2026-10-14-m12-browserfetch.md）
_SELF_RE = re.compile(r"douyin\.com/user/self")
_COLLECT_API = re.compile(r"/aweme/v1/web/aweme/listcollection/")   # 取证确认（见 m12-ladder2-dd）
_FAVORITE_API = re.compile(r"/aweme/v1/web/aweme/favorite/")   # 喜欢列表（取证确认）
_DETAIL_API = re.compile(r"/aweme/v1/web/aweme/detail/")   # 视频 detail（B1：2026-10-16 浏览器路径实地验证）
_TAB_SETTLE_MS = 4000   # B1（Round4 实测）：user/self 页 SPA 水合前点击 tab 会静默丢失
_COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".douyin_cookie")

# cookie 一键导出规格（el.py cookie douyin；见 ADAPTER_SPEC §4）——访客 cookie 即可，无需登录
# probe_url：完整加载一个公开视频页，让风控 JS 生成完整新鲜 cookie（yt-dlp 无签名，依赖此套 cookie）
COOKIE_EXPORT = {
    "url": "https://www.douyin.com/",
    "read_url": "https://www.douyin.com",
    "probe_url": "https://www.douyin.com/video/7279377986187431202",
    "required": ["ttwid"],
    "ready": ["ttwid", "__ac_signature", "odin_tt"],   # odin_tt 风控 JS 后才写入 → 等完整集合
    "wanted": ["*"],
    "file": ".douyin_cookie",
}


class DouyinError(ValueError):
    """抖音取数失败（cookie 缺失/提取失败，可读）"""


_yt_dlp_module = None


def _load_ytdlp():
    """懒加载 yt-dlp（import 阶段不联网、不报错）"""
    global _yt_dlp_module
    if _yt_dlp_module is None:
        try:
            import yt_dlp
            _yt_dlp_module = yt_dlp
        except ImportError as e:
            raise DouyinError("未安装 yt-dlp，请执行: pip install yt-dlp") from e
    return _yt_dlp_module


def _cookie_path() -> str:
    try:
        from core import config as core_config
        cfg = (core_config.load_config().get("auth") or {}).get("douyin") or {}
        if cfg.get("cookies_file"):
            return cfg["cookies_file"]
    except Exception:
        pass
    return _COOKIE_FILE


def load_cookie() -> str:
    """环境变量 DOUYIN_COOKIE 或 cookie 文件（name=value; ...）"""
    raw = os.environ.get("DOUYIN_COOKIE", "") or ""
    path = _cookie_path()
    if not raw and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    return raw


def to_netscape(cookie_str: str, domain: str = ".douyin.com") -> str:
    """name=value; ... → Netscape cookie 文件文本（yt-dlp cookiefile 用）

    过期时间写远期（2100）：写 0 会被 cookie jar 视为会话/已过期而丢弃，
    导致 yt-dlp 误判「Fresh cookies are needed」。"""
    expiry = "4102444800"
    lines = ["# Netscape HTTP Cookie File"]
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        lines.append("\t".join([domain, "TRUE", "/", "FALSE", expiry, k, v]))
    return "\n".join(lines) + "\n"


def detail_api_url(ref) -> str:
    """视频 ref → aweme detail API URL（浏览器页内 fetch 兜底用）"""
    return ("https://www.douyin.com/aweme/v1/web/aweme/detail/"
            f"?aweme_id={ref.item_id}")


def detail_to_info(detail: dict) -> dict:
    """aweme_detail（浏览器路径）→ yt-dlp 同形 info dict（fetch_meta/fetch_media_url 复用）

    注意：payload 的 duration 为毫秒；create_time 为 epoch（upload_date 取 UTC）。
    formats：play_addr.url_list（含 api-play.amemv.com 官方 play 接口）+ bit_rate 各档（CDN）。
    """
    detail = detail or {}
    v = detail.get("video") or {}
    desc = (detail.get("desc") or "").strip()
    first = (desc.splitlines() or [""])[0].strip()
    dur_ms = detail.get("duration") or v.get("duration") or 0
    ct = int(detail.get("create_time") or 0)
    formats = []
    pa = v.get("play_addr") or {}
    for u in (pa.get("url_list") or []):
        formats.append({"url": u, "height": v.get("height") or 0, "tbr": None})
    for b in (v.get("bit_rate") or []):
        bp = b.get("play_addr") or {}
        for u in (bp.get("url_list") or []):
            formats.append({"url": u, "height": 0, "tbr": (b.get("bit_rate") or 0) / 1000.0})
    st = detail.get("statistics") or {}
    return {
        "id": str(detail.get("aweme_id") or ""),
        "title": first or f"抖音视频 {detail.get('aweme_id')}",
        "uploader": (detail.get("author") or {}).get("nickname") or "",
        "duration": int(dur_ms / 1000) if dur_ms else 0,
        "upload_date": time.strftime("%Y%m%d", time.gmtime(ct)) if ct else "",
        "description": desc,
        "view_count": st.get("play_count"), "like_count": st.get("digg_count"),
        "comment_count": st.get("comment_count"), "repost_count": st.get("share_count"),
        "formats": formats,
    }


def parse_user_post_payload(payloads: list) -> list:
    """主页作品接口响应 → 归一化 [{aweme_id,title,desc,create_time,view,like,duration}]（去重保序）"""
    out, seen = [], set()
    for p in (payloads or []):
        for aw in ((p or {}).get("aweme_list") or []):
            vid = str(aw.get("aweme_id") or "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            stats = aw.get("statistics") or {}
            desc = (aw.get("desc") or "").strip()
            first = (desc.splitlines() or [""])[0].strip()
            out.append({
                "aweme_id": vid,
                "title": (first[:60] if first else f"抖音视频 {vid}"),
                "desc": desc,
                "create_time": int(aw.get("create_time") or 0),
                "view": int(stats.get("play_count") or 0),
                "like": int(stats.get("digg_count") or 0),
                "duration": int((aw.get("video") or {}).get("duration") or 0),
            })
    return out


def post_to_itemref(it: dict, index: int) -> ItemRef:
    """归一化条目 → ItemRef（作品/收藏共用）"""
    return ItemRef(platform="douyin", item_id=it["aweme_id"],
                   url=f"https://www.douyin.com/video/{it['aweme_id']}",
                   sub_id=str(index), title=it["title"], duration=it["duration"],
                   extra={"desc": it["desc"], "view": it["view"], "like": it["like"]})


@register
class DouyinAdapter:
    name = "douyin"
    kinds = {"video"}
    has_video = True       # 具备画面层（档位复检可用）

    def __init__(self, session=None):
        self._session = session
        self._meta_cache = {}
        self._cookie_file_path = None

    @property
    def session(self):
        if self._session is None:
            self._session = ThrottledSession("douyin")
            self._session.headers.update({"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        return bool(_VIDEO_RE.search(url or "") or _SHORT_RE.search(url or "")
                    or _USER_RE.search(url or "") or _SELF_RE.search(url or ""))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        if _SHORT_RE.search(q):
            url = q if q.startswith("http") else "https://" + q
            try:
                r = self.session.get(url, timeout=20, allow_redirects=True)
                m = _VIDEO_RE.search(getattr(r, "url", "") or "")
                if m:
                    q = r.url
            except Exception:
                pass
        m = _VIDEO_RE.search(q)
        if m:
            vid = m.group(1)
            return ItemRef(platform="douyin", item_id=vid,
                           url=f"https://www.douyin.com/video/{vid}", sub_id="1")
        if _SELF_RE.search(q):
            tab = (parse_qs(urlparse(q).query).get("tab") or [""])[0]
            if tab == "favorite":
                return ItemRef(platform="douyin", item_id="self-favorite",
                               url="https://www.douyin.com/user/self", sub_id="1",
                               extra={"kind": "self_favorite"})
            return ItemRef(platform="douyin", item_id="self-collect",
                           url="https://www.douyin.com/user/self", sub_id="1",
                           extra={"kind": "self_collect"})
        mu = _USER_RE.search(q)
        if mu:
            sec = mu.group(1)
            return ItemRef(platform="douyin", item_id=f"user-{sec}",
                           url=f"https://www.douyin.com/user/{sec}", sub_id="1",
                           extra={"kind": "user_posts", "sec_uid": sec})
        raise SearchNeeded(q)

    def list_items(self, ref):
        kind = (ref.extra or {}).get("kind")
        if kind == "user_posts":
            return self._list_user_posts(ref)
        if kind == "self_collect":
            return self._list_self_collect(ref)
        if kind == "self_favorite":
            return self._list_self_favorite(ref)
        return [ref]

    def _list_user_posts(self, ref, limit=20, scroll_times=3):
        """主页作品列表（browserfetch 拦截；签名由页面完成，不逆向）"""
        with browserfetch.BrowserSession("douyin") as s:
            s.goto(ref.url)
            payloads = s.tap_json(_USER_POST_API.pattern,
                                  action=lambda: s.scroll(scroll_times))
        posts = parse_user_post_payload(payloads)
        if not posts:
            raise DouyinError("未解析到作品（可能无公开作品或页面结构变化）")
        return [post_to_itemref(it, i) for i, it in enumerate(posts[:limit], 1)]

    def _list_self_collect(self, ref, limit=20, scroll_times=3):
        """自己账号的收藏列表（浏览器拦截；需登录）"""
        with browserfetch.BrowserSession("douyin") as s:
            if not s.has_cookie("sessionid"):
                raise DouyinError("抖音未登录：请先在本机自动化浏览器中登录抖音"
                                  "（el.py cookie douyin 打开窗口后扫码）")
            s.goto(ref.url)
            s.pause(_TAB_SETTLE_MS)   # 等 SPA 水合（否则点击 tab 不生效，实测复现）

            def act():
                s.click("text=收藏", timeout=8000)
                s.scroll(scroll_times)

            payloads = s.tap_json(_COLLECT_API.pattern, action=act)
        posts = parse_user_post_payload(payloads)
        if not posts:
            raise DouyinError("收藏列表为空或解析失败（可能结构变化）")
        return [post_to_itemref(it, i) for i, it in enumerate(posts[:limit], 1)]

    def _list_self_favorite(self, ref, limit=20, scroll_times=3):
        """自己账号的喜欢列表（浏览器拦截；需登录）"""
        with browserfetch.BrowserSession("douyin") as s:
            if not s.has_cookie("sessionid"):
                raise DouyinError("抖音未登录：请先在本机自动化浏览器中登录抖音"
                                  "（el.py cookie douyin 打开窗口后扫码）")
            s.goto(ref.url)
            s.pause(_TAB_SETTLE_MS)   # 等 SPA 水合（否则点击 tab 不生效，实测复现）

            def act():
                s.click("text=喜欢", timeout=8000)
                s.scroll(scroll_times)

            payloads = s.tap_json(_FAVORITE_API.pattern, action=act)
        posts = parse_user_post_payload(payloads)
        if not posts:
            raise DouyinError("喜欢列表为空或解析失败（可能结构变化）")
        return [post_to_itemref(it, i) for i, it in enumerate(posts[:limit], 1)]

    # ---------------- yt-dlp 封装 ----------------

    def _cookie_file(self) -> str:
        if self._cookie_file_path:
            return self._cookie_file_path
        raw = load_cookie()
        if not raw:
            raise DouyinError("缺少抖音 cookie：请先运行  python scripts/el.py cookie douyin"
                              "（访客 cookie 即可，无需登录）")
        fd, path = tempfile.mkstemp(prefix="douyin_cookies_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(to_netscape(raw))
        self._cookie_file_path = path
        return path

    def _extract(self, url: str, **opts):
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                    "cookiefile": self._cookie_file(), **opts}
        try:
            with _load_ytdlp().YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        except DouyinError:
            raise
        except Exception as e:
            msg = str(e)[:160]
            if "cookie" in msg.lower():
                raise DouyinError(f"抖音需要新鲜 cookie（请重新导出：el.py cookie douyin）：{msg}")
            raise DouyinError(f"抖音提取失败：{msg}")

    def _extract_browser(self, ref) -> dict:
        """browserfetch 取 aweme_detail：tap_json 拦截页面自身请求优先 → fetch_json 兜底（B1）

        direct API（requests/yt-dlp）已被 Argus 硬拦（403 Uifid Not Found，2026-10-16 取证）；
        真实浏览器上下文（带风控指纹/登录态）请求同一接口 200。
        """
        with browserfetch.BrowserSession("douyin") as s:
            try:
                payloads = s.tap_json(_DETAIL_API.pattern, action=lambda: s.goto(ref.url))
            except browserfetch.BrowserFetchError:
                payloads = []
            detail = next(((p or {}).get("aweme_detail") for p in payloads
                           if (p or {}).get("aweme_detail")), None)
            if not detail:
                data = s.fetch_json(detail_api_url(ref), warm_url=ref.url)
                detail = (data or {}).get("aweme_detail")
        if not detail:
            raise DouyinError("浏览器未取得 aweme_detail（可能未登录/页面结构变化）")
        return detail_to_info(detail)

    def _info(self, ref):
        """detail 信息：浏览器路径主选（B1）→ yt-dlp 兜底（旧路径保留，平台若恢复可用）"""
        if ref.item_id not in self._meta_cache:
            errs = []
            try:
                self._meta_cache[ref.item_id] = self._extract_browser(ref)
                return self._meta_cache[ref.item_id]
            except Exception as e:
                errs.append(f"浏览器: {str(e)[:100]}")
            try:
                self._meta_cache[ref.item_id] = self._extract(ref.url)
            except Exception as e:
                errs.append(f"yt-dlp: {str(e)[:100]}")
                raise DouyinError("抖音取数失败（" + "；".join(errs) + "）")
        return self._meta_cache[ref.item_id]

    # ---------------- 元信息 / 内容 / 媒体 ----------------

    def fetch_meta(self, ref) -> ItemMeta:
        info = self._info(ref)
        up = info.get("upload_date") or ""
        published = f"{up[:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 else ""
        return ItemMeta(title=info.get("title") or "",
                        author=info.get("uploader") or info.get("creator") or "",
                        url=ref.url, published=published,
                        desc=(info.get("description") or "")[:200],
                        duration=int(info.get("duration") or 0),
                        stats={"view": info.get("view_count"), "like": info.get("like_count"),
                               "comment": info.get("comment_count"), "repost": info.get("repost_count")},
                        page=ref.sub_id or "1", page_part="")

    def fetch_content(self, ref) -> ContentResult:
        try:
            self._info(ref)
        except DouyinError as e:
            return ContentResult(kind="timeline", status="empty", reason=str(e)[:120],
                                 label="无字幕", provenance="none")
        # 抖音无平台字幕（如未来出现 subtitle 轨，可在此接入 vtt 解析）
        return ContentResult(kind="timeline", status="empty",
                             reason="抖音无平台字幕（将走 Whisper 转写）", label="无字幕",
                             provenance="none")

    def media_headers(self):
        """媒体 CDN 请求头（douyinvod 拒裸 UA/bilibili Referer；api-play 转跳后也依赖正常 UA）"""
        return {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}

    def fetch_media_url(self, ref, media="audio", lowest=False):
        try:
            info = self._info(ref)
        except DouyinError:
            return None
        fmts = [f for f in (info.get("formats") or []) if f.get("url")]
        if fmts:
            # 优先 api-play*.amemv.com（官方 play 接口）；CDN 直链（douyinvod/365yg）在
            # lowest（抽帧省带宽）时放行——B1 已实地验证 206 可下（2026-10-16）
            if media == "video" and lowest:
                pool = fmts
            else:
                api = [f for f in fmts if "amemv.com/aweme/v1/play" in (f.get("url") or "")]
                pool = api or fmts
            pool.sort(key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)))
            if media == "audio":
                # 实测坑：最低码率常是 DASH 纯视频（media-video-*），无音轨 → Whisper 崩
                # （tuple index out of range）。音频优先选 muxed（含音轨）流，最小省带宽。
                muxed = [f for f in pool
                         if "media-video" not in (f.get("url") or "")
                         and "/dash/" not in (f.get("url") or "")]
                cand = muxed or pool
                cand.sort(key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)))
                return cand[0]["url"]
            return (pool[0] if lowest else pool[-1])["url"]
        return info.get("url") or None
