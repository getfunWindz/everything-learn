"""小红书适配器（document）：单篇笔记（图文/视频）+ explore token 兜底（实验性）。

尽调（2026-10-13）：访客可读但依赖短时效 xsec_token（分享链自带或 explore 现取）；
note 页 SSR __INITIAL_STATE__.noteDetailMap；图/视频 CDN 直链可下。缓速 12s。
账号级能力（主页/收藏）未来里程碑，见 docs/plans/2026-10-13-m10-xiaohongshu.md。
"""
import json
import re
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
_SHORT_RE = re.compile(r"https?://xhslink\.(?:cn|com)/[A-Za-z0-9/]+")
_TOKEN_IN_EXPLORE = r"/explore/{nid}\?xsec_token=([A-Za-z0-9_\-=]+)"
_EXPLORE_URL = "https://www.xiaohongshu.com/explore"


class XhsError(ValueError):
    """小红书取数失败（token 失效/笔记不可读，可读）"""


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
        return bool(_ID_RE.search(url) or _SHORT_RE.match(url) or _USER_RE.search(url))

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
        mu = _USER_RE.search(q)
        if mu:
            uid = mu.group(1)
            return ItemRef(platform="xiaohongshu", item_id=f"user-{uid}",
                           url=f"https://www.xiaohongshu.com/user/profile/{uid}",
                           sub_id="1",
                           extra={"kind": "user_notes", "user_id": uid})
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

    def _list_user_notes(self, ref, limit=20):
        """主页笔记首屏（SSR 解析；需登录）"""
        with browserfetch.BrowserSession("xiaohongshu") as s:
            s.goto(ref.url)
            html = s.html()
        user = ((extract_initial_state(html) or {}).get("user") or {})
        if not user.get("loggedIn"):
            raise XhsError("小红书未登录：请先在本机自动化浏览器中登录小红书（可见窗口扫码）")
        notes = parse_user_notes(html)
        if not notes:
            raise XhsError("未解析到主页笔记（可能页面结构变化或该用户无公开笔记）")
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
        """explore 兜底：加载访客页，找目标笔记的现取 token（实验性，命中率低）"""
        pat = _TOKEN_IN_EXPLORE.format(nid=re.escape(note_id))
        for _ in range(max(0, self.harvest_loads)):
            try:
                r = self.session.get(_EXPLORE_URL, timeout=25)
            except requests.RequestException:
                continue
            m = re.search(pat, (r.text or "").replace("&amp;", "&"))
            if m:
                return m.group(1)
        return None

    def _fetch_and_parse(self, ref, note_id: str, token: str) -> dict:
        html = self._get_note_html(self._note_url(note_id, token))
        values = list(((extract_initial_state(html).get("note") or {})
                       .get("noteDetailMap") or {}).values())
        if not values or not values[0].get("note"):
            raise XhsError("笔记页面无内容（可能已删除）")
        data = parse_note(values[0]["note"])
        self._notes[ref.item_id] = data
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
        # token 缺失或失效 → explore 兜底（懒触发：不浪费请求）
        harvested = self._harvest_token(nid)
        if harvested:
            try:
                return self._fetch_and_parse(ref, nid, harvested)
            except XhsError as e:
                last = e
        if not token and not harvested:
            raise XhsError("笔记链接缺少 xsec_token，且 explore 兜底未命中；"
                           "请从小红书 App「分享 → 复制链接」重新获取（含 token），稍后再试")
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
        if d.get("video_url") and d.get("video_ms"):
            blocks.append(self._transcribe_block(d))
        text_len = sum(len(b["text"] or "") for b in blocks)
        status = "ok" if (text_len >= 20 or d["images"]) else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字）"
        return ContentResult(kind="document", status=status, blocks=blocks,
                             images=d["images"], reason=reason, label="正文")

    def _transcribe_block(self, d: dict) -> dict:
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
            return {"heading": "视频口播内容", "text": text or "（视频无口播内容）"}
        except Exception as e:
            return {"heading": "视频口播内容", "text": f"（视频转写失败：{str(e)[:80]}）"}

    def fetch_media_url(self, ref, media="audio", lowest=False):
        try:
            return self._data(ref).get("video_url")
        except XhsError:
            return None
