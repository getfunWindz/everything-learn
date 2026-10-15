"""B站内容源适配器：ApiClient（官方只读 API + 限流重试）+ BilibiliAdapter（SourceAdapter 实现）"""
import re
import requests

from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

BASE = "https://api.bilibili.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com",
}

# 限流/风控类可重试 HTTP 状态码（其余 4xx 不重试）
RETRYABLE_STATUS = frozenset({412, 429, 500, 502, 503, 504})

class BiliError(Exception):
    def __init__(self, code, message):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message

class Page:
    def __init__(self, cid: int, page: int, part: str, duration: int):
        self.cid, self.page, self.part, self.duration = cid, page, part, duration

class VideoInfo:
    def __init__(self, data: dict):
        self.bvid = data["bvid"]
        self.aid = data["aid"]
        self.title = data["title"]
        self.owner = data["owner"]["name"]
        self.duration = data["duration"]
        self.pubdate = data["pubdate"]
        self.desc = data.get("desc", "")
        self.stat = data.get("stat") or {}  # C3：view/like/favorite/coin/share/danmaku
        self.pages = [Page(p["cid"], p["page"], p["part"], p["duration"]) for p in data["pages"]]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_by_index(self, page: int) -> Page:
        if page < 1 or page > len(self.pages):
            raise BiliError(-400, f"页码 {page} 超出范围（共 {len(self.pages)} P）")
        return self.pages[page - 1]

class SubtitleLine:
    def __init__(self, start: float, end: float, content: str):
        self.start, self.end, self.content = start, end, content

    def __eq__(self, other):
        return (isinstance(other, SubtitleLine)
                and self.start == other.start
                and self.end == other.end
                and self.content == other.content)


# wbi 签名打乱表（bilibili 公开算法）
_WBI_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]

import hashlib
import os
import random
import time
import urllib.parse


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """wbi 签名密钥：img_key + sub_key 按打乱表重排取前 32 位"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in _WBI_TAB)[:32]


def _get_wbi_keys(session, timeout: int) -> tuple:
    nav = session.get(BASE + "/x/web-interface/nav", timeout=timeout)
    nav.raise_for_status()
    wbi = nav.json()["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    return img_key, sub_key


_COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".bili_cookie")

# cookie 一键导出规格（scripts/cookie_export.py / el.py cookie bilibili 读取；见 ADAPTER_SPEC §4）
COOKIE_EXPORT = {
    "url": "https://passport.bilibili.com/login",
    "read_url": "https://www.bilibili.com",
    "required": ["SESSDATA"],
    "ready": ["SESSDATA"],
    "wanted": ["SESSDATA", "bili_jct", "buvid3"],
    "file": ".bili_cookie",
}


def load_cookie(client) -> bool:
    """从环境变量 BILI_COOKIE 或 scripts/.bili_cookie 注入登录 cookie；有则 True"""
    raw = os.environ.get("BILI_COOKIE", "") or ""
    if not raw and os.path.exists(_COOKIE_FILE):
        with open(_COOKIE_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
    if not raw:
        return False
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            client.session.cookies.set(k.strip(), v.strip(), domain=".bilibili.com")
    return True

_SPACE_RE = re.compile(r"space\.bilibili\.com/(\d+)")


def _length_to_sec(text: str) -> int:
    """B站时长字符串（12:34 / 1:02:03）→ 秒"""
    parts = [p for p in (text or "").strip().split(":") if p != ""]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    sec = 0
    for n in nums:
        sec = sec * 60 + n
    return sec


class ApiClient:
    def __init__(self, session: requests.Session = None, timeout: int = 15, warm: bool = True,
                 max_retries: int = 4, retry_base_delay: float = 1.5):
        self.session = session or ThrottledSession("bilibili")
        self.session.headers.update(HEADERS)
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        if warm:
            try:
                self._warm_cookie()
            except Exception:
                pass  # 预热失败不致命（如测试 FakeSession），后续请求仍会尝试
        try:
            load_cookie(self)  # 自动注入登录 cookie（搜索/AI 字幕均需要）
        except Exception:
            pass

    def _warm_cookie(self, force: bool = False) -> None:
        """访问主页预热 buvid3 cookie（bilibili 对无 cookie 的裸 API 请求返回 412）；force=True 强制重取"""
        cookies = getattr(self.session, "cookies", None)
        if not force and cookies is not None and cookies.get("buvid3"):
            return
        params = {"_": int(time.time())} if force else None
        try:
            self.session.get("https://www.bilibili.com", params=params, timeout=self.timeout)
        except Exception:
            pass  # 预热失败不致命，后续请求仍会尝试

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避 + 抖动：base * 2^attempt + U(0, 0.4)"""
        return self.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.4)

    def _get(self, url_or_path: str, params: dict = None, envelope: bool = True):
        """带限流重试的 GET：仅对 412/429/5xx、超时/连接错误、风控 HTML 重试；
        业务错误码（code != 0）不重试。envelope=True 时解包 {code,data} 信封。
        412 风控重试前强制刷新 buvid3 cookie。"""
        url = url_or_path if url_or_path.startswith("http") else BASE + url_or_path
        last = None
        rate_limited = False
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self._backoff_delay(attempt - 1))
                if rate_limited:
                    self._warm_cookie(force=True)
            rate_limited = False
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last = e
                continue
            if resp.status_code in RETRYABLE_STATUS:
                rate_limited = resp.status_code == 412
                last = BiliError(resp.status_code, f"HTTP {resp.status_code}（限流/风控）")
                continue
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                last = BiliError(resp.status_code,
                                 f"响应不是 JSON（HTTP {resp.status_code}，可能被风控拦截）")
                rate_limited = True  # 风控页 → 重试并刷新 cookie
                continue
            if envelope:
                if data.get("code") != 0:
                    raise BiliError(data.get("code", -1), data.get("message", "未知错误"))
                return data["data"]
            return data
        if isinstance(last, BiliError):
            raise last
        raise BiliError(-1, f"请求失败（已重试 {self.max_retries} 次）：{str(last)[:120]}")

    def get_video_info(self, bvid: str = "", aid: int = 0) -> VideoInfo:
        """视频元信息 + 分P列表（批量扩展点：pages 即全部分P）"""
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return VideoInfo(self._get("/x/web-interface/view", params))

    def get_subtitles(self, bvid: str, cid: int) -> list:
        """字幕列表（含 AI 字幕）；空列表 = 无字幕"""
        data = self._get("/x/player/v2", {"bvid": bvid, "cid": cid})
        return (data.get("subtitle") or {}).get("subtitles") or []

    @staticmethod
    def _provenance_of(sub: dict) -> str:
        """来源分级（M13 §8.3）：lan 以 ai- 开头或 lan_doc 含「自动生成」→ auto；否则 human"""
        lan = (sub.get("lan") or "").lower()
        doc = sub.get("lan_doc") or ""
        if lan.startswith("ai-") or "自动生成" in doc:
            return "auto"
        return "human"

    def fetch_subtitle(self, bvid: str, cid: int, duration: int = None,
                       lang: str = None) -> tuple:
        """取字幕：取轨道（人工优先）→ 取全文 → (lines, provenance)。
        无字幕（无轨道或轨道内容为空）→ ([], "none")；duration 仅为调用兼容保留
        （旧覆盖度/双抓一致性防线已退役，校验由 core 统一承担）。"""
        subs = self.get_subtitles(bvid, cid)
        if not subs:
            return [], "none"
        chosen = self._pick_subtitle(subs, lang)
        lines = self._fetch_subtitle_once(chosen)
        if not lines:
            return [], "none"
        return lines, self._provenance_of(chosen)

    @staticmethod
    def _pick_subtitle(subs: list, lang: str = None) -> dict:
        """选择字幕：指定 lang 优先（如 ai-ja / ja-JP）；未指定时中文优先，其次任意"""
        if lang:
            for s in subs:
                if lang.lower() in s.get("lan", "").lower():
                    return s
        for s in subs:
            if "zh" in s.get("lan", "") and not s.get("lan", "").startswith("ai-"):
                return s
        for s in subs:
            if "zh" in s.get("lan", ""):
                return s
        return subs[0]

    def _fetch_subtitle_once(self, chosen: dict) -> list:
        """单次获取所选字幕轨全文；轨道无 url → 视为无字幕返回 []，由上层降级 Whisper"""
        url = (chosen or {}).get("subtitle_url") or ""
        if not url:
            return []
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        payload = self._get(url, envelope=False)  # 字幕文件是裸 JSON（无 code/data 信封）
        return [SubtitleLine(float(l["from"]), float(l["to"]), l["content"])
                for l in (payload.get("body") or [])]

    def get_space_videos(self, mid, ps: int = 30, pn: int = 1, order: str = "pubdate") -> dict:
        """UP 主视频列表（wbi 签名；基建与 search 同源）→ {videos, total, ps}"""
        img_key, sub_key = _get_wbi_keys(self.session, self.timeout)
        params = {"mid": int(mid), "ps": int(ps), "pn": int(pn), "order": order,
                  "platform": "web", "web_location": 1550101}
        params["wts"] = int(time.time())
        params["w_rid"] = hashlib.md5(
            (urllib.parse.urlencode(sorted(params.items()))
             + get_mixin_key(img_key, sub_key)).encode()
        ).hexdigest()
        data = self._get("/x/space/wbi/arc/search", params)
        page = data.get("page") or {}
        return {"videos": ((data.get("list") or {}).get("vlist") or []),
                "total": page.get("count") or 0, "ps": page.get("ps") or ps}

    def search(self, keyword: str, limit: int = 5) -> list:
        """搜索视频 → [{bvid,title,author,duration}]；需要登录 cookie（未登录搜索被风控）"""
        if not (self.session.cookies.get("SESSDATA") or load_cookie(self)):
            raise BiliError(-101, "搜索需要登录 cookie：请将 SESSDATA=xxx 写入 scripts/.bili_cookie "
                                  "或设置环境变量 BILI_COOKIE，或直接提供视频链接")
        img_key, sub_key = _get_wbi_keys(self.session, self.timeout)
        params = {"search_type": "video", "keyword": keyword,
                  "page": 1, "page_size": max(1, limit)}
        params["wts"] = int(time.time())
        params["w_rid"] = hashlib.md5(
            (urllib.parse.urlencode(sorted(params.items())) + get_mixin_key(img_key, sub_key)).encode()
        ).hexdigest()
        data = self._get("/x/web-interface/wbi/search/type", params)
        out = []
        for r in (data.get("result") or [])[:limit]:
            out.append({"bvid": r.get("bvid", ""),
                        "title": re.sub(r"<[^>]+>", "", r.get("title", "")),
                        "author": r.get("author", ""),
                        "duration": r.get("duration", "")})
        return out

    def get_audio_url(self, bvid: str, cid: int) -> str:
        """音频直链（Whisper 兜底）；取码率最高一路"""
        data = self._get("/x/player/playurl", {"bvid": bvid, "cid": cid, "fnval": 16})
        audios = (data.get("dash") or {}).get("audio") or []
        if not audios:
            raise BiliError(-404, "无法获取音频流（可能需要登录）")
        audios.sort(key=lambda a: a.get("bandwidth", 0), reverse=True)
        return audios[0]["baseUrl"]

    def get_video_url(self, bvid: str, cid: int, lowest: bool = False) -> str:
        """视频画面流直链；lowest=True 取最低码率（归属复检抽帧省带宽），默认最高"""
        data = self._get("/x/player/playurl", {"bvid": bvid, "cid": cid, "fnval": 16})
        videos = (data.get("dash") or {}).get("video") or []
        if not videos:
            raise BiliError(-404, "无法获取视频流（可能需要登录）")
        videos.sort(key=lambda v: v.get("bandwidth", 0), reverse=not lowest)
        return videos[0]["baseUrl"]

    def check_login(self) -> bool:
        """检测登录态：nav 接口 code=-101 表示 cookie 过期/未登录"""
        try:
            self._get("/x/web-interface/nav", {})
            return True
        except BiliError as e:
            if e.code == -101:
                return False
            raise

    def get_fav_folders(self) -> list:
        """当前账号的收藏夹列表 → [{id, title, media_count}]；需登录"""
        nav = self._get("/x/web-interface/nav", {})
        mid = nav.get("mid") or 0
        data = self._get("/x/v3/fav/folder/created/list-all", {"up_mid": mid})
        out = []
        for f in (data.get("list") or []):
            out.append({"id": f["id"], "title": f.get("title", ""),
                        "media_count": f.get("media_count", 0)})
        return out

    def get_fav_medias(self, media_id: int, pn: int = 1, ps: int = 20) -> list:
        """收藏夹内容 → [{bvid, title, duration}]；ps 上限 20"""
        ps = min(max(1, ps), 20)  # 接口上限 20，超出会被 -400 拒绝
        data = self._get("/x/v3/fav/resource/list",
                         {"media_id": media_id, "pn": pn, "ps": ps})
        out = []
        for m in (data.get("medias") or []):
            out.append({"bvid": m.get("bvid", ""), "title": m.get("title", ""),
                        "duration": m.get("duration", 0)})
        return out

    def get_toview(self) -> list:
        """稍后再看 → [{bvid,title,duration,owner}]（需登录 cookie）"""
        d = self._get("/x/v2/history/toview")
        return [{"bvid": it.get("bvid") or "",
                 "title": it.get("title") or "",
                 "duration": it.get("duration") or 0,
                 "owner": ((it.get("owner") or {}).get("name") or "")}
                for it in (d.get("list") or [])]

    def get_history(self, limit: int = 30, ps: int = 30) -> list:
        """观看历史（data 为列表；以末条 view_at 作 max 游标续拉）→ [{bvid,title,duration}]"""
        items, max_at, seen = [], None, set()
        while len(items) < limit:
            params = {"ps": min(ps, max(1, limit - len(items)))}
            if max_at:
                params["max"] = max_at
            d = self._get("/x/v2/history", params)
            page = d if isinstance(d, list) else (d.get("list") or [])
            new = [it for it in page if (it.get("bvid"), it.get("view_at")) not in seen]
            if not new:
                break
            for it in new:
                seen.add((it.get("bvid"), it.get("view_at")))
                items.append({"bvid": it.get("bvid") or "",
                              "title": it.get("title") or "",
                              "duration": it.get("duration") or 0,
                              "view_at": it.get("view_at") or 0})
            max_at = page[-1].get("view_at") if page else None
            if not max_at or len(page) < params["ps"]:
                break
        return items[:limit]

    def get_followings(self, limit: int = 50, ps: int = 50) -> list:
        """关注列表 → [{mid,uname,sign}]（需登录 cookie）"""
        nav = self._get("/x/web-interface/nav", {})
        own_mid = nav.get("mid") or 0
        items, pn = [], 1
        while len(items) < limit:
            d = self._get("/x/relation/followings",
                          {"vmid": own_mid, "pn": pn, "ps": ps, "order": "desc"})
            page = d.get("list") or []
            items.extend({"mid": u.get("mid"), "uname": u.get("uname") or "",
                          "sign": (u.get("sign") or "")[:40]}
                         for u in page)
            if not page or len(page) < ps:
                break
            pn += 1
        return items[:limit]


# ---------------- 输入解析（链接 / BV号 / av号 → VideoSpec） ----------------

class VideoSpec:
    def __init__(self, bvid: str = "", aid: int = 0, page: int = 1, raw: str = ""):
        self.bvid, self.aid, self.page, self.raw = bvid, aid, page, raw

    def __repr__(self):
        return f"VideoSpec(bvid={self.bvid!r}, aid={self.aid}, page={self.page})"


_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_PAGE_RE = re.compile(r"[?&]p=(\d+)")


def resolve_input(text: str) -> VideoSpec:
    """链接/BV/av → VideoSpec；名称类输入抛 SearchNeeded（由 CLI 走搜索）"""
    text = text.strip()
    page = 1
    m = _PAGE_RE.search(text)
    if m:
        page = max(1, int(m.group(1)))
    m = _BV_RE.search(text)
    if m:
        return VideoSpec(bvid=m.group(0), page=page, raw=text)
    m = _AV_RE.search(text)
    if m:
        return VideoSpec(aid=int(m.group(1)), page=page, raw=text)
    raise SearchNeeded(text)


# ---------------- 适配器（SourceAdapter 实现） ----------------

@register
class BilibiliAdapter:
    name = "bilibili"
    kinds = {"video"}
    language_hint = "zh"   # 平台主语言（M13 §12：转写语言提示）
    has_video = True       # 具备画面层（档位复检可用）

    def __init__(self, client=None):
        self._client = client
        self.lang = None          # 字幕语言（流水线按 CLI 参数注入）

    @property
    def client(self):
        """惰性创建 ApiClient（import/注册时不联网）"""
        if self._client is None:
            self._client = ApiClient()
        return self._client

    def match(self, url: str) -> bool:
        if not url:
            return False
        u = url.strip()
        return bool(re.search(r"(bilibili\.com|b23\.tv)", u, re.I)
                    or _BV_RE.fullmatch(u) or _AV_RE.fullmatch(u))

    def resolve(self, q: str) -> ItemRef:
        sm = _SPACE_RE.search(q or "")
        if sm and "/favlist" not in (q or ""):
            mid = sm.group(1)
            return ItemRef(platform="bilibili", item_id=f"space-{mid}",
                           url=f"https://space.bilibili.com/{mid}", sub_id="1",
                           extra={"kind": "space", "mid": mid})
        spec = resolve_input(q)
        return ItemRef(platform="bilibili", item_id=spec.bvid,
                       url=(f"https://www.bilibili.com/video/{spec.bvid}" if spec.bvid else ""),
                       sub_id=str(spec.page or 1),
                       extra={"aid": spec.aid, "page": spec.page, "spec": spec})

    def _list_space(self, ref, limit: int = 300) -> list:
        """UP 主视频列表：wbi 分页（≤limit 条）"""
        mid = ref.extra["mid"]
        items, pn, total = [], 1, None
        while len(items) < limit:
            d = self.client.get_space_videos(mid, ps=30, pn=pn)
            vlist = d.get("videos") or []
            total = d.get("total") if total is None else total
            if not vlist:
                break
            for v in vlist:
                bvid = (v.get("bvid") or "").strip()
                if not bvid:
                    continue
                items.append(ItemRef(
                    platform="bilibili", item_id=bvid,
                    url=f"https://www.bilibili.com/video/{bvid}",
                    sub_id=str(len(items) + 1), title=(v.get("title") or "").strip(),
                    duration=_length_to_sec(v.get("length")),
                    extra={"bvid": bvid, "aid": v.get("aid"),
                           "owner": (v.get("author") or "").strip(),
                           "desc": (v.get("description") or "")[:200],
                           "view": v.get("play") or 0, "pubdate": v.get("created")}))
            if (total and len(items) >= total) or len(vlist) < 30:
                break
            pn += 1
        return items

    def list_items(self, ref):
        """多 P 展开：每个分P 一个 ItemRef（带上 info/cid 供后续复用，避免重复请求）"""
        if (ref.extra or {}).get("kind") == "space":
            return self._list_space(ref)
        info = self.client.get_video_info(bvid=ref.item_id, aid=ref.extra.get("aid") or 0)
        items = []
        for p in info.pages:
            items.append(ItemRef(
                platform="bilibili", item_id=info.bvid,
                url=f"https://www.bilibili.com/video/{info.bvid}?p={p.page}",
                sub_id=str(p.page), title=p.part, duration=p.duration,
                extra={"aid": info.aid, "cid": p.cid, "owner": info.owner,
                       "desc": info.desc, "pubdate": info.pubdate,
                       "info": info, "page_obj": p}))
        return items

    def fetch_meta(self, ref) -> ItemMeta:
        info = ref.extra.get("info")
        if info is None:
            info = self.client.get_video_info(bvid=ref.item_id, aid=ref.extra.get("aid") or 0)
        page_obj = ref.extra.get("page_obj")
        return ItemMeta(title=info.title, author=info.owner,
                        url=ref.url, published=str(info.pubdate), duration=ref.duration,
                        desc=info.desc, stats=dict(info.stat or {}),
                        page=str(page_obj.page) if page_obj else str(ref.sub_id or ""),
                        page_part=ref.title or "",
                        extra={"video_duration": info.duration, "bvid": info.bvid})

    def fetch_content(self, ref) -> ContentResult:
        lines, provenance = self.client.fetch_subtitle(
            ref.item_id, ref.extra["cid"], duration=ref.duration, lang=self.lang)
        return ContentResult(
            kind="timeline", status="ok" if lines else "empty",
            segments=[{"start": l.start, "end": l.end, "text": l.content} for l in lines],
            provenance=provenance, language="zh",
            label="字幕(AI)" if provenance == "auto" else "字幕")

    def fetch_media_url(self, ref, media: str = "audio", lowest: bool = False):
        """media=video → 画面流（复检抽帧，lowest 省带宽）；audio → 音频流（Whisper）"""
        if media == "video":
            return self.client.get_video_url(ref.item_id, ref.extra["cid"], lowest=lowest)
        return self.client.get_audio_url(ref.item_id, ref.extra["cid"])
