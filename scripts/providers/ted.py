"""TED 适配器（timeline）：官网公开字幕（多语含 zh-cn）+ h264 mp4 兜底。

尽调（2026-10-08）：talk 页 `__NEXT_DATA__`.videoData 提供元数据与 mp4；
字幕接口 talks/subtitles/id/<id>/lang/<lang> 返回 JSON（毫秒）；无鉴权、低风控；
yt-dlp 的 TedTalk 提取器当前失效（站点改版），不复用。
"""
import json
import re

import requests

from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_TALK_RE = re.compile(r"ted\.com/talks/([a-z0-9_]+)", re.I)
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SUB_API = "https://www.ted.com/talks/subtitles/id/{tid}/lang/{lang}"
_LANG_FALLBACK = ["zh-cn", "zh-tw", "en"]
_LANG_ALIASES = {"zh": "zh-cn", "zh-hans": "zh-cn", "zh-hant": "zh-tw", "cn": "zh-cn"}


class TedError(ValueError):
    """页面结构/接口异常（可读异常）"""


def _norm_lang(lang: str) -> str:
    lang = (lang or "").strip().lower().replace("_", "-")
    return _LANG_ALIASES.get(lang, lang)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@register
class TedAdapter:
    name = "ted"
    kinds = {"video"}

    def __init__(self, session: requests.Session = None):
        self._session = session
        self._page_cache = {}

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("ted")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        return bool(_TALK_RE.search(url or ""))

    def resolve(self, q: str) -> ItemRef:
        m = _TALK_RE.search(q or "")
        if not m:
            raise SearchNeeded(q)
        slug = m.group(1).lower()
        return ItemRef(platform="ted", item_id=f"talk-{slug}",
                       url=f"https://www.ted.com/talks/{slug}", sub_id="1",
                       extra={"slug": slug})

    def list_items(self, ref):
        return [ref]

    # ---------------- 取数 ----------------

    def _video_data(self, ref) -> dict:
        """页面 → videoData（单次运行内缓存，减少请求）"""
        cached = self._page_cache.get(ref.url)
        if cached is not None:
            return cached
        r = self.session.get(ref.url, timeout=30)
        if r.status_code != 200:
            raise TedError(f"TED 页面不可达（HTTP {r.status_code}）")
        m = _NEXT_RE.search(r.text or "")
        if not m:
            raise TedError("TED 页面缺少 __NEXT_DATA__（站点结构可能变化）")
        vd = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("videoData") or {}
        if not vd.get("id"):
            raise TedError("TED 页面缺少 videoData")
        self._page_cache[ref.url] = vd
        return vd

    def fetch_meta(self, ref) -> ItemMeta:
        vd = self._video_data(ref)
        return ItemMeta(title=vd.get("title") or ref.extra.get("slug", ref.title),
                        author=vd.get("presenterDisplayName", ""), url=ref.url,
                        published=(vd.get("publishedAt") or "")[:10], desc=vd.get("description", ""),
                        duration=int(vd.get("duration") or 0),
                        stats={"view": vd.get("viewedCount")},
                        page=ref.sub_id or "1", page_part="")

    def fetch_media_url(self, ref, media="audio", lowest=False):
        """h264 mp4 兜底（音频经 PyAV 解）；无则 None"""
        try:
            vd = self._video_data(ref)
        except TedError:
            return None
        for f in (((vd.get("videoPlayerData") or {}).get("resources") or {}).get("h264") or []):
            if isinstance(f, dict) and f.get("file"):
                return f["file"]
        return None

    def fetch_content(self, ref) -> ContentResult:
        try:
            vd = self._video_data(ref)
        except TedError as e:
            return ContentResult(kind="timeline", status="empty", reason=str(e)[:120], label="字幕")
        tid = vd.get("id")
        langs = []
        pref = _norm_lang(getattr(self, "lang", None))
        if pref:
            langs.append(pref)
        langs += [l for l in _LANG_FALLBACK if l not in langs]
        last_err = ""
        for lang in langs:
            try:
                r = self.session.get(_SUB_API.format(tid=tid, lang=lang), timeout=25,
                                     headers={"Referer": ref.url})
            except Exception as e:
                last_err = str(e)[:80]
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            try:
                caps = (r.json() or {}).get("captions") or []
            except Exception:
                continue
            segs = []
            for c in caps:
                text = _clean((c.get("content") or "").replace("\n", " "))
                if not text:
                    continue
                start = float(c.get("startTime") or 0) / 1000.0
                end = start + float(c.get("duration") or 0) / 1000.0
                segs.append({"start": start, "end": end, "text": text})
            if not segs:
                continue
            status, reason = self._grade(segs, int(vd.get("duration") or 0))
            return ContentResult(kind="timeline", status=status, segments=segs,
                                 reason=reason, label=f"字幕(官方 {lang})")
        return ContentResult(kind="timeline", status="empty",
                             reason=f"无可用官方字幕（{last_err or '接口无数据'}）", label="字幕")

    @staticmethod
    def _grade(segs, duration):
        """分级：段数过少或覆盖不足半程 → suspect"""
        text_len = sum(len(s["text"]) for s in segs)
        if len(segs) < 10 or (duration and segs[-1]["end"] < duration * 0.5):
            return "suspect", f"覆盖不足（{len(segs)} 段/{text_len} 字）"
        return "ok", ""
