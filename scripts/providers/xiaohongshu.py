"""小红书适配器（document）：单篇笔记（图文/视频）+ explore token 兜底（实验性）。

尽调（2026-10-13）：访客可读但依赖短时效 xsec_token（分享链自带或 explore 现取）；
note 页 SSR __INITIAL_STATE__.noteDetailMap；图/视频 CDN 直链可下。缓速 12s。
账号级能力（主页/收藏）未来里程碑，见 docs/plans/2026-10-13-m10-xiaohongshu.md。
"""
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

from core import browserfetch
from core import config
from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_ID_RE = re.compile(r"xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]{24})")
_USER_RE = re.compile(r"xiaohongshu\.com/user/profile/([0-9a-f]{24})")
_COLLECT_PAGE_API = re.compile(r"/api/sns/web/v2/note/collect/page")   # 取证确认（见 m12-ladder2-dd）
_USER_POSTED_API = re.compile(r"/api/sns/web/v1/user_posted")   # 主页笔记分页（滚动触发）
_SHORT_RE = re.compile(r"https?://xhslink\.(?:cn|com)/[A-Za-z0-9/]+")
_URL_RE = re.compile(r"https?://[^\s\"'<>，。]+")


def extract_share_url(text: str) -> str:
    """从 App 分享文本中提取首个链接（分享文本常夹带 emoji/「复制本条信息」）"""
    m = _URL_RE.search(text or "")
    return m.group(0) if m else ""
_TOKEN_IN_EXPLORE = r"/explore/{nid}\?xsec_token=([A-Za-z0-9_\-=]+)"
_EXPLORE_URL = "https://www.xiaohongshu.com/explore"
# token 缓存（成功取数后写入；无 token 链接优先复用，避免只能依赖随机 explore 流）
_TOKEN_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "cache", "xhs_tokens.json")
_TOKEN_TTL = 6 * 3600          # 保守 6h；失效会自动回落兜底并给可读提示


def _load_token_cache() -> dict:
    try:
        with open(_TOKEN_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_token_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_TOKEN_CACHE), exist_ok=True)
        tmp = _TOKEN_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, _TOKEN_CACHE)
    except OSError:
        pass


def _cached_token(note_id: str) -> str:
    ent = _load_token_cache().get(note_id) or {}
    try:
        fresh = (time.time() - float(ent.get("ts") or 0)) <= _TOKEN_TTL
    except (TypeError, ValueError):
        fresh = False
    return ent.get("token") or "" if fresh else ""


def _remember_token(note_id: str, token: str) -> None:
    if not note_id or not token:
        return
    cache = _load_token_cache()
    cache[note_id] = {"token": token, "ts": time.time()}
    _save_token_cache(cache)


class XhsError(ValueError):
    """小红书取数失败（token 失效/笔记不可读，可读）"""


def _click_tab_js(name: str) -> str:
    """JS：按文本精准点击 tab（XHS tab 为 SPAN，text= 选择器不稳）"""
    return ("() => { const els = [...document.querySelectorAll('div,span,a')]"
            f".filter(e => (e.textContent || '').trim() === {name!r} && e.children.length <= 1);"
            "if (!els.length) return 'no-match'; els[els.length - 1].click(); return 'clicked'; }")


def extract_initial_state(html: str) -> dict:
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*</script>", html or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1).replace("undefined", "null"))
    except Exception:
        return {}


def parse_note(note: dict) -> dict:
    """note 对象 → 归一化 dict（正文/图片/视频/元信息）"""
    desc = note.get("desc") or ""
    tags = [t.get("name") for t in (note.get("tagList") or []) if t.get("name")]
    text = desc + (("\n\n标签：" + " ".join("#" + t for t in tags)) if tags else "")
    images = []
    for item in (note.get("imageList") or []):
        for info in (item.get("infoList") or []):
            if info.get("imageScene") == "WB_DFT" and info.get("url"):
                images.append(info["url"].replace("http://", "https://"))
                break
    video_url, video_ms = None, 0
    if note.get("type") == "video":
        media = (note.get("video") or {}).get("media") or {}
        h264 = ((media.get("stream") or {}).get("h264") or [])
        if h264:
            video_url = (h264[0].get("masterUrl") or "").replace("http://", "https://") or None
            video_ms = int(h264[0].get("duration") or 0)
        images = []          # 视频笔记封面不作为正文图
    user = note.get("user") or {}
    interact = note.get("interactInfo") or {}
    published = ""
    if note.get("time"):
        try:
            published = datetime.fromtimestamp(int(note["time"]) / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            published = ""
    title = (note.get("title") or "").strip() or (desc.splitlines() or [""])[0].strip()[:40] or "小红书笔记"
    return {"title": title, "text": text, "author": user.get("nickname") or "",
            "published": published, "images": images,
            "video_url": video_url, "video_ms": video_ms,
            "stats": {"like": interact.get("likedCount"), "collect": interact.get("collectedCount"),
                      "comment": interact.get("commentCount")}}


def parse_user_notes(html: str) -> list:
    """个人页 SSR → [{note_id,title,type,token}]（首屏；去重保序）"""
    user = ((extract_initial_state(html) or {}).get("user") or {})
    pages = user.get("notes") or []
    first = pages[0] if pages and isinstance(pages[0], list) else []
    out, seen = [], set()
    for it in first:
        nc = (it or {}).get("noteCard") or {}
        nid = nc.get("noteId")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        title = (nc.get("displayTitle") or "").strip()
        out.append({"note_id": nid, "title": title or f"小红书笔记 {nid}",
                    "type": nc.get("type") or "",
                    "token": (it or {}).get("xsecToken") or ""})
    return out


def parse_collect_payload(payloads: list) -> list:
    """收藏接口响应 → [{note_id,title,type,token}]（去重保序）"""
    out, seen = [], set()
    for p in (payloads or []):
        data = (p or {}).get("data") or {}
        for it in (data.get("notes") or []):
            nid = it.get("note_id") or it.get("noteId")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            title = (it.get("display_title") or it.get("displayTitle") or "").strip()
            out.append({"note_id": nid, "title": title or f"小红书笔记 {nid}",
                        "type": it.get("type") or "",
                        "token": it.get("xsec_token") or it.get("xsecToken") or ""})
    return out


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
        return bool(_ID_RE.search(url) or _SHORT_RE.search(url) or _USER_RE.search(url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        if not (_ID_RE.search(q) or _SHORT_RE.search(q) or _USER_RE.search(q)):
            extracted = extract_share_url(q)
            if extracted:
                q = extracted
        m = _ID_RE.search(q)
        if not m and _SHORT_RE.search(q):
            short = _SHORT_RE.search(q).group(0)
            try:
                r = self.session.get(short, allow_redirects=True, timeout=20, stream=True)
                r.close()
                m = _ID_RE.search(getattr(r, "url", "") or "")
                q = getattr(r, "url", q)
            except requests.RequestException as e:
                raise XhsError(f"短链展开失败：{str(e)[:80]}")
        mu = _USER_RE.search(q)
        if mu:
            uid = mu.group(1)
            tab = (parse_qs(urlparse(q).query).get("tab") or [""])[0]
            extra = {"kind": "user_notes", "user_id": uid}
            if tab:
                extra["tab"] = tab
            return ItemRef(platform="xiaohongshu", item_id=f"user-{uid}",
                           url=f"https://www.xiaohongshu.com/user/profile/{uid}",
                           sub_id="1", extra=extra)
        if m:
            nid = m.group(1)
            token = (parse_qs(urlparse(q).query).get("xsec_token") or [""])[0]
            return ItemRef(platform="xiaohongshu", item_id=f"xhs-{nid}",
                           url=q, sub_id="1",
                           extra={"kind": "note", "note_id": nid, "token": token})
        raise SearchNeeded(q)

    def list_items(self, ref):
        if (ref.extra or {}).get("kind") == "user_notes":
            return self._list_user_notes(ref)
        return [ref]

    @staticmethod
    def _notes_to_items(notes, limit):
        items = []
        for i, n in enumerate(notes[:limit], 1):
            url = f"https://www.xiaohongshu.com/explore/{n['note_id']}"
            if n["token"]:
                url += f"?xsec_token={n['token']}&xsec_source=pc_user"
            items.append(ItemRef(platform="xiaohongshu", item_id=f"xhs-{n['note_id']}",
                                 url=url, sub_id=str(i), title=n["title"],
                                 extra={"kind": "note", "note_id": n["note_id"],
                                        "token": n["token"]}))
        return items

    def _list_user_notes(self, ref, limit=60, scroll_times=4):
        """主页笔记：SSR 首屏 + 滚动分页（user_posted 拦截）；?tab=collect 时枚举收藏"""
        if (ref.extra or {}).get("tab") == "collect":
            return self._list_collect(ref, limit=limit)
        with browserfetch.BrowserSession("xiaohongshu") as s:
            s.goto(ref.url)
            html = s.html()
            user = ((extract_initial_state(html) or {}).get("user") or {})
            if not user.get("loggedIn"):
                raise XhsError("小红书未登录：请先在本机自动化浏览器中登录小红书（可见窗口扫码）")

            def act():
                s.pause(1200)
                s.scroll_wheel(scroll_times)

            payloads = s.tap_json(_USER_POSTED_API.pattern, action=act, min_results=0)
        notes = parse_user_notes(html)
        seen = {n["note_id"] for n in notes}
        for n in parse_collect_payload(payloads):
            if n["note_id"] not in seen:
                seen.add(n["note_id"])
                notes.append(n)
        if not notes:
            raise XhsError("未解析到主页笔记（可能页面结构变化或该用户无公开笔记）")
        return self._notes_to_items(notes, limit)

    def _list_collect(self, ref, limit=60, scroll_times=3):
        """我的收藏列表（collect/page 拦截；需登录）"""
        with browserfetch.BrowserSession("xiaohongshu") as s:
            s.goto(ref.url)
            user = ((extract_initial_state(s.html()) or {}).get("user") or {})
            if not user.get("loggedIn"):
                raise XhsError("小红书未登录：请先在本机自动化浏览器中登录小红书（可见窗口扫码）")

            def act():
                s.pause(1500)                        # 等页面水合出 tab
                for _ in range(10):
                    if s.evaluate(_click_tab_js("收藏")) == "clicked":
                        break
                    s.pause(500)
                s.scroll_wheel(scroll_times)

            payloads = s.tap_json(_COLLECT_PAGE_API.pattern, action=act)
        notes = parse_collect_payload(payloads)
        if not notes:
            raise XhsError("收藏为空或未解析到（可能未登录/结构变化）")
        return self._notes_to_items(notes, limit)

    # ---------------- 数据获取（token 现取现用 + explore 兜底） ----------------

    def _note_url(self, note_id: str, token: str) -> str:
        return (f"https://www.xiaohongshu.com/explore/{note_id}"
                f"?xsec_token={token}&xsec_source=pc_share")

    def _get_note_html(self, url: str) -> str:
        r = self.session.get(url, headers={"Referer": "https://www.xiaohongshu.com/"},
                             timeout=25)
        if "/404" in (r.url or ""):
            raise XhsError("笔记 token 失效或笔记不可读（sec-404）")
        return r.text

    def _harvest_token(self, note_id: str):
        """explore 兜底：先 requests 访客页，再登录态浏览器滚动收集卡片 token"""
        pat = _TOKEN_IN_EXPLORE.format(nid=re.escape(note_id))
        for _ in range(max(0, self.harvest_loads)):
            try:
                r = self.session.get(_EXPLORE_URL, timeout=25)
            except requests.RequestException:
                continue
            m = re.search(pat, (r.text or "").replace("&amp;", "&"))
            if m:
                return m.group(1)
        return self._harvest_token_browser(note_id)

    # 浏览器 explore 流滚动次数（虚拟列表；实测 20s 可收集 ~95 个 token）
    harvest_scrolls = 3

    @staticmethod
    def _token_from_links(hrefs, note_id: str):
        pat = _TOKEN_IN_EXPLORE.format(nid=re.escape(note_id))
        for h in hrefs or []:
            m = re.search(pat, (h or "").replace("&amp;", "&"))
            if m:
                return m.group(1)
        return None

    def _harvest_token_browser(self, note_id: str):
        """登录态浏览器 explore：滚动虚拟列表，按目标 note_id 命中 token

        实测（2026-10-18）：无 token 的笔记页在登录浏览器仍 404；但 explore
        流的卡片链接带现取 token（mouse.wheel 触发加载，window.scrollTo 无效）。
        任何异常/未登录 → None（保留上层可读错误）。"""
        try:
            from core import browserfetch
        except Exception:
            return None
        js = ("(() => [...document.querySelectorAll('a[href*=\"/explore/\"]')]"
              ".map(a => a.getAttribute('href'))"
              ".filter(h => h && h.includes('xsec_token')))()")
        try:
            with browserfetch.BrowserSession("xiaohongshu") as s:
                s.goto(_EXPLORE_URL)
                s.pause(3000)
                for i in range(max(0, self.harvest_scrolls) + 1):
                    token = self._token_from_links(s.evaluate(js), note_id)
                    if token:
                        return token
                    if i < self.harvest_scrolls:
                        s.page.mouse.move(600, 500)
                        s.page.mouse.wheel(0, 1600)
                        s.pause(2500)
        except Exception:
            return None
        return None

    def _fetch_and_parse(self, ref, note_id: str, token: str) -> dict:
        html = self._get_note_html(self._note_url(note_id, token))
        values = list(((extract_initial_state(html).get("note") or {})
                       .get("noteDetailMap") or {}).values())
        if not values or not values[0].get("note"):
            raise XhsError("笔记页面无内容（可能已删除）")
        data = parse_note(values[0]["note"])
        self._notes[ref.item_id] = data
        _remember_token(note_id, token)
        return data

    def _data(self, ref) -> dict:
        if ref.item_id in self._notes:
            return self._notes[ref.item_id]
        nid = ref.extra["note_id"]
        token = ref.extra.get("token") or ""
        last = None
        if token:
            try:
                return self._fetch_and_parse(ref, nid, token)
            except XhsError as e:
                last = e
        # 链接无 token 或已失效 → 先复用缓存 token（确定性路径）
        cached = _cached_token(nid)
        if cached and cached != token:
            try:
                return self._fetch_and_parse(ref, nid, cached)
            except XhsError as e:
                last = e
        # 最后才走 explore 兜底（requests → 浏览器滚动，实验性）
        harvested = self._harvest_token(nid)
        if harvested:
            try:
                return self._fetch_and_parse(ref, nid, harvested)
            except XhsError as e:
                last = e
        if not token and not harvested:
            raise XhsError("笔记链接缺少 xsec_token，且 explore 兜底未命中；"
                           "获取方法：打开小红书 App → 笔记右上角「分享」→「复制链接」，"
                           "把整段分享文字直接粘给 el.py（会自动提取链接），稍后再试")
        raise XhsError(f"{last or '笔记不可读'}；建议从小红书 App 重新分享（含 token 的链接）")

    def fetch_meta(self, ref) -> ItemMeta:
        d = self._data(ref)
        return ItemMeta(title=d["title"], author=d["author"], url=ref.url,
                        published=d["published"], desc=d["text"][:200],
                        stats=d["stats"], page=ref.sub_id or "1")

    def fetch_content(self, ref) -> ContentResult:
        try:
            d = self._data(ref)
        except XhsError as e:
            return ContentResult(kind="document", status="empty", reason=str(e)[:140], label="正文")
        blocks = [{"heading": "", "text": d["text"]}]
        video_lines = []
        if d.get("video_url") and d.get("video_ms"):
            block, video_lines = self._transcribe_block(d)
            blocks.append(block)
        text_len = sum(len(b["text"] or "") for b in blocks)
        status = "ok" if (text_len >= 20 or d["images"]) else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字）"
        return ContentResult(kind="document", status=status, blocks=blocks,
                             images=d["images"], reason=reason, label="正文",
                             video_lines=video_lines,
                             video_duration=(d["video_ms"] / 1000 if video_lines else 0.0))

    def _transcribe_block(self, d: dict):
        """视频口播转写 → (block, lines)；B2：lines 供流水线做档位评估"""
        try:
            import transcriber
            cfg = config.load_config()
            mode = cfg.get("whisper_vad", "auto")
            vad = None if mode == "auto" else bool(mode)
            lines = transcriber.transcribe_url(
                d["video_url"], model_size=cfg.get("whisper_model") or "medium",
                vad=vad, duration_sec=d["video_ms"] / 1000,
                vad_long_sec=cfg.get("vad_long_sec", 1800),
                headers={"User-Agent": _UA, "Referer": "https://www.xiaohongshu.com/"},
                language=None)          # 多语言内容：自动检测
            text = " ".join((l.get("text") or "").strip() for l in lines if (l.get("text") or "").strip())
            return {"heading": "视频口播内容", "text": text or "（视频无口播内容）"}, lines
        except Exception as e:
            return {"heading": "视频口播内容", "text": f"（视频转写失败：{str(e)[:80]}）"}, []

    def fetch_media_url(self, ref, media="audio", lowest=False):
        try:
            return self._data(ref).get("video_url")
        except XhsError:
            return None
